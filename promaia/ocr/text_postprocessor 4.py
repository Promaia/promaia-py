"""
Text postprocessing utilities for OCR output.

Provides functions to clean and format OCR text:
- Remove extra whitespace
- Fix common OCR errors
- Normalize line breaks
- Add formatting
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TextPostprocessor:
    """Handles text postprocessing for OCR results."""

    def __init__(
        self,
        fix_common_errors: bool = True,
        normalize_whitespace: bool = True,
        preserve_paragraphs: bool = True
    ):
        """
        Initialize text postprocessor.

        Args:
            fix_common_errors: Whether to fix common OCR errors
            normalize_whitespace: Whether to normalize whitespace
            preserve_paragraphs: Whether to preserve paragraph structure
        """
        self.fix_common_errors = fix_common_errors
        self.normalize_whitespace = normalize_whitespace
        self.preserve_paragraphs = preserve_paragraphs

        # Common OCR error patterns
        self.error_patterns = {
            r'\bl\b': 'I',  # lowercase L often misread as I
            r'\b0\b': 'O',  # zero misread as O in some contexts
            r'(\w+)\s+([.,!?;:])': r'\1\2',  # Remove space before punctuation
            r'([.,!?;:])\s*([.,!?;:])': r'\1',  # Remove duplicate punctuation
        }

    def postprocess_text(self, text: str) -> str:
        """
        Postprocess OCR text.

        Args:
            text: Raw OCR text

        Returns:
            Cleaned and formatted text
        """
        if not text:
            return text

        # Normalize whitespace
        if self.normalize_whitespace:
            text = self._normalize_whitespace(text)

        # Fix common OCR errors
        if self.fix_common_errors:
            text = self._fix_common_errors(text)

        # Preserve paragraph structure
        if self.preserve_paragraphs:
            text = self._preserve_paragraphs(text)

        return text.strip()

    def _normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace in text.

        Args:
            text: Input text

        Returns:
            Text with normalized whitespace
        """
        # Replace multiple spaces with single space
        text = re.sub(r' +', ' ', text)

        # Remove spaces at start/end of lines
        lines = [line.strip() for line in text.split('\n')]

        # Remove empty lines at start and end
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()

        return '\n'.join(lines)

    def _fix_common_errors(self, text: str) -> str:
        """
        Fix common OCR errors.

        Args:
            text: Input text

        Returns:
            Text with errors fixed
        """
        for pattern, replacement in self.error_patterns.items():
            text = re.sub(pattern, replacement, text)

        return text

    def _preserve_paragraphs(self, text: str) -> str:
        """
        Preserve paragraph structure by normalizing line breaks.

        Args:
            text: Input text

        Returns:
            Text with preserved paragraphs
        """
        # Split into lines
        lines = text.split('\n')

        # Group lines into paragraphs
        paragraphs = []
        current_paragraph = []

        for line in lines:
            line = line.strip()

            if not line:
                # Empty line indicates paragraph break
                if current_paragraph:
                    paragraphs.append(' '.join(current_paragraph))
                    current_paragraph = []
            else:
                current_paragraph.append(line)

        # Add last paragraph
        if current_paragraph:
            paragraphs.append(' '.join(current_paragraph))

        # Join paragraphs with double line break
        return '\n\n'.join(paragraphs)

    def create_markdown(
        self,
        text: str,
        title: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> str:
        """
        Create markdown document from OCR text.

        Args:
            text: OCR text content
            title: Optional document title
            metadata: Optional frontmatter metadata

        Returns:
            Markdown formatted text
        """
        parts = []

        # Add frontmatter if metadata provided
        if metadata:
            parts.append("---")
            for key, value in metadata.items():
                parts.append(f"{key}: {value}")
            parts.append("---")
            parts.append("")

        # Add title if provided
        if title:
            parts.append(f"# {title}")
            parts.append("")

        # Add text content
        parts.append(text)

        return '\n'.join(parts)


def postprocess_ocr_text(
    text: str,
    config: dict = None
) -> str:
    """
    Convenience function to postprocess OCR text.

    Args:
        text: Raw OCR text
        config: Optional postprocessing config

    Returns:
        Cleaned text
    """
    config = config or {}
    postprocessor = TextPostprocessor(
        fix_common_errors=config.get("fix_common_errors", True),
        normalize_whitespace=config.get("normalize_whitespace", True),
        preserve_paragraphs=config.get("preserve_paragraphs", True)
    )

    return postprocessor.postprocess_text(text)


def create_ocr_markdown(
    text: str,
    title: Optional[str] = None,
    metadata: Optional[dict] = None
) -> str:
    """
    Create markdown document from OCR text.

    Args:
        text: OCR text content
        title: Optional document title
        metadata: Optional frontmatter metadata

    Returns:
        Markdown formatted text
    """
    postprocessor = TextPostprocessor()
    cleaned_text = postprocessor.postprocess_text(text)
    return postprocessor.create_markdown(cleaned_text, title, metadata)
