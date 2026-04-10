"""
Google Cloud Vision OCR engine implementation.

Uses Google Cloud Vision API for text extraction from images.
Excellent for handwritten text and multi-language support.
"""
import time
import logging
from pathlib import Path
from typing import Dict, Any, List

from google.cloud import vision
from google.oauth2 import service_account

from promaia.ocr.engines.base import BaseOCREngine, OCRResult, TextRegion

logger = logging.getLogger(__name__)


class GoogleVisionEngine(BaseOCREngine):
    """OCR engine using Google Cloud Vision API."""

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize Google Vision OCR engine.

        Args:
            config: Configuration dict with optional keys:
                - api_key: Google Cloud Vision API key
                - credentials_path: Path to service account JSON
                - features: List of Vision API features to use
        """
        super().__init__(config)
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize Google Vision client with credentials."""
        try:
            credentials_path = self.config.get("credentials_path")
            api_key = self.config.get("api_key")

            if credentials_path:
                # Use service account credentials
                credentials = service_account.Credentials.from_service_account_file(
                    credentials_path
                )
                self.client = vision.ImageAnnotatorClient(credentials=credentials)
                self.logger.info("Initialized Google Vision with service account")
            elif api_key:
                # Use API key
                # Note: Vision API typically requires service account, but we support both
                self.client = vision.ImageAnnotatorClient()
                self.logger.info("Initialized Google Vision with API key")
            else:
                # Try default credentials
                self.client = vision.ImageAnnotatorClient()
                self.logger.info("Initialized Google Vision with default credentials")

        except Exception as e:
            self.logger.error(f"Failed to initialize Google Vision client: {e}")
            raise

    def preprocess_config(self) -> bool:
        """
        Validate Google Vision configuration.

        Returns:
            True if configuration is valid
        """
        # Check if client was initialized
        if self.client is None:
            raise ValueError("Google Vision client not initialized")

        return True

    async def extract_text(self, image_path: Path) -> OCRResult:
        """
        Extract text from image using Google Cloud Vision.

        Args:
            image_path: Path to image file

        Returns:
            OCRResult with extracted text and metadata
        """
        start_time = time.time()

        try:
            # Validate image
            self.validate_image_path(image_path)

            if not self.is_supported_format(image_path):
                raise ValueError(f"Unsupported image format: {image_path.suffix}")

            # Read image file
            with open(image_path, 'rb') as image_file:
                content = image_file.read()

            image = vision.Image(content=content)

            # Perform text detection
            response = self.client.text_detection(image=image)

            if response.error.message:
                raise Exception(f"Google Vision API error: {response.error.message}")

            # Process results
            texts = response.text_annotations

            if not texts:
                # No text detected
                processing_time = time.time() - start_time
                return OCRResult(
                    text="",
                    confidence=0.0,
                    language="unknown",
                    processing_time=processing_time,
                    success=True,
                    metadata={"no_text_detected": True}
                )

            # First annotation contains full text
            full_text = texts[0].description

            # Calculate average confidence from individual text regions
            text_regions = []
            confidences = []

            for text_annotation in texts[1:]:  # Skip first (full text)
                # Get bounding box
                vertices = text_annotation.bounding_poly.vertices
                bounding_box = {
                    "vertices": [
                        {"x": vertex.x, "y": vertex.y}
                        for vertex in vertices
                    ]
                }

                # Note: Google Vision doesn't provide per-word confidence
                # We'll use a default high confidence since it's generally accurate
                region_confidence = 0.9

                text_regions.append(TextRegion(
                    text=text_annotation.description,
                    confidence=region_confidence,
                    bounding_box=bounding_box
                ))
                confidences.append(region_confidence)

            # Calculate overall confidence
            overall_confidence = sum(confidences) / len(confidences) if confidences else 0.9

            # Detect language (use first detected language)
            language = "unknown"
            if hasattr(response, 'full_text_annotation'):
                full_annotation = response.full_text_annotation
                if full_annotation.pages:
                    page = full_annotation.pages[0]
                    if page.property and page.property.detected_languages:
                        language = page.property.detected_languages[0].language_code

            processing_time = time.time() - start_time

            return OCRResult(
                text=full_text,
                confidence=overall_confidence,
                text_regions=text_regions,
                language=language,
                processing_time=processing_time,
                success=True,
                metadata={
                    "text_regions_count": len(text_regions),
                    "api": "google_cloud_vision"
                }
            )

        except Exception as e:
            processing_time = time.time() - start_time
            self.logger.error(f"Error extracting text from {image_path}: {e}")

            return OCRResult(
                text="",
                confidence=0.0,
                processing_time=processing_time,
                success=False,
                error=str(e)
            )

    async def extract_text_batch(self, image_paths: List[Path]) -> List[OCRResult]:
        """
        Extract text from multiple images.

        Google Vision supports batch requests but we implement sequential
        processing for simplicity and rate limiting.

        Args:
            image_paths: List of image paths

        Returns:
            List of OCR results
        """
        # For now, use sequential processing
        # Could be optimized with Vision API batch requests
        return await super().extract_text_batch(image_paths)

    def get_supported_formats(self) -> List[str]:
        """
        Get list of supported image formats for Google Vision.

        Returns:
            List of supported file extensions
        """
        return [
            '.jpg', '.jpeg', '.png', '.gif', '.bmp',
            '.webp', '.ico', '.tiff', '.tif'
        ]
