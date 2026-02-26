"""
Gemini LLM Provider for GROWZA Platform
Uses google.generativeai (compatible with current installation)
"""
import google.generativeai as genai
from typing import Optional, Type
from pydantic import BaseModel
import logging
import json
import os

from .llm_provider import LLMProvider
from core.config import Settings

logger = logging.getLogger(__name__)


class Gemini(LLMProvider):
    """Gemini LLM provider using google.generativeai"""

    def __init__(self, settings: Settings, system_prompt: str = None):
        self.settings = settings
        self.system_prompt = system_prompt
        self.model_name = settings.GEMINI_MODEL

        # Configure the client
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_prompt
        )
        logger.info(f"✅ Gemini initialized with model: {self.model_name}")

    def get_response(
        self,
        prompt: str,
        expecting_longer_output: bool = False,
        need_json_output: bool = False,
        schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.1
    ):
        try:
            generation_config = {
                "temperature": temperature,
                "max_output_tokens": 4000 if expecting_longer_output else None,
            }

            response = self.model.generate_content(
                prompt,
                generation_config=generation_config
            )

            if not response.text:
                logger.warning("Empty response from Gemini")
                return None

            if need_json_output and schema:
                try:
                    data = json.loads(response.text)
                    return schema.model_validate(data)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON: {e}")
                    return None
                except Exception as e:
                    logger.error(f"Schema validation failed: {e}")
                    return None
            elif need_json_output:
                try:
                    return json.loads(response.text)
                except json.JSONDecodeError:
                    return response.text
            else:
                return response.text.strip()

        except Exception as e:
            logger.error(f"Gemini API error: {e}", exc_info=True)
            return None

    def get_embedding(self, content: str, model: Optional[str] = None, task_type: Optional[str] = None):
        logger.warning("Embeddings not implemented in this Gemini provider")
        return None