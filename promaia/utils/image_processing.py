"""
Image processing utilities for chat with vision models.

This module provides functionality to handle image inputs for all supported AI models:
- OpenAI GPT-4o (vision)
- Anthropic Claude Sonnet 4 (vision) 
- Google Gemini 2.5 Pro (multimodal)
- Local Llama (vision models like LLaVA)
"""

import base64
import io
from typing import List, Dict, Any, Optional, Union, Tuple
from PIL import Image
import mimetypes
import os
from pathlib import Path

# Supported image formats
SUPPORTED_FORMATS = {
    'image/jpeg': ['.jpg', '.jpeg'],
    'image/png': ['.png'],
    'image/webp': ['.webp'],
    'image/gif': ['.gif']  # Non-animated only for most models
}

# Maximum image size (in bytes) - 20MB for inline base64
MAX_IMAGE_SIZE = 20 * 1024 * 1024

# Threshold for using Gemini File API instead of base64 (20MB by default)
# Can be overridden with GEMINI_FILE_API_THRESHOLD_MB environment variable
_threshold_mb = int(os.getenv("GEMINI_FILE_API_THRESHOLD_MB", "20"))
GEMINI_FILE_API_THRESHOLD = _threshold_mb * 1024 * 1024

# Maximum image size for Gemini File API - 2GB
GEMINI_FILE_API_MAX_SIZE = 2 * 1024 * 1024 * 1024

# Maximum image dimensions
MAX_IMAGE_DIMENSIONS = (4096, 4096)

def validate_image_format(filename: str, media_type: str = None) -> str:
    """
    Validate that the image format is supported.
    
    Args:
        filename: The filename of the image
        media_type: Optional MIME type override
        
    Returns:
        The validated MIME type
        
    Raises:
        ValueError: If the format is not supported
    """
    if media_type:
        if media_type not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported image format: {media_type}")
        return media_type
    
    # Try to determine from filename
    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type or mime_type not in SUPPORTED_FORMATS:
        # Try by extension
        ext = Path(filename).suffix.lower()
        for supported_type, extensions in SUPPORTED_FORMATS.items():
            if ext in extensions:
                return supported_type
        
        raise ValueError(f"Unsupported image format for file: {filename}")
    
    return mime_type

