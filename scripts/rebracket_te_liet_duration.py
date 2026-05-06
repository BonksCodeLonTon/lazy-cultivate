"""Apply the DebuffTeLiet duration bracket: R1-4 = 1 turn, R5-9 = 2 turns.

Walks every skill JSON under ``src/data/skills`` and writes/updates the
``effect_overrides.DebuffTeLiet.duration`` field for any skill that lists
``DebuffTeLiet`` in its ``effects`` array.
"""
from __future__ import annotations

import json
from pathlib import Path

SKILLS_DIR = Path("src/data/skills")
EFFECT_KEY = "DebuffTeLiet"


def target_duration(realm: int) -> int:
    """R1-4 = 1 turn (low realm), R5-9 = 2 turns (high realm cap)."""
    return 1 if realm <= 4 else 2


def main() -> None:
    changes: list[tuple[Path, str, int, int]] = []  # (path, key, old, new)

    for path in sorted(SKILLS_DIR.rglob("*.json")):
        skills = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(skills, list):
            continue
        dirty = False

        for skill in skills:
            if EFFECT_KEY not in skill.get("effects", []):
                continue

            realm = int(skill.get("realm", 1))
            target = target_duration(realm)

            overrides = skill.setdefault("effect_overrides", {})
            entry = overrides.setdefault(EFFECT_KEY, {})
            old = entry.get("duration", "(default 2)")

            if entry.get("duration") == target:
                continue

            entry["duration"] = target
            dirty = True
            changes.append((path, skill["key"], old, target))

        if dirty:
            path.write_text(
                json.dumps(skills, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    print(f"Updated {len(changes)} skill entries:")
    for path, key, old, new in changes:
        rel = str(path.relative_to(SKILLS_DIR))
        print(f"  {rel:<35} | {key:<35} | {str(old):>12} -> {new}")


if __name__ == "__main__":
    main()
