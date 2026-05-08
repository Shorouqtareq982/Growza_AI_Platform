import asyncio
import logging
from pathlib import Path
from typing import Dict, List

from fastapi import HTTPException

from features.mock_interview.schemas.mock_interview_schemas import GeminiFeedback
from shared.providers.llm_models.gemini import Gemini

logger = logging.getLogger(__name__)


class TechnicalReportService:
    def __init__(self, gemini: Gemini, prompts_dir: Path):
        self.gemini = gemini
        self.prompts_dir = prompts_dir

    async def build_report_smart(self, questions_text: str, full_transcript: str, role_name: str) -> str:
        template = self._load_prompt("technical_report_prompt.txt")
        prompt = (
            template.replace("[Insert Job Title]", role_name)
            .replace("[Paste Questions Here]", questions_text)
            .replace("[Paste Full Transcript Here]", full_transcript)
        )
        feedback = await self._get_gemini_feedback(prompt)
        return self._format_feedback(feedback)

    async def build_report(self, qa_pairs: List[Dict[str, str]], role_name: str) -> str:
        payload = "\n".join(
            [
                f"Q{i + 1}: {pair.get('question', '')}\nA{i + 1}: {pair.get('answer', '')}"
                for i, pair in enumerate(qa_pairs)
            ]
        )
        template = self._load_prompt("technical_report_prompt.txt")
        prompt = template.replace("[Insert Job Title]", role_name).replace("[Paste Answers Here]", payload)
        feedback = await self._get_gemini_feedback(prompt)
        return self._format_feedback(feedback)

    def _load_prompt(self, filename: str) -> str:
        prompt_path = self.prompts_dir / filename
        if not prompt_path.exists():
            raise HTTPException(status_code=500, detail=f"Prompt file missing: {filename}")
        return prompt_path.read_text(encoding="utf-8")

    async def _get_gemini_feedback(self, prompt: str) -> GeminiFeedback:
        last_error = None
        for attempt in range(3):
            result = await self.gemini.get_response(
                prompt=prompt,
                need_json_output=True,
                schema=GeminiFeedback,
                expecting_longer_output=False,
            )
            if result is None:
                last_error = HTTPException(status_code=422, detail="Gemini response failed")
            elif isinstance(result, GeminiFeedback):
                return result
            elif isinstance(result, str):
                try:
                    return GeminiFeedback.model_validate_json(result)
                except Exception as e:
                    last_error = e
                    logger.error(f"Gemini JSON parse failed: {e}")
            elif isinstance(result, dict):
                return GeminiFeedback(**result)
            else:
                last_error = HTTPException(status_code=422, detail="Gemini response invalid")

            if attempt < 2:
                await asyncio.sleep(5)

        if isinstance(last_error, HTTPException):
            raise last_error
        raise HTTPException(status_code=422, detail="Gemini response failed")

    def _format_feedback(self, feedback: GeminiFeedback) -> str:
        return (
            f"Strengths: {feedback.strengths}\n"
            f"Weaknesses: {feedback.weaknesses}\n"
            f"Suggestions: {feedback.suggestions}"
        )
