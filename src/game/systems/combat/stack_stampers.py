"""Stack-stamping registry for ``inflict_debuff``.

When a stack-based debuff lands on a target, ``inflict_debuff`` used to
carry a long if-chain of ``if effect_key == EffectKey.DEBUFF_X:`` branches,
each one running the same three-step routine: optionally propagate the
attacker's build flags (so DoT ticks honor the attacker's per-stack
amps), bump the stack counter, and log a "[×count/cap] (Nt)" line. The
list grew to 13 branches plus 3 specials before extraction — every new
stacking debuff was another copy-paste.

This module inverts that flow. Each stack-stamping behavior is a small
handler registered against its ``EffectKey``. The dispatcher
``dispatch_stack_stamp`` consults the registry and returns ``True`` if
a handler fired (so the caller can early-return). Adding a new simple
stacker is one ``register_simple_stamp(...)`` call; specials register
a custom handler via ``@register_stack_stamper(effect_key)``.

The simple-stamper template covers three knobs that describe every
non-special branch in today's code:
  * ``stack_kind``       — the ``_STACK_EFFECT_KEY`` key (also the
                           Combatant ``<name>_stacks`` attribute prefix)
  * ``propagate_build``  — call ``_propagate_stack_build(actor, target,
                           kind)`` before incrementing? Required for any
                           stack whose DoT ticks read attacker-side flags
                           (burn / bleed / shock / poison / chan_hoa /
                           nghiep_hoa); off for pure target-state stacks
                           (phong_nhan_thuc, tran_son_ha, u_minh, hoa_van).
  * ``include_cap``      — show ``/cap`` in the log? On for everything
                           except Nghiệp Hỏa, whose cap (99) is too high
                           to be meaningful in the log line.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from src.game.constants.effects import EffectKey
from src.game.engine.effects import effective_stack_cap

from .helpers import _propagate_stack_build

if TYPE_CHECKING:
    from src.game.engine.effects import EffectMeta
    from src.game.systems.combatant import Combatant

    from .session import CombatSession


# Stamper signature: (session, actor, target, meta, dur, overrides) → None.
# All side effects (stack bump, log append, hp_max edit, …) happen inside
# the handler. The dispatcher's only job is routing.
StackStamperFn = Callable[
    ["CombatSession", "Combatant | None", "Combatant",
     "EffectMeta", int, "dict | None"],
    None,
]


_STACK_STAMPERS: dict[str, StackStamperFn] = {}


def register_stack_stamper(
    effect_key: EffectKey,
) -> Callable[[StackStamperFn], StackStamperFn]:
    """Decorator — register ``fn`` as the stamper for ``effect_key``.

    Use for specials (Phượng Hỏa per-stack seed, Cửu Khúc actor snapshot,
    Nhược Thủy Ấn detonation) that don't fit the simple template. For
    everything else, call ``register_simple_stamp`` instead — same
    semantics, one line.
    """
    def deco(fn: StackStamperFn) -> StackStamperFn:
        _STACK_STAMPERS[effect_key.value] = fn
        return fn
    return deco


def register_simple_stamp(
    effect_key: EffectKey,
    stack_kind: str,
    *,
    propagate_build: bool = True,
    include_cap: bool = True,
) -> None:
    """Register the common stamp pattern: optional propagate + add_stack + log.

    See module docstring for what each knob controls. The handler reads
    the live stack count via ``getattr(target, f"{stack_kind}_stacks", 0)``
    which matches the ``_STACK_EFFECT_KEY`` convention in ``combatant.py``.
    """
    def handler(
        session: "CombatSession", actor: "Combatant | None",
        target: "Combatant", meta: "EffectMeta", dur: int,
        overrides: "dict | None",
    ) -> None:
        if propagate_build and actor is not None:
            _propagate_stack_build(actor, target, stack_kind)
        target.add_stack(stack_kind, 1)
        stack_count = getattr(target, f"{stack_kind}_stacks", 0)
        if include_cap:
            cap = effective_stack_cap(target, effect_key.value)
            session.log.append(
                f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
                f"[×{stack_count}/{cap}] ({dur}t)"
            )
        else:
            session.log.append(
                f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
                f"[×{stack_count}] ({dur}t)"
            )

    _STACK_STAMPERS[effect_key.value] = handler


def dispatch_stack_stamp(
    session: "CombatSession", effect_key: str, actor: "Combatant | None",
    target: "Combatant", meta: "EffectMeta", dur: int,
    overrides: "dict | None",
) -> bool:
    """Look up the stamper for ``effect_key``; run it if present.

    Returns ``True`` when a stamper fired (caller should ``return`` so the
    generic DoT-propagate-and-log tail in ``inflict_debuff`` doesn't also
    run); ``False`` when ``effect_key`` has no registered stamper (caller
    falls through to the generic tail).
    """
    handler = _STACK_STAMPERS.get(effect_key)
    if handler is None:
        return False
    handler(session, actor, target, meta, dur, overrides)
    return True


# ─── Simple stampers (registered via the template factory) ─────────────
# Order mirrors the original if-chain in inflict_debuff so anyone reading
# diffs side-by-side can verify nothing was reordered semantically.

# Burn (Hỏa playstyle cornerstone)
register_simple_stamp(EffectKey.DEBUFF_THIEU_DOT, "burn")
# Wind-Blade Erosion — pure target-state, no DoT propagation
register_simple_stamp(EffectKey.DEBUFF_PHONG_NHAN_THUC, "phong_nhan_thuc",
                      propagate_build=False)
# Mountain-River Suppression Seal — pure target-state, no DoT propagation
register_simple_stamp(EffectKey.DEBUFF_TRAN_SON_HA, "tran_son_ha",
                      propagate_build=False)
# Tam Muội Chân Hỏa — burn-shape, amps fire DoTs on holder
register_simple_stamp(EffectKey.DEBUFF_CHAN_HOA, "chan_hoa")
# Nghiệp Hỏa — burn-shape but cap (99) too high to log meaningfully
register_simple_stamp(EffectKey.DEBUFF_NGHIEP_HOA, "nghiep_hoa",
                      include_cap=False)
# U Minh — MP-burn DoT, pure target-state
register_simple_stamp(EffectKey.DEBUFF_U_MINH, "u_minh",
                      propagate_build=False)
# Hỏa Vân — combo marker for the auto-cast finisher
register_simple_stamp(EffectKey.DEBUFF_HOA_VAN, "hoa_van",
                      propagate_build=False)
# Bleed (Kim playstyle mirror)
register_simple_stamp(EffectKey.DEBUFF_CHAY_MAU, "bleed")
# Shock (Lôi playstyle mirror)
register_simple_stamp(EffectKey.DEBUFF_SOC_DIEN, "shock")
# Poison (Mộc / Âm playstyle mirror)
register_simple_stamp(EffectKey.DEBUFF_DOC_TO, "poison")


# ─── Special stampers (custom handlers) ────────────────────────────────


@register_stack_stamper(EffectKey.DEBUFF_PHUONG_HOA)
def _stamp_phuong_hoa(
    session: "CombatSession", actor: "Combatant | None",
    target: "Combatant", meta: "EffectMeta", dur: int,
    overrides: "dict | None",
) -> None:
    """Phượng Hỏa — fire DoT + per-stack heal-reduction.

    Reflective: normally applied by ``apply_reactive_damage`` when the
    defender owns ``BuffPhuongHoangChanHoa``; direct applies (testing /
    future skills) still flow through this branch.

    Seeds the holder's ``phuong_hoa_per_stack_pct`` field (a Combatant
    seed read by the DoT pipeline in dot.py) from the per-cast override
    or the meta default — whichever is larger. A lower-magnitude refresh
    can never downgrade the seed.
    """
    ovr = overrides or {}
    pct_seed = float(
        ovr.get("per_stack_pct", 0.0) or meta.per_stack_pct or 0.0
    )
    if pct_seed > target.phuong_hoa_per_stack_pct:
        target.phuong_hoa_per_stack_pct = pct_seed
    target.add_stack("phuong_hoa", 1)
    cap = effective_stack_cap(target, EffectKey.DEBUFF_PHUONG_HOA.value)
    session.log.append(
        f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
        f"[×{target.phuong_hoa_stacks}/{cap}] ({dur}t)"
    )


@register_stack_stamper(EffectKey.DEBUFF_CUU_KHUC)
def _stamp_cuu_khuc(
    session: "CombatSession", actor: "Combatant | None",
    target: "Combatant", meta: "EffectMeta", dur: int,
    overrides: "dict | None",
) -> None:
    """Cửu Khúc Hoàng Hà — formation auto-stamp.

    Increments by ``per_hit`` (from the applier's ``cuu_khuc`` config dict,
    default 1) and snapshots the applier's tier magnitudes onto the holder
    so the debuff's strength tracks the formation that applied it — not
    the holder's own (which would be zero on a fresh enemy). Snapshots
    take the strongest seen so a lower-tier reapply can't downgrade.
    """
    actor_ck = (getattr(actor, "cuu_khuc", None) or {}) if actor else {}
    per_hit = max(1, int(actor_ck.get("per_hit", 1)))
    target.add_stack("cuu_khuc", per_hit)
    if actor is not None:
        target.cuu_khuc_atk_reduce_active = max(
            target.cuu_khuc_atk_reduce_active,
            float(actor_ck.get("atk_reduce_pct", 0.0)),
        )
        target.cuu_khuc_res_shred_active = max(
            target.cuu_khuc_res_shred_active,
            float(actor_ck.get("res_shred_pct", 0.0)),
        )
    cap = effective_stack_cap(target, EffectKey.DEBUFF_CUU_KHUC.value)
    session.log.append(
        f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
        f"[×{target.cuu_khuc_stacks}/{cap}] ({dur}t)"
    )


@register_stack_stamper(EffectKey.DEBUFF_LOI_KIEP_AN)
def _stamp_loi_kiep_an(
    session: "CombatSession", actor: "Combatant | None",
    target: "Combatant", meta: "EffectMeta", dur: int,
    overrides: "dict | None",
) -> None:
    """Vạn Kiếp Lôi Ngục Trận — per-formation-tick prison-tax stamp.

    Always lands at least 1 mark per tick. The applier's ``loi_kiep_an``
    config dict (populated from formation gem bonuses in CombatStats) can
    carry ``per_cast_bonus`` to push more marks per tick at higher gem
    tiers (3-gem +1, 10-gem +1 again — additive via _merge_bonus_dict).

    No per-target snapshot needed: the per-stack ``dmg_taken_bonus_loi`` /
    ``res_loi`` expansion lives entirely in the debuff meta's
    ``scaling_rules`` and reads ``stack:loi_kiep_an`` directly, so all
    stacking arithmetic happens on the holder side.

    The milestone-bolt / 10-stack capstone checks fire from the formation
    cast site (``formation_prison.process_loi_kiep_an_tick``), not from
    here — keeps the stamper free of side-effect casts and ordering coupling.
    """
    actor_vk = (getattr(actor, "loi_kiep_an", None) or {}) if actor else {}
    per_cast = 1 + max(0, int(actor_vk.get("per_cast_bonus", 0)))
    target.add_stack("loi_kiep_an", per_cast)
    cap = effective_stack_cap(target, EffectKey.DEBUFF_LOI_KIEP_AN.value)
    session.log.append(
        f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
        f"[×{target.loi_kiep_an_stacks}/{cap}] ({dur}t)"
    )


@register_stack_stamper(EffectKey.DEBUFF_NHUOC_THUY_AN)
def _stamp_nhuoc_thuy_an(
    session: "CombatSession", actor: "Combatant | None",
    target: "Combatant", meta: "EffectMeta", dur: int,
    overrides: "dict | None",
) -> None:
    """Nhược Thủy Ấn — stacking mark that detonates at cap.

    Each application adds 1 stack (per-stack -res_thuy expanded by
    ``get_combat_modifiers``). When the cap is reached the mark detonates:
    damage = ``thuy_mark_detonate_lost_hp_pct × (hp_max - hp)`` of the
    target, then the counter resets to 0 and the debuff entry is stripped
    so the next cast starts a fresh build-up.
    """
    target.add_stack("thuy_mark", 1)
    cap = effective_stack_cap(target, EffectKey.DEBUFF_NHUOC_THUY_AN.value)
    if target.thuy_mark_stacks < cap:
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.thuy_mark_stacks}/{cap}] ({dur}t)"
        )
        return
    # Detonation. Read the lost-HP fraction from the meta so designers
    # can tune via effects.py without code edits.
    lost_pct = float(meta.stat_bonus.get("thuy_mark_detonate_lost_hp_pct", 0.10))
    lost_hp = max(0, target.hp_max - target.hp)
    det_dmg = max(1, int(lost_hp * lost_pct))
    target.take_damage(det_dmg)
    target.consume_stacks("thuy_mark")
    # Strip the debuff entry so per-stack -res_thuy clears with the marks.
    target.effects.pop(EffectKey.DEBUFF_NHUOC_THUY_AN, None)
    target.effect_overrides.pop(EffectKey.DEBUFF_NHUOC_THUY_AN, None)
    session.log.append(
        f"    💥 **{meta.vi}** bùng nổ trên **{target.name}** "
        f"({cap} tầng) — -{det_dmg:,} HP (10% HP đã mất) | "
        f"{target.hp:,}/{target.hp_max:,} HP"
    )
