"""Classification guard for every stat key in the game data.

``stat_bonus`` / ``stat_bonuses`` blocks carry two very different things:
real aggregatable stats, and config values consumed by engine hooks. The
engine separates them with three mechanisms:

  * ``_``-prefixed keys — THE convention for config keys (popped by
    ``get_combat_modifiers``, hidden by ``displayable_stat_bonus``);
  * scaling-rule placeholders — popped by ``_apply_scaling_rules``;
  * ``_CONFIG_ONLY_STAT_KEYS`` — the FROZEN legacy list for config keys
    named before the underscore convention existed.

Everything else must be a known real stat. This guard walks every JSON under
``src/data`` and fails loudly on any key that fits none of the lanes — the
two silent failure modes it converts into CI failures:

  1. a new config key without ``_`` prefix and not in the legacy list would
     leak into combat modifiers as a phantom stat;
  2. a real stat accidentally added to the legacy list would be silently
     dropped from combat.

When this test fails on a NEW key, decide which it is:
  * real stat → add it to ``KNOWN_REAL_STAT_KEYS`` below (consciously);
  * config    → rename it with a ``_`` prefix (preferred; zero registration).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.game.engine import effects as fx

_DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "data"
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"

# Open-ended key families that are real by construction.
_OPEN_REAL_PREFIXES = ("effect_resist:",)   # per-effect immunity entries

# The real-stat vocabulary as it exists today — every non-config, non-
# placeholder key found across src/data. A failure here after adding content
# is the "is this a stat or config?" checkpoint working as intended.
KNOWN_REAL_STAT_KEYS: frozenset[str] = frozenset({
    "accuracy_rating", "accuracy_rating_pct", "accuracy_rating_per_spd_diff",
    "am_hit_to_shield_pct", "am_max_resist_bonus", "atk_pct",
    "barrier_on_cleanse", "bleed_on_hit_pct", "blind_on_hit_pct",
    "bon_loi_consume_amp", "bonus_base_dmg_per_self_def_pct", "bonus_base_dmg_per_self_hp_pct",
    "bonus_base_dmg_per_self_shield_pct", "bonus_dmg_vs_burn", "burn_dmg_bonus",
    "burn_on_hit_pct", "cleanse_heal_pct", "cleanse_on_turn_pct",
    "cleanse_retaliate_dmg_pct", "cooldown_reduce", "crit_dmg_rating",
    "crit_rating", "crit_rating_per_spd_diff", "crit_res_rating",
    "cultivation_speed_bonus", "damage_bonus_from_evasion_pct", "damage_bonus_from_hp_pct",
    "damage_bonus_from_mp_pct", "damage_bonus_from_shield_pct", "damage_convert_am",
    "damage_convert_hoa", "damage_convert_moc", "damage_from_heal_pct",
    "debuff_apply_bonus", "debuff_immune_pct", "debuff_transfer_on_turn_pct",
    "def_applies_to_elemental_pct", "def_bonus", "def_pct",
    "dmg_bonus_am", "dmg_bonus_hoa", "dmg_bonus_kim",
    "dmg_bonus_loi", "dmg_bonus_moc", "dmg_bonus_phong",
    "dmg_bonus_phong_per_spd_diff", "dmg_bonus_quang", "dmg_bonus_tho",
    "dmg_bonus_thuy", "dmg_bonus_thuy_if_spd_higher", "dmg_taken_bonus_loi",
    "dmg_taken_bonus_phong", "dmg_taken_bonus_tho", "dmg_taken_mp_gain_pct",
    "dmg_to_mp_pct", "dot_can_crit", "dot_dmg_bonus",
    "dot_dmg_bonus_by_kind", "dot_leech_pct", "dot_per_stack_pct_bonus",
    "dot_taken_bonus", "element_dmg_bonus", "endure_cooldown",
    "endure_threshold_pct", "evasion_rating", "evasion_rating_pct",
    "evasion_rating_per_spd_diff", "final_dmg_bonus", "final_dmg_reduce",
    "final_dmg_taken_bonus", "fortify_per_turn_pct", "fortify_post_hit_dr_pct",
    "fortify_stack_cap", "freeze_on_skill_chance", "hai_thi_slow_chance",
    "hai_thi_slow_magnitude", "heal_can_crit", "heal_pct",
    "heal_taken_bonus", "heal_taken_reduce", "hoa_max_resist_bonus",
    "hp_max_pct", "hp_pct", "hp_regen_pct",
    "life_steal_pct", "loi_shred_on_hit_pct", "loot_luck_bonus",
    "magic_reflect_pct", "mark_on_hit_pct", "matk_pct",
    "max_dmg_per_hit_pct_hp_max", "moc_max_resist_bonus", "mp_leech_pct",
    "mp_pct", "mp_regen_pct", "multi_strike_pct",
    "overheal_to_shield_pct", "paralysis_on_crit", "phoenix_revive_buff_pct",
    "phoenix_revive_pct", "phong_shred_on_hit_pct", "poison_immunity",
    "poison_on_hit_pct", "qm_strip_vs_blind_chance", "reflect_pct",
    "res_all", "res_am", "res_hoa",
    "res_kim", "res_loi", "res_moc",
    "res_phong", "res_tho", "res_thuy",
    "revive_hp_pct", "shield_max_flat", "shield_max_pct",
    "shield_max_per_spd", "shield_regen_pct", "shock_on_hit_pct",
    "slow_on_hit_pct", "solar_aura_pct", "soul_drain_on_hit_pct",
    "spd_bonus", "spd_pct", "spd_pct_if_spd_lower",
    "stat_steal_on_hit_pct", "stun_on_hit_pct",
    # Thái Dương build-time knobs — consumed from the CONSTITUTION-bonuses
    # lane (``character_stats.py`` reads them via ``bonuses.get``), which
    # never passes through get_combat_modifiers; mechanically real-stat lane.
    "td_hoa_dmg_cap", "td_hoa_per_1k_hp_per_realm",
    "te_liet_on_hit_pct", "thorn_pct",
    "true_dmg_pct",
})


def _walk_stat_keys(node, keys: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("stat_bonuses", "stat_bonus") and isinstance(v, dict):
                keys.update(v.keys())
            else:
                _walk_stat_keys(v, keys)
    elif isinstance(node, list):
        for item in node:
            _walk_stat_keys(item, keys)


def _walk_placeholders(node, placeholders: set[str]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("scaling_rules"), list):
            for rule in node["scaling_rules"]:
                if isinstance(rule, dict) and "key" in rule:
                    placeholders.add(rule["key"])
        for v in node.values():
            _walk_placeholders(v, placeholders)
    elif isinstance(node, list):
        for item in node:
            _walk_placeholders(item, placeholders)


@pytest.fixture(scope="module")
def data_keys() -> tuple[set[str], set[str]]:
    keys: set[str] = set()
    placeholders: set[str] = set()
    for path in _DATA_DIR.rglob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        _walk_stat_keys(data, keys)
        _walk_placeholders(data, placeholders)
    assert keys, "no stat keys found — data dir moved?"
    return keys, placeholders


# ── The classification guard ──────────────────────────────────────────────────

def test_every_stat_key_is_classified(data_keys) -> None:
    keys, placeholders = data_keys
    unclassified = sorted(
        k for k in keys
        if not k.startswith("_")
        and not k.startswith(_OPEN_REAL_PREFIXES)
        and k not in placeholders
        and k not in fx._CONFIG_ONLY_STAT_KEYS
        and k not in KNOWN_REAL_STAT_KEYS
    )
    assert not unclassified, (
        "Unclassified stat_bonus key(s) — each would leak into combat "
        f"modifiers as a phantom stat: {unclassified}\n"
        "Real stat → add it to KNOWN_REAL_STAT_KEYS in this test.\n"
        "Config    → rename it with a '_' prefix (preferred; the engine pops "
        "'_' keys automatically — do NOT grow _CONFIG_ONLY_STAT_KEYS)."
    )


def test_legacy_config_list_never_contains_real_stats(data_keys) -> None:
    """The second silent failure mode: a real stat added to the legacy list
    would be silently dropped from combat."""
    overlap = sorted(fx._CONFIG_ONLY_STAT_KEYS & KNOWN_REAL_STAT_KEYS)
    assert not overlap, f"real stat(s) shadowed by _CONFIG_ONLY_STAT_KEYS: {overlap}"


def test_no_dead_legacy_entries(data_keys) -> None:
    """Every legacy entry must still be used somewhere — in the data JSONs or
    stamped by code (some keys are seeded at build / via effect overrides).
    Dead entries after a retune should be deleted, not hoarded."""
    keys, _ = data_keys
    source_blob = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in _SRC_DIR.rglob("*.py")
    )
    dead = sorted(
        k for k in fx._CONFIG_ONLY_STAT_KEYS
        if k not in keys and f'"{k}"' not in source_blob and f"'{k}'" not in source_blob
    )
    assert not dead, f"dead _CONFIG_ONLY_STAT_KEYS entries (no JSON or code use): {dead}"


# ── Behavioral: the underscore convention actually pops ───────────────────────

def test_underscore_config_keys_pop_from_combat_modifiers(monkeypatch) -> None:
    """A '_'-prefixed stat_bonus key must never reach the aggregated combat
    modifiers — this is the contract that lets new config keys skip
    registration entirely."""
    meta = fx.EffectMeta(
        key="BuffGuardSynthTest",
        vi="Thử Nghiệm Guard",
        en="Guard Synth Test",
        kind=fx.EffectKind.BUFF,
        description_vi="synthetic — test only",
        stat_bonus={"_guard_test_cfg": 5.0, "crit_rating": 10.0},
    )
    monkeypatch.setitem(fx.EFFECTS, meta.key, meta)

    combatant = SimpleNamespace(
        effects=[meta.key],
        effect_overrides={},
        summons=None,
    )
    result = fx.get_combat_modifiers(combatant)
    assert result == {"crit_rating": 10.0}


def test_underscore_config_keys_hidden_from_display() -> None:
    meta = fx.EffectMeta(
        key="BuffGuardSynthDisplay",
        vi="Thử Nghiệm Hiển Thị",
        en="Guard Display Test",
        kind=fx.EffectKind.BUFF,
        description_vi="synthetic — test only",
        stat_bonus={"_guard_test_cfg": 5.0, "slow_immune": 1.0, "crit_rating": 10.0},
    )
    assert fx.displayable_stat_bonus(meta) == {"crit_rating": 10.0}
