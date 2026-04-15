"""
Mock OCR engine for testing.

Returns fake OCR results without making actual API calls.
Useful for development and testing without incurring API costs.
"""
import time
import logging
from pathlib import Path
from typing import Dict, Any

from promaia.ocr.engines.base import BaseOCREngine, OCRResult, TextRegion

logger = logging.getLogger(__name__)


class MockOCREngine(BaseOCREngine):
    """Mock OCR engine that returns fake results for testing."""

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize mock OCR engine.

        Args:
            config: Configuration dict with optional keys:
                - confidence: Default confidence score (0.0-1.0)
                - language: Default language code
                - processing_delay: Artificial delay in seconds
                - fail_rate: Percentage of requests to fail (0.0-1.0)
        """
        super().__init__(config)
        self.default_confidence = config.get("confidence", 0.85) if config else 0.85
        self.default_language = config.get("language", "en") if config else "en"
        self.processing_delay = config.get("processing_delay", 0.1) if config else 0.1
        self.fail_rate = config.get("fail_rate", 0.0) if config else 0.0
        self.request_count = 0

    async def extract_text(self, image_path: Path) -> OCRResult:
        """
        Extract fake text from image.

        Args:
            image_path: Path to image file

        Returns:
            OCRResult with mock data
        """
        start_time = time.time()
        self.request_count += 1

        try:
            # Validate image exists
            self.validate_image_path(image_path)

            # Simulate processing delay
            if self.processing_delay > 0:
                time.sleep(self.processing_delay)

            # Simulate random failures based on fail_rate
            import random
            if random.random() < self.fail_rate:
                raise Exception("Simulated OCR failure")

            # Generate mock text based on filename
            filename = image_path.stem
            mock_text = self._generate_mock_text(filename)

            # Generate mock text regions
            text_regions = [
                TextRegion(
                    text="Mock line 1",
                    confidence=self.default_confidence,
                    bounding_box={"vertices": [
                        {"x": 10, "y": 10},
                        {"x": 200, "y": 10},
                        {"x": 200, "y": 30},
                        {"x": 10, "y": 30}
                    ]}
                ),
                TextRegion(
                    text="Mock line 2",
                    confidence=self.default_confidence,
                    bounding_box={"vertices": [
                        {"x": 10, "y": 40},
                        {"x": 200, "y": 40},
                        {"x": 200, "y": 60},
                        {"x": 10, "y": 60}
                    ]}
                )
            ]

            processing_time = time.time() - start_time

            return OCRResult(
                text=mock_text,
                confidence=self.default_confidence,
                text_regions=text_regions,
                language=self.default_language,
                processing_time=processing_time,
                success=True,
                metadata={
                    "engine": "mock",
                    "request_count": self.request_count,
                    "source_file": str(image_path)
                }
            )

        except Exception as e:
            processing_time = time.time() - start_time
            self.logger.error(f"Mock OCR error for {image_path}: {e}")

            return OCRResult(
                text="",
                confidence=0.0,
                processing_time=processing_time,
                success=False,
                error=str(e)
            )

    def _generate_mock_text(self, filename: str) -> str:
        """
        Generate mock text content based on filename.

        Args:
            filename: Image filename (without extension)

        Returns:
            Mock text content
        """
        return f"""Mock OCR Result for {filename}

This is simulated handwritten journal text.
The mock OCR engine generated this content for testing purposes.

Some sample content:
- Today was a productive day
- I worked on implementing the OCR pipeline
- The weather was nice and sunny

This text would normally come from Google Cloud Vision API,
but we're using the mock engine for development and testing.

Image: {filename}
Mock Engine v1.0
"""

    def preprocess_config(self) -> bool:
        """
        Validate mock engine configuration.

        Returns:
            True (mock engine always valid)
        """
        # Validate confidence range
        if not 0.0 <= self.default_confidence <= 1.0:
            self.logger.warning(
                f"Invalid confidence {self.default_confidence}, using 0.85"
            )
            self.default_confidence = 0.85

        # Validate fail rate
        if not 0.0 <= self.fail_rate <= 1.0:
            self.logger.warning(f"Invalid fail_rate {self.fail_rate}, using 0.0")
            self.fail_rate = 0.0

        return True
