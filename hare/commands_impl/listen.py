"""Port of: src/commands/listen/. Show voice/listen mode info and setup help."""

from __future__ import annotations
from typing import Any

COMMAND_NAME = "listen"
DESCRIPTION = "Show voice/listen mode info and setup"
ALIASES: list[str] = []


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Display voice/listen mode status, configuration, and setup instructions."""
    lines: list[str] = []

    # Gather state from context if available
    get_app_state = context.get("get_app_state")
    is_voice_available = context.get("is_voice_available")

    voice_available = False
    voice_enabled = False

    if is_voice_available:
        try:
            voice_available = await is_voice_available()
        except Exception:
            voice_available = False

    if get_app_state:
        try:
            app_state = get_app_state()
            voice_enabled = app_state.get("voiceEnabled", False)
        except Exception:
            voice_enabled = False

    # Status header
    lines.append("Voice / Listen Mode")
    lines.append("=" * 40)

    if voice_available:
        lines.append("Status        : Microphone detected")
    else:
        lines.append("Status        : No microphone detected (or access denied)")

    lines.append(f"Voice enabled : {'Yes' if voice_enabled else 'No'}")

    # Configuration summary
    lines.append("")
    lines.append("Configuration")
    lines.append("-" * 40)
    lines.append("Command               : /listen  (show this info)")
    lines.append("Toggle voice on/off  : /voice")
    lines.append("Config file           : ~/.claude/settings.json")
    lines.append("Relevant key          : voiceEnabled (boolean)")

    # Setup instructions
    lines.append("")
    lines.append("Setup")
    lines.append("-" * 40)
    lines.append("1. Ensure microphone permissions are granted for your terminal/IDE.")
    lines.append("2. Use /voice to toggle voice input on or off.")
    lines.append("3. When voice is enabled, press and hold the listen hotkey")
    lines.append("   (default: Option+Space on macOS, Alt+Space on Linux/Windows)")
    lines.append("   to start capturing voice input.")
    lines.append("4. Release the hotkey to send the transcribed text as your prompt.")
    lines.append("")
    lines.append("Keyboard shortcuts can be customized in ~/.claude/keybindings.json")
    lines.append("under the \"voice\" action.")

    # Troubleshooting
    lines.append("")
    lines.append("Troubleshooting")
    lines.append("-" * 40)
    if not voice_available:
        lines.append("- Check system privacy settings: System Preferences >")
        lines.append("  Security & Privacy > Microphone, and enable your terminal.")
        lines.append("- Restart your terminal after granting microphone permissions.")
    else:
        lines.append("- If voice recognition is inaccurate, try speaking more slowly")
        lines.append("  and clearly, or adjust input volume in system settings.")
        lines.append("- Use /voice to disable and re-enable if the connection drops.")

    return {"type": "text", "value": "\n".join(lines)}
