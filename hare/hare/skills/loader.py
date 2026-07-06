"""
Skill directory loader.

Port of: src/skills/loadSkillsDir.ts
"""

from __future__ import annotations
import os
import json
from dataclasses import dataclass


@dataclass
class SkillDefinition:
    name: str
    description: str = ""
    when_to_use: str = ""
    source: str = "user"
    type: str = "prompt"
    content: str = ""
    path: str = ""
    enabled: bool = True


def load_skills_dir(skills_dir: str) -> list[SkillDefinition]:
    skills: list[SkillDefinition] = []
    if not os.path.isdir(skills_dir):
        return skills
    for entry in os.listdir(skills_dir):
        full = os.path.join(skills_dir, entry)
        if os.path.isfile(full) and entry.endswith(".md"):
            name = entry[:-3]
            try:
                with open(full, "r", encoding="utf-8") as f:
                    content = f.read()
                desc = content.split("\n")[0].strip("# ").strip() if content else name
                skills.append(
                    SkillDefinition(
                        name=name,
                        description=desc,
                        content=content,
                        path=full,
                    )
                )
            except OSError:
                pass
        elif os.path.isdir(full):
            manifest = os.path.join(full, "skill.json")
            if os.path.isfile(manifest):
                try:
                    with open(manifest, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    skills.append(
                        SkillDefinition(
                            name=data.get("name", entry),
                            description=data.get("description", ""),
                            when_to_use=data.get("whenToUse", ""),
                            content=data.get("content", ""),
                            path=full,
                        )
                    )
                except (OSError, json.JSONDecodeError):
                    pass
    return skills
