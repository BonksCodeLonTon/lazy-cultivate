"""Pre-damage ``final_dmg_bonus`` rider registry.

A *rider* is a small hook that reads one ``skill_data`` field, inspects
the actor/target state, and folds a bonus into ``actor_mods["final_dmg_bonus"]``
for the current cast only. Before this module, every rider lived as its own
25-line if-block inside ``cast_skill`` (~115 lines for 4 riders); adding a
new one meant copy-pasting the pattern and risking subtle drift in guards
or log formatting.

The registry inverts that flow: each rider is a small function decorated
with ``@register_dmg_bonus_rider("skill_field_name")`` that returns either
``None`` (rider didn't fire) or a ``(bonus, log_line)`` tuple. Dispatcher
``apply_dmg_bonus_riders`` walks the registry once per cast, folds non-zero
contributions into ``actor_mods``, and appends log lines. Adding a new
rider is now one decorated function — see the four below for templates.

Riders are pure of side effects on the actor/target — they only read state
and produce a (bonus, log) result. Mutation lives in the dispatcher so
ordering, capping, and log policy stay consistent across riders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.game.systems.skill_mastery import power_mult
from src.utils.config import settings

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant

# Rider signature: (spec, actor, target) → (bonus, log_line) | None.
# Returning ``None`` (or a (bonus<=0, ...) tuple) means the rider did not
# fire — dispatcher skips silently.
RiderFn = Callable[
    [Any, "Combatant", "Combatant"],
    Optional[tuple[float, str]],
]


@dataclass(frozen=True)
class DmgBonusRider:
    """One registered rider: the skill_data field it watches + its handler."""
    skill_field: str
    fn: RiderFn


_DMG_BONUS_RIDERS: list[DmgBonusRider] = []


def register_dmg_bonus_rider(skill_field: str) -> Callable[[RiderFn], RiderFn]:
    """Decorator — register ``fn`` as the rider for ``skill_data[skill_field]``.

    The dispatcher only invokes the rider when ``skill_data`` actually
    carries that field (truthy), so the rider's first responsibility is
    just to compute the bonus — it doesn't need to re-check the field's
    presence.
    """
    def deco(fn: RiderFn) -> RiderFn:
        _DMG_BONUS_RIDERS.append(DmgBonusRider(skill_field=skill_field, fn=fn))
        return fn
    return deco


def apply_dmg_bonus_riders(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    actor_mods: dict, log: list[str],
) -> None:
    """Walk every registered rider, fold its bonus into ``actor_mods``.

    A rider can opt out by returning ``None`` or any tuple whose bonus is
    ``<= 0``; the dispatcher then skips both the fold and the log append.
    Riders run in registration order — register them in the order you want
    them logged when they all fire together.
    """
    for rider in _DMG_BONUS_RIDERS:
        spec = skill_data.get(rider.skill_field)
        if not spec:
            continue
        result = rider.fn(spec, actor, target)
        if result is None:
            continue
        bonus, log_line = result
        if bonus <= 0:
            continue
        actor_mods["final_dmg_bonus"] = (
            actor_mods.get("final_dmg_bonus", 0.0) + bonus
        )
        if log_line:
            log.append(log_line)


# ── Built-in riders ─────────────────────────────────────────────────────
# Each block mirrors a former inline block in ``cast_skill``. Keep the log
# strings byte-identical to the originals so existing snapshot/expectation
# tests don't drift.


@register_dmg_bonus_rider("dmg_per_target_buff_pct")
def _buff_count_rider(
    spec: Any, actor: "Combatant", target: "Combatant",
) -> Optional[tuple[float, str]]:
    """Thẩm Phán Chi Nộ — +X% per ``EffectKind.BUFF`` entry on target.

    Spec is a flat float (the per-buff percentage). Counts only buffs, not
    CC/debuffs, so a buff-stacked target is the natural payoff state.
    """
    from src.game.engine.effects import EFFECTS, EffectKind

    per_buff_pct = float(spec or 0.0)
    if per_buff_pct <= 0:
        return None
    buff_n = sum(
        1 for k in target.effects
        if (m := EFFECTS.get(k)) and m.kind is EffectKind.BUFF
    )
    if buff_n == 0:
        return None
    bonus = per_buff_pct * buff_n
    return (
        bonus,
        f"    ⚖️ *Thẩm Phán* — +{bonus * 100:.0f}% ST "
        f"(×{buff_n} trạng thái tốt)",
    )


@register_dmg_bonus_rider("final_dmg_bonus_per_target_hp_lost_bucket")
def _hp_lost_bucket_rider(
    spec: Any, actor: "Combatant", target: "Combatant",
) -> Optional[tuple[float, str]]:
    """Bạch Hổ Khiếu Thiên — bucketed missing-HP rider.

    Spec shape:
        {"bucket": 0.05, "per_bucket": 0.02, "max_bonus": 0.40,
         "label": "...", "emoji": "..."}
    → +per_bucket for every ``bucket`` fraction of HP the target has lost,
    capped at ``max_bonus``. Defaults preserve the original Bạch Hổ
    log flavor so legacy data keeps its line.
    """
    if target.hp_max <= 0:
        return None
    bucket = float(spec.get("bucket", 0.05))
    per_bucket = float(spec.get("per_bucket", 0.0))
    max_bonus = float(spec.get("max_bonus", 0.0))
    if bucket <= 0 or per_bucket <= 0:
        return None
    missing_pct = max(0.0, 1.0 - target.hp / target.hp_max)
    buckets = int(missing_pct / bucket)
    bonus = buckets * per_bucket
    if max_bonus > 0:
        bonus = min(bonus, max_bonus)
    if bonus <= 0:
        return None
    emoji = spec.get("emoji", "🐯")
    label = spec.get("label", "Bạch Hổ Hành Hung")
    return (
        bonus,
        f"    {emoji} *{label}* — +{int(bonus * 100)}% ST "
        f"({buckets}×{int(bucket * 100)}% HP đã mất)",
    )


@register_dmg_bonus_rider("final_dmg_bonus_per_target_debuff_count")
def _debuff_count_rider(
    spec: Any, actor: "Combatant", target: "Combatant",
) -> Optional[tuple[float, str]]:
    """Thừa Phong Phá Lãng — +X% per ``EffectKind.DEBUFF`` entry on target.

    Spec shape:
        {"per_debuff": 0.08, "max_bonus": 0.40,
         "label": "...", "emoji": "..."}
    Counts only DEBUFFs (CC and buffs don't qualify) — same semantics as
    the ``requires_target_debuff_count`` gate in session.py.
    """
    from src.game.engine.effects import EFFECTS, EffectKind

    per_debuff = float(spec.get("per_debuff", 0.0))
    max_bonus = float(spec.get("max_bonus", 0.0))
    if per_debuff <= 0:
        return None
    debuff_count = sum(
        1 for k in target.effects
        if (mm := EFFECTS.get(k)) and mm.kind is EffectKind.DEBUFF
    )
    bonus = debuff_count * per_debuff
    if max_bonus > 0:
        bonus = min(bonus, max_bonus)
    if bonus <= 0:
        return None
    emoji = spec.get("emoji", "🌊")
    label = spec.get("label", "Phá Lãng")
    return (
        bonus,
        f"    {emoji} *{label}* — +{int(bonus * 100)}% ST "
        f"({debuff_count} suy yếu)",
    )


@register_dmg_bonus_rider("combo_counter_scaling")
def _combo_counter_fdb_rider(
    spec: Any, actor: "Combatant", target: "Combatant",
) -> Optional[tuple[float, str]]:
    """Truy Kích Liên Vũ (Phong B1) — final-dmg slice of the self-combo.

    Reads the actor's persistent ``<stack_field>`` combo counter (already
    incremented for THIS cast in the cast path, clamped to ``combo_cap``) and
    folds ``per_consecutive_final_dmg_pct × n`` into ``final_dmg_bonus``. The
    additive base-dmg slice rides the companion base-dmg rider below; both
    read the same counter so they stay in lock-step.

    Inert for enemies: they never cast the skill, so ``phong_combo`` stays 0
    → ``n == 0`` → ``None``.
    """
    if not isinstance(spec, dict):
        return None
    field = spec.get("stack_field")
    per_pct = float(spec.get("per_consecutive_final_dmg_pct", 0.0))
    if not field or per_pct <= 0:
        return None
    cap = int(spec.get("combo_cap", 0))
    n = int(getattr(actor, field, 0))
    if cap > 0:
        n = min(n, cap)
    if n <= 0:
        return None
    bonus = per_pct * n
    return (
        bonus,
        f"    🌀 *Liên Vũ* — chuỗi ×{n} (+{int(bonus * 100)}% ST cuối)",
    )


@register_dmg_bonus_rider("seal_refresh_dmg_bonus")
def _seal_refresh_rider(
    spec: Any, actor: "Combatant", target: "Combatant",
) -> Optional[tuple[float, str]]:
    """Sơn Hà Ấn — flat compound when target already wears a named seal.

    Spec shape:
        {"effect": "DebuffXyz", "bonus": 0.50,
         "label": "...", "emoji": "..."}
    Bonus is flat (does NOT scale with stacks of the named effect) so
    re-casts compound predictably regardless of stack count.
    """
    effect = spec.get("effect")
    bonus = float(spec.get("bonus", 0.0))
    if not effect or bonus <= 0:
        return None
    if not target.has_effect(effect):
        return None
    emoji = spec.get("emoji", "🗿")
    label = spec.get("label", "Trấn Ấn")
    return (
        bonus,
        f"    {emoji} *{label}* — +{int(bonus * 100)}% ST "
        f"(ấn ký đã có sẵn)",
    )


# ═══════════════════════════════════════════════════════════════════════
# Base-damage rider registry
# ═══════════════════════════════════════════════════════════════════════
#
# These riders fold an additive integer bonus into ``base_dmg`` *before*
# the damage pipeline runs — so the bonus rides through crit / element /
# final-bonus the same way as the skill's declared base. Distinct from
# the ``final_dmg_bonus`` riders above (those are multiplicative on the
# computed damage; these grow the input integer).
#
# Riders receive the full ``skill_data`` and ``actor_mods`` so they can
# read multiple fields or pull live actor stats — anti-regen needs two
# spec fields, caster-scaling reads actor.spd / actor.evasion_rating /
# actor_mods["dmg_bonus_<elem>"]. Returns ``(bonus, log_line)`` or None.

BaseDmgRiderFn = Callable[
    [dict, "Combatant", "Combatant", int, dict],
    Optional[tuple[int, str]],
]


@dataclass(frozen=True)
class BaseDmgRider:
    """One registered base-dmg rider — just its handler.

    Unlike ``DmgBonusRider`` there is no single ``skill_field`` key,
    because some base-dmg riders read multiple fields (anti-regen has
    flat + pct variants under one logical block). The rider itself
    decides whether it fires by inspecting ``skill_data``.
    """
    fn: BaseDmgRiderFn


_BASE_DMG_RIDERS: list[BaseDmgRider] = []


def register_base_dmg_rider() -> Callable[[BaseDmgRiderFn], BaseDmgRiderFn]:
    """Decorator — register ``fn`` as a base_dmg rider.

    Riders run in registration order. They receive the *current* (already-
    modified) ``base_dmg`` so a later rider can see prior riders' bonuses
    if it ever needs to — none of today's riders do, but the contract is
    in place for future extensions.
    """
    def deco(fn: BaseDmgRiderFn) -> BaseDmgRiderFn:
        _BASE_DMG_RIDERS.append(BaseDmgRider(fn=fn))
        return fn
    return deco


def apply_base_dmg_riders(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict, log: list[str],
) -> tuple[int, dict]:
    """Walk every registered base-dmg rider; return ``(new_base_dmg, new_skill_data)``.

    The dispatcher refreshes ``skill_data["base_dmg"]`` after each fold so
    downstream readers (the damage pipeline, on-hit hooks) see the grown
    base. ``max(1, ...)`` clamp matches the original caster-scaling rider's
    safety net for negative contributions.

    Riders are skipped entirely once ``base_dmg`` drops to zero — support
    skills and 0-dmg utility casts pay no overhead.
    """
    if base_dmg <= 0:
        return base_dmg, skill_data
    for rider in _BASE_DMG_RIDERS:
        result = rider.fn(skill_data, actor, target, base_dmg, actor_mods)
        if result is None:
            continue
        bonus, log_line = result
        if bonus == 0:
            continue
        base_dmg = max(1, base_dmg + bonus)
        skill_data = {**skill_data, "base_dmg": base_dmg}
        if log_line:
            log.append(log_line)
    return base_dmg, skill_data


# ── Built-in base-dmg riders ────────────────────────────────────────────
# Same byte-identical log strings as the former inline blocks in cast_skill.


@register_base_dmg_rider()
def _mastery_base_dmg_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Skill Mastery — scale the *declared* base by the caster's mastery level.

    Registered FIRST so mastery amplifies the skill's declared base before any
    situational rider (anti-regen / HP-lost / caster-scaling) compounds on top
    (locked decision: those grow the post-mastery base).

    Provably inert by default: the feature flag is OFF, so this returns ``None``
    on the very first guard before touching mastery state — the Phase-0 golden
    guard stays byte-identical. Enemies/bosses carry empty ``skill_mastery`` →
    level 1 → ``power_mult`` 1.0 → ``None`` even when the flag is on.

    Effect-potency scaling (0-base utility skills) is Phase 3b, not here.
    """
    if not settings.skill_mastery_enabled:
        return None
    # 0-base skills get nothing on the damage path — 3b handles their potency.
    if base_dmg <= 0:
        return None
    skill_key = skill_data.get("key")
    if not skill_key:
        return None
    level = actor.skill_mastery.get(skill_key, 1)
    mult = power_mult(level)
    if mult == 1.0:
        return None
    bonus = int(round(base_dmg * (mult - 1.0)))
    if bonus <= 0:
        return None
    return (
        bonus,
        f"    📈 *Lĩnh Ngộ* (Tầng {level}) — +{bonus:,} ST nền "
        f"*{skill_data.get('vi', '')}*",
    )


