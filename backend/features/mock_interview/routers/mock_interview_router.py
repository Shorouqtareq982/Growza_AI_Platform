import io
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from features.mock_interview.schemas.mock_interview_schemas import (
    StartSessionRequest,
    StartSessionResponse,
    NotifyUploadRequest,
    NotifyUploadResponse,
    UploadStatusResponse,
    BehavioralReportResponse,
    TechnicalReportResponse,
)
from features.mock_interview.repositories.mock_interview_repository import MockInterviewRepository
from features.mock_interview.services.interview_service import MockInterviewService
from shared.providers.supabase.database import db as supabase_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mock-interview", tags=["mock-interview"])


def get_repository() -> MockInterviewRepository:
    return MockInterviewRepository(supabase_db)


def get_service(repo: MockInterviewRepository = Depends(get_repository)) -> MockInterviewService:
    return MockInterviewService(repository=repo)


@router.post("/sessions/start", response_model=StartSessionResponse)
async def start_session(
    payload: StartSessionRequest,
    service: MockInterviewService = Depends(get_service),
):
    return await service.start_session(payload.role_name, payload.user_id)


@router.get("/questions/{question_id}/audio-stream")
async def stream_question_audio(
    question_id: UUID,
    service: MockInterviewService = Depends(get_service),
):
    audio_bytes = await service.get_question_audio_bytes(question_id)
    return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")


@router.post("/behavioural/notify-upload", response_model=NotifyUploadResponse, status_code=202)
async def notify_behavioral_upload(
    payload: NotifyUploadRequest,
    background_tasks: BackgroundTasks,
    service: MockInterviewService = Depends(get_service),
):
    session = await service.repo.get_session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    background_tasks.add_task(
        service.queue_behavioral_upload,
        payload.session_id,
        payload.blob_url,
    )
    return NotifyUploadResponse(session_id=payload.session_id)


@router.get("/upload-status/{session_id}", response_model=UploadStatusResponse)
async def get_upload_status(
    session_id: UUID,
    service: MockInterviewService = Depends(get_service),
):
    status = await service.get_session_status(session_id)
    return UploadStatusResponse(status=status)


@router.get("/analysis/{session_id}/behavioral-report", response_model=BehavioralReportResponse)
async def get_behavioral_report(
    session_id: UUID,
    service: MockInterviewService = Depends(get_service),
):
    analysis = await service.get_analysis(session_id)
    return BehavioralReportResponse(
        analysis_id=UUID(analysis["analysis_id"]),
        behavioral_report=analysis.get("behavioral_report"),
        analysis_metrics=analysis.get("analysis_metrics") or {},
        analyzed_at=analysis["analyzed_at"],
    )


@router.get("/analysis/{session_id}/technical-report", response_model=TechnicalReportResponse)
async def get_technical_report(
    session_id: UUID,
    service: MockInterviewService = Depends(get_service),
):
    analysis = await service.get_analysis(session_id)
    return TechnicalReportResponse(
        analysis_id=UUID(analysis["analysis_id"]),
        technical_report=analysis.get("technical_report"),
        analyzed_at=analysis["analyzed_at"],
    )
