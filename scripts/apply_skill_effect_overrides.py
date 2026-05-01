"""Bulk-apply ``effect_overrides`` to every skill that carries effects.

Strategy: derive the override magnitude from the skill's ``realm`` field so
late-game skills stamp meaningfully stronger versions of the same effect
than R1 baseline skills. The scale curve:

    realm 1-2  → no override (meta defaults are calibrated for early game)
    realm 3-9  → magnitude scales 1.10×..1.70× (10% per realm above 2)
    realm 7+   → effect duration bumped by +1 turn

What gets touched:
  • Every JSON in src/data/skills/player/  and src/data/skills/enemy/realm_*.json
  • Skips files that already ship hand-tuned overrides
    (the_chat.json, linh_can_apex.json, linh_can_<element>.json T3 entries).
  • Skips individual skills that already have an ``effect_overrides`` field
    (so re-running the script is idempotent).

Run:  python -m scripts.apply_skill_effect_overrides
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from src.game.engine.effects import EFFECTS, default_duration


SKILLS_ROOT = Path("src/data/skills")
# Files we already curated by hand — leave their ``effect_overrides`` alone.
HAND_TUNED_FILES = {
    "the_chat.json",
    "linh_can_apex.json",
    "linh_can_kim.json", "linh_can_moc.json", "linh_can_thuy.json",
    "linh_can_hoa.json", "linh_can_tho.json", "linh_can_phong.json",
    "linh_can_loi.json", "linh_can_quang.json", "linh_can_am.json",
}

# Effect keys that aren't real EFFECTS entries — they're handled inline by
# the casting engine (heals, mana bursts, soul drain hooks, etc.).
SKIPPED_EFFECT_KEYS = {
    "HpRegen", "MpRegen",
    "ConsumeBurnBurst", "ConsumeManaBurst", "ConsumeShieldBurst",
    "ApplySoulDrain", "ApplyStatSteal",
}


def override_for_effect(effect_key: str, realm: int) -> dict | None:
    """Return a per-effect override dict scaled to the skill's realm tier.

    Returns ``None`` when no override should be applied (low-tier skills
    or unregistered effect keys).
    """
    if realm < 3 or effect_key in SKIPPED_EFFECT_KEYS:
        return None
    meta = EFFECTS.get(effect_key)
    if meta is None:
        return None

    scale = 1.0 + 0.10 * (realm - 2)   # R3 = 1.10×, R9 = 1.70×
    out: dict = {}

    if meta.dot_pct > 0:
        out["dot_pct"] = round(meta.dot_pct * scale, 4)

    if meta.stat_bonus:
        scaled: dict = {}
        for stat, val in meta.stat_bonus.items():
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                # Round magnitudes to 2 decimals (or whole numbers for ratings
                # like crit_rating which are integer counts).
                if abs(val) >= 10 and float(val).is_integer():
                    scaled[stat] = int(round(val * scale))
                else:
                    scaled[stat] = round(val * scale, 3)
        if scaled:
            out["stat_bonus"] = scaled

    # Late-realm skills stretch effect duration by 1 turn so endgame CC and
    # debuffs aren't trivially shrugged off in 1-2 turn cycles.
    if realm >= 7:
        out["duration"] = default_duration(effect_key) + 1

    return out or None


def patch_skill(skill: dict) -> bool:
    """Mutate a single skill dict to add ``effect_overrides``. Returns True
    if anything changed.
    """
    if "effect_overrides" in skill:
        return False
    effects = skill.get("effects") or []
    if not effects:
        return False
    realm = int(skill.get("realm", 1))
    overrides: dict = {}
    for effect_key in effects:
        ov = override_for_effect(effect_key, realm)
        if ov:
            overrides[effect_key] = ov
    if not overrides:
        return False
    skill["effect_overrides"] = overrides
    return True


def main() -> None:
    touched_skills = 0
    touched_files = 0
    for path in sorted(SKILLS_ROOT.rglob("*.json")):
        if path.name in HAND_TUNED_FILES:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        file_changed = False
        for skill in data:
            if patch_skill(skill):
                touched_skills += 1
                file_changed = True
        if file_changed:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            touched_files += 1
            print(f"  patched {path.relative_to(Path('.'))}")
    print(f"\nDone — {touched_skills} skills patched across {touched_files} files.")


if __name__ == "__main__":
    main()
