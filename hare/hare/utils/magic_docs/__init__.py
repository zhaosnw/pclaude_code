"""
MagicDocs utilities -- low-level document inspection primitives.

Provides file-type detection via magic bytes, content classification,
encoding sniffing, content preview generation, and document statistics.
These are the building blocks consumed by the higher-level
`hare.services.magic_docs` documentation generation service.

Usage:
    from hare.utils.magic_docs import (
        detect_file_type,
        classify_content,
        detect_encoding,
        generate_content_preview,
        get_document_stats,
        is_binary,
        extract_frontmatter,
        MAGIC_SIGNATURES,
    )
"""

from hare.utils.magic_docs.magic_docs import (
    # Constants
    MAGIC_SIGNATURES,
    TEXT_ENCODINGS_ORDERED,
    _EXTENSION_TO_MIME,
    # Detection
    detect_file_type,
    detect_file_type_from_bytes,
    detect_file_type_from_path,
    # Content classification
    classify_content,
    # Language detection
    detect_language,
    # Encoding
    detect_encoding,
    is_binary,
    is_readable_text,
    # Content extraction
    extract_frontmatter,
    generate_content_preview,
    # Line endings
    detect_line_endings,
    normalize_line_endings,
    # Shebang
    detect_shebang,
    # MIME helpers
    mime_from_extension,
    extension_from_mime,
    mime_category,
    is_text_mime,
    is_image_mime,
    # Comment analysis
    get_comment_ratio,
    # Statistics
    get_document_stats,
)
