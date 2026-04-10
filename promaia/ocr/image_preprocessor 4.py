"""
Image preprocessing utilities for OCR.

Provides functions to prepare images for OCR processing:
- Format conversion (WebP)
- Resizing
- Contrast enhancement
- Noise reduction
"""
import logging
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING, Any
from PIL import Image

# Optional dependencies for advanced preprocessing
try:
    import numpy as np
    import cv2
    opencv_available = True
except ImportError:
    opencv_available = False
    np = None
    cv2 = None
    # Create dummy type for type hints
    if TYPE_CHECKING:
        import numpy as np

logger = logging.getLogger(__name__)


class ImagePreprocessor:
    """Handles image preprocessing for OCR."""

    def __init__(
        self,
        resize_max: int = 4096,
        enhance_contrast: bool = True,
        denoise: bool = False
    ):
        """
        Initialize image preprocessor.

        Args:
            resize_max: Maximum dimension for resizing
            enhance_contrast: Whether to enhance contrast
            denoise: Whether to apply denoising
        """
        self.resize_max = resize_max
        self.enhance_contrast = enhance_contrast and opencv_available
        self.denoise = denoise and opencv_available

        if not opencv_available and (enhance_contrast or denoise):
            logger.warning(
                "OpenCV not available. Advanced preprocessing disabled. "
                "Install with: pip install opencv-python numpy"
            )

    async def preprocess_image(
        self,
        image_path: Path,
        output_path: Optional[Path] = None
    ) -> Path:
        """
        Preprocess an image for OCR.

        Args:
            image_path: Path to input image
            output_path: Optional path for output image (default: temp file)

        Returns:
            Path to preprocessed image
        """
        try:
            # Load image
            image = Image.open(image_path)

            # Convert HEIC/HEIF if needed
            if image.format in ['HEIC', 'HEIF']:
                image = image.convert('RGB')

            # Convert to RGB if not already
            if image.mode != 'RGB':
                image = image.convert('RGB')

            # Resize if too large
            if max(image.size) > self.resize_max:
                image = self._resize_image(image)
                logger.debug(f"Resized image to {image.size}")

            # Convert to numpy array for OpenCV processing
            img_array = np.array(image)

            # Enhance contrast if enabled
            if self.enhance_contrast:
                img_array = self._enhance_contrast(img_array)
                logger.debug("Enhanced image contrast")

            # Denoise if enabled
            if self.denoise:
                img_array = self._denoise_image(img_array)
                logger.debug("Applied denoising")

            # Convert back to PIL Image
            processed_image = Image.fromarray(img_array)

            # Determine output path
            if output_path is None:
                output_path = image_path.with_suffix('.preprocessed.webp')

            # Save processed image as WebP for better compression
            processed_image.save(output_path, 'WEBP', quality=85)
            logger.debug(f"Saved preprocessed image to {output_path}")

            return output_path

        except Exception as e:
            logger.error(f"Error preprocessing image {image_path}: {e}")
            # Return original path if preprocessing fails
            return image_path

    def _resize_image(self, image: Image.Image) -> Image.Image:
        """
        Resize image while maintaining aspect ratio.

        Args:
            image: PIL Image

        Returns:
            Resized PIL Image
        """
        width, height = image.size
        max_dim = max(width, height)

        if max_dim <= self.resize_max:
            return image

        scale = self.resize_max / max_dim
        new_width = int(width * scale)
        new_height = int(height * scale)

        return image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def _enhance_contrast(self, img_array: Any) -> Any:
        """
        Enhance image contrast using CLAHE.

        Args:
            img_array: Image as numpy array (RGB)

        Returns:
            Contrast-enhanced image array
        """
        # Convert to LAB color space
        lab = cv2.cvtColor(img_array, cv2.COLOR_RGB2LAB)

        # Apply CLAHE to L channel
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])

        # Convert back to RGB
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

        return enhanced

    def _denoise_image(self, img_array: Any) -> Any:
        """
        Apply denoising to image.

        Args:
            img_array: Image as numpy array (RGB)

        Returns:
            Denoised image array
        """
        # Use Non-local Means Denoising
        denoised = cv2.fastNlMeansDenoisingColored(
            img_array,
            None,
            h=10,
            hColor=10,
            templateWindowSize=7,
            searchWindowSize=21
        )

        return denoised

    async def convert_to_webp(
        self,
        image_path: Path,
        output_path: Optional[Path] = None,
        quality: int = 85
    ) -> Path:
        """
        Convert image to WebP format.

        Args:
            image_path: Path to input image
            output_path: Optional output path (default: same name with .webp)
            quality: WebP quality (0-100)

        Returns:
            Path to WebP image
        """
        try:
            # Load image
            image = Image.open(image_path)

            # Convert to RGB if needed
            if image.mode in ('RGBA', 'LA', 'P'):
                image = image.convert('RGB')

            # Determine output path
            if output_path is None:
                output_path = image_path.with_suffix('.webp')

            # Save as WebP
            image.save(output_path, 'WEBP', quality=quality)
            logger.debug(f"Converted image to WebP: {output_path}")

            return output_path

        except Exception as e:
            logger.error(f"Error converting image to WebP: {e}")
            # Return original path if conversion fails
            return image_path

    def get_image_info(self, image_path: Path) -> dict:
        """
        Get basic information about an image.

        Args:
            image_path: Path to image

        Returns:
            Dict with image info (size, format, mode)
        """
        try:
            with Image.open(image_path) as img:
                return {
                    "size": img.size,
                    "format": img.format,
                    "mode": img.mode,
                    "width": img.width,
                    "height": img.height
                }
        except Exception as e:
            logger.error(f"Error getting image info: {e}")
            return {}


async def preprocess_for_ocr(
    image_path: Path,
    config: dict = None
) -> Path:
    """
    Convenience function to preprocess an image for OCR.

    Args:
        image_path: Path to image
        config: Optional preprocessing config

    Returns:
        Path to preprocessed image
    """
    config = config or {}
    preprocessor = ImagePreprocessor(
        resize_max=config.get("resize_max", 4096),
        enhance_contrast=config.get("enhance_contrast", True),
        denoise=config.get("denoise", False)
    )

    return await preprocessor.preprocess_image(image_path)
