"""
LLM-based post-processing for OCR text.

Uses Claude Haiku to clean up and format raw OCR output into well-structured markdown.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from anthropic import AsyncAnthropic
    anthropic_available = True
except ImportError:
    anthropic_available = False
    AsyncAnthropic = None


DEFAULT_CLEANUP_PROMPT = """You are helping clean up OCR output from handwritten journal pages for display in Notion.

The text below is raw OCR output that may have:
- Formatting inconsistencies
- Missing line breaks
- Unclear structure
- Minor OCR errors

Please clean it up into well-formatted markdown:
- Use proper markdown formatting (headings with #, **bold**, *italic*, etc.)
- Put EACH sentence or thought on its OWN LINE (very important for Notion display)
- Use blank lines to separate major sections
- Fix obvious OCR errors
- Preserve ALL the original content and meaning
- Keep special notes like [crossed out: text], [illegible], [drawing: description]
- Use --- for dividers between pages or major sections

IMPORTANT: Each line of thought should be on its own line. Don't combine multiple sentences into long paragraphs.

Raw OCR text:
"""


class LLMPostprocessor:
    """Post-processes OCR text using Claude Haiku to improve formatting and readability."""

    def __init__(self, config: dict = None):
        """
        Initialize LLM post-processor.

        Config options:
            enabled: Whether to use LLM post-processing (default: True)
            model: Model to use (default: "claude-haiku-4-5-20251001")
            prompt: Custom cleanup prompt
            api_key: Anthropic API key
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

        if not self.enabled:
            logger.info("LLM post-processing disabled")
            return

        if not anthropic_available:
            logger.warning(
                "Anthropic not available for LLM post-processing. "
                "Install with: pip install anthropic"
            )
            self.enabled = False
            return

        api_key = self.config.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("No ANTHROPIC_API_KEY found for LLM post-processing, disabling")
            self.enabled = False
            return

        self.client = AsyncAnthropic(api_key=api_key)
        self.model_name = self.config.get("model", "claude-haiku-4-5-20251001")
        self.prompt = self.config.get("prompt", DEFAULT_CLEANUP_PROMPT)
        self.temperature = self.config.get("temperature", 0.3)
        self.max_output_tokens = self.config.get("max_output_tokens", 8192)

        logger.info(f"Initialized LLM post-processor with model: {self.model_name}")

    async def cleanup_text(self, raw_text: str) -> Optional[str]:
        """
        Clean up raw OCR text using LLM.

        Args:
            raw_text: Raw OCR output text

        Returns:
            Cleaned and formatted markdown text, or None if processing fails
        """
        if not self.enabled:
            return raw_text

        if not raw_text or len(raw_text.strip()) == 0:
            return raw_text

        try:
            response = await self.client.messages.create(
                model=self.model_name,
                max_tokens=self.max_output_tokens,
                temperature=self.temperature,
                messages=[
                    {
                        "role": "user",
                        "content": self.prompt + "\n\n" + raw_text,
                    }
                ],
            )

            cleaned_text = response.content[0].text

            logger.info(
                f"LLM post-processing: {len(raw_text)} chars → {len(cleaned_text)} chars"
            )

            return cleaned_text

        except Exception as e:
            logger.error(f"LLM post-processing failed: {e}")
            return raw_text
