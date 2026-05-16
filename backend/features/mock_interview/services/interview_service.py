import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, cast
from uuid import UUID

from azure.storage.blob import BlobClient, BlobSasPermissions, generate_blob_sas
from fastapi import HTTPException

from core.config import settings
from features.mock_interview.ml_model import behavioral_pipeline
from features.mock_interview.repositories.mock_interview_repository import MockInterviewRepository
from features.mock_interview.services.assemblyai_service import AssemblyAIService
from features.mock_interview.services.behavioral_report_service import BehavioralReportService
from features.mock_interview.services.elevenlabs_service import ElevenLabsService
from features.mock_interview.services.technical_report_service import TechnicalReportService
from shared.providers.llm_models.gemini import Gemini
from shared.providers.storage.azure_blob_storage import get_azure_storage_provider

logger = logging.getLogger(__name__)


class MockInterviewService:
    def __init__(self, repository: MockInterviewRepository):
        self.repo = repository
        self.tts = ElevenLabsService()
        self.gemini = Gemini(settings)
        self.transcriber = AssemblyAIService()
        self.prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        self.behavioral_reports = BehavioralReportService(self.gemini, self.prompts_dir)
        self.technical_reports = TechnicalReportService(self.gemini, self.prompts_dir)

    # ------------------------------------------------------------------ #
    #  Session creation                                                    #
    # ------------------------------------------------------------------ #

    async def start_behavioral_session(self, role_name: str, user_id: UUID) -> Dict[str, Any]:
        role = await self.repo.get_role_by_name(role_name)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        session = await self.repo.create_session(
            user_id=user_id,
            role_id=UUID(role["role_id"]),
            session_type="behavioral",
        )
        if not session:
            raise HTTPException(status_code=500, detail="Failed to create session")

        questions = await self.repo.list_behavioral_questions(role_name)
        sas_token, blob_url, sas_expires_at = await self._create_video_upload_sas(
            UUID(session["session_id"])
        )

        return {
            "session_id": UUID(session["session_id"]),
            "questions": questions,
            "sas_token": sas_token,
            "blob_url": blob_url,
            "sas_expires_at": sas_expires_at,
        }

    async def start_technical_session(self, role_name: str, user_id: UUID) -> Dict[str, Any]:
        role = await self.repo.get_role_by_name(role_name)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        session = await self.repo.create_session(
            user_id=user_id,
            role_id=UUID(role["role_id"]),
            session_type="technical",
        )
        if not session:
            raise HTTPException(status_code=500, detail="Failed to create session")

        questions = await self.repo.list_technical_questions(UUID(role["role_id"]))
        sas_token, blob_url, sas_expires_at = await self._create_audio_upload_sas(
            UUID(session["session_id"])
        )

        return {
            "session_id": UUID(session["session_id"]),
            "questions": questions,
            "sas_token": sas_token,
            "blob_url": blob_url,
            "sas_expires_at": sas_expires_at,
        }

    # ------------------------------------------------------------------ #
    #  Audio TTS                                                           #
    # ------------------------------------------------------------------ #

    async def get_question_audio_bytes(self, question_id: UUID) -> bytes:
        question_text = await self.repo.get_question_text(question_id)
        if not question_text:
            raise HTTPException(status_code=404, detail="Question not found")
        try:
            return self.tts.synthesize(question_text)
        except Exception as e:
            logger.error(f"ElevenLabs TTS failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="ElevenLabs TTS failed")

    # ------------------------------------------------------------------ #
    #  Behavioral pipeline — upload → process → notify → delete blob      #
    # ------------------------------------------------------------------ #

    async def queue_behavioral_upload(
        self,
        session_id: UUID,
        blob_url: str,
        max_wait_seconds: int = 120,
        poll_interval_seconds: int = 5,
        min_size_bytes: int = 1024,
    ) -> None:
        deadline = datetime.utcnow().timestamp() + max_wait_seconds
        while datetime.utcnow().timestamp() < deadline:
            if await self._is_blob_ready(blob_url, min_size_bytes=min_size_bytes):
                await self._process_behavioral_upload(session_id, blob_url)
                return
            await asyncio.sleep(poll_interval_seconds)

        logger.warning("Behavioral blob not available before timeout; session left as pending")

    async def _process_behavioral_upload(self, session_id: UUID, blob_url: str) -> None:
        user_id: Optional[str] = None
        role_name: Optional[str] = None

        try:
            session = await self.repo.get_session(session_id)
            if not session:
                logger.warning("Behavioral: session not found, skipping")
                return

            claimed = await self.repo.claim_session_for_processing(session_id)
            if not claimed:
                logger.info("Behavioral: session already in_progress/completed, skipping duplicate run")
                return

            user_id = session.get("user_id")

            try:
                role = await self.repo.get_role_by_id(UUID(session["role_id"]))
                if role:
                    role_name = role.get("role_name")
            except Exception as e:
                logger.warning(f"Behavioral: failed to resolve role: {e}")

            video_bytes = await self._download_blob_bytes(blob_url)
            metrics = await asyncio.to_thread(self._run_behavioral_pipeline, video_bytes)

            behavioral_metrics = self._extract_behavioral_metrics(metrics)
            report = await self.behavioral_reports.build_report(
                behavioral_metrics, role_name=role_name
            )
            transcript_text = cast(str, metrics.get("transcript") or "")
            behavioral_score = self._extract_score(behavioral_metrics)

            await self.repo.upsert_behavioral_analysis(
                session_id=session_id,
                analysis_metrics=behavioral_metrics,
                behavioral_report=report,
                transcript=transcript_text,
                video_url=None,
                score=behavioral_score,
            )

            await self._safe_update_session_status(session_id, "completed")
            logger.info(f"Behavioral pipeline completed for session {session_id}")

            if user_id:
                asyncio.create_task(
                    self._send_fcm_notification(
                        session_id=session_id,
                        user_id=user_id,
                        role_name=role_name,
                        session_type="behavioral",
                    )
                )

        except Exception:
            await self._safe_update_session_status(session_id, "pending")
            logger.exception(f"Behavioral pipeline failed for session {session_id}")

        finally:
            deleted = await self._safe_delete_blob(blob_url)
            logger.info(f"Behavioral pipeline finished for session {session_id}; blob deleted: {deleted}")
            print(f"Behavioral pipeline finished for session {session_id}; blob deleted: {deleted}")

    # ------------------------------------------------------------------ #
    #  Technical pipeline — upload → process → notify → delete blob       #
    # ------------------------------------------------------------------ #

    async def queue_technical_upload(
        self,
        session_id: UUID,
        blob_url: str,
        max_wait_seconds: int = 120,
        poll_interval_seconds: int = 5,
        min_size_bytes: int = 1024,
    ) -> None:
        deadline = datetime.utcnow().timestamp() + max_wait_seconds
        while datetime.utcnow().timestamp() < deadline:
            if await self._is_blob_ready(blob_url, min_size_bytes=min_size_bytes):
                await self._process_technical_upload(session_id, blob_url)
                return
            await asyncio.sleep(poll_interval_seconds)

        logger.warning("Technical blob not available before timeout; session left as pending")

    async def _process_technical_upload(self, session_id: UUID, blob_url: str) -> None:
        user_id: Optional[str] = None

        try:
            session = await self.repo.get_session(session_id)
            if not session:
                logger.warning("Technical: session not found, skipping")
                return

            claimed = await self.repo.claim_session_for_processing(session_id)
            if not claimed:
                logger.info("Technical: session already in_progress/completed, skipping duplicate run")
                return

            user_id = session.get("user_id")

            role = await self.repo.get_role_by_id(UUID(session["role_id"]))
            if not role:
                logger.error(f"Technical: role not found for session {session_id}")
                await self._safe_update_session_status(session_id, "pending")
                return

            questions = await self.repo.list_technical_questions(UUID(session["role_id"]))
            if len(questions) < 5:
                logger.error(f"Technical: insufficient questions for session {session_id}")
                await self._safe_update_session_status(session_id, "pending")
                return

            audio_bytes = await self._download_blob_bytes(blob_url)
            transcript_text = await asyncio.to_thread(
                self._transcribe_audio_bytes, audio_bytes
            )

            if not transcript_text or not transcript_text.strip():
                raise RuntimeError("Transcription returned empty text")

            role_requirements = (
                role.get("role_requirements")
                or role.get("requirements")
                or role.get("role_description")
                or role.get("description")
                or ""
            )
            role_name = role.get("role_name") or "the role"
            questions_list = [q.get("question_text", "") for q in questions]
            metrics = await asyncio.to_thread(
                self._run_technical_audio_metrics, audio_bytes, transcript_text
            )
            report = await self.technical_reports.build_report(
                role_requirements=role_requirements,
                questions=questions_list,
                user_response=transcript_text,
                metrics=metrics,
                role_name=role_name,
            )

            await self.repo.upsert_technical_analysis(
                session_id=session_id,
                technical_report=report,
                transcript=transcript_text,
                video_url=None,
            )

            await self._safe_update_session_status(session_id, "completed")
            logger.info(f"Technical pipeline completed for session {session_id}")

            if user_id:
                asyncio.create_task(
                    self._send_fcm_notification(
                        session_id=session_id,
                        user_id=user_id,
                        role_name=role_name,
                        session_type="technical",
                    )
                )

        except Exception:
            await self._safe_update_session_status(session_id, "pending")
            logger.exception(f"Technical pipeline failed for session {session_id}")

        finally:
            deleted = await self._safe_delete_blob(blob_url)
            logger.info(f"Technical pipeline finished for session {session_id}; blob deleted: {deleted}")
            print(f"Technical pipeline finished for session {session_id}; blob deleted: {deleted}")

    # ------------------------------------------------------------------ #
    #  FCM Push Notification                                               #
    # ------------------------------------------------------------------ #

    async def _send_fcm_notification(
        self,
        session_id: UUID,
        user_id: str,
        role_name: Optional[str],
        session_type: str,
    ) -> None:
        try:
            import firebase_admin
            from firebase_admin import credentials, messaging

            if not firebase_admin._apps:
                cred_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", "")
                if not cred_path or not os.path.exists(cred_path):
                    logger.warning(
                        "FIREBASE_CREDENTIALS_PATH not set or file not found — skipping FCM notification"
                    )
                    return
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)

            token_result = (
                self.repo.client.table("user_fcm_tokens")
                .select("fcm_token")
                .eq("user_id", str(user_id))
                .limit(1)
                .execute()
            )
            if not token_result.data:
                logger.info(f"No FCM token for user {user_id} — skipping notification")
                return

            fcm_token = token_result.data[0]["fcm_token"]
            role_display = role_name or session_type.capitalize()

            message = messaging.Message(
                notification=messaging.Notification(
                    title="Interview Feedback Ready! 🎉",
                    body=f"Your {role_display} interview feedback is now available. Tap to view.",
                ),
                data={
                    "session_id": str(session_id),
                    "session_type": session_type,
                    "type": "interview_feedback",
                    "click_action": "FLUTTER_NOTIFICATION_CLICK",
                },
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id="interview_feedback",
                        icon="ic_launcher",
                    ),
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(badge=1, sound="default"),
                    ),
                ),
                token=fcm_token,
            )

            messaging.send(message)
            logger.info(f"FCM notification sent successfully for session {session_id}")

        except Exception as e:
            logger.error(f"FCM notification failed for session {session_id}: {e}")

    # ------------------------------------------------------------------ #
    #  Read results                                                        #
    # ------------------------------------------------------------------ #

    async def get_analysis(self, session_id: UUID) -> Dict[str, Any]:
        analysis = await self.repo.get_session_analysis(session_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return analysis

    async def get_session_status(self, session_id: UUID) -> str:
        session = await self.repo.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session.get("status") or "unknown"

    # ------------------------------------------------------------------ #
    #  Azure blob helpers                                                  #
    # ------------------------------------------------------------------ #

    async def _create_video_upload_sas(self, session_id: UUID) -> tuple[str, str, datetime]:
        try:
            storage = get_azure_storage_provider(container_name=settings.AZURE_CONTAINER_NAME)
            blob_name = f"behavioral/video/{session_id}.mp4"
            blob_client = storage.blob_service_client.get_blob_client(
                container=storage.container_name,
                blob=blob_name,
            )
            expires_at = datetime.utcnow() + timedelta(hours=1)
            sas_token = generate_blob_sas(
                account_name=storage.blob_service_client.account_name,
                container_name=storage.container_name,
                blob_name=blob_name,
                account_key=storage.blob_service_client.credential.account_key,
                permission=BlobSasPermissions(write=True, create=True),
                expiry=expires_at,
            )
            return sas_token, blob_client.url, expires_at
        except Exception as e:
            logger.error(f"Azure SAS generation failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to generate upload SAS token")

    async def _create_audio_upload_sas(self, session_id: UUID) -> tuple[str, str, datetime]:
        try:
            storage = get_azure_storage_provider(container_name=settings.AZURE_CONTAINER_NAME)
            blob_name = f"technical/audio/{session_id}.mp3"
            blob_client = storage.blob_service_client.get_blob_client(
                container=storage.container_name,
                blob=blob_name,
            )
            expires_at = datetime.utcnow() + timedelta(hours=1)
            sas_token = generate_blob_sas(
                account_name=storage.blob_service_client.account_name,
                container_name=storage.container_name,
                blob_name=blob_name,
                account_key=storage.blob_service_client.credential.account_key,
                permission=BlobSasPermissions(write=True, create=True),
                expiry=expires_at,
            )
            return sas_token, blob_client.url, expires_at
        except Exception as e:
            logger.error(f"Azure SAS generation failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to generate upload SAS token")

    async def _download_blob_bytes(self, blob_url: str) -> bytes:
        try:
            credential = settings.STORAGE_ACCOUNT_KEY or None
            blob_client = BlobClient.from_blob_url(blob_url, credential=credential)
            return blob_client.download_blob().readall()
        except Exception as e:
            logger.error(f"Azure blob download failed: {e}", exc_info=True)
            raise

    async def _is_blob_ready(self, blob_url: str, min_size_bytes: int = 1) -> bool:
        try:
            credential = settings.STORAGE_ACCOUNT_KEY or None
            blob_client = BlobClient.from_blob_url(blob_url, credential=credential)
            props = blob_client.get_blob_properties()
            size = getattr(props, "size", None)
            return size is not None and size >= min_size_bytes
        except Exception as e:
            logger.debug(f"Blob readiness check failed: {e}")
            return False

    async def _safe_delete_blob(self, blob_url: str) -> bool:
        try:
            credential = settings.STORAGE_ACCOUNT_KEY or None
            blob_client = BlobClient.from_blob_url(blob_url, credential=credential)
            blob_client.delete_blob(delete_snapshots="include")
            logger.info(f"Blob deleted successfully: {blob_client.blob_name}")
            print(f"Blob deleted successfully: {blob_client.blob_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete blob '{blob_url}': {e}", exc_info=True)
            print(f"Failed to delete blob '{blob_url}': {e}")
            return False

    async def _safe_update_session_status(self, session_id: UUID, status: str) -> None:
        try:
            await self.repo.update_session_status(session_id, status)
        except Exception as e:
            logger.warning(f"Failed to update session status: {e}")

    # ------------------------------------------------------------------ #
    #  ML pipeline runners (sync — called via asyncio.to_thread)          #
    # ------------------------------------------------------------------ #

    def _transcribe_video_bytes(self, video_bytes: bytes) -> str:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                f.write(video_bytes)
                temp_path = f.name
            transcript_text, _ = self.transcriber.transcribe_video_file(
                temp_path, max_wait_seconds=900, poll_interval_seconds=5,
            )
            return transcript_text or ""
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _transcribe_audio_bytes(self, audio_bytes: bytes) -> str:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                f.write(audio_bytes)
                temp_path = f.name
            transcript_text, _ = self.transcriber.transcribe_video_file(
                temp_path, max_wait_seconds=900, poll_interval_seconds=5,
            )
            return transcript_text or ""
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _run_behavioral_pipeline(self, video_bytes: bytes) -> Dict[str, Any]:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                f.write(video_bytes)
                temp_path = f.name

            device = behavioral_pipeline.torch.device(
                "cuda" if behavioral_pipeline.torch.cuda.is_available() else "cpu"
            )
            (
                vocab, nlp, smile, audio_mins, audio_denom,
                visual, audio, text, baseline, siamese, final,
            ) = behavioral_pipeline.load_all_models(device)

            transcript_text, transcript_words = self.transcriber.transcribe_video_file(
                temp_path, max_wait_seconds=900, poll_interval_seconds=5,
            )
            if not transcript_text or not transcript_words:
                raise RuntimeError("Transcription failed or missing word timestamps")

            predictions, transcript_text, transcript_words = behavioral_pipeline.predict_video(
                temp_path,
                visual, audio, text, baseline, siamese, final,
                vocab, nlp, smile, audio_mins, audio_denom, device,
                transcript_text=transcript_text,
                transcript_words=transcript_words,
            )

            return {
                "predictions":    predictions,
                "visual_metrics": behavioral_pipeline.extract_visual_metrics(temp_path),
                "audio_metrics":  behavioral_pipeline.extract_audio_metrics(temp_path),
                "text_metrics":   behavioral_pipeline.extract_text_metrics(transcript_text),
                "transcript":     transcript_text or "",
            }
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _run_technical_audio_metrics(self, audio_bytes: bytes, transcript_text: str) -> Dict[str, Any]:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                f.write(audio_bytes)
                temp_path = f.name

            return {
                "audio_metrics": behavioral_pipeline.extract_audio_metrics(temp_path) or {},
                "text_metrics": behavioral_pipeline.extract_text_metrics(transcript_text) or {},
            }
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _extract_behavioral_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "predictions":    metrics.get("predictions") or {},
            "visual_metrics": metrics.get("visual_metrics") or {},
            "audio_metrics":  metrics.get("audio_metrics") or {},
            "text_metrics":   metrics.get("text_metrics") or {},
        }

    def _extract_score(self, metrics: Dict[str, Any]) -> Optional[float]:
        predictions = metrics.get("predictions")
        if not isinstance(predictions, dict):
            return None
        values = [float(v) for v in predictions.values() if isinstance(v, (int, float))]
        if not values:
            return None
        return float(sum(values) / len(values))