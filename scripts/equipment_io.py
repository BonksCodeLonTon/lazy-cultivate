"""Export / import equipment JSON ↔ XLSX for easy bulk editing.

Round-trips three datasets between JSON (under ``src/data/equipment``) and a
single workbook at ``docs/equipment.xlsx``:

  * ``bases.json``         → sheet ``Bases``
  * ``affixes.json``       → sheet ``Affixes``
  * ``uniques/<elem>.json`` → sheets ``Uniques`` + ``UniqueRolled`` + ``UniquePassives``

Usage from the repo root::

    python scripts/equipment_io.py export   # JSON → docs/equipment.xlsx
    python scripts/equipment_io.py import   # docs/equipment.xlsx → JSON

Pass ``--xlsx <path>`` to use a different workbook location.

Edit-friendly conventions
-------------------------
* Bases with multiple implicit stats become multiple rows (one per stat),
  sharing the base ``key`` / ``vi`` / ``slot`` columns.
* ``tags`` and ``slots`` are stored as comma-separated strings.
* ``two_handed`` and ``is_pct`` are written as ``TRUE`` / ``FALSE``.
* Empty cells are treated as "field absent" (e.g. blank ``two_handed`` →
  the base is one-handed and the key is omitted from JSON).
* The grade columns ``g1_min`` … ``g9_max`` follow the existing 9-grade
  convention. Missing grades may be blank but each grade must either have
  both ``min`` and ``max`` filled or both empty.
* Dict / list passive values (e.g. ``element_pen: {"am": 0.05}``) are
  serialized as compact JSON in the cell so they round-trip cleanly.
* Excel does not distinguish ``6`` from ``6.0``; integer-valued numbers
  may import back as ``int`` in JSON. Values remain numerically identical.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError as exc:  # pragma: no cover
    sys.exit("openpyxl is required. Install with: pip install openpyxl")


REPO_ROOT = Path(__file__).resolve().parent.parent
EQUIP_DIR = REPO_ROOT / "src" / "data" / "equipment"
BASES_JSON = EQUIP_DIR / "bases.json"
AFFIXES_JSON = EQUIP_DIR / "affixes.json"
UNIQUES_DIR = EQUIP_DIR / "uniques"
DEFAULT_XLSX = REPO_ROOT / "docs" / "equipment.xlsx"

GRADES = list(range(1, 10))  # 1..9
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="4F6BED")
HEADER_ALIGN = Alignment(horizontal="center", vertical="center")


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _csv_join(values: Iterable[str] | None) -> str:
    if not values:
        return ""
    return ",".join(values)


def _csv_split(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [piece.strip() for piece in text.split(",") if piece.strip()]


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "none", "null"}:
        return None
    if text in {"true", "1", "yes", "y", "x", "✓"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"Cannot interpret {value!r} as boolean")


def _to_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        # collapse 3.0 → 3 for cleaner JSON
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    text = str(value).strip()
    if not text:
        return None
    if "." in text:
        return float(text)
    return int(text)


def _to_cell(value: Any) -> Any:
    """Serialize dict/list values as compact JSON so they fit in a cell."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _from_cell(value: Any) -> Any:
    """Inverse of _to_cell — parse JSON-encoded objects/arrays back out."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return _to_number(text) if text.lstrip("-").replace(".", "", 1).isdigit() else text


def _style_header(ws) -> None:
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGN
    ws.freeze_panes = "A2"


def _autosize(ws, min_width: int = 10, max_width: int = 40) -> None:
    for column_cells in ws.columns:
        letter = get_column_letter(column_cells[0].column)
        longest = max((len(str(c.value)) for c in column_cells if c.value is not None), default=0)
        ws.column_dimensions[letter].width = min(max(longest + 2, min_width), max_width)


def _grade_columns() -> list[str]:
    cols: list[str] = []
    for g in GRADES:
        cols.extend([f"g{g}_min", f"g{g}_max"])
    return cols


def _read_grade_pairs(row: dict[str, Any]) -> list[list[float | int]]:
    pairs: list[list[float | int]] = []
    for g in GRADES:
        lo = _to_number(row.get(f"g{g}_min"))
        hi = _to_number(row.get(f"g{g}_max"))
        if lo is None and hi is None:
            continue
        if lo is None or hi is None:
            raise ValueError(f"Grade {g} has only one of min/max filled (row: {row.get('key')})")
        pairs.append([lo, hi])
    return pairs


# ──────────────────────────────────────────────────────────────────────────
# Export: JSON → XLSX
# ──────────────────────────────────────────────────────────────────────────

def _write_bases_sheet(ws, bases: list[dict[str, Any]]) -> None:
    headers = ["key", "vi", "slot", "tags", "two_handed", "stat", *_grade_columns()]
    ws.append(headers)
    for base in bases:
        common = {
            "key": base["key"],
            "vi": base.get("vi", ""),
            "slot": base.get("slot", ""),
            "tags": _csv_join(base.get("tags")),
            "two_handed": "TRUE" if base.get("two_handed") else "",
        }
        implicits: dict[str, list[list[float]]] = base.get("implicit_by_grade", {}) or {}
        if not implicits:
            ws.append([common[k] for k in ("key", "vi", "slot", "tags", "two_handed")] + [""] + [""] * (len(GRADES) * 2))
            continue
        for stat, grade_pairs in implicits.items():
            row = [common["key"], common["vi"], common["slot"], common["tags"], common["two_handed"], stat]
            for idx in range(len(GRADES)):
                if idx < len(grade_pairs):
                    row.extend(grade_pairs[idx])
                else:
                    row.extend(["", ""])
            ws.append(row)
    _style_header(ws)
    _autosize(ws)


def _write_affixes_sheet(ws, affixes: list[dict[str, Any]]) -> None:
    headers = ["key", "vi", "type", "stat", "is_pct", "slots", *_grade_columns()]
    ws.append(headers)
    for affix in affixes:
        row = [
            affix["key"],
            affix.get("vi", ""),
            affix.get("type", ""),
            affix.get("stat", ""),
            "TRUE" if affix.get("is_pct") else "FALSE",
            _csv_join(affix.get("slots")),
        ]
        grade_pairs = affix.get("by_grade", []) or []
        for idx in range(len(GRADES)):
            if idx < len(grade_pairs):
                row.extend(grade_pairs[idx])
            else:
                row.extend(["", ""])
        ws.append(row)
    _style_header(ws)
    _autosize(ws)


def _write_uniques_sheets(wb, unique_files: dict[str, list[dict[str, Any]]]) -> None:
    main = wb.create_sheet("Uniques")
    rolled = wb.create_sheet("UniqueRolled")
    passives = wb.create_sheet("UniquePassives")

    main.append(["file", "key", "vi", "base", "slot", "realm", "grade", "description_vi"])
    rolled.append(["unique_key", "stat", "min", "max"])
    passives.append(["unique_key", "stat", "value"])

    for filename, items in sorted(unique_files.items()):
        for item in items:
            main.append([
                filename,
                item["key"],
                item.get("vi", ""),
                item.get("base", ""),
                item.get("slot", ""),
                item.get("realm"),
                item.get("grade"),
                item.get("description_vi", ""),
            ])
            for stat, pair in (item.get("rolled_stats") or {}).items():
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError(
                        f"Unique {item['key']} rolled_stats[{stat}] must be [min, max], got {pair!r}"
                    )
                rolled.append([item["key"], stat, pair[0], pair[1]])
            for stat, value in (item.get("passive_bonus") or {}).items():
                passives.append([item["key"], stat, _to_cell(value)])

    for ws in (main, rolled, passives):
        _style_header(ws)
        _autosize(ws)


def export_to_xlsx(xlsx_path: Path) -> None:
    bases = _load_json(BASES_JSON)
    affixes = _load_json(AFFIXES_JSON)
    unique_files = {f.stem: _load_json(f) for f in sorted(UNIQUES_DIR.glob("*.json"))}

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _write_bases_sheet(wb.create_sheet("Bases"), bases)
    _write_affixes_sheet(wb.create_sheet("Affixes"), affixes)
    _write_uniques_sheets(wb, unique_files)

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)

    total_uniques = sum(len(v) for v in unique_files.values())
    print(f"Exported {len(bases)} bases, {len(affixes)} affixes, {total_uniques} uniques -> {xlsx_path}")


# ──────────────────────────────────────────────────────────────────────────
# Import: XLSX → JSON
# ──────────────────────────────────────────────────────────────────────────

def _sheet_to_dicts(ws) -> list[dict[str, Any]]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    out: list[dict[str, Any]] = []
    for raw in rows[1:]:
        if all(cell is None or str(cell).strip() == "" for cell in raw):
            continue
        out.append({headers[i]: raw[i] for i in range(len(headers))})
    return out


def _import_bases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        if key not in grouped:
            grouped[key] = {
                "key": key,
                "vi": (row.get("vi") or "").strip(),
                "slot": (row.get("slot") or "").strip(),
                "tags": _csv_split(row.get("tags")),
                "two_handed": _to_bool(row.get("two_handed")),
                "implicit_by_grade": {},
            }
            order.append(key)
        stat = (row.get("stat") or "").strip()
        if not stat:
            continue
        pairs = _read_grade_pairs(row)
        if pairs:
            grouped[key]["implicit_by_grade"][stat] = pairs

    out: list[dict[str, Any]] = []
    for key in order:
        entry = grouped[key]
        clean: dict[str, Any] = {
            "key": entry["key"],
            "vi": entry["vi"],
            "slot": entry["slot"],
            "tags": entry["tags"],
        }
        if entry["two_handed"]:
            clean["two_handed"] = True
        clean["implicit_by_grade"] = entry["implicit_by_grade"]
        out.append(clean)
    return out


def _import_affixes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        out.append({
            "key": key,
            "vi": (row.get("vi") or "").strip(),
            "type": (row.get("type") or "").strip(),
            "stat": (row.get("stat") or "").strip(),
            "is_pct": bool(_to_bool(row.get("is_pct"))),
            "slots": _csv_split(row.get("slots")),
            "by_grade": _read_grade_pairs(row),
        })
    return out


def _import_uniques(
    main_rows: list[dict[str, Any]],
    rolled_rows: list[dict[str, Any]],
    passive_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    rolled_by_key: dict[str, dict[str, list[float | int]]] = defaultdict(dict)
    for row in rolled_rows:
        key = (row.get("unique_key") or "").strip()
        stat = (row.get("stat") or "").strip()
        if not key or not stat:
            continue
        lo = _to_number(row.get("min"))
        hi = _to_number(row.get("max"))
        if lo is None or hi is None:
            raise ValueError(f"UniqueRolled[{key}/{stat}] missing min or max")
        rolled_by_key[key][stat] = [lo, hi]

    passive_by_key: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in passive_rows:
        key = (row.get("unique_key") or "").strip()
        stat = (row.get("stat") or "").strip()
        if not key or not stat:
            continue
        raw = row.get("value")
        value = _from_cell(raw)
        if value is None:
            raise ValueError(f"UniquePassives[{key}/{stat}] missing value")
        passive_by_key[key][stat] = value

    files: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_keys: set[str] = set()
    for row in main_rows:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        if key in seen_keys:
            raise ValueError(f"Duplicate unique key: {key}")
        seen_keys.add(key)
        filename = (row.get("file") or "general").strip()
        item: dict[str, Any] = {
            "key": key,
            "vi": (row.get("vi") or "").strip(),
            "base": (row.get("base") or "").strip(),
            "slot": (row.get("slot") or "").strip(),
            "realm": _to_number(row.get("realm")),
            "grade": _to_number(row.get("grade")),
            "rolled_stats": rolled_by_key.get(key, {}),
        }
        passives = passive_by_key.get(key, {})
        if passives:
            item["passive_bonus"] = passives
        desc = (row.get("description_vi") or "").strip()
        if desc:
            item["description_vi"] = desc
        files[filename].append(item)
    return files


def import_from_xlsx(xlsx_path: Path) -> None:
    if not xlsx_path.exists():
        sys.exit(f"Workbook not found: {xlsx_path}")

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    required = {"Bases", "Affixes", "Uniques", "UniqueRolled", "UniquePassives"}
    missing = required - set(wb.sheetnames)
    if missing:
        sys.exit(f"Workbook is missing required sheets: {sorted(missing)}")

    bases = _import_bases(_sheet_to_dicts(wb["Bases"]))
    affixes = _import_affixes(_sheet_to_dicts(wb["Affixes"]))
    unique_files = _import_uniques(
        _sheet_to_dicts(wb["Uniques"]),
        _sheet_to_dicts(wb["UniqueRolled"]),
        _sheet_to_dicts(wb["UniquePassives"]),
    )

    _dump_json(BASES_JSON, bases)
    _dump_json(AFFIXES_JSON, affixes)

    UNIQUES_DIR.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in UNIQUES_DIR.glob("*.json")}
    written = set()
    for filename, items in unique_files.items():
        path = UNIQUES_DIR / f"{filename}.json"
        _dump_json(path, items)
        written.add(filename)
    stale = existing - written
    if stale:
        print(f"Warning: existing unique files not present in workbook (left untouched): {sorted(stale)}")

    total_uniques = sum(len(v) for v in unique_files.values())
    print(f"Imported {len(bases)} bases, {len(affixes)} affixes, {total_uniques} uniques from {xlsx_path}")


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("export", "import"), help="export JSON→XLSX or import XLSX→JSON")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX, help=f"workbook path (default: {DEFAULT_XLSX.relative_to(REPO_ROOT)})")
    args = parser.parse_args(argv)

    if args.mode == "export":
        export_to_xlsx(args.xlsx)
    else:
        import_from_xlsx(args.xlsx)


if __name__ == "__main__":
    main()
