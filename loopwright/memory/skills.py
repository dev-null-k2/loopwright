"""SKILLS: procedural memory. Not what the agent knows, but how it does things.

A skill is a folder with one SKILL.md: a few lines of front matter and a body
of instructions. The trick is PROGRESSIVE DISCLOSURE, which keeps skills from
eating the context window:

  level 1  every skill's name and one-line description is always in the
           prompt (a few dozen tokens each), so the agent knows what exists
  level 2  a skill's full body is loaded only on a turn whose message matches
           one of its triggers

Ten skills cost ten lines until one is needed. Add your own by dropping a
folder into ~/.loopwright/skills/; no Python required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loopwright.config import PACKAGE_DIR


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    body: str = ""
    path: str = ""

    def matches(self, message: str) -> str | None:
        """The first trigger found in the message as a whole word or phrase."""
        lowered = message.lower()
        for trigger in self.triggers:
            if re.search(rf"(?<![a-z]){re.escape(trigger.lower())}(?![a-z])", lowered):
                return trigger
        return None


def parse_skill(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not match:
        return None
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
    if not meta.get("name") or not meta.get("description"):
        return None
    triggers = [t.strip() for t in meta.get("triggers", "").split(",") if t.strip()]
    return Skill(meta["name"], meta["description"], triggers, match.group(2).strip(), str(path))


def load_skills(home: Path | None = None) -> list[Skill]:
    """Bundled skills first, then the user's own; a user skill with the same
    name replaces the bundled one."""
    found: dict[str, Skill] = {}
    roots = [PACKAGE_DIR / "skills"] + ([Path(home) / "skills"] if home else [])
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            skill = parse_skill(path)
            if skill:
                found[skill.name] = skill
    return list(found.values())


def match_skills(skills: list[Skill], message: str) -> list[tuple[Skill, str]]:
    out = []
    for skill in skills:
        trigger = skill.matches(message)
        if trigger:
            out.append((skill, trigger))
    return out


def save_skill(home: Path, name: str, description: str, triggers: list[str], body: str) -> Path:
    """Write a user skill to <home>/skills/<name>/SKILL.md. The name is reduced
    to a safe slug first, so it can never point outside the skills folder."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    if not slug or not description.strip() or not body.strip():
        raise ValueError("a skill needs a name, a description and instructions")
    path = Path(home) / "skills" / slug / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    one_line = lambda text: " ".join(text.split())      # noqa: E731  (front matter is line-based)
    path.write_text(f"---\nname: {slug}\ndescription: {one_line(description)}\n"
                    f"triggers: {', '.join(one_line(t) for t in triggers if t.strip())}\n---\n{body.strip()}\n",
                    encoding="utf-8")
    return path


def delete_skill(home: Path, name: str) -> bool:
    """Remove a user skill. Bundled skills cannot be deleted, only overridden."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    folder = Path(home) / "skills" / slug
    if not (folder / "SKILL.md").is_file():
        return False
    (folder / "SKILL.md").unlink()
    folder.rmdir()
    return True
