"""Rename ``element_res_shred`` → ``element_pen`` in JSON game data.

The passive attacker-side stat (constitutions/equipment/formations/linh_can/
skills/items) is being renamed to make room for ``shred`` to mean a
turn-duration debuff (DebuffHoaXuyenThau et al.) instead. The math is
unchanged — only the key name moves.

Run from repo root:
    python scripts/rename_element_pen.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

TARGETS = [
    *Path("src/data/constitutions").glob("*.json"),
    *Path("src/data/equipment/uniques").glob("*.json"),
    *Path("src/data/skills/player").glob("*.json"),
    Path("src/data/formations.json"),
    Path("src/data/items/unique_gems.json"),
]

OLD_KEY = "element_res_shred"
NEW_KEY = "element_pen"


def _walk(node):
    if isinstance(node, dict):
        # Rename in-place but preserve insertion order
        keys = list(node.keys())
        for k in keys:
            v = node.pop(k)
            new_k = NEW_KEY if k == OLD_KEY else k
            node[new_k] = _walk(v)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            node[i] = _walk(item)
    return node


def main() -> None:
    total = 0
    for path in TARGETS:
        if not path.exists():
            continue
        text_before = path.read_text(encoding="utf-8")
        if OLD_KEY not in text_before:
            continue
        data = json.loads(text_before)
        _walk(data)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        total += text_before.count(f'"{OLD_KEY}"')
        print(f"  {path}: renamed")
    print(f"Done. {total} occurrences renamed.")


if __name__ == "__main__":
    main()
