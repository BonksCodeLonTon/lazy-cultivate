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
from src.game.engine.effects import EffectKind, get_combat_modifiers

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
    if not (target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc")):
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


def _is_vo_cau_cc(effect_key: str, meta: "EffectMeta") -> bool:
    """Shared CC classifier for the two Vô Cấu Lưu Ly gates.

    CC = turn-skip / skill-lock metas (freeze, knock-up, silence, interrupt)
    plus the two chance/soft controls the design names explicitly (Tê Liệt's
    per-turn skip chance, Làm Chậm). The two interceptors below split the
    negative-effect space on this predicate so a CC never rolls twice.
    """
    return bool(
        meta.skips_turn
        or meta.prevents_skills
        or effect_key in (EffectKey.DEBUFF_TE_LIET, EffectKey.DEBUFF_LAM_CHAM)
    )


def _bank_vo_cau_stack(target: "Combatant") -> str:
    """Bank +1 Vô Cấu on a successful block; returns the log suffix.

    The cap is only set by the L9 milestone, so pre-L9 blocks bank nothing.
    """
    if target.vo_cau_cap > 0 and target.vo_cau_stacks < target.vo_cau_cap:
        target.vo_cau_stacks += 1
        return f" [Vô Cấu ×{target.vo_cau_stacks}/{target.vo_cau_cap}]"
    return ""


@register_pre_stamp_interceptor
def _minh_tam_cc_resist(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """Vô Cấu Lưu Ly L1 (Minh Tâm Kiến Tánh) — ``vc_cc_resist_pct`` chance
    (75%) to shrug every CC class EXCEPT stun.

    ``CCStun`` deliberately bypasses — the body's "not yet fully
    transcendent" flaw. CC is exclusively THIS gate's lane: the L9 shrug
    below skips CC entirely so the resist chance stays exactly the authored
    75% (no double-roll). At L9, successful CC blocks also bank Vô Cấu.
    """
    if target.vc_cc_resist_pct <= 0:
        return False
    if effect_key == EffectKey.CC_STUN:
        return False
    if meta.kind is EffectKind.BUFF:
        return False
    if not _is_vo_cau_cc(effect_key, meta):
        return False
    if session.rng.random() >= target.vc_cc_resist_pct:
        return False
    session.log.append(
        f"    💠 **{target.name}** Minh Tâm Kiến Tánh — **{meta.vi}** "
        f"vô hiệu!{_bank_vo_cau_stack(target)}"
    )
    return True


@register_pre_stamp_interceptor
def _van_phap_bat_triem(
    session: "CombatSession", effect_key: str, meta: "EffectMeta",
    target: "Combatant", actor: "Combatant | None",
    overrides: "dict | None",
) -> bool:
    """Vô Cấu Lưu Ly L9 (Vạn Pháp Bất Triêm) — chance-shrug every NON-CC
    negative effect (DoTs, shreds, marks); stun bypasses.

    NOT an absolute immunity (per the no-total-immunity design rule — the
    #24 Tiêu Dao precedent): ``vc_bat_triem_immune_pct`` rolls per
    application. CC is excluded — that's the L1 gate's lane, so a failed
    L1 resist means the CC lands (75% flat, no second roll here). Each
    successful block tempers the lapis body: +1 Vô Cấu stack (cap
    ``vo_cau_cap``) which BuffVanPhapBatTriem's scaling rule turns into
    +magic_reflect_pct — "the more they target it, the more spotless it
    becomes".
    """
    if target.vc_bat_triem_immune_pct <= 0:
        return False
    if effect_key == EffectKey.CC_STUN:
        return False
    if meta.kind is EffectKind.BUFF:
        return False
    if _is_vo_cau_cc(effect_key, meta):
        return False
    if session.rng.random() >= target.vc_bat_triem_immune_pct:
        return False
    session.log.append(
        f"    🪷 **{target.name}** Vạn Pháp Bất Triêm — "
        f"**{meta.vi}** không nhuốm!{_bank_vo_cau_stack(target)}"
    )
    return True
