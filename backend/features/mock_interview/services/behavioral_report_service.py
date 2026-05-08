import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from features.mock_interview.schemas.mock_interview_schemas import GeminiFeedback
from shared.providers.llm_models.gemini import Gemini

logger = logging.getLogger(__name__)


class BehavioralReportService:
    def __init__(self, gemini: Gemini, prompts_dir: Path):
        self.gemini = gemini
        self.prompts_dir = prompts_dir

    async def build_report(self, metrics: Dict[str, Any], role_name: Optional[str] = None) -> str:
        template = self._load_prompt("behavioral_report_prompt.txt")

        predictions = metrics.get("predictions", {})
        visual = metrics.get("visual_metrics", {})
        audio = metrics.get("audio_metrics", {})
        text = metrics.get("text_metrics", {})
        role_context = metrics.get("role_context", {})

        prompt = template
        prompt = prompt.replace("Openness: __", f"Openness: {self._coerce_metric_value(predictions.get('openness'))}")
        prompt = prompt.replace(
            "Conscientiousness: __",
            f"Conscientiousness: {self._coerce_metric_value(predictions.get('conscientiousness'))}",
        )
        prompt = prompt.replace(
            "Extraversion: __",
            f"Extraversion: {self._coerce_metric_value(predictions.get('extraversion'))}",
        )
        prompt = prompt.replace(
            "Agreeableness: __",
            f"Agreeableness: {self._coerce_metric_value(predictions.get('agreeableness'))}",
        )
        prompt = prompt.replace(
            "Neuroticism: __",
            f"Neuroticism: {self._coerce_metric_value(predictions.get('neuroticism'))}",
        )

        prompt = prompt.replace(
            "Eye contact & presence: __",
            f"Eye contact & presence: {self._coerce_metric_value(visual.get('eye_contact'))}",
        )
        prompt = prompt.replace(
            "Body language & composure: __",
            f"Body language & composure: {self._coerce_metric_value(visual.get('body_language'))}",
        )
        prompt = prompt.replace(
            "Non-verbal confidence: __",
            f"Non-verbal confidence: {self._coerce_metric_value(visual.get('non_verbal_confidence'))}",
        )

        prompt = prompt.replace(
            "Vocal clarity & pace: __",
            f"Vocal clarity & pace: {self._coerce_metric_value(audio.get('vocal_clarity'))}",
        )
        prompt = prompt.replace(
            "Emotional tone consistency: __",
            f"Emotional tone consistency: {self._coerce_metric_value(audio.get('emotional_tone'))}",
        )
        prompt = prompt.replace(
            "Filler word frequency (inverse): __",
            f"Filler word frequency (inverse): {self._coerce_metric_value(audio.get('filler_word_frequency'))}",
        )

        prompt = prompt.replace(
            "Vocabulary & articulation: __",
            f"Vocabulary & articulation: {self._coerce_metric_value(text.get('vocabulary'))}",
        )
        prompt = prompt.replace(
            "Logical structure of answers: __",
            f"Logical structure of answers: {self._coerce_metric_value(text.get('logical_structure'))}",
        )
        prompt = prompt.replace(
            "Role-relevant keyword alignment: __",
            f"Role-relevant keyword alignment: {self._coerce_metric_value(text.get('keyword_alignment'))}",
        )

        role_title = role_context.get("job_title") or role_name
        prompt = prompt.replace(
            "Job title: __",
            f"Job title: {self._coerce_metric_value(role_title)}",
        )
        prompt = prompt.replace(
            "Key competencies required: __",
            f"Key competencies required: {self._coerce_metric_value(role_context.get('key_competencies'))}",
        )
        prompt = prompt.replace(
            "Seniority level: __",
            f"Seniority level: {self._coerce_metric_value(role_context.get('seniority_level'))}",
        )

        feedback = await self._get_gemini_feedback(prompt)
        return self._format_feedback(feedback)

    def _coerce_metric_value(self, value: Any) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        text_value = str(value).strip()
        return text_value if text_value else "N/A"

    def _load_prompt(self, filename: str) -> str:
        prompt_path = self.prompts_dir / filename
        if not prompt_path.exists():
            raise HTTPException(status_code=500, detail=f"Prompt file missing: {filename}")
        return prompt_path.read_text(encoding="utf-8")

    async def _get_gemini_feedback(self, prompt: str) -> GeminiFeedback:
        last_error: Optional[Exception] = None
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
