"""Migrate cost_merit / cost_stones into a unified cost {merit, stones} object.

Run from project root:
    python scripts/migrate_const_cost.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

CONST_DIR = Path("src/data/constitutions")


def migrate_entry(entry: dict) -> dict:
    """Return a new dict with merged ``cost`` field; preserve original key order."""
    if not isinstance(entry, dict):
        return entry

    has_merit = "cost_merit" in entry
    has_stones = "cost_stones" in entry
    if not (has_merit or has_stones):
        return entry

    merit = int(entry.get("cost_merit", 0))
    stones = int(entry.get("cost_stones", 0))
    cost_block = {"merit": merit, "stones": stones}

    new_entry: dict = {}
    inserted = False
    for k, v in entry.items():
        if k in ("cost_merit", "cost_stones"):
            if not inserted:
                new_entry["cost"] = cost_block
                inserted = True
            continue
        new_entry[k] = v
    if not inserted:
        new_entry["cost"] = cost_block
    return new_entry


def migrate_file(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print(f"skip (not a list): {path}")
        return 0

    changed = 0
    new_list = []
    for entry in raw:
        migrated = migrate_entry(entry)
        if migrated is not entry:
            changed += 1
        new_list.append(migrated)

    if changed:
        path.write_text(
            json.dumps(new_list, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> None:
    if not CONST_DIR.exists():
        raise SystemExit(f"missing dir: {CONST_DIR}")

    total = 0
    for json_path in sorted(CONST_DIR.glob("*.json")):
        n = migrate_file(json_path)
        total += n
        print(f"  {json_path.name}: {n} entries migrated")
    print(f"Done. {total} entries migrated.")


if __name__ == "__main__":
    main()