def encode_image_from_path(image_path: str, max_size: Optional[Tuple[int, int]] = None) -> Dict[str, str]:
    """
    Encode an image from file path to base64.
    
    Args:
        image_path: Path to the image file
        max_size: Optional maximum dimensions (width, height)
        
    Returns:
        Dict with 'data' (base64) and 'media_type' keys
        
    Raises:
        FileNotFoundError: If the image file doesn't exist
        ValueError: If the image format is not supported or file is too large
        IOError: If the image cannot be processed
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")
    
    # Check file size
    file_size = os.path.getsize(image_path)
    if file_size > MAX_IMAGE_SIZE:
        raise ValueError(f"Image file too large: {file_size} bytes (max: {MAX_IMAGE_SIZE})")
    
    # Validate format
    media_type = validate_image_format(image_path)
    
    try:
        # Open and process image
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (for JPEG compatibility)
            if img.mode in ('RGBA', 'LA', 'P'):
                if media_type == 'image/jpeg':
                    # Convert RGBA to RGB with white background for JPEG
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
            
            # Resize if needed
            if max_size or img.size[0] > MAX_IMAGE_DIMENSIONS[0] or img.size[1] > MAX_IMAGE_DIMENSIONS[1]:
                target_size = max_size or MAX_IMAGE_DIMENSIONS
                img.thumbnail(target_size, Image.Resampling.LANCZOS)
            
            # Save to bytes
            img_bytes = io.BytesIO()
            format_map = {
                'image/jpeg': 'JPEG',
                'image/png': 'PNG', 
                'image/webp': 'WEBP',
                'image/gif': 'GIF'
            }
            img.save(img_bytes, format=format_map[media_type], quality=85, optimize=True)
            img_bytes.seek(0)
            
            # Encode to base64
            base64_data = base64.b64encode(img_bytes.getvalue()).decode('utf-8')
            
            return {
                'data': base64_data,
                'media_type': media_type
            }
            
    except Exception as e:
        raise IOError(f"Failed to process image {image_path}: {str(e)}")

def encode_image_from_bytes(image_data: bytes, filename: str = None, media_type: str = None) -> Dict[str, str]:
    """
    Encode an image from bytes to base64.
    
    Args:
        image_data: Raw image bytes
        filename: Optional filename for format detection
        media_type: Optional MIME type override
        
    Returns:
        Dict with 'data' (base64) and 'media_type' keys
        
    Raises:
        ValueError: If the image format is not supported or data is too large
        IOError: If the image cannot be processed
    """
    if len(image_data) > MAX_IMAGE_SIZE:
        raise ValueError(f"Image data too large: {len(image_data)} bytes (max: {MAX_IMAGE_SIZE})")
    
    try:
        # Open image from bytes to validate
        img = Image.open(io.BytesIO(image_data))
        
        # Try to determine media type
        if not media_type:
            if filename:
                media_type = validate_image_format(filename)
            else:
                # Try to detect from image format
                format_to_mime = {
                    'JPEG': 'image/jpeg',
                    'PNG': 'image/png',
                    'WEBP': 'image/webp',
                    'GIF': 'image/gif'
                }
                media_type = format_to_mime.get(img.format, 'image/jpeg')
        
        validate_image_format("", media_type)  # Validate the detected type
        
        # Process similar to file encoding
        if img.mode in ('RGBA', 'LA', 'P'):
            if media_type == 'image/jpeg':
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
        
        # Resize if needed
        if img.size[0] > MAX_IMAGE_DIMENSIONS[0] or img.size[1] > MAX_IMAGE_DIMENSIONS[1]:
            img.thumbnail(MAX_IMAGE_DIMENSIONS, Image.Resampling.LANCZOS)
        
        # Save to bytes
        img_bytes = io.BytesIO()
        format_map = {
            'image/jpeg': 'JPEG',
            'image/png': 'PNG',
            'image/webp': 'WEBP', 
            'image/gif': 'GIF'
        }
        img.save(img_bytes, format=format_map[media_type], quality=85, optimize=True)
        img_bytes.seek(0)
        
        # Encode to base64
        base64_data = base64.b64encode(img_bytes.getvalue()).decode('utf-8')
        
        return {
            'data': base64_data,
            'media_type': media_type
        }
        
    except Exception as e:
        raise IOError(f"Failed to process image data: {str(e)}")

def convert_to_webp(
    image_input: Union[str, bytes],
    quality: int = 85,
    max_dimension: Optional[int] = None
) -> Tuple[bytes, str]:
    """
    Convert PNG/JPEG image to WebP format with optional resizing.

    WebP provides 30-80% smaller file sizes compared to JPEG/PNG while maintaining
    similar visual quality. This function is designed for web optimization, particularly
    for CMS/Webflow uploads.

    Args:
        image_input: Either a file path (str) or image bytes
        quality: WebP compression quality (1-100, default: 85)
                 Lower = smaller file, higher = better quality
        max_dimension: Optional maximum width/height in pixels.
                       If image exceeds this, it will be resized proportionally.

    Returns:
        Tuple of (webp_bytes, new_filename)
        - webp_bytes: The converted image as bytes
        - new_filename: Original filename with .webp extension

    Raises:
        ValueError: If quality is out of range or image cannot be opened
        IOError: If conversion fails

    Example:
        >>> webp_data, webp_name = convert_to_webp("photo.jpg", quality=90)
        >>> # Save the webp_data to webp_name
    """
    if not 1 <= quality <= 100:
        raise ValueError(f"Quality must be between 1 and 100, got {quality}")

    try:
        # Load image from path or bytes
        if isinstance(image_input, str):
            img = Image.open(image_input)
            original_filename = Path(image_input).name
        else:
            img = Image.open(io.BytesIO(image_input))
            original_filename = "image"

        # Convert RGBA/LA/P modes to RGB for WebP compatibility
        # WebP supports transparency, but RGB conversion is safer for broad compatibility
        if img.mode in ('RGBA', 'LA'):
            # Create white background for transparent images
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                background.paste(img, mask=img.split()[-1])
            else:
                background.paste(img, mask=img.split()[1])
            img = background
        elif img.mode == 'P':
            # Convert palette images to RGB
            img = img.convert('RGB')
        elif img.mode not in ('RGB', 'L'):
            # Convert any other mode to RGB
            img = img.convert('RGB')

        # Resize if max_dimension is specified
        if max_dimension and (img.size[0] > max_dimension or img.size[1] > max_dimension):
            # Calculate new size maintaining aspect ratio
            ratio = min(max_dimension / img.size[0], max_dimension / img.size[1])
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # Convert to WebP
        webp_bytes = io.BytesIO()
        img.save(webp_bytes, format='WEBP', quality=quality, method=6)
        webp_bytes.seek(0)

        # Generate new filename with .webp extension
        original_name = Path(original_filename).stem
        new_filename = f"{original_name}.webp"

        return webp_bytes.getvalue(), new_filename

    except Exception as e:
        raise IOError(f"Failed to convert image to WebP: {str(e)}")

def process_image_for_gemini(image_path: str, max_size: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """
    Process an image for Gemini API, automatically choosing between base64 and File API.

    For files <= 20MB: Uses base64 encoding (faster, no upload needed)
    For files > 20MB: Uses File API (supports up to 2GB)

    Args:
        image_path: Path to the image file
        max_size: Optional maximum dimensions (width, height)

    Returns:
        Dict with either:
        - 'data', 'media_type', and 'method': 'base64' for inline images
        - 'file_uri' and 'method': 'file_api' for uploaded files

    Raises:
        FileNotFoundError: If the image file doesn't exist
        ValueError: If the image format is not supported or file is too large
        IOError: If the image cannot be processed
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    # Check file size to determine method
    file_size = os.path.getsize(image_path)

    if file_size <= GEMINI_FILE_API_THRESHOLD:
        # Use base64 encoding for smaller files
        encoded = encode_image_from_path(image_path, max_size)
        encoded['method'] = 'base64'
        return encoded
    else:
        # Use File API for larger files
        if file_size > GEMINI_FILE_API_MAX_SIZE:
            raise ValueError(f"Image file too large: {file_size} bytes (max: {GEMINI_FILE_API_MAX_SIZE})")

        file_uri = upload_image_to_gemini_file_api(image_path)
        return {
            'file_uri': file_uri,
            'method': 'file_api',
            'media_type': validate_image_format(image_path)
        }

