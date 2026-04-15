"""OCR module for Promaia."""

from promaia.ocr.engines.base import BaseOCREngine, OCRResult, TextRegion
from promaia.ocr.engines.mock import MockOCREngine
from promaia.ocr.image_preprocessor import ImagePreprocessor
from promaia.ocr.text_postprocessor import TextPostprocessor

# Try to import Google Vision engine (optional dependency)
try:
    from promaia.ocr.engines.google_vision import GoogleVisionEngine
    google_vision_available = True
except ImportError:
    google_vision_available = False
    GoogleVisionEngine = None

__all__ = [
    "BaseOCREngine",
    "OCRResult",
    "TextRegion",
    "MockOCREngine",
    "ImagePreprocessor",
    "TextPostprocessor",
]

if google_vision_available:
    __all__.append("GoogleVisionEngine")
