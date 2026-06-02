"""Pre-stamp interceptor registry for ``inflict_debuff``.

Before this module, ``inflict_debuff`` opened with a chain of five
hand-coded "early-return" guards that decided whether to abort the
stamp: poison immunity, Vô Tướng Phong charge-absorber, hard-CC immunity,
generic ``effect_resist`` RNG roll, and Tuyệt Diệu Vô Ảnh slow immunity.
Each guard had its own condition, its own side effects (log lines, charge
decrement, buff override mutation), and its own bail-out. Adding a sixth
guard meant another 10-line block at the top of ``inflict_debuff``.

The registry inverts that flow. Each guard is a function decorated with
``@register_pre_stamp_interceptor``. The dispatcher walks the registry in
registration order; the first handler to return ``True`` aborts the
stamp. Handlers own ALL their side effects (logs, charge bookkeeping,
buff strip on charge exhaustion) — the dispatcher's only job is routing
and short-circuit on the first abort.

### Why ``bool`` instead of an ``Abort | Continue`` sum type?

Some guards aren't pure "abort if X" — Vô Tướng Phong mutates the buff's
override dict, decrements the charge counter, and may pop the buff
entirely before returning. An ``Abort(log_lines)`` sum type can't carry
that side-effecting freedom; a plain ``bool`` plus direct mutation does.
Each interceptor decides what state to touch; the dispatcher only cares
whether to stop processing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from src.game.constants.effects import EffectKey
from src.game.engine.effects import get_combat_modifiers

if TYPE_CHECKING:
    from src.game.engine.effects import EffectMeta
    from src.game.systems.combatant import Combatant

    from .session import CombatSession


# Interceptor signature: same args as ``inflict_debuff``. Returns True to
# abort the stamp (caller bails out immediately), False to continue down
# the chain.
InterceptorFn = Callable[
    ["CombatSession", str, "EffectMeta", "Combatant", "Combatant | None",
     "dict | None"],
    bool,
]


_PRE_STAMP_INTERCEPTORS: list[InterceptorFn] = []


def register_pre_stamp_interceptor(fn: InterceptorFn) -> InterceptorFn:
    """Decorator — append ``fn`` to the pre-stamp interceptor chain.

    Order matters: the dispatcher walks in registration order and the
    first ``True`` return short-circuits. Register cheap/common gates
    (immunity flags) before expensive ones (RNG rolls / mod aggregation).
    """
    _PRE_STAMP_INTERCEPTORS.append(fn)
    return fn


def dispatch_pre_stamp_interceptors(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """Walk registered interceptors; return True if any aborted the stamp.

    Each interceptor inspects the cast context and may perform side
    effects (log appends, charge decrements, buff strips) before
    deciding. The caller should ``return`` immediately when this
    returns True.
    """
    for fn in _PRE_STAMP_INTERCEPTORS:
        if fn(session, effect_key, meta, target, actor, overrides):
            return True
    return False


# ── Built-in interceptors ──────────────────────────────────────────────
# Order is the same as the legacy if-chain in inflict_debuff so anyone
# reading diffs side-by-side can verify nothing was reordered.


@register_pre_stamp_interceptor
def _poison_immunity(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """Hard immunity to ``DebuffDocTo`` via ``target.poison_immunity`` flag."""
    if effect_key != EffectKey.DEBUFF_DOC_TO or not target.poison_immunity:
        return False
    session.log.append(f"    💚 **{target.name}** miễn dịch Độc Tố!")
    return True


@register_pre_stamp_interceptor
def _vo_tuong_phong_absorber(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """Vô Tướng Phong — burn one charge to negate a non-DoT debuff/CC.

    Each consumed charge bumps the buff's ``spd_pct`` override by the
    declared ``spd_per_consume`` so the holder snowballs faster the more
    attempts they shrug off. DoT-class effects (burn / bleed / poison /
    shock ticks, and any per-cast override that carries ``dot_pct``)
    bypass this gate so the precious charges aren't burned on chip
    damage. Stack-amp debuffs that scale via ``scaling_rules`` rather
    than per-stack damage (e.g. DebuffPhongNhanThuc) still consume
    charges normally.

    Buff is popped immediately when charges hit 0; natural duration
    expiry is handled by ``tick_effects`` on its normal cycle.
    """
    is_dot_like = (
        meta.dot_pct > 0
        or meta.per_stack_pct > 0
        or float((overrides or {}).get("dot_pct", 0.0)) > 0
    )
    if is_dot_like:
        return False
    if target.vo_tuong_phong_charges <= 0:
        return False
    if not target.has_effect("BuffVoTuongPhong"):
        return False
    target.vo_tuong_phong_charges -= 1
    ovr = target.effect_overrides.setdefault("BuffVoTuongPhong", {})
    sb = ovr.setdefault("stat_bonus", {})
    spd_growth = float(ovr.get("spd_per_consume", 0.0))
    if spd_growth > 0:
        sb["spd_pct"] = float(sb.get("spd_pct", 0.0)) + spd_growth
    session.log.append(
        f"    🌬️ **{target.name}** **Vô Tướng Phong** thổi tan "
        f"**{meta.vi}** ({target.vo_tuong_phong_charges} lần còn lại"
        + (f", +{int(spd_growth * 100)}% Tốc Độ" if spd_growth > 0 else "")
        + ")"
    )
    if target.vo_tuong_phong_charges <= 0:
        target.effects.pop("BuffVoTuongPhong", None)
        target.effect_overrides.pop("BuffVoTuongPhong", None)
        session.log.append(
            f"    🌬️ **{target.name}** Vô Tướng Phong tan biến."
        )
    return True


@register_pre_stamp_interceptor
def _immune_hard_cc(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """World bosses + ``immune_hard_cc`` combatants shrug off hard CC.

    Hard CC = anything that skips a turn or prevents skill use (stun,
    freeze, silence, interrupt, knock-up). Soft debuffs (slow, armor
    shred, res shred, etc.) still apply normally.
    """
    if not target.immune_hard_cc:
        return False
    if not (meta.skips_turn or meta.prevents_skills):
        return False
    session.log.append(
        f"    🛡️ **{target.name}** miễn dịch khống chế — **{meta.vi}** vô hiệu!"
    )
    return True


@register_pre_stamp_interceptor
def _effect_resist(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """Generic per-effect resist via ``effect_resist:<key>`` stat aggregation.

    Any active buff/passive can carry a ``stat_bonus`` entry keyed
    ``effect_resist:<effect_key>`` with a 0-1 chance to shrug off that
    specific application. Aggregates via ``get_combat_modifiers`` so
    multiple sources stack additively; capped at 1.0 below so it can
    never be guaranteed (some RNG must remain). Lets defensive auras
    like Cửu Dương Hộ Thể declare "90% freeze resist" without bloating
    Combatant with a dedicated field per resisted effect.
    """
    resist_key = f"effect_resist:{effect_key}"
    resist_chance = min(1.0, max(
        0.0, float(get_combat_modifiers(target).get(resist_key, 0.0))
    ))
    if resist_chance <= 0:
        return False
    if session.rng.random() >= resist_chance:
        return False
    session.log.append(
        f"    🛡️ **{target.name}** kháng **{meta.vi}** "
        f"({int(resist_chance * 100)}% kháng)"
    )
    return True


@register_pre_stamp_interceptor
def _slow_immune_check(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """Generic slow-immunity gate — aborts any incoming negative ``spd_pct``
    application when the target carries an active effect that declares
    ``slow_immune`` in its ``stat_bonus`` (meta-level or per-instance
    override).

    Catches DebuffLamCham, DebuffTroBuoc, DebuffLunDat, EffectNgungDong,
    and any future custom slow stamped via overrides. Source-agnostic so
    multiple immunity-granting buffs (Tuyệt Diệu Vô Ảnh on Phong, Thạch
    Ảnh Mê Tung on Tho, future skills on any element) all flow through
    one gate without per-buff hardcoding.
    """
    # First gate: the incoming effect must actually be a slow (negative
    # spd_pct after override merge). Resist of non-slow debuffs goes
    # through other interceptors.
    ovr_sb = (overrides or {}).get("stat_bonus") or {}
    spd_delta = float(ovr_sb.get("spd_pct", meta.stat_bonus.get("spd_pct", 0.0)))
    if spd_delta >= 0:
        return False
    # Second gate: target must carry SOME active effect declaring
    # slow_immune. Per-instance override stat_bonus wins over meta — same
    # precedence rule as everywhere else in the engine.
    from src.game.engine.effects import EFFECTS

    immune_vi: str | None = None
    for key in target.effects:
        immune_meta = EFFECTS.get(key)
        if immune_meta is None:
            continue
        ovr_imm = (target.effect_overrides.get(key) or {}).get("stat_bonus") or {}
        # Override wins if present; otherwise fall back to meta default.
        if ovr_imm.get("slow_immune") is not None:
            if float(ovr_imm["slow_immune"]) > 0:
                immune_vi = immune_meta.vi
                break
            continue
        if float(immune_meta.stat_bonus.get("slow_immune", 0)) > 0:
            immune_vi = immune_meta.vi
            break
    if immune_vi is None:
        return False
    session.log.append(
        f"    👣 **{target.name}** trong {immune_vi} — "
        f"**{meta.vi}** tan biến!"
    )
    return True