def upload_image_to_gemini_file_api(image_path: str, display_name: str = None) -> str:
    """
    Upload an image to Gemini File API for large files (>20MB).

    Args:
        image_path: Path to the image file
        display_name: Optional display name for the file

    Returns:
        The file URI that can be used in Gemini API calls

    Raises:
        ValueError: If the file is too large or format unsupported
        RuntimeError: If upload fails
    """
    try:
        from google import genai
    except ImportError:
        raise ImportError("google-genai package is required for File API. Install with: pip install google-genai")

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    file_size = os.path.getsize(image_path)
    if file_size > GEMINI_FILE_API_MAX_SIZE:
        raise ValueError(f"Image file too large for Gemini File API: {file_size} bytes (max: {GEMINI_FILE_API_MAX_SIZE})")

    media_type = validate_image_format(image_path)

    try:
        if display_name is None:
            display_name = os.path.basename(image_path)

        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        uploaded_file = client.files.upload(file=image_path, config={"display_name": display_name})

        return uploaded_file.uri

    except Exception as e:
        raise RuntimeError(f"Failed to upload image to Gemini File API: {str(e)}")

def process_document_for_gemini(document_path: str) -> Dict[str, Any]:
    """
    Process a document (PDF, DOCX, etc.) for Gemini API using File API.

    Gemini can process documents natively, including PDFs up to 1000 pages.
    All documents use the File API regardless of size.

    Args:
        document_path: Path to the document file

    Returns:
        Dict with 'file_uri', 'method': 'file_api', and 'media_type'

    Raises:
        FileNotFoundError: If the document file doesn't exist
        ValueError: If the document format is not supported or file is too large
        RuntimeError: If the document cannot be uploaded
    """
    if not os.path.exists(document_path):
        raise FileNotFoundError(f"Document file not found: {document_path}")

    # Check file size
    file_size = os.path.getsize(document_path)
    if file_size > GEMINI_FILE_API_MAX_SIZE:
        raise ValueError(f"Document file too large: {file_size} bytes (max: {GEMINI_FILE_API_MAX_SIZE} = 2GB)")

    # Determine MIME type
    mime_type, _ = mimetypes.guess_type(document_path)
    if not mime_type:
        # Try by extension
        ext = Path(document_path).suffix.lower()
        mime_type_map = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.csv': 'text/csv',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        }
        mime_type = mime_type_map.get(ext)
        if not mime_type:
            raise ValueError(f"Unsupported document format: {ext}")

    try:
        from google import genai

        display_name = os.path.basename(document_path)
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        uploaded_file = client.files.upload(
            file=document_path,
            config={"display_name": display_name, "mime_type": mime_type},
        )

        return {
            'file_uri': uploaded_file.uri,
            'method': 'file_api',
            'media_type': mime_type,
            'display_name': display_name
        }

    except Exception as e:
        raise RuntimeError(f"Failed to upload document to Gemini File API: {str(e)}")

