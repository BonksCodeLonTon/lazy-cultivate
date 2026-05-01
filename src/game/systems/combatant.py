"""Combatant dataclass — live combat state for a player or enemy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Combatant:
    """Live combatant state during combat — player or enemy."""
    key: str
    name: str
    hp: int
    hp_max: int
    mp: int
    mp_max: int
    spd: int
    element: Optional[str]
    resistances: dict[str, float] = field(default_factory=dict)
    # Attack and defense stats — computed from cultivation path progression
    atk: int = 0       # Physical attack power (Luyện Thể path gives most ATK)
    matk: int = 0      # Magic attack power (Luyện Khí path gives most MATK)
    def_stat: int = 0  # Physical defense (Luyện Thể path gives most DEF)
    crit_rating: int = 0
    crit_dmg_rating: int = 0
    evasion_rating: int = 0
    crit_res_rating: int = 0
    final_dmg_bonus: float = 0.0
    # Damage reduction on received hits (from formation/constitution)
    final_dmg_reduce: float = 0.0
    # Per-turn HP regen as fraction of hp_max (e.g. 0.02 = 2%/turn)
    hp_regen_pct: float = 0.0
    # Per-turn flat HP regen, stacks with hp_regen_pct
    hp_regen_flat: int = 0
    # Per-turn MP regen as fraction of mp_max (e.g. 0.03 = 3%/turn)
    mp_regen_pct: float = 0.0
    # Per-turn flat MP regen, stacks with mp_regen_pct
    mp_regen_flat: int = 0
    # Healing effectiveness multiplier (from Quang Linh Căn passive)
    heal_pct: float = 0.0
    # Cooldown reduction multiplier (e.g. 0.15 → CD * 0.85)
    cooldown_reduce: float = 0.0
    # On-hit proc chances (from threshold bonuses)
    burn_on_hit_pct: float = 0.0
    slow_on_hit_pct: float = 0.0
    paralysis_on_crit: bool = False
    freeze_on_skill: bool = False
    poison_immunity: bool = False
    # Flat chance (0.0–1.0) to resist any incoming debuff or CC proc.
    # e.g. 0.20 → 20% of debuffs that would land are blocked.
    # Stacks additively from formation, constitution, and linh_can bonuses.
    debuff_immune_pct: float = 0.0
    # Total immunity to hard CC (stun/freeze/silence/interrupt/knock-up).
    # Used by world bosses — soft debuffs (slow, DoT, armor break) still apply.
    immune_hard_cc: bool = False
    # True for persistent world-boss Combatants. Prevents any mechanic from
    # mutating the boss's ``hp_max`` (e.g. Âm Hồn Phệ soul-drain) so the
    # shared HP pool stays authoritative and can't be trivialized by a
    # single attack's local-sim mutations.
    is_world_boss: bool = False
    # Immunity / special flags
    active_flags: dict[str, bool] = field(default_factory=dict)
    # Active effects: effect_key → turns_remaining
    effects: dict[str, int] = field(default_factory=dict)
    # Per-instance magnitude overrides applied on top of the EffectMeta
    # defaults. Keyed by effect_key; the inner dict mirrors the subset of
    # EffectMeta fields a skill may override (stat_bonus, dot_pct,
    # dot_element). Read by ``effects.get_combat_modifiers`` and the DoT
    # tick path. Cleared automatically when the effect expires.
    effect_overrides: dict[str, dict] = field(default_factory=dict)
    # Skill cooldowns: skill_key → turns_remaining
    cooldowns: dict[str, int] = field(default_factory=dict)
    skill_keys: list[str] = field(default_factory=list)
    # Formation skills — one entry per active formation slot. Fire in PARALLEL
    # each turn after the main skill (Trận Tu multi-formation simultaneity):
    # every formation that's off cooldown + can afford MP pings the target
    # independently, so 3 active formations = up to 4 attacks per turn
    # (main + 3 formations). Stored separately from ``skill_keys`` so they
    # don't compete with the player's chosen rotation.
    formation_skill_keys: list[str] = field(default_factory=list)
    # Linh Căn keys (spiritual roots) + per-element progression level (1..9).
    # ``linh_can`` stays as a bare list so existing membership checks
    # (``"kim" in actor.linh_can``) keep working; ``linh_can_levels`` is what
    # the effect modules read for proc-chance / potency scaling.
    linh_can: list[str] = field(default_factory=list)
    linh_can_levels: dict[str, int] = field(default_factory=dict)
    # Thổ Hộ Thể shield (absorbs damage)
    shield: int = 0
    ho_the_used: bool = False

    # ── Fire DoT build support ────────────────────────────────────────────────
    # Burn stacks: multiple stacks pile on the same target, each contributing
    # per-stack damage to the DebuffThieuDot tick. Stacks decay when the burn
    # effect fully expires.
    burn_stacks: int = 0
    # Max concurrent stacks. Base 5; fire-build items raise this.
    burn_stack_cap: int = 5
    # Bonus damage multiplier vs targets that currently have burn stacks.
    # e.g. 0.25 → +25% final damage if target has any burn stack.
    bonus_dmg_vs_burn: float = 0.0
    # Flat fire-resistance shred applied by this actor when landing a hit
    # on a target. Stacks additively with debuffs in the same hit.
    fire_res_shred: float = 0.0
    # Whether DoTs ticking on the opposing combatant can roll crits.
    # Set on the attacker; combat engine reads the attacker's flag each tick.
    dot_can_crit: bool = False
    # Per-stack burn damage fraction of hp_max (applied by get_periodic_damage).
    burn_per_stack_pct: float = 0.005

    # ── Kim (Bleed) build support ─────────────────────────────────────────────
    # Bleed stacks: like burn, but physical (kim element) and slows healing.
    bleed_stacks: int = 0
    bleed_stack_cap: int = 5
    # Per-stack bleed damage fraction of hp_max.
    bleed_per_stack_pct: float = 0.005
    # When holder has bleed stacks, incoming heals are multiplied by (1 - bleed_heal_reduce).
    # e.g. 0.40 means heals on a bleeding target are reduced by 40%.
    bleed_heal_reduce: float = 0.0
    # On-hit: chance actor applies a bleed stack.
    bleed_on_hit_pct: float = 0.0
    # Bonus crit chance vs bleeding targets (flat rating added to crit_rating).
    crit_rating_vs_bleed: int = 0
    # Bonus crit damage vs bleeding targets (flat rating added to crit_dmg_rating).
    crit_dmg_vs_bleed: int = 0
    # True damage: percentage of target hp_max applied unblockable on each hit.
    # Max per-hit cap is TRUE_DMG_PCT_CAP in the damage pipeline.
    true_dmg_pct: float = 0.0

    # ── Mộc (Wood / Poison Leech) build ──────────────────────────────────────
    # When any DoT ticks on the opposing combatant, this combatant leeches
    # this fraction of the tick damage as HP + MP.
    dot_leech_pct: float = 0.0
    # Flat shred on target's moc resistance (mirror of fire_res_shred).
    moc_res_shred: float = 0.0
    # Damage-from-heal: every heal this combatant receives queues
    # heal × damage_from_heal_pct bonus damage on their next hit.
    damage_from_heal_pct: float = 0.0
    queued_heal_dmg: int = 0
    # Damage scaling with hp_max: flat damage bonus = hp_max × this per attack.
    damage_bonus_from_hp_pct: float = 0.0

    # ── Thủy (Water / Mana) build ────────────────────────────────────────────
    # Fraction of incoming damage reflected back to the attacker after HP loss.
    reflect_pct: float = 0.0
    # When True, the defender's own on-hit procs (freeze_on_skill, slow, etc.)
    # fire on the attacker as part of the reflected hit — the mirror carries
    # not just damage but the defender's build effects.
    reflect_applies_effects: bool = False
    # Flat damage bonus per attack = mp_max × this. Grows damage with mana pool.
    damage_bonus_from_mp_pct: float = 0.0
    # On-hit: leech this fraction of damage dealt as MP for the attacker.
    mp_leech_pct: float = 0.0
    # Mana-stack accumulator. Gained per attack/skill cast; each stack adds
    # mana_stack_dmg_bonus × final_dmg_bonus, or can be burst-consumed.
    mana_stacks: int = 0
    mana_stack_cap: int = 10
    mana_stack_per_attack: int = 0      # passive: stacks gained each turn
    mana_stack_dmg_bonus: float = 0.0   # per-stack final-damage bonus
    # Flat shred on target's thủy resistance (mirror of fire_res_shred).
    thuy_res_shred: float = 0.0

    # ── Thổ (Earth / Shield) build ──────────────────────────────────────────
    # Per-turn shield regen as a fraction of hp_max (e.g. 0.05 = +5% hp_max/turn).
    shield_regen_pct: float = 0.0
    # Flat shield gained per turn (stacks with shield_regen_pct).
    shield_regen_flat: int = 0
    # Maximum shield that can be held at any time, as a fraction of hp_max.
    # 1.0 = can stack shield up to 100% of hp_max. Default 0.30 (old Hộ Thể cap).
    shield_cap_pct: float = 0.30
    # Every hit adds flat damage = current shield × this fraction.
    damage_bonus_from_shield_pct: float = 0.0
    # Thorn: fraction of incoming damage reflected as physical thorn damage.
    # Differs from reflect_pct by always dealing physical kim-style damage
    # regardless of defender element.
    thorn_pct: float = 0.0
    # Shield takes the reflected damage before thorn bleeds into HP.
    thorn_from_shield: bool = False
    # On-hit: chance to stun the target (any hit, not just bạo kích).
    stun_on_hit_pct: float = 0.0

    # ── Phong (Wind / Evasion / Mark) build ──────────────────────────────────
    # On-hit: chance actor applies Phong Ấn on the target. Once marked the
    # target loses evasion (via the debuff's stat_bonus) and the attacker gains
    # crit + crit-dmg advantage vs them until the mark drops off.
    mark_on_hit_pct: float = 0.0
    # Flat damage bonus = evasion_rating × this fraction (mirror of
    # damage_bonus_from_hp_pct / damage_bonus_from_mp_pct). Converts a
    # defensive stat into offensive power — core Phong playstyle.
    damage_bonus_from_evasion_pct: float = 0.0
    # Bonus crit rating and crit-dmg rating vs targets carrying Phong Ấn.
    crit_rating_vs_marked: int = 0
    crit_dmg_vs_marked: int = 0
    # Flat shred on target's phong resistance (mirror of fire_res_shred).
    phong_res_shred: float = 0.0

    # ── Quang (Light / Silence / Anti-Heal) build ────────────────────────────
    # On-crit: chance the actor applies CCMuted (silence) to the target. Gated
    # on crit so it rewards the crit-heavy setup Quang uniques push toward.
    silence_on_crit_pct: float = 0.0
    # On-hit: chance the actor applies DebuffCatDut (anti-heal / hp_regen shred)
    # to the target. Integrates into the generic on-hit proc table.
    heal_reduce_on_hit_pct: float = 0.0
    # Pre-turn: extra cleanse roll added on top of the base 15% Quang Linh Căn
    # chance. Sourced from formation gem thresholds and unique equipment.
    cleanse_on_turn_pct: float = 0.0
    # Flat shred on target's quang resistance (mirror of fire_res_shred).
    quang_res_shred: float = 0.0
    # When True, a successful cleanse grants a small MATK-scaled barrier
    # (delta from Thổ Hộ Thể, which is one-shot at low HP — this repeats).
    barrier_on_cleanse: bool = False
    # When True, heals may crit (25% chance × 1.5 mult — mirrors dot_can_crit).
    # Shared by Mộc and Quang builds; granted via formation + late-realm uniques.
    heal_can_crit: bool = False

    # ── Âm (Shadow / Soul-Devour) build ──────────────────────────────────────
    # On-hit: chance the actor drains a slice of target's hp_max for the rest
    # of the fight. Drain permanently shrinks ``target.hp_max`` (and target.hp
    # at the same time if overflow). A fraction of each drain is transferred
    # to the actor as hp_max growth, making the Âm build snowball vs long
    # fights. Per-proc and total-drain caps are enforced in balance.py.
    soul_drain_on_hit_pct: float = 0.0
    # Tracker: cumulative hp_max drained from this combatant this fight.
    # Read only by the engine to clamp further drains at the cap.
    hp_max_drained: int = 0
    # Initial hp_max snapshot — captured on first drain so cap math always
    # references the pre-drain value rather than chasing a shrinking pool.
    hp_max_original: int = 0
    # On-hit: chance actor steals a flat slice of target's atk/matk/def
    # (subtracts from target, adds to actor) for the rest of the fight. Total
    # stolen amount tracked below and capped at STAT_STEAL_CAP_PCT of each
    # source stat's snapshot.
    stat_steal_on_hit_pct: float = 0.0
    stolen_atk: int = 0
    stolen_matk: int = 0
    stolen_def: int = 0
    # Starting-stat snapshots so the cap math references pre-theft values.
    atk_original: int = 0
    matk_original: int = 0
    def_stat_original: int = 0
    # Flat shred on target's am resistance (mirror of fire_res_shred).
    am_res_shred: float = 0.0
    # Bonus crit rating vs targets already marked for soul-drain (i.e.
    # hp_max_drained > 0). Rewards stacking drains before the finisher.
    crit_rating_vs_drained: int = 0

    # ── Lôi (Lightning / Shock) build ────────────────────────────────────────
    # Shock stacks — applied by on-hit procs or loi skills. Each stack makes
    # the holder take an additional ``shock_per_stack_pct`` of any incoming
    # Lôi-element hit as flat final-damage amplification, up to
    # ``shock_stack_cap`` (the lightning payload resonates with the shock;
    # non-Lôi skills do not trigger the bonus).
    shock_stacks: int = 0
    shock_stack_cap: int = 5
    # Per-stack final-damage multiplier the attacker adds when a Lôi-element
    # hit lands on a shocked target. e.g. 0.03 → +3% final damage per stack.
    shock_per_stack_pct: float = 0.03
    # On-hit: chance the actor lands a Sốc Điện stack on the target.
    shock_on_hit_pct: float = 0.0
    # Flat shred on target's loi resistance (mirror of fire_res_shred).
    loi_res_shred: float = 0.0
    # After a normal turn, flat chance to immediately steal an extra turn
    # (independent of the SPD-based extra-turn roll — stacks additively).
    turn_steal_pct: float = 0.0

    # ── DoT damage amplifiers (any build can stack these) ──────────────────
    # Additive multiplier on ALL DoT ticks this combatant's own DoTs cause.
    # e.g. 0.25 → DoTs tick 25% harder.
    dot_dmg_bonus: float = 0.0
    # Per-type multipliers stacked on top of dot_dmg_bonus.
    burn_dmg_bonus: float = 0.0
    bleed_dmg_bonus: float = 0.0
    poison_dmg_bonus: float = 0.0
    # Per-attacker DoT-bonus contributions received while holding DoTs.
    # Keyed by attacker.key; values are dicts with {dot_dmg_bonus, burn, bleed,
    # poison, power, scales_hp_pct}. The live target.dot_*_bonus fields are the
    # SUM of all source entries — so multiple attackers' bonuses add together
    # instead of max-merging. ``power`` = max(actor.atk, actor.matk) at apply
    # time; ``scales_hp_pct`` = actor's dot_scales_hp_pct flag.
    dot_bonus_sources: dict = field(default_factory=dict)
    # When True, DoTs ticking on this combatant scale with the target's hp_max
    # (the legacy model). When False (default), DoTs scale with the APPLIER's
    # atk/matk power — the regular-play model. The flag propagates from the
    # attacker at DoT-apply time; rare late-game uniques set it True.
    dot_scales_hp_pct: bool = False

    def is_alive(self) -> bool:
        return self.hp > 0

    def add_burn_stack(self, count: int = 1) -> int:
        """Add burn stacks, clamped by ``burn_stack_cap``. Returns stacks gained."""
        before = self.burn_stacks
        self.burn_stacks = min(self.burn_stack_cap, self.burn_stacks + count)
        return self.burn_stacks - before

    def consume_burn_stacks(self) -> int:
        """Remove all burn stacks and return how many were consumed."""
        stacks = self.burn_stacks
        self.burn_stacks = 0
        return stacks

    def add_shock_stack(self, count: int = 1) -> int:
        """Add shock stacks, clamped by ``shock_stack_cap``. Returns stacks gained."""
        before = self.shock_stacks
        self.shock_stacks = min(self.shock_stack_cap, self.shock_stacks + count)
        return self.shock_stacks - before

    def consume_shock_stacks(self) -> int:
        """Remove all shock stacks and return how many were consumed."""
        stacks = self.shock_stacks
        self.shock_stacks = 0
        return stacks

    def add_bleed_stack(self, count: int = 1) -> int:
        """Add bleed stacks, clamped by ``bleed_stack_cap``. Returns stacks gained."""
        before = self.bleed_stacks
        self.bleed_stacks = min(self.bleed_stack_cap, self.bleed_stacks + count)
        return self.bleed_stacks - before

    def consume_bleed_stacks(self) -> int:
        stacks = self.bleed_stacks
        self.bleed_stacks = 0
        return stacks

    def add_mana_stack(self, count: int = 1) -> int:
        before = self.mana_stacks
        self.mana_stacks = min(self.mana_stack_cap, self.mana_stacks + count)
        return self.mana_stacks - before

    def consume_mana_stacks(self) -> int:
        stacks = self.mana_stacks
        self.mana_stacks = 0
        return stacks

    def shield_cap(self) -> int:
        """Maximum shield value this combatant can hold."""
        return int(self.hp_max * self.shield_cap_pct)

    def add_shield(self, amount: int) -> int:
        """Add shield capped at shield_cap. Returns actual gained."""
        if amount <= 0:
            return 0
        cap = self.shield_cap()
        before = self.shield
        self.shield = min(cap, self.shield + amount)
        return self.shield - before

    def consume_shield(self) -> int:
        """Empty the shield pool, return the consumed amount."""
        amt = self.shield
        self.shield = 0
        return amt

    def has_effect(self, effect: str) -> bool:
        return self.effects.get(effect, 0) > 0

    def apply_effect(
        self, effect: str, duration: int, overrides: dict | None = None,
    ) -> None:
        """Apply or refresh an effect with optional per-instance overrides.

        ``overrides`` lets a skill stamp custom magnitudes onto the holder's
        copy of the effect — e.g. a powerful skill can apply DebuffXeRach
        with ``stat_bonus={"res_all": -0.20}`` instead of the meta default
        (-0.08). Recognised keys: ``stat_bonus`` (dict, merged into the
        meta's stat_bonus per-key), ``dot_pct`` (float), ``dot_element``
        (str). When the same effect is reapplied the *stronger* value of
        each numeric field wins, so refreshing a -0.08 res shred with
        another -0.08 doesn't accidentally erase a previously stamped
        -0.20 from a heavier skill.
        """
        self.effects[effect] = max(self.effects.get(effect, 0), duration)
        if overrides:
            existing = self.effect_overrides.get(effect, {})
            self.effect_overrides[effect] = _merge_effect_overrides(existing, overrides)

    def tick_effects(self) -> list[str]:
        expired = [k for k, v in self.effects.items() if v <= 1]
        self.effects = {k: v - 1 for k, v in self.effects.items() if v > 1}
        # Per-instance overrides die with the effect — a refreshed application
        # has to re-stamp them rather than inherit a stale magnitude.
        for k in expired:
            self.effect_overrides.pop(k, None)
        # Burn stacks decay with the burn debuff: when DebuffThieuDot expires,
        # all remaining stacks are cleared.
        if "DebuffThieuDot" in expired:
            self.burn_stacks = 0
        if "DebuffChayMau" in expired:
            self.bleed_stacks = 0
        if "DebuffSocDien" in expired:
            self.shock_stacks = 0
        return expired

    def tick_cooldowns(self) -> None:
        self.cooldowns = {k: max(0, v - 1) for k, v in self.cooldowns.items()}

    def skill_on_cooldown(self, skill_key: str) -> bool:
        return self.cooldowns.get(skill_key, 0) > 0

    def set_cooldown(self, skill_key: str, turns: int) -> None:
        effective = max(1, int(turns * (1.0 - self.cooldown_reduce)))
        self.cooldowns[skill_key] = effective



# ── Effect-override merge helper ─────────────────────────────────────────────

def _merge_effect_overrides(existing: dict, incoming: dict) -> dict:
    """Combine two override dicts so the *stronger* magnitude wins.

    Used when an effect is reapplied while still active — a fresh stamp
    from a heavy skill must not be erased by a weaker reapplication of
    the same effect from a lighter skill. Stronger means:

      • numeric stat_bonus values (debuffs are negative): farther from 0
      • dot_pct: the higher value
      • dot_element: incoming wins (str overrides aren't ranked)

    Unknown override keys are pass-through (incoming wins) so future
    fields don't need to retro-fit this helper.
    """
    out: dict = dict(existing)

    # stat_bonus is the most common override — merge per-stat by magnitude.
    inc_stats = incoming.get("stat_bonus") or {}
    if inc_stats:
        merged_stats = dict(out.get("stat_bonus") or {})
        for k, v in inc_stats.items():
            if k in merged_stats and isinstance(v, (int, float)) and not isinstance(v, bool):
                # Keep whichever value has the larger absolute magnitude.
                merged_stats[k] = v if abs(v) > abs(merged_stats[k]) else merged_stats[k]
            else:
                merged_stats[k] = v
        out["stat_bonus"] = merged_stats

    if "dot_pct" in incoming:
        out["dot_pct"] = max(float(incoming["dot_pct"]), float(out.get("dot_pct", 0.0)))
    if "dot_element" in incoming:
        out["dot_element"] = incoming["dot_element"]

    # Any other custom keys: incoming wins.
    for k, v in incoming.items():
        if k in ("stat_bonus", "dot_pct", "dot_element"):
            continue
        out[k] = v
    return out
