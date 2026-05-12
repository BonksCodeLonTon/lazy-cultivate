"""One-shot migration: flat per-element/per-kind bonus keys → nested grouped keys.

Walks every JSON value (dicts of arbitrary depth, lists), and rewrites
flat keys to the new nested shape introduced alongside Combatant's
``dot_dmg_bonus_by_kind`` / ``crit_amp_vs`` / etc.

Run with: ``python scripts/migrate_grouped_bonuses.py path1 path2 ...``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Flat → (grouped_key, inner_key) mapping for keys whose value is a scalar.
_KIND_SUFFIX_RULES: dict[str, tuple[str, str]] = {
    # Group 1 — DoT-kind damage amps
    "burn_dmg_bonus":   ("dot_dmg_bonus_by_kind",   "burn"),
    "bleed_dmg_bonus":  ("dot_dmg_bonus_by_kind",   "bleed"),
    "poison_dmg_bonus": ("dot_dmg_bonus_by_kind",   "poison"),
    # Group 2 — DoT stack-cap / per-stack-pct bonuses
    "burn_stack_cap_bonus":     ("dot_stack_cap_bonus",     "burn"),
    "bleed_stack_cap_bonus":    ("dot_stack_cap_bonus",     "bleed"),
    "poison_stack_cap_bonus":   ("dot_stack_cap_bonus",     "poison"),
    "burn_per_stack_pct_bonus":   ("dot_per_stack_pct_bonus", "burn"),
    "bleed_per_stack_pct_bonus":  ("dot_per_stack_pct_bonus", "bleed"),
    "poison_per_stack_pct_bonus": ("dot_per_stack_pct_bonus", "poison"),
}

# Group 3 — crit-vs-debuff. Outer grouped key is the same for all five;
# inner shape is {state: {rating: int, dmg: int}}.
_CRIT_AMP_RULES: dict[str, tuple[str, str]] = {
    "crit_rating_vs_bleed":   ("bleed",   "rating"),
    "crit_dmg_vs_bleed":      ("bleed",   "dmg"),
    "crit_rating_vs_marked":  ("marked",  "rating"),
    "crit_dmg_vs_marked":     ("marked",  "dmg"),
    "crit_rating_vs_drained": ("drained", "rating"),
}


def migrate_dict(d: dict) -> bool:
    """Rewrite flat keys in this dict in place. Returns True if anything changed."""
    changed = False
    # Pull all relevant flat keys first so we can mutate the dict safely.
    kind_hits: list[tuple[str, str, str, object]] = []   # (flat, grouped, inner, val)
    crit_hits: list[tuple[str, str, str, object]] = []   # (flat, state, sub, val)
    for k in list(d.keys()):
        if k in _KIND_SUFFIX_RULES:
            grouped, inner = _KIND_SUFFIX_RULES[k]
            kind_hits.append((k, grouped, inner, d[k]))
        elif k in _CRIT_AMP_RULES:
            state, sub = _CRIT_AMP_RULES[k]
            crit_hits.append((k, state, sub, d[k]))

    for flat, grouped, inner, val in kind_hits:
        bucket = d.setdefault(grouped, {})
        if not isinstance(bucket, dict):
            continue
        bucket[inner] = bucket.get(inner, 0) + val if isinstance(val, (int, float)) else val
        d.pop(flat, None)
        changed = True

    for flat, state, sub, val in crit_hits:
        bucket = d.setdefault("crit_amp_vs", {})
        if not isinstance(bucket, dict):
            continue
        state_bucket = bucket.setdefault(state, {})
        state_bucket[sub] = state_bucket.get(sub, 0) + val if isinstance(val, (int, float)) else val
        d.pop(flat, None)
        changed = True

    return changed


def walk(node: object) -> bool:
    """Recurse through dicts and lists, migrating any inner dicts."""
    changed = False
    if isinstance(node, dict):
        if migrate_dict(node):
            changed = True
        for v in node.values():
            if walk(v):
                changed = True
    elif isinstance(node, list):
        for v in node:
            if walk(v):
                changed = True
    return changed


def migrate_file(path: Path) -> bool:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if walk(data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    return False


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: migrate_grouped_bonuses.py path1.json [path2.json ...]")
        sys.exit(1)
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"skip (missing): {p}")
            continue
        changed = migrate_file(p)
        print(f"{'wrote' if changed else 'noop'}: {p}")


if __name__ == "__main__":
    main()