def format_image_for_openai(base64_data: str, media_type: str) -> Dict[str, Any]:
    """
    Format image data for OpenAI GPT-4o Vision API.

    Args:
        base64_data: Base64 encoded image data
        media_type: MIME type of the image

    Returns:
        Formatted message content for OpenAI API
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{media_type};base64,{base64_data}",
            "detail": "high"  # Can be "low", "high", or "auto"
        }
    }

def format_image_for_anthropic(base64_data: str, media_type: str) -> Dict[str, Any]:
    """
    Format image data for Anthropic Claude API.
    
    Args:
        base64_data: Base64 encoded image data
        media_type: MIME type of the image
        
    Returns:
        Formatted message content for Anthropic API
    """
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64_data
        }
    }

def format_image_for_gemini(base64_data: str = None, media_type: str = None, file_uri: str = None):
    """
    Format image data for Google Gemini API.
    Supports both base64 inline data and File API URIs.

    Args:
        base64_data: Base64 encoded image data (for inline images)
        media_type: MIME type of the image (required with base64_data)
        file_uri: File URI from Gemini File API (alternative to base64_data)

    Returns:
        Formatted image data for Gemini API - either a dict with mime_type/data
        or a file reference object

    Raises:
        ValueError: If neither base64_data nor file_uri is provided
    """
    if file_uri:
        # Return File API reference
        try:
            from google import genai
            client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
            return client.files.get(name=file_uri.split('/')[-1])
        except Exception:
            return {"file_uri": file_uri}
    elif base64_data and media_type:
        # Return inline base64 data
        return {
            "mime_type": media_type,
            "data": base64_data
        }
    else:
        raise ValueError("Either base64_data with media_type, or file_uri must be provided")

def format_image_for_llama(base64_data: str, media_type: str) -> Dict[str, Any]:
    """
    Format image data for local Llama models (like LLaVA).
    This follows the OpenAI-compatible format used by many local inference servers.
    
    Args:
        base64_data: Base64 encoded image data
        media_type: MIME type of the image
        
    Returns:
        Formatted message content for Llama API
    """
    return {
        "type": "image_url", 
        "image_url": {
            "url": f"data:{media_type};base64,{base64_data}"
        }
    }

def is_vision_supported(model_type: str) -> bool:
    """
    Check if the given model type supports vision/image inputs.
    
    Args:
        model_type: The model type ('openai', 'anthropic', 'gemini', 'llama')
        
    Returns:
        True if vision is supported, False otherwise
    """
    # All the configured models support vision
    vision_models = {'openai', 'anthropic', 'gemini', 'llama'}
    return model_type.lower() in vision_models

def get_model_image_limits(model_type: str) -> Dict[str, Any]:
    """
    Get image processing limits for a specific model.

    Args:
        model_type: The model type

    Returns:
        Dict with 'max_images', 'max_size', and 'supported_formats' keys
    """
    limits = {
        'openai': {
            'max_images': 10,  # Per message
            'max_size': MAX_IMAGE_SIZE,
            'supported_formats': list(SUPPORTED_FORMATS.keys())
        },
        'anthropic': {
            'max_images': 5,   # Per message
            'max_size': MAX_IMAGE_SIZE,
            'supported_formats': ['image/jpeg', 'image/png', 'image/webp']
        },
        'gemini': {
            'max_images': 16,  # Per message
            'max_size': GEMINI_FILE_API_MAX_SIZE,  # 2GB via File API
            'supported_formats': list(SUPPORTED_FORMATS.keys()),
            'note': f'Uses base64 for images <={GEMINI_FILE_API_THRESHOLD/(1024*1024):.0f}MB, File API for larger images up to 2GB'
        },
        'llama': {
            'max_images': 1,   # Most vision models support 1 image
            'max_size': MAX_IMAGE_SIZE,
            'supported_formats': ['image/jpeg', 'image/png']
        }
    }

    return limits.get(model_type.lower(), limits['openai'])  # Default to OpenAI limits
