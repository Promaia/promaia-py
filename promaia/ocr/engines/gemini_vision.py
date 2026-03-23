"""
Gemini Vision OCR engine for Promaia.

Uses Google's Gemini models with vision capabilities to transcribe
handwritten text, journal entries, and other visual content.
"""
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional

from promaia.ocr.engines.base import BaseOCREngine, OCRResult

logger = logging.getLogger(__name__)

# Try to import the new Google GenAI SDK
try:
    from google import genai
    gemini_available = True
except ImportError:
    gemini_available = False
    genai = None


DEFAULT_GEMINI_PROMPT = """Please carefully transcribe all the handwritten text in this image.

Instructions:
- Transcribe exactly what you see, word for word
- Preserve the original spelling, grammar, and phrasing
- If text is crossed out, note it as [crossed out: text]
- If something is unclear or illegible, mark it as [illegible]
- Maintain paragraph breaks and structure
- Note any drawings or sketches as [drawing: brief description]
- Output in clean markdown format

This is personal journal content being digitized for archival purposes."""


class GeminiVisionEngine(BaseOCREngine):
    """
    OCR engine using Google's Gemini vision models.

    This engine sends images directly to Gemini for transcription,
    which works well for handwritten text and journal entries.
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize Gemini Vision engine.

        Config options:
            api_key: Google AI API key (required)
            model: Model to use (default: "gemini-2.0-flash")
            prompt: Custom transcription prompt
            temperature: Sampling temperature (default: 0.1 for accuracy)
            max_output_tokens: Maximum tokens in response (default: 8192)
        """
        super().__init__(config)

        if not gemini_available:
            raise ImportError(
                "Google GenAI library not installed. "
                "Install with: pip install google-genai"
            )

        # Get API key
        api_key = self.config.get("api_key")
        if not api_key:
            import os
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError(
                    "Gemini API key not found. "
                    "Set it in config or GOOGLE_API_KEY/GEMINI_API_KEY environment variable."
                )

        # Initialize client
        self.client = genai.Client(api_key=api_key)

        # Configuration
        self.model_name = self.config.get("model", "gemini-2.0-flash")
        self.prompt = self.config.get("prompt", DEFAULT_GEMINI_PROMPT)
        self.temperature = self.config.get("temperature", 0.1)
        self.max_output_tokens = self.config.get("max_output_tokens", 8192)

        logger.info(f"Initialized Gemini Vision engine with model: {self.model_name}")

    async def extract_text(self, image_path: Path) -> OCRResult:
        """
        Extract text from image using Gemini vision.

        Args:
            image_path: Path to image file

        Returns:
            OCRResult with transcribed text
        """
        start_time = time.time()

        try:
            # Validate image
            self.validate_image_path(image_path)

            # Upload image
            uploaded_file = self.client.files.upload(file=str(image_path))

            # Wait for processing
            while uploaded_file.state.name == "PROCESSING":
                time.sleep(0.5)
                uploaded_file = self.client.files.get(name=uploaded_file.name)

            if uploaded_file.state.name == "FAILED":
                raise ValueError(f"File processing failed: {uploaded_file.state.name}")

            # Generate content
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[uploaded_file, self.prompt],
                config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_output_tokens,
                },
            )

            # Extract text from response
            transcribed_text = response.text

            # Calculate processing time
            processing_time = time.time() - start_time

            # Estimate confidence
            confidence = self._estimate_confidence(response, transcribed_text)

            # Build metadata
            metadata = {
                "api": "gemini",
                "model": self.model_name,
            }

            # Try to get token usage if available
            try:
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    metadata["usage"] = {
                        "prompt_tokens": response.usage_metadata.prompt_token_count,
                        "completion_tokens": response.usage_metadata.candidates_token_count,
                        "total_tokens": response.usage_metadata.total_token_count,
                    }
            except Exception:
                pass

            logger.info(
                f"Gemini Vision processed {image_path.name} "
                f"({processing_time:.2f}s)"
            )

            # Clean up uploaded file
            try:
                self.client.files.delete(name=uploaded_file.name)
            except Exception:
                pass

            return OCRResult(
                text=transcribed_text,
                confidence=confidence,
                metadata=metadata,
                language="en",
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
            logger.error(f"Gemini Vision API error: {e}")
            return OCRResult(
                text="",
                confidence=0.0,
                success=False,
                error=str(e),
                processing_time=time.time() - start_time
            )

    def _estimate_confidence(self, response, text: str) -> float:
        """
        Estimate confidence score from Gemini response.

        Args:
            response: Gemini API response
            text: Transcribed text

        Returns:
            Estimated confidence (0.0 to 1.0)
        """
        # Start with high base confidence for Gemini
        base_confidence = 0.95

        # Check for uncertainty markers in text
        uncertainty_markers = [
            "[illegible]",
            "[unclear]",
            "cannot read",
            "unable to read",
            "can't make out",
            "possibly"
        ]

        uncertainty_count = sum(
            text.lower().count(marker) for marker in uncertainty_markers
        )

        # Reduce confidence based on uncertainty markers
        confidence = max(0.6, base_confidence - (uncertainty_count * 0.05))

        return confidence

    def get_supported_formats(self) -> list:
        """Get list of supported image formats for Gemini."""
        return ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif']
