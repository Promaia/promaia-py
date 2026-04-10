"""
Base OCR engine interface for Promaia.

Defines the abstract base class that all OCR engines must implement,
along with data structures for OCR results.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TextRegion:
    """Represents a region of text detected in an image."""
    text: str
    confidence: float
    bounding_box: Optional[Dict[str, Any]] = None  # Coordinates of text region
    page: int = 0  # Page number for multi-page documents


@dataclass
class OCRResult:
    """
    Result from OCR processing of an image.

    Attributes:
        text: Extracted text content
        confidence: Overall confidence score (0.0 to 1.0)
        metadata: Additional metadata from OCR engine
        text_regions: Optional list of positioned text blocks
        language: Detected language code (e.g., 'en', 'es')
        processing_time: Time taken to process in seconds
        success: Whether OCR was successful
        error: Error message if OCR failed
    """
    text: str
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    text_regions: List[TextRegion] = field(default_factory=list)
    language: str = "en"
    processing_time: float = 0.0
    success: bool = True
    error: Optional[str] = None

    def __post_init__(self):
        """Validate OCR result data."""
        if self.confidence < 0.0 or self.confidence > 1.0:
            logger.warning(f"Confidence score {self.confidence} is out of range [0, 1]")
            self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> Dict[str, Any]:
        """Convert OCR result to dictionary."""
        return {
            "text": self.text,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "language": self.language,
            "processing_time": self.processing_time,
            "success": self.success,
            "error": self.error,
            "text_regions_count": len(self.text_regions)
        }


class BaseOCREngine(ABC):
    """
    Abstract base class for OCR engines.

    All OCR engine implementations must inherit from this class and
    implement the extract_text method.
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize OCR engine.

        Args:
            config: Engine-specific configuration
        """
        self.config = config or {}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    async def extract_text(self, image_path: Path) -> OCRResult:
        """
        Extract text from an image.

        Args:
            image_path: Path to the image file

        Returns:
            OCRResult containing extracted text and metadata

        Raises:
            FileNotFoundError: If image file doesn't exist
            Exception: For other processing errors
        """
        pass

    def validate_image_path(self, image_path: Path) -> None:
        """
        Validate that image path exists and is a file.

        Args:
            image_path: Path to validate

        Raises:
            FileNotFoundError: If path doesn't exist or is not a file
        """
        if not image_path.exists():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        if not image_path.is_file():
            raise ValueError(f"Path is not a file: {image_path}")

    def get_supported_formats(self) -> List[str]:
        """
        Get list of supported image formats.

        Returns:
            List of file extensions (e.g., ['.jpg', '.png'])
        """
        # Default supported formats
        return ['.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp']

    def is_supported_format(self, image_path: Path) -> bool:
        """
        Check if image format is supported.

        Args:
            image_path: Path to image

        Returns:
            True if format is supported
        """
        suffix = image_path.suffix.lower()
        return suffix in self.get_supported_formats()

    def preprocess_config(self) -> bool:
        """
        Validate and preprocess engine configuration.

        Returns:
            True if configuration is valid

        Raises:
            ValueError: If required configuration is missing
        """
        # Override in subclasses if needed
        return True

    async def extract_text_batch(self, image_paths: List[Path]) -> List[OCRResult]:
        """
        Extract text from multiple images.

        Default implementation processes images sequentially.
        Override for engines that support batch processing.

        Args:
            image_paths: List of image paths

        Returns:
            List of OCR results
        """
        results = []
        for image_path in image_paths:
            try:
                result = await self.extract_text(image_path)
                results.append(result)
            except Exception as e:
                self.logger.error(f"Error processing {image_path}: {e}")
                results.append(OCRResult(
                    text="",
                    confidence=0.0,
                    success=False,
                    error=str(e)
                ))
        return results
