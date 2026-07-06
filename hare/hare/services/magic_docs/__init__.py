"""
MagicDocs — automated documentation generation for tools, skills, and components.

Usage:
    from hare.services.magic_docs import (
        search_docs,
        generate_tool_docs,
        generate_skill_docs,
        generate_all_docs,
        format_as_markdown,
        MagicDocsResult,
        ToolDoc,
        SkillDoc,
        GeneratedDocs,
    )
"""

from hare.services.magic_docs.magic_docs import (
    # Data models
    MagicDocsResult,
    ParameterDoc,
    ToolDoc,
    SkillDoc,
    ModuleDoc,
    GeneratedDocs,
    # Generation
    generate_tool_doc_single,
    generate_tool_docs,
    generate_skill_docs,
    generate_module_docs,
    generate_all_docs,
    # Search
    search_docs,
    search_docs_async,
    # Formatting
    format_tool_doc_as_markdown,
    format_skill_doc_as_markdown,
    format_module_doc_as_markdown,
    format_as_markdown,
    format_as_html,
    # Export
    export_docs_to_file,
    # Utilities
    get_tool_quick_reference,
    get_skill_quick_reference,
    # Async
    generate_all_docs_async,
)
