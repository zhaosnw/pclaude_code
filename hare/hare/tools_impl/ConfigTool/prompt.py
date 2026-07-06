"""Port of: src/tools/ConfigTool/prompt.ts"""

from __future__ import annotations

from typing import Optional

DESCRIPTION = "Get or set Hare configuration settings."


def generate_prompt(
    *,
    supported_settings: Optional[dict] = None,
    model_options: Optional[list[dict]] = None,
) -> str:
    """Generate the prompt documentation from the registry.

    When ``supported_settings`` or ``model_options`` are not supplied the
    function falls back to a sensible static default so the prompt can be
    assembled without the full runtime config subsystem.
    """
    global_settings: list[str] = []
    project_settings: list[str] = []

    if supported_settings:
        for key, config in supported_settings.items():
            if key == "model":
                continue

            options = config.get("options")
            line = f"- {key}"

            if options:
                line += ": " + ", ".join(f'"{o}"' for o in options)
            elif config.get("type") == "boolean":
                line += ": true/false"

            line += f" - {config['description']}"

            if config.get("source") == "global":
                global_settings.append(line)
            else:
                project_settings.append(line)

    model_section = _generate_model_section(model_options)

    return f"""Get or set Hare configuration settings.

  View or change Hare settings. Use when the user requests configuration changes, asks about current settings, or when adjusting a setting would benefit them.


## Usage
- **Get current value:** Omit the "value" parameter
- **Set new value:** Include the "value" parameter

## Configurable settings list
The following settings are available for you to change:

### Global Settings (stored in ~/.hare.json)
{chr(10).join(global_settings) if global_settings else "(none registered)"}

### Project Settings (stored in settings.json)
{chr(10).join(project_settings) if project_settings else "(none registered)"}

{model_section}
## Examples
- Get theme: {{ "setting": "theme" }}
- Set dark theme: {{ "setting": "theme", "value": "dark" }}
- Enable vim mode: {{ "setting": "editorMode", "value": "vim" }}
- Enable verbose: {{ "setting": "verbose", "value": true }}
- Change model: {{ "setting": "model", "value": "opus" }}
- Change permission mode: {{ "setting": "permissions.defaultMode", "value": "plan" }}
"""


def _generate_model_section(model_options: Optional[list[dict]] = None) -> str:
    if model_options:
        try:
            lines: list[str] = []
            for o in model_options:
                value = (
                    'null/"default"' if o.get("value") is None else f'"{o["value"]}"'
                )
                desc = o.get("descriptionForModel") or o.get("description", "")
                lines.append(f"  - {value}: {desc}")
            return (
                "## Model\n- model - Override the default model. Available options:\n"
                + "\n".join(lines)
            )
        except Exception:
            pass
    return "## Model\n- model - Override the default model (sonnet, opus, haiku, best, or full model ID)"
