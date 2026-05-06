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
    # Fractional CDR carry. Each ``set_cooldown`` accumulates the fractional
    # turn savings (``turns * cooldown_reduce``) here and only refunds whole
    # turns when the accumulator crosses 1.0. Keeps savings linear over time
    # — a 12% CDR truly saves ~12% of total cooldown turns instead of being
    # gated by integer-truncation step thresholds. Persists for the fight.
    cdr_credit: float = 0.0
    # On-hit proc chances (from threshold bonuses)
    burn_on_hit_pct: float = 0.0
    slow_on_hit_pct: float = 0.0
    paralysis_on_crit: bool = False
    freeze_on_skill: bool = False
    # Explicit chance override for the freeze proc (cast-trigger). When > 0
    # this takes precedence over the legacy ``freeze_on_skill: True`` flag's
    # hardcoded 15%. Mirror proc on reflect uses the same value scaled by
    # ``_FREEZE_MIRROR_BOOST`` (in procs.py) to preserve the legacy 0.15→0.25
    # relationship for legacy bool-flag combatants.
    freeze_on_skill_chance: float = 0.0
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
    # Whether DoTs ticking on the opposing combatant can roll crits.
    # Set on the attacker; combat engine reads the attacker's flag each tick.
    dot_can_crit: bool = False
    # Per-stack burn damage fraction of hp_max (applied by get_periodic_damage).
    burn_per_stack_pct: float = 0.005
    # Solar aura — every turn, deal hp_max × pct as fire damage to opponent.
    # Independent of skill actions and DoTs; benefits from final_dmg_bonus,
    # burn_dmg_bonus, bonus_dmg_vs_burn, and shreds opponent's hoa resist.
    solar_aura_pct: float = 0.0
    # Wither aura — every turn, deal hp_max × pct as moc damage to opponent
    # and leech the dealt amount back as healing on the holder. Benefits from
    # final_dmg_bonus + dot_dmg_bonus, shreds moc resistance.
    wither_aura_pct: float = 0.0
    # Niết Bàn Trùng Sinh — once-per-combat revive. When the holder would die
    # the first time, restore HP to ``phoenix_revive_pct × hp_max`` (e.g. 0.70
    # = 70%) and grant a permanent (rest-of-combat) buff:
    #   atk/matk/def_stat × (1 + phoenix_revive_buff_pct)
    #   final_dmg_bonus  += phoenix_revive_buff_pct
    #   final_dmg_reduce += phoenix_revive_buff_pct (capped at MAX_FINAL_DMG_REDUCE)
    # The flag ``phoenix_revive_used`` is flipped on trigger and prevents
    # second revives from any other source within the same fight.
    phoenix_revive_pct: float = 0.0
    phoenix_revive_buff_pct: float = 0.0
    phoenix_revive_used: bool = False
    # Thôn Thiên Ma Khí — at combat start, drain ``pct`` of the opponent's
    # core combat stats (atk/matk/def/spd) and add the same amount to the
    # holder. Applied once per fight; flag below tracks consumption so the
    # transfer doesn't compound on every step() call.
    stat_drain_aura_pct: float = 0.0
    stat_drain_aura_applied: bool = False
    # Thánh Tuyền Thể — only ``damage_defer_pct`` of incoming damage is
    # deferred; the rest (``1 - damage_defer_pct``) is taken immediately.
    # The deferred portion is split into ``damage_defer_turns`` chunks queued
    # in ``deferred_damage_queue`` and paid out one per turn-end (FIFO).
    # Both fields must be > 0 for the mechanic to engage. Total damage is
    # preserved — only its timing changes. Multiple hits append to the queue.
    damage_defer_turns: int = 0
    damage_defer_pct: float = 0.0
    deferred_damage_queue: list[int] = field(default_factory=list)
    # Hào Quang Củng Cố (Fortify Aura) — Hoang Cổ Thánh Thể Chain 9 payoff.
    # ``fortify_per_turn_pct`` × ``fortify_stacks`` is added to BOTH
    # ``final_dmg_bonus`` and ``final_dmg_reduce`` while in combat.
    # ``fortify_stacks`` ticks up by 1 each periodic-phase, capped at
    # ``fortify_stack_cap``. After non-DoT damage lands, ``fortify_braced_turns``
    # is set so the holder gets an extra ``fortify_post_hit_dr_pct`` of damage
    # reduction for the next incoming hit's resolution turn.
    fortify_per_turn_pct: float = 0.0
    fortify_stack_cap: int = 0
    fortify_post_hit_dr_pct: float = 0.0
    fortify_stacks: int = 0
    fortify_braced_turns: int = 0
    # Loot economy passives — additive on top of the session's baseline
    # ``loot_qty_multiplier`` / ``loot_luck_pct`` (elite roll, dungeon grade).
    # Both fields are read by ``CombatSession._roll_loot``.
    loot_qty_bonus: float = 0.0
    loot_luck_bonus: float = 0.0
    # Generic per-element bonus dicts. Replaces the ad-hoc
    # ``damage_to_fire_convert_pct`` / ``burn_dmg_bonus`` style fields with
    # element-keyed dicts that any constitution / equipment can target:
    #
    #   damage_taken_convert_pct["hoa"] = 0.40
    #     → 40% of damage taken is reclassified as hoa damage and mitigated
    #       by the holder's own hoa resistance (Đế Liệt Diệm pattern).
    #
    #   element_dmg_bonus["hoa"] = 0.20
    #     → +20% final damage on outgoing hits whose skill element is hoa.
    #
    # Multiple equipped sources merge additively per element via the dict
    # merge in ``_merge_bonus_dict``.
    damage_taken_convert_pct: dict[str, float] = field(default_factory=dict)
    element_dmg_bonus: dict[str, float] = field(default_factory=dict)
    # Per-element penetration — passive attacker-side stat that lowers the
    # target's effective res when this combatant attacks. Read by
    # build_defense_stats and by the Linh Căn / formation / burst paths via
    # element_pen.get(elem, 0.0). Multiple sources merge additively via
    # _merge_bonus_dict, so multi-element pen composes naturally.
    # NOTE: the *debuff* counterpart is ``DebuffXuyenThau<Elem>`` (turn-duration
    # shred applied to the target via apply_effect), which stacks additively
    # with this passive pen and with DebuffXeRach (res_all).
    element_pen: dict[str, float] = field(default_factory=dict)

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

    # ── Energy Shield (Thổ + new equipment/constitution paths) ─────────────
    # PoE-style Energy Shield: ``shield`` absorbs incoming damage BEFORE HP
    # (Combatant.take_damage handles this when ``is_dot=False``). DoT damage
    # bypasses the shield — direct damage doesn't.
    #
    # Shield cap is computed by ``shield_cap()`` from three contributions:
    #   shield_max_base   — flat baseline from equipment implicit (e.g. shield base)
    #   shield_max_flat   — additive flat from affixes/constitutions
    #   shield_max_pct    — multiplicative on the base+flat sum
    #
    # ``shield_regen_pct`` is a fraction of the holder's own ``shield_cap()``
    # restored each periodic phase (NOT hp_max — that was the legacy model
    # that produced over-cap regen once Energy Shield got rebuilt around its
    # own base+flat+pct sources). ``shield_regen_flat`` adds on top.
    # Turn-based combat regenerates every turn; ``shield_recharge_pause`` is
    # preserved as a knob for build-specific delays but defaults to 0.
    shield_regen_pct: float = 0.0
    shield_regen_flat: int = 0
    shield_max_base: int = 0
    shield_max_flat: int = 0
    shield_max_pct: float = 0.0
    # HP→Shield conversion is resolved at compute_combat_stats time (the
    # holder's hp_max is already shrunk and shield_max_base bumped before the
    # Combatant is built). The field is kept on Combatant so introspection /
    # status embeds can show "30% HP converted to Shield".
    hp_to_shield_pct: float = 0.0
    # Per-attack MATK / ATK bonus = current_shield × this fraction. Read in
    # build_attack_stats so depleted shield = depleted punch. Symmetric pair
    # so mage and physical builds can both pivot shield into offense.
    matk_from_shield_pct: float = 0.0
    atk_from_shield_pct: float = 0.0
    # Endure — Đế Sinh Mộc Thể's "Cội Nguồn Bất Tận". When a hit would drop
    # HP to 0, the engine clamps HP to ``hp_max × endure_threshold_pct`` and
    # engages a cooldown. ``endure_remaining`` is the runtime turn counter,
    # decremented in CombatSession._process_periodic.
    endure_threshold_pct: float = 0.0
    endure_cooldown: int = 0
    endure_remaining: int = 0
    # Transient flag set inside ``take_damage`` when Endure clamps HP to the
    # survival floor. CombatSession reads + logs + resets it during periodic
    # processing so the player sees a clear "Endure triggered" line.
    endure_just_triggered: bool = False
    # Cleanse Heal — Đế Bạch Liên Thể's "Liên Hoa Tịnh Hóa". Each successful
    # cleanse restores ``hp_max × cleanse_heal_pct`` HP. Hooks into the same
    # quang.try_cleanse path as barrier_on_cleanse.
    cleanse_heal_pct: float = 0.0
    # Cleanse Retaliate — Đế Tịnh Quang Thể's "Tịnh Hóa Phản Đòn". Each
    # successful cleanse fires ``matk × this_pct`` Quang-flavored damage at
    # the cleanser's opponent. Resolved in CombatSession after try_cleanse.
    cleanse_retaliate_dmg_pct: float = 0.0
    # Kill Streak — Đế Sát Kim Thể's "Sát Khí Đại Thành". Each enemy killed
    # grants permanent +``kill_buff_per_kill_pct`` final_dmg_bonus, capped at
    # ``kill_buff_cap`` stacks. ``kill_streak_stacks`` is the runtime count
    # incremented in CombatSession._victory(). Persists across waves of a
    # single dungeon entry; resets when player_c is rebuilt.
    kill_buff_per_kill_pct: float = 0.0
    kill_buff_cap: int = 0
    kill_streak_stacks: int = 0
    # Multi-Strike — per-attack chance to land an additional hit at reduced
    # damage. Resolved in casting.py after the main hit; the second strike
    # reuses the same skill's ``take_damage`` path so DR/shield/resists all
    # apply naturally. Theme: lightning combos, sword storm, wind cascade.
    multi_strike_pct: float = 0.0
    multi_strike_dmg_pct: float = 0.50
    shield_recharge_delay: int = 0
    shield_recharge_pause: int = 0
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
    # On-hit: chance actor applies Ấn Phong on the target. Once marked the
    # target loses evasion (via the debuff's stat_bonus) and the attacker gains
    # crit + crit-dmg advantage vs them until the mark drops off.
    mark_on_hit_pct: float = 0.0
    # Flat damage bonus = evasion_rating × this fraction (mirror of
    # damage_bonus_from_hp_pct / damage_bonus_from_mp_pct). Converts a
    # defensive stat into offensive power — core Phong playstyle.
    damage_bonus_from_evasion_pct: float = 0.0
    # Bonus crit rating and crit-dmg rating vs targets carrying Ấn Phong.
    crit_rating_vs_marked: int = 0
    crit_dmg_vs_marked: int = 0

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
    # Bonus crit rating vs targets already marked for soul-drain (i.e.
    # hp_max_drained > 0). Rewards stacking drains before the finisher.
    crit_rating_vs_drained: int = 0
    # On-hit: chance actor applies DebuffLoaMat (Lóa Mắt). The blinded target
    # then has BLIND_MISS_CHANCE per swing to whiff. Goes through the generic
    # on-hit proc table.
    blind_on_hit_pct: float = 0.0

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

    # ── Skill-extras state ───────────────────────────────────────────────────
    # Per-skill cast counter — feeds the ``charge_bonus`` mechanic where a
    # skill detonates extra damage on every Nth cast. Keyed by skill_key.
    skill_cast_counts: dict[str, int] = field(default_factory=dict)
    # Active summons spawned by ``summon_spec`` skills. Each entry:
    #   {"name": str, "vi": str, "element": str|None,
    #    "dmg": int, "turns": int, "emoji": str}
    # ``_process_periodic`` ticks every entry: deals ``dmg`` to the opponent,
    # decrements ``turns``, and removes expired entries.
    summons: list[dict] = field(default_factory=list)

    def is_alive(self) -> bool:
        return self.hp > 0

    def take_damage(self, amount: int, is_dot: bool = False) -> int:
        """Apply ``amount`` damage with PoE-style Energy Shield absorption.

        Pipeline (in order):
          1. Fortify brace prime — any non-DoT hit primes the next-turn
             damage-reduction brace.
          2. Element conversion — ``damage_taken_convert_pct`` of the amount
             is reclassified as elemental damage and mitigated by the holder's
             matching resistance. The post-mitigation chunk rejoins the
             unconverted remainder.
          3. Defer split — when both ``damage_defer_turns`` and
             ``damage_defer_pct`` are > 0, ``damage_defer_pct`` of the
             post-conversion amount is split into N installments queued onto
             ``deferred_damage_queue`` (FIFO). The rest moves to step 4.
          4. Energy Shield absorption (NON-DoT only) — ``shield`` is consumed
             FIRST. Damage that fits inside the shield never touches HP. Any
             leftover spills into HP. DoT damage skips this step entirely
             (matches PoE's chaos damage bypassing ES).
          5. HP damage — the leftover (or full DoT amount) hits ``hp``.

        Any non-DoT damage that landed (whether absorbed by shield or hit HP)
        triggers the recharge delay: ``shield_recharge_pause`` is set to
        ``shield_recharge_delay``. The periodic phase decrements it; while
        > 0, ``shield_regen_pct/flat`` are skipped.

        ``is_dot=True`` marks the call as DoT-tick damage — bypasses the
        Fortify brace prime AND the shield, going straight to HP.

        Returns the amount applied to HP this call (queued chunks + shield
        absorption are excluded).
        """
        if amount <= 0:
            return 0
        # Fortify Aura: any non-DoT incoming damage primes the post-hit brace
        # for the next combat resolution. Set BEFORE conversion/defer so even
        # a fully-deferred installment still arms the brace this turn.
        if not is_dot and self.fortify_post_hit_dr_pct > 0:
            self.fortify_braced_turns = max(self.fortify_braced_turns, 1)

        # Step 1 — element conversion (self-mitigation through holder's res
        # for the named element). Loops over ``damage_taken_convert_pct`` so
        # any element (and any combination) routes through this generic path.
        if self.damage_taken_convert_pct:
            from src.game.constants.balance import MAX_ELEMENTAL_RES
            total_pct = sum(
                p for p in self.damage_taken_convert_pct.values() if p > 0
            )
            # Cap aggregate conversion at 100% so the holder always takes at
            # least the unconverted remainder.
            total_pct = min(1.0, total_pct)
            unconverted = int(amount * (1.0 - total_pct))
            new_amount = unconverted
            for elem, pct in self.damage_taken_convert_pct.items():
                if pct <= 0:
                    continue
                # Each element's slice scales proportionally if total_pct was
                # capped (so {hoa: 0.7, thuy: 0.6} effectively becomes
                # {hoa: 7/13, thuy: 6/13} of the converted total).
                share = pct / sum(self.damage_taken_convert_pct.values()) \
                    if sum(self.damage_taken_convert_pct.values()) > 0 else 0
                converted = int(amount * total_pct * share)
                if converted <= 0:
                    continue
                elem_res = max(
                    0.0,
                    min(MAX_ELEMENTAL_RES, self.resistances.get(elem, 0.0)),
                )
                new_amount += max(1, int(converted * (1.0 - elem_res)))
            amount = new_amount

        # Step 2 — deferred-damage split
        n = self.damage_defer_turns
        pct = self.damage_defer_pct
        if n > 0 and pct > 0 and not is_dot:
            deferred_total = int(amount * pct)
            immediate = amount - deferred_total
            if deferred_total > 0:
                chunk = deferred_total // n
                remainder = deferred_total - chunk * n
                for i in range(n):
                    self.deferred_damage_queue.append(chunk + (1 if i < remainder else 0))
            amount = immediate

        # Step 3 — Energy Shield absorption (PoE-style, non-DoT only). The
        # shield acts as a first-defense pool; damage that fits inside the
        # shield never touches HP. Any non-zero non-DoT damage primes the
        # recharge delay regardless of whether shield was 0 before — taking a
        # hit always pauses regen for the pre-configured window.
        if not is_dot and amount > 0:
            if self.shield > 0:
                absorbed = min(self.shield, amount)
                self.shield -= absorbed
                amount -= absorbed
            self.shield_recharge_pause = max(
                self.shield_recharge_pause, self.shield_recharge_delay,
            )

        # Step 4 — HP damage (leftover spill or full DoT amount).
        self.hp = max(0, self.hp - amount)

        # Step 5 — Endure (Cội Nguồn Bất Tận). When a hit would kill the
        # holder and the cooldown is not engaged, clamp HP to a survival
        # floor instead and engage the cooldown. Repeats every
        # ``endure_cooldown`` turns — different from BuffBatTu (single-use)
        # and phoenix_revive (revive-from-death). DoTs can still kill if
        # they tick the holder while cooldown is active; that's intentional
        # so the build remains vulnerable to sustained pressure.
        if (
            self.hp == 0
            and self.endure_threshold_pct > 0
            and self.endure_remaining == 0
        ):
            self.hp = max(1, int(self.hp_max * self.endure_threshold_pct))
            self.endure_remaining = self.endure_cooldown
            self.endure_just_triggered = True

        return amount

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
        """Maximum shield value this combatant can hold.

        Aggregates three sources:
          * ``shield_max_base``  — flat baseline (equipment implicit)
          * ``shield_max_flat``  — additive flat (affixes / constitutions)
          * ``shield_max_pct``   — multiplicative on the base+flat sum

        Formula: ``(base + flat) * (1 + max_pct)``.
        """
        flat_total = self.shield_max_base + self.shield_max_flat
        return max(0, int(flat_total * (1.0 + self.shield_max_pct)))

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
        """Set ``skill_key``'s cooldown, applying CDR via fractional accumulator.

        Each call adds ``turns * cooldown_reduce`` to ``cdr_credit`` and
        refunds whole turns once accumulated. So 12% CDR on 8-CD skills
        saves ~0.96 turn each cast — accumulating to a full +1 turn refund
        on the 2nd cast (then +1 every cast thereafter), vs. the old
        truncation behavior that arbitrarily refunded 1 turn for any
        non-zero CDR.
        """
        if self.cooldown_reduce > 0:
            self.cdr_credit += turns * self.cooldown_reduce
            refund = int(self.cdr_credit)
            self.cdr_credit -= refund
            effective = max(1, turns - refund)
        else:
            effective = turns
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
