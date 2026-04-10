"""
OpenAI Vision OCR engine for Promaia.

Uses OpenAI's GPT-4o or other vision-capable models to transcribe
handwritten text, journal entries, and other visual content.
"""
import logging
import base64
import time
from pathlib import Path
from typing import Dict, Any, Optional

from promaia.ocr.engines.base import BaseOCREngine, OCRResult

logger = logging.getLogger(__name__)

# Try to import OpenAI
try:
    from openai import AsyncOpenAI
    openai_available = True
except ImportError:
    openai_available = False
    AsyncOpenAI = None


DEFAULT_JOURNAL_PROMPT = """I need you to transcribe this handwritten page from my personal journal. This is my own writing that I'm digitizing for personal archival purposes.

Please transcribe the text exactly as written:
- Keep the original wording, spelling, and phrasing
- If text is crossed out, note it with [crossed out: text]
- If something is illegible, mark it as [illegible]
- Preserve paragraph breaks and structure
- Note any drawings as [drawing: description]
- Output in clean markdown format

This is authorized personal content that I own and have permission to transcribe."""


class OpenAIVisionEngine(BaseOCREngine):
    """
    OCR engine using OpenAI's vision-capable models (GPT-4o, etc.).

    This engine sends images directly to OpenAI's API for transcription,
    which is particularly good for handwritten text and journal entries.
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize OpenAI Vision engine.

        Config options:
            api_key: OpenAI API key (required)
            model: Model to use (default: "gpt-4o")
            prompt: Custom transcription prompt
            max_tokens: Maximum tokens in response (default: 4096)
            temperature: Sampling temperature (default: 0.1 for accuracy)
            detail: Image detail level - "low" or "high" (default: "high")
        """
        super().__init__(config)

        if not openai_available:
            raise ImportError(
                "OpenAI library not installed. "
                "Install with: pip install openai"
            )

        # Get API key
        api_key = self.config.get("api_key")
        if not api_key:
            # Try environment variable
            import os
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OpenAI API key not found. "
                    "Set it in config or OPENAI_API_KEY environment variable."
                )

        # Initialize OpenAI client
        self.client = AsyncOpenAI(api_key=api_key)

        # Configuration
        self.model = self.config.get("model", "gpt-4o")
        self.prompt = self.config.get("prompt", DEFAULT_JOURNAL_PROMPT)
        self.max_tokens = self.config.get("max_tokens", 4096)
        self.temperature = self.config.get("temperature", 0.1)
        self.detail = self.config.get("detail", "high")

        logger.info(f"Initialized OpenAI Vision engine with model: {self.model}")

    async def extract_text(self, image_path: Path) -> OCRResult:
        """
        Extract text from image using OpenAI vision API.

        Args:
            image_path: Path to image file

        Returns:
            OCRResult with transcribed text
        """
        start_time = time.time()

        try:
            # Validate image
            self.validate_image_path(image_path)

            # Read and encode image
            image_data = self._encode_image(image_path)

            # Call OpenAI API
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an OCR transcription assistant. Your job is to accurately transcribe handwritten text from images into digital format. You should transcribe all visible text faithfully, regardless of content, as this is personal archival work."
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self.prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
                                    "detail": self.detail
                                }
                            }
                        ]
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature
            )

            # Extract text from response
            transcribed_text = response.choices[0].message.content

            # Calculate processing time
            processing_time = time.time() - start_time

            # OpenAI doesn't provide confidence scores, so we estimate based on
            # response quality indicators
            confidence = self._estimate_confidence(response)

            # Build metadata
            metadata = {
                "api": "openai",
                "model": self.model,
                "finish_reason": response.choices[0].finish_reason,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }
            }

            logger.info(
                f"OpenAI Vision processed {image_path.name} "
                f"({response.usage.total_tokens} tokens, "
                f"{processing_time:.2f}s)"
            )

            return OCRResult(
                text=transcribed_text,
                confidence=confidence,
                metadata=metadata,
                language="en",  # OpenAI doesn't auto-detect, assume English
                processing_time=processing_time,
                success=True
            )

        except FileNotFoundError as e:
            logger.error(f"Image file not found: {e}")
            return OCRResult(
                text="",
                confidence=0.0,
                success=False,
                error=str(e),
                processing_time=time.time() - start_time
            )

        except Exception as e:
            logger.error(f"OpenAI Vision API error: {e}")
            return OCRResult(
                text="",
                confidence=0.0,
                success=False,
                error=str(e),
                processing_time=time.time() - start_time
            )

    def _encode_image(self, image_path: Path) -> str:
        """
        Encode image to base64 for OpenAI API.

        Args:
            image_path: Path to image

        Returns:
            Base64 encoded image string
        """
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def _estimate_confidence(self, response) -> float:
        """
        Estimate confidence score from OpenAI response.

        OpenAI doesn't provide explicit confidence scores, so we estimate
        based on response characteristics.

        Args:
            response: OpenAI API response

        Returns:
            Estimated confidence (0.0 to 1.0)
        """
        # Check finish reason
        if response.choices[0].finish_reason != "stop":
            # Response was cut off - lower confidence
            return 0.7

        # Check for uncertainty markers in text
        text = response.choices[0].message.content.lower()
        uncertainty_markers = [
            "[illegible]",
            "[unclear]",
            "cannot read",
            "unable to read",
            "can't make out"
        ]

        uncertainty_count = sum(
            text.count(marker) for marker in uncertainty_markers
        )

        # Base confidence starts at 0.95 for GPT-4o
        base_confidence = 0.95

        # Reduce confidence based on uncertainty markers
        # Each marker reduces confidence by 0.05, minimum 0.6
        confidence = max(0.6, base_confidence - (uncertainty_count * 0.05))

        return confidence

    def get_supported_formats(self) -> list:
        """Get list of supported image formats for OpenAI."""
        # OpenAI supports common image formats
        return ['.jpg', '.jpeg', '.png', '.webp', '.gif']