@register_base_dmg_rider()
def _anti_regen_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Vô Đạo-style — punish target's per-turn HP recovery.

    Reads two spec fields under one logical block:
      * ``bonus_dmg_per_target_regen_flat`` × target.hp_regen_flat
      * ``bonus_dmg_per_target_regen_pct``  × (target.hp_max × hp_regen_pct)
    Skipped when both scalars are zero so vanilla skills pay no overhead.
    """
    flat_mult = float(skill_data.get("bonus_dmg_per_target_regen_flat", 0.0))
    pct_mult = float(skill_data.get("bonus_dmg_per_target_regen_pct", 0.0))
    if not (flat_mult or pct_mult):
        return None
    regen_flat_per_turn = max(0, int(getattr(target, "hp_regen_flat", 0)))
    regen_pct_per_turn = max(
        0, int(target.hp_max * float(getattr(target, "hp_regen_pct", 0.0)))
    )
    regen_bonus = int(
        flat_mult * regen_flat_per_turn + pct_mult * regen_pct_per_turn
    )
    if regen_bonus <= 0:
        return None
    return (
        regen_bonus,
        f"    🩸 *{skill_data['vi']}* khắc chế hồi sinh — "
        f"+{regen_bonus:,} ST cộng dồn",
    )


@register_base_dmg_rider()
def _hp_lost_flat_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Executioner-style flat HP-lost rider.

    Spec field: ``bonus_dmg_per_target_hp_lost_pct`` (float). Adds
    ``pct × (target.hp_max - target.hp)`` flat to base_dmg so the bonus
    rides through crit / element / final-bonus like the rest of the base.
    Distinct from the ``final_dmg_bonus_per_target_hp_lost_bucket`` rider
    above — that one is multiplicative; this one is additive.
    """
    hp_lost_mult = float(skill_data.get("bonus_dmg_per_target_hp_lost_pct", 0.0))
    if not hp_lost_mult:
        return None
    hp_lost = max(0, target.hp_max - target.hp)
    hp_lost_bonus = int(hp_lost * hp_lost_mult)
    if hp_lost_bonus <= 0:
        return None
    return (
        hp_lost_bonus,
        f"    🐯 *{skill_data['vi']}* uy hiếp — "
        f"+{hp_lost_bonus:,} ST ({int(hp_lost_mult * 100)}% HP đã mất)",
    )


