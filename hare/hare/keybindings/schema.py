"""
Zod schema metadata for keybindings.json (port of src/keybindings/schema.ts).

Runtime validation uses plain Python checks elsewhere; this module holds
constants and descriptions for tooling / documentation parity.
"""

from __future__ import annotations


KEYBINDING_CONTEXTS: tuple[str, ...] = (
    "Global",
    "Chat",
    "Autocomplete",
    "Confirmation",
    "Help",
    "Transcript",
    "HistorySearch",
    "Task",
    "ThemePicker",
    "Settings",
    "Tabs",
    "Attachments",
    "Footer",
    "MessageSelector",
    "DiffDialog",
    "ModelPicker",
    "Select",
    "Plugin",
)

KEYBINDING_CONTEXT_DESCRIPTIONS: dict[str, str] = {
    "Global": "Active everywhere, regardless of focus",
    "Chat": "When the chat input is focused",
    "Autocomplete": "When autocomplete menu is visible",
    "Confirmation": "When a confirmation/permission dialog is shown",
    "Help": "When the help overlay is open",
    "Transcript": "When viewing the transcript",
    "HistorySearch": "When searching command history (ctrl+r)",
    "Task": "When a task/agent is running in the foreground",
    "ThemePicker": "When the theme picker is open",
    "Settings": "When the settings menu is open",
    "Tabs": "When tab navigation is active",
    "Attachments": "When navigating image attachments in a select dialog",
    "Footer": "When footer indicators are focused",
    "MessageSelector": "When the message selector (rewind) is open",
    "DiffDialog": "When the diff dialog is open",
    "ModelPicker": "When the model picker is open",
    "Select": "When a select/list component is focused",
    "Plugin": "When the plugin dialog is open",
}

# Subset of canonical actions — full list in TS KEYBINDING_ACTIONS
KEYBINDING_ACTIONS: tuple[str, ...] = (
    "app:interrupt",
    "app:exit",
    "chat:submit",
    "chat:cancel",
    "history:previous",
    "history:next",
)


def keybindings_schema_description() -> str:
    return "Hare keybindings configuration. Customize keyboard shortcuts by context."
