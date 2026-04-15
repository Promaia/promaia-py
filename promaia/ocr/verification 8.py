"""
Cross-model verification for OCR output.

Sends the original image alongside the transcription to a second model
(Claude Opus) for proofreading. Different model architectures catch
different errors, so this step reduces correlated mistakes.
"""
import base64
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from anthropic import AsyncAnthropic
    anthropic_available = True
except ImportError:
    anthropic_available = False
    AsyncAnthropic = None


DEFAULT_VERIFICATION_PROMPT = """You are proofreading an OCR transcription of a handwritten page.

Below is a transcription produced by another AI model. Compare it carefully against the image and correct any errors you find.

Rules:
- Fix misread words by comparing against the handwriting in the image
- Preserve the existing markdown formatting
- Keep [crossed out: text], [illegible], and [drawing: description] markers
- If you find something marked [illegible] that you CAN read, replace it with the actual text
- Do NOT add commentary or notes — just output the corrected transcription
- If the transcription is already accurate, return it unchanged

Current transcription:

"""


class OCRVerifier:
    """Verifies OCR output by cross-checking with a second vision model."""

    def __init__(self, config: dict = None):
        """
        Initialize OCR verifier.

        Config options:
            enabled: Whether verification is enabled (default: False)
            model: Model to use (default: "claude-opus-4-6")
            api_key: Anthropic API key
            max_output_tokens: Max tokens (default: 8192)
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", False)

        if not self.enabled:
            return

        if not anthropic_available:
            logger.warning("Anthropic not available for OCR verification, disabling")
            self.enabled = False
            return

        api_key = self.config.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("No ANTHROPIC_API_KEY found for OCR verification, disabling")
            self.enabled = False
            return

        self.client = AsyncAnthropic(api_key=api_key)
        self.model_name = self.config.get("model", "claude-opus-4-6")
        self.prompt = self.config.get("prompt", DEFAULT_VERIFICATION_PROMPT)
        self.max_output_tokens = self.config.get("max_output_tokens", 8192)

        logger.info(f"Initialized OCR verifier with model: {self.model_name}")

    async def verify(self, image_path: Path, transcription: str) -> Optional[str]:
        """
        Verify and correct a transcription against the original image.

        Args:
            image_path: Path to the original (or preprocessed) image
            transcription: Current transcription text to verify

        Returns:
            Corrected transcription, or original if verification fails
        """
        if not self.enabled:
            return transcription

        if not transcription or not transcription.strip():
            return transcription

        try:
            image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

            suffix = image_path.suffix.lower()
            media_types = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp",
                ".gif": "image/gif",
            }
            media_type = media_types.get(suffix, "image/jpeg")

            response = await self.client.messages.create(
                model=self.model_name,
                max_tokens=self.max_output_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": self.prompt + transcription,
                            },
                        ],
                    }
                ],
            )

            verified_text = response.content[0].text

            logger.info(
                f"OCR verification complete: "
                f"{len(transcription)} chars → {len(verified_text)} chars"
            )

            return verified_text

        except Exception as e:
            logger.error(f"OCR verification failed: {e}")
            return transcription
