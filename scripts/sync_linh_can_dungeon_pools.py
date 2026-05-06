"""Sync each linh_can dungeon's ``enemy_pool`` with its element's full
realm-1..10 lineup. Reads ``src/data/enemies/linh_can/<elem>.json`` and
writes the keys (sorted by realm_level) into the matching dungeon entry
in ``src/data/dungeons.json``.

Run from repo root:
    python scripts/sync_linh_can_dungeon_pools.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ENEMY_DIR = Path("src/data/enemies/linh_can")
DUNGEONS_PATH = Path("src/data/dungeons.json")


def main() -> None:
    dungeons = json.loads(DUNGEONS_PATH.read_text(encoding="utf-8"))

    # Build {element: [enemy_keys ordered by realm]}
    pools: dict[str, list[str]] = {}
    for path in sorted(ENEMY_DIR.glob("*.json")):
        elem = path.stem
        enemies = json.loads(path.read_text(encoding="utf-8"))
        enemies.sort(key=lambda e: e.get("realm_level", 0))
        pools[elem] = [e["key"] for e in enemies]

    updated = 0
    for d in dungeons:
        if d.get("dungeon_type") != "linh_can":
            continue
        elem = d.get("linh_can_element")
        new_pool = pools.get(elem)
        if not new_pool:
            continue
        if d.get("enemy_pool") == new_pool:
            continue
        d["enemy_pool"] = new_pool
        updated += 1
        print(f"  {d['key']}: {len(new_pool)} enemies")

    DUNGEONS_PATH.write_text(
        json.dumps(dungeons, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Done. {updated} dungeons updated.")


if __name__ == "__main__":
    main()