@register_base_dmg_rider()
def _target_hp_max_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Sơn Băng Địa Liệt-style — flat % of target's hp_max as additive base_dmg.

    Distinct from the existing HP-related riders:
      * ``bonus_dmg_per_target_hp_lost_pct`` reads HP *lost* (executioner)
      * ``final_dmg_bonus_per_target_hp_lost_bucket`` reads HP *lost*
      * (Thất Sát) ``true_dmg_pct_missing_hp`` reads HP *missing*

    This one reads ``target.hp_max`` directly — a fundamental-mass scaling
    that doesn't care about the target's current state. Anti-tank tech:
    strongest against bulky enemies, naturally weaker against fragile ones,
    cap-clamped so a million-HP boss doesn't get vaporized.

    Spec fields (both live on skill_data, not under a nested dict, to match
    the other base_dmg riders' surface convention):
      * ``bonus_dmg_pct_target_hp_max``      — fraction (0.08 = 8%)
      * ``bonus_dmg_pct_target_hp_max_cap``  — hard ceiling (0 = uncapped)
    """
    pct = float(skill_data.get("bonus_dmg_pct_target_hp_max", 0.0))
    if pct <= 0 or target.hp_max <= 0:
        return None
    bonus = int(target.hp_max * pct)
    cap = int(skill_data.get("bonus_dmg_pct_target_hp_max_cap", 0))
    if cap > 0:
        bonus = min(bonus, cap)
    if bonus <= 0:
        return None
    return (
        bonus,
        f"    🏔️ *Sơn Băng* — +{bonus:,} ST gốc "
        f"({int(pct * 100)}% HP tối đa địch)",
    )


@register_base_dmg_rider()
def _kim_devour_armor_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Kim Phệ Giáp — "devour" the target's armor into additive base_dmg.

    Anti-armor / anti-shield Kim tech: the harder the target turtles, the
    harder the strike lands. Reads the target's CURRENT def_stat and shield
    (a fundamental-mass scaling like ``_target_hp_max_rider`` — situational
    on the target's defensive state, not its HP).

    Spec fields (flat on skill_data, matching the other base_dmg riders'
    surface convention):
      * ``bonus_dmg_per_target_def``        — ST per point of def_stat
      * ``bonus_dmg_per_target_shield_pct`` — fraction of current shield
      * ``bonus_dmg_cap``                   — hard ceiling (0 = uncapped)

    No-op when neither scalar is set so vanilla skills pay no overhead.
    """
    def_mult = float(skill_data.get("bonus_dmg_per_target_def", 0.0))
    shield_mult = float(skill_data.get("bonus_dmg_per_target_shield_pct", 0.0))
    if not (def_mult or shield_mult):
        return None
    def_part = max(0, int(target.def_stat)) * def_mult
    shield_part = max(0, int(getattr(target, "shield", 0))) * shield_mult
    bonus = int(def_part + shield_part)
    cap = int(skill_data.get("bonus_dmg_cap", 0))
    if cap > 0:
        bonus = min(bonus, cap)
    if bonus <= 0:
        return None
    return (
        bonus,
        f"    🦷 *Phệ Giáp* — +{bonus:,} ST gốc "
        f"(nuốt giáp + khiên địch)",
    )


@register_base_dmg_rider()
def _hau_tho_tank_conversion_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Hậu Thổ Phong Ma Trận — convert caster's tank stats into base_dmg.

    Reads each lane pct from TWO sources, summed:

      * **Active-effect aggregation** via ``actor_mods`` — the buff
        ``BuffHauThoPhongMa`` stamps base 1% lanes; any future buff wiring
        these keys feeds the same lane (source-agnostic).
      * **Permanent actor field** via ``getattr(actor, key)`` — the
        formation gem ladder folds bonuses into the Combatant's named
        fields, so progressing the formation directly amps the lanes
        without depending on the buff being applied.

    Lane keys (both surfaces):
      * ``bonus_base_dmg_per_self_hp_pct``     — × actor.hp_max
      * ``bonus_base_dmg_per_self_shield_pct`` — × actor.shield
      * ``bonus_base_dmg_per_self_def_pct``    — × actor.def_stat

    Distinct from ``_target_hp_max_rider``: that one taxes the enemy's mass;
    this one rewards the caster's own bulk. Naturally couples with Tho
    formation reservation (HP boost) and stacking shield/def buffs.
    """
    hp_pct = float(actor_mods.get("bonus_base_dmg_per_self_hp_pct", 0.0)) \
        + float(getattr(actor, "bonus_base_dmg_per_self_hp_pct", 0.0))
    shield_pct = float(actor_mods.get("bonus_base_dmg_per_self_shield_pct", 0.0)) \
        + float(getattr(actor, "bonus_base_dmg_per_self_shield_pct", 0.0))
    def_pct = float(actor_mods.get("bonus_base_dmg_per_self_def_pct", 0.0)) \
        + float(getattr(actor, "bonus_base_dmg_per_self_def_pct", 0.0))
    if hp_pct <= 0 and shield_pct <= 0 and def_pct <= 0:
        return None
    hp_part = int(max(0, actor.hp_max) * hp_pct)
    shield_part = int(max(0, getattr(actor, "shield", 0)) * shield_pct)
    def_part = int(max(0, getattr(actor, "def_stat", 0)) * def_pct)
    bonus = hp_part + shield_part + def_part
    if bonus <= 0:
        return None
    parts: list[str] = []
    if hp_part > 0:
        parts.append(f"{hp_part:,} HP")
    if shield_part > 0:
        parts.append(f"{shield_part:,} Khiên")
    if def_part > 0:
        parts.append(f"{def_part:,} Phòng")
    breakdown = " + ".join(parts)
    return (
        bonus,
        f"    🗿 *Hậu Thổ trấn hồn* — +{bonus:,} ST nền ({breakdown})",
    )


@register_base_dmg_rider()
def _combo_counter_base_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Truy Kích Liên Vũ (Phong B1) — additive base slice of the self-combo.

    Companion to ``_combo_counter_fdb_rider`` (final-dmg slice). Reads the
    same persistent ``<stack_field>`` counter — already incremented for THIS
    cast in the cast path and clamped to ``combo_cap`` — and adds
    ``per_consecutive_base_dmg × n`` to base_dmg so the combo bonus rides
    crit / element / final-bonus like the declared base. The final-dmg
    percent is logged by the fdb rider; this rider logs only the base slice.

    Inert for enemies (``phong_combo`` stays 0) and for any skill lacking the
    ``combo_counter_scaling`` block.
    """
    spec = skill_data.get("combo_counter_scaling")
    if not isinstance(spec, dict):
        return None
    field = spec.get("stack_field")
    per_base = int(spec.get("per_consecutive_base_dmg", 0))
    if not field or per_base <= 0:
        return None
    cap = int(spec.get("combo_cap", 0))
    n = int(getattr(actor, field, 0))
    if cap > 0:
        n = min(n, cap)
    if n <= 0:
        return None
    bonus = per_base * n
    return (
        bonus,
        f"    🌀 *Liên Vũ* — chuỗi ×{n} (+{bonus:,} ST nền)",
    )


@register_base_dmg_rider()
def _caster_scaling_rider(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    base_dmg: int, actor_mods: dict,
) -> Optional[tuple[int, str]]:
    """Thái Ất Kim Canh-style — this skill scales off the caster's stats.

    Spec field ``base_dmg_caster_scaling`` is a dict with optional sub-fields:
      * ``spd``                  — flat × actor.spd
      * ``evasion_rating``       — flat × actor.evasion_rating
      * ``element_dmg_bonus_pct``— {element, per_pct}: per percentage point
            of caster's permanent element_dmg_bonus + active-buff
            dmg_bonus_<element> (read from actor_mods).

    Absent sub-fields contribute nothing — a skill can opt into any subset.
    The combined ``stat_bonus`` may be negative; the dispatcher clamps
    base_dmg to ``max(1, ...)``.
    """
    scaling = skill_data.get("base_dmg_caster_scaling")
    if not scaling:
        return None
    spd_scale = float(scaling.get("spd", 0.0))
    eva_scale = float(scaling.get("evasion_rating", 0.0))
    elem_scale = scaling.get("element_dmg_bonus_pct")
    stat_bonus = 0
    breakdown: list[str] = []
    if spd_scale:
        inc = int(spd_scale * actor.spd)
        if inc:
            stat_bonus += inc
            breakdown.append(f"TĐ:{inc:+,}")
    if eva_scale:
        inc = int(eva_scale * actor.evasion_rating)
        if inc:
            stat_bonus += inc
            breakdown.append(f"NT:{inc:+,}")
    if elem_scale:
        _elem = elem_scale.get("element")
        _per_pct = float(elem_scale.get("per_pct", 0.0))
        if _elem and _per_pct:
            _frac = float(actor.element_dmg_bonus.get(_elem, 0.0))
            _frac += float(actor_mods.get(f"dmg_bonus_{_elem}", 0.0))
            inc = int(_per_pct * _frac * 100)
            if inc:
                stat_bonus += inc
                breakdown.append(f"{_elem.title()}:{inc:+,}")
    if stat_bonus == 0:
        return None
    return (
        stat_bonus,
        f"    🌟 *{skill_data['vi']}* — ST nền {stat_bonus:+,} "
        f"({' '.join(breakdown)})",
    )


# ═══════════════════════════════════════════════════════════════════════
# In-damage multiplier rider registry
# ═══════════════════════════════════════════════════════════════════════
#
# These riders fire *inside* the damage block, after the pipeline has
# already computed ``dmg`` and before it lands on the target. Each rider
# inspects skill_data + target state, returns a multiplier (the amp
# fraction, e.g. 0.40 = +40%), and the dispatcher folds it into ``dmg``
# via ``dmg = max(1, int(dmg * (1.0 + amp)))`` — same math as the original
# inline blocks. Differs from the other two registries:
#   * ``final_dmg_bonus`` riders — multiplicative, but fold into actor.stats
#     pre-pipeline (so element/crit math sees the bonus).
#   * ``base_dmg`` riders — additive, mutate the input base integer.
#   * ``in_dmg`` riders (this one) — multiplicative, mutate the already-
#     computed final integer. Fires AFTER mitigation so the displayed dmg
#     already reflects the boost. Useful for "hits harder vs a state"
#     gates that don't want to feed crit/element math.

InDmgRiderFn = Callable[
    [Any, "Combatant", "Combatant", int],
    Optional[tuple[float, Optional[str]]],
]


@dataclass(frozen=True)
class InDmgRider:
    """One registered in-damage rider — keyed on its ``skill_data`` field."""
    skill_field: str
    fn: InDmgRiderFn


_IN_DMG_RIDERS: list[InDmgRider] = []


def register_in_dmg_rider(
    skill_field: str,
) -> Callable[[InDmgRiderFn], InDmgRiderFn]:
    """Decorator — register ``fn`` as the in-damage rider for ``skill_field``.

    The dispatcher only invokes the rider when ``skill_data[skill_field]``
    is truthy; rider only needs to compute the amp + log.
    """
    def deco(fn: InDmgRiderFn) -> InDmgRiderFn:
        _IN_DMG_RIDERS.append(InDmgRider(skill_field=skill_field, fn=fn))
        return fn
    return deco


def apply_in_dmg_riders(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    dmg: int, log: list[str],
) -> int:
    """Walk every in-damage rider; return the (possibly amped) final ``dmg``.

    Riders run in registration order. Each rider returns ``(amp, log_line)``
    where ``log_line`` may be ``None`` (some legacy riders intentionally
    don't log — e.g. the original ``bonus_dmg_vs_shielded`` and
    ``bonus_dmg_vs_hp_below`` were silent). The dispatcher applies the
    multiplier and only appends a log line when the rider supplied one.

    Skipped entirely when ``dmg <= 0`` — vanilla skills and 0-damage
    casts pay no overhead.
    """
    if dmg <= 0:
        return dmg
    for rider in _IN_DMG_RIDERS:
        spec = skill_data.get(rider.skill_field)
        if not spec:
            continue
        result = rider.fn(spec, actor, target, dmg)
        if result is None:
            continue
        amp, log_line = result
        if amp <= 0:
            continue
        dmg = max(1, int(dmg * (1.0 + amp)))
        if log_line:
            log.append(log_line)
    return dmg


# ── Built-in in-damage riders ───────────────────────────────────────────
# Byte-identical log strings to the former inline blocks. ``None`` in
# the log slot preserves originals that intentionally fired silently.


@register_in_dmg_rider("bonus_dmg_vs_shielded")
def _shielded_rider(
    spec: Any, actor: "Combatant", target: "Combatant", dmg: int,
) -> Optional[tuple[float, Optional[str]]]:
    """Shield-piercer amp — extra damage when target carries a shield.

    Spec is a flat float (the amp fraction). Original block fired silently
    so this rider returns ``log_line=None``; the dispatcher honors that
    and skips the log append.
    """
    amp = float(spec or 0.0)
    if amp <= 0 or target.shield <= 0:
        return None
    return (amp, None)


@register_in_dmg_rider("bonus_dmg_vs_hp_above")
def _hp_above_rider(
    spec: Any, actor: "Combatant", target: "Combatant", dmg: int,
) -> Optional[tuple[float, Optional[str]]]:
    """Phá Phủ Trầm Chu style — hits harder while target is fresh.

    Spec shape:
        {"hp_pct": 0.70, "dmg_bonus": 0.40}
    → +40% dmg while target HP fraction is *above* 70%.
    """
    if target.hp_max <= 0:
        return None
    threshold = float(spec.get("hp_pct", 1.0))
    amp = float(spec.get("dmg_bonus", 0.0))
    if amp <= 0:
        return None
    if (target.hp / target.hp_max) <= threshold:
        return None
    return (
        amp,
        f"    🪓 *Phá Phủ* — +{int(amp * 100)}% ST "
        f"(HP địch > {int(threshold * 100)}%)",
    )


@register_in_dmg_rider("bonus_dmg_vs_hp_below")
def _hp_below_rider(
    spec: Any, actor: "Combatant", target: "Combatant", dmg: int,
) -> Optional[tuple[float, Optional[str]]]:
    """Executioner-style — hits harder once target is bloodied.

    Spec shape:
        {"hp_pct": 0.30, "dmg_bonus": 0.40}
    → +40% dmg while target HP fraction is *below* 30%. Original block
    fired silently (no log line) — preserved here.
    """
    if target.hp_max <= 0:
        return None
    threshold = float(spec.get("hp_pct", 0.0))
    amp = float(spec.get("dmg_bonus", 0.0))
    if amp <= 0:
        return None
    if (target.hp / target.hp_max) >= threshold:
        return None
    return (amp, None)


@register_in_dmg_rider("bonus_dmg_per_fire_dot_pct")
def _fire_dot_amp_rider(
    spec: Any, actor: "Combatant", target: "Combatant", dmg: int,
) -> Optional[tuple[float, Optional[str]]]:
    """Liệt Diễm Phần Thiên Chưởng — amp per distinct fire DoT on target.

    Counts via ``count_elemental_dots`` so the gate matches the DoT
    pipeline — markers like Hỏa Xuyên Thấu / Hồng Liên don't count, only
    fire effects that actually tick.
    """
    fire_dot_amp = float(spec or 0.0)
    if fire_dot_amp <= 0:
        return None
    from src.game.engine.effects import count_elemental_dots
    fire_dot_count = count_elemental_dots(target, "hoa")
    if fire_dot_count <= 0:
        return None
    amp = fire_dot_amp * fire_dot_count
    return (
        amp,
        f"    🔥 *Liệt Diễm* — +{int(amp * 100)}% ST "
        f"({fire_dot_count} loại Hỏa DoT)",
    )
