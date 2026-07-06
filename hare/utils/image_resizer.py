"""
Image resizing utilities — resize images for API token budget compliance.

Port of: src/utils/imageResizer.ts

Handles image resizing to meet API requirements: max file size,
max dimensions, and token-aware compression for many-image requests.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

# API image limits
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_DIMENSION = 8000  # Max dimension for single image
MAX_IMAGE_DIMENSION_MANY = 2000  # Max dimension for many-image requests


class ImageSizeError(Exception):
    """Image exceeds API size limits."""
    def __init__(self, message: str):
        super().__init__(message)


class ImageResizeError(Exception):
    """Failed to resize image."""
    def __init__(self, message: str):
        super().__init__(message)


def get_image_dimensions(image_data: bytes) -> Tuple[int, int]:
    """Get image dimensions (width, height) from raw bytes.

    Uses PIL/Pillow if available, otherwise estimates from headers.
    """
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_data))
        return img.size
    except ImportError:
        pass

    # Fallback: try to parse common image headers
    if image_data[:4] == b'\x89PNG':
        # PNG: IHDR chunk at offset 16
        import struct
        if len(image_data) >= 24:
            w, h = struct.unpack('>II', image_data[16:24])
            return w, h
    elif image_data[:2] == b'\xff\xd8':
        # JPEG: scan for SOF marker
        return _parse_jpeg_dimensions(image_data)
    elif image_data[:4] in (b'GIF8', b'GIF9'):
        # GIF: width/height at offset 6 (little-endian)
        import struct
        if len(image_data) >= 10:
            return struct.unpack('<HH', image_data[6:10])

    return 0, 0


def _parse_jpeg_dimensions(data: bytes) -> Tuple[int, int]:
    """Parse JPEG dimensions from SOF marker."""
    i = 2
    while i < len(data) - 7:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xD8 or marker == 0xD9:
            i += 2
            continue
        if 0xC0 <= marker <= 0xC3 or 0xC5 <= marker <= 0xC7 or 0xC9 <= marker <= 0xCB:
            import struct
            if i + 8 < len(data):
                h, w = struct.unpack('>HH', data[i+5:i+9])
                return w, h
        if i + 3 < len(data):
            length = struct.unpack('>H', data[i+2:i+4])[0]
            i += 2 + length
        else:
            break
    return 0, 0


def maybe_resize_image(image_data: bytes, max_size: int = MAX_IMAGE_BYTES,
                       max_dimension: int = MAX_IMAGE_DIMENSION,
                       for_many_image: bool = False) -> bytes:
    """Resize an image if it exceeds size or dimension limits.

    Args:
        image_data: Raw image bytes
        max_size: Maximum file size in bytes (default: 5MB)
        max_dimension: Maximum width/height (default: 8000px)
        for_many_image: Use stricter limits for many-image requests (2000px)

    Returns:
        Potentially resized image bytes, or original if within limits.
    """
    effective_max_dim = MAX_IMAGE_DIMENSION_MANY if for_many_image else max_dimension

    # Check file size first
    if len(image_data) <= max_size:
        # Still check dimensions
        w, h = get_image_dimensions(image_data)
        if w <= effective_max_dim and h <= effective_max_dim:
            return image_data

    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_data))
        orig_w, orig_h = img.size

        # Resize if dimensions exceed limits
        if orig_w > effective_max_dim or orig_h > effective_max_dim:
            ratio = min(effective_max_dim / orig_w, effective_max_dim / orig_h)
            new_w = int(orig_w * ratio)
            new_h = int(orig_h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # Save with quality reduction if needed
        output = io.BytesIO()
        img_format = img.format or 'JPEG'
        quality = 85
        while quality >= 30:
            output.seek(0)
            output.truncate()
            if img_format.upper() in ('JPEG', 'JPG'):
                img.save(output, format=img_format, quality=quality)
            elif img_format.upper() == 'PNG':
                img.save(output, format='PNG', optimize=True)
            elif img_format.upper() == 'WEBP':
                img.save(output, format='WEBP', quality=quality)
            else:
                img.save(output, format='JPEG', quality=quality)

            if output.tell() <= max_size or quality <= 30:
                break
            quality -= 15

        result = output.getvalue()
        if len(result) > max_size:
            raise ImageResizeError(
                f"Could not resize image to under {max_size:,} bytes "
                f"(final size: {len(result):,} bytes)"
            )
        return result

    except ImportError:
        # Pillow not available - can't resize
        if len(image_data) > max_size:
            raise ImageSizeError(
                f"Image exceeds {max_size:,} byte limit ({len(image_data):,} bytes). "
                "Install Pillow (pip install Pillow) for automatic resizing."
            )
        return image_data


def create_image_metadata_text(path: str) -> str:
    """Create human-readable metadata text for an image file."""
    if not os.path.exists(path):
        return ""
    size = os.path.getsize(path)
    basename = os.path.basename(path)

    # Try to get dimensions
    try:
        with open(path, 'rb') as f:
            header = f.read(65536)
        w, h = get_image_dimensions(header)
        dims = f"{w}x{h}" if w and h else "unknown"
    except Exception:
        dims = "unknown"

    return (
        f"Image: {basename}\n"
        f"  Size: {size:,} bytes\n"
        f"  Dimensions: {dims}"
    )
