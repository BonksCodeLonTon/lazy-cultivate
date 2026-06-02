"""Two-phase consumer registry for ``cast_skill``.

Some skill hooks need state to span the damage roll: a pre-damage phase
that snapshots target state or mutates actor stats, and a post-damage
phase that applies deferred work (true damage, stack consumption, log).
Before this module each lived as a pair of inline blocks in
``cast_skill`` — one before the damage roll, one after — coordinating
via local variables (``consume_marks_exec_n``, ``consume_bleed_n``).

The registry inverts that flow via a **CastContext + closure** pattern:

  1. Each consumer is a function decorated with
     ``@register_pre_damage_consumer("skill_field")``. Its body runs in
     the pre-damage phase.
  2. The consumer body returns either ``None`` (didn't fire — nothing to
     defer) or an ``AfterDamageFn`` closure that captures whatever state
     the post-damage phase needs (snapshot counts, spec values, …).
  3. The dispatcher walks the registry once pre-damage, collects the
     returned closures, and runs them once after the damage block.

Closures replace the explicit "deferred work" state that the old inline
pattern threaded through ``cast_skill`` locals. The author of a new
two-phase consumer doesn't need to invent a slot name on ``CastContext``
— captured locals just work.

### Why a ``CastContext`` at all?

Two reasons:

  * **base_dmg / skill_data mutation** — bleed-on-cast grows base_dmg
    and refreshes ``skill_data["base_dmg"]`` mirror. The context is the
    one place those mutations are visible to subsequent riders.
  * **Actor state restoration** — element_pen / crit_dmg_rating bumps
    that should ONLY apply during the damage roll need to be reverted in
    the roll's ``finally`` block. The context's ``prev_pen`` /
    ``prev_crit_dmg_rating`` slots use a "first writer captures" pattern
    so multiple riders mutating the same field compose correctly: the
    single restore at the end of the roll returns to the ORIGINAL value
    regardless of how many riders stacked their bumps in between.

The ``mutate_actor_*`` helpers are the public hooks for "bump this stat
for the damage roll only" — both the registered consumers and the
inline rider blocks in ``cast_skill`` (element_pen_self, target_stack_scale)
go through them so there's exactly one mechanism for pre-roll mutation +
restoration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant

    from .session import CombatSession


@dataclass
class CastContext:
    """Mutable state threaded through both phases of a two-phase consumer.

    Built once per cast in ``cast_skill`` right after ``base_dmg_riders``
    finish and the local ``actor_mods`` / ``target_mods`` dicts settle.
    Held by reference; consumers mutate it directly.

    Field meanings:
      * ``base_dmg`` / ``skill_data`` — current values; consumers that
        grow base damage MUST update both (the immutable skill_data dict
        is rebuilt with ``{**skill_data, "base_dmg": new_value}``).
      * ``actor_mods`` — per-cast multiplier dict already aggregated from
        ``get_combat_modifiers(actor)``. Mutations here are local to the
        cast; no restoration needed.
      * ``prev_pen`` / ``prev_crit_dmg_rating`` — "first writer captures"
        snapshots. ``None`` means no rider mutated the field yet. The
        damage-roll ``finally`` block reads these to revert.
    """
    actor: "Combatant"
    target: "Combatant"
    session: "CombatSession"
    skill_element: str | None
    skill_data: dict
    actor_mods: dict
    base_dmg: int
    prev_pen: float | None = None
    prev_crit_dmg_rating: int | None = None
    prev_final_dmg_bonus: float | None = None


# Signature: ``(ctx, final_dmg) -> None``. ``final_dmg`` is the integer
# damage that landed (post-mitigation, post-in-damage-riders) so a post-
# phase can read it if needed; today's consumers ignore it.
AfterDamageFn = Callable[["CastContext", int], None]

# Signature: ``(spec, ctx) -> Optional[AfterDamageFn]``. Returning ``None``
# means the consumer did not fire — dispatcher skips silently and no
# after-callback is recorded.
PreDamageConsumerFn = Callable[
    [Any, "CastContext"],
    Optional[AfterDamageFn],
]


_PRE_DAMAGE_CONSUMERS: list[tuple[str, PreDamageConsumerFn]] = []


def register_pre_damage_consumer(
    skill_field: str,
) -> Callable[[PreDamageConsumerFn], PreDamageConsumerFn]:
    """Decorator — register ``fn`` as the consumer for ``skill_data[skill_field]``.

    Only invoked when the field is truthy on the cast's ``skill_data``.
    The consumer is responsible for any additional gating (target alive,
    stacks > 0, …) and for returning ``None`` when no deferred work is
    needed.
    """
    def deco(fn: PreDamageConsumerFn) -> PreDamageConsumerFn:
        _PRE_DAMAGE_CONSUMERS.append((skill_field, fn))
        return fn
    return deco


def apply_pre_damage_consumers(ctx: CastContext) -> list[AfterDamageFn]:
    """Walk every pre-damage consumer; collect after-damage callbacks.

    Mutates ``ctx`` in-place (base_dmg, skill_data, prev_pen,
    prev_crit_dmg_rating, actor state). Returns the ordered list of
    after-callbacks to invoke once the damage block resolves; the caller
    is responsible for driving them at the right phase via
    ``run_after_damage_callbacks``.
    """
    after_callbacks: list[AfterDamageFn] = []
    for skill_field, fn in _PRE_DAMAGE_CONSUMERS:
        spec = ctx.skill_data.get(skill_field)
        if not spec:
            continue
        after = fn(spec, ctx)
        if after is not None:
            after_callbacks.append(after)
    return after_callbacks


def run_after_damage_callbacks(
    callbacks: list[AfterDamageFn], ctx: CastContext, final_dmg: int,
) -> None:
    """Fire each callback in order. Caller picks the right phase.

    Today's callbacks are idempotent over ``ctx`` (each handles its own
    "target alive" / "stack count > 0" guards), so the order matches
    registration order — same as the original inline blocks.
    """
    for cb in callbacks:
        cb(ctx, final_dmg)


# ── Pre-roll mutation helpers ──────────────────────────────────────────
# Both the registry consumers AND the inline rider blocks in cast_skill
# (element_pen_self, target_stack_scale) write actor stats through these
# helpers so the "first writer captures" snapshot mechanism stays
# uniform. The damage-roll ``finally`` reads ``ctx.prev_*`` to restore.


def mutate_actor_element_pen(ctx: CastContext, delta: float) -> None:
    """Add ``delta`` to ``actor.element_pen[ctx.skill_element]`` for the roll.

    Captures the pre-mutation value into ``ctx.prev_pen`` on first write
    so subsequent riders mutating the same stat layer on top without
    losing the original. The damage-roll ``finally`` restores to
    ``ctx.prev_pen``. No-op when ``ctx.skill_element`` is unset.
    """
    if not ctx.skill_element:
        return
    if ctx.prev_pen is None:
        ctx.prev_pen = ctx.actor.element_pen.get(ctx.skill_element, 0.0)
    ctx.actor.element_pen[ctx.skill_element] = (
        ctx.actor.element_pen.get(ctx.skill_element, 0.0) + delta
    )


def mutate_actor_crit_dmg_rating(ctx: CastContext, delta: int) -> None:
    """Add ``delta`` to ``actor.crit_dmg_rating`` for the roll.

    Same first-writer-captures discipline as ``mutate_actor_element_pen``;
    the damage-roll ``finally`` restores to ``ctx.prev_crit_dmg_rating``.
    """
    if ctx.prev_crit_dmg_rating is None:
        ctx.prev_crit_dmg_rating = ctx.actor.crit_dmg_rating
    ctx.actor.crit_dmg_rating += delta


def mutate_actor_final_dmg_bonus(ctx: CastContext, delta: float) -> None:
    """Add ``delta`` to ``actor.final_dmg_bonus`` for the roll.

    Same first-writer-captures discipline as the other helpers; the
    damage-roll ``finally`` restores to ``ctx.prev_final_dmg_bonus``. Used
    by buff-driven conditional amps (Bôn Lôi Thuật's Sốc Điện consume amp).
    """
    if ctx.prev_final_dmg_bonus is None:
        ctx.prev_final_dmg_bonus = ctx.actor.final_dmg_bonus
    ctx.actor.final_dmg_bonus += delta


# ── Built-in two-phase consumers ───────────────────────────────────────
# Byte-identical log strings to the former inline blocks in cast_skill.


@register_pre_damage_consumer("consume_target_marks_for_execute")
def _mark_execute_consumer(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Thất Sát Trảm Trận finisher — execute when target carries enough marks.

    Pre-damage: snapshot ``<marker_stack>_stacks`` and check the
    threshold. If met, capture the count via closure for the after-phase;
    if not met, return ``None`` and nothing fires post-damage.

    Post-damage (after-callback): deal additional true damage equal to
    ``true_dmg_pct_missing_hp × (hp_max - hp)`` on the now-reduced HP
    (so the execute fraction sees the post-strike state), then consume
    all marks. Skipped if the target died from the main hit.
    """
    kind = spec.get("marker_stack")
    threshold = int(spec.get("threshold", 0))
    cur = int(getattr(ctx.target, f"{kind}_stacks", 0)) if kind else 0
    if threshold <= 0 or cur < threshold:
        return None
    snapshot = cur
    pct = float(spec.get("true_dmg_pct_missing_hp", 0.0))

    def after(ctx2: CastContext, _final_dmg: int) -> None:
        if not ctx2.target.is_alive():
            return
        missing = max(0, ctx2.target.hp_max - ctx2.target.hp)
        bonus = max(1, int(missing * pct))
        ctx2.target.take_damage(bonus, bypass_shield=True)
        ctx2.session.log.append(
            f"    🗡️ **Thất Sát Trảm** — nuốt {snapshot} "
            f"Sát Ấn → Sát Thương Chuẩn -{bonus:,} HP "
            f"({int(pct * 100)}% HP đã mất) "
            f"| {ctx2.target.name}: {ctx2.target.hp:,}/{ctx2.target.hp_max:,} HP"
        )
        ctx2.target.consume_stacks(kind)

    return after


@register_pre_damage_consumer("cycling_mark")
def _cycling_mark(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Tụ Tán Lưu Sa — self-cycling consume mark; gather places, scatter consumes.

    Two-mode skill in a single spec:
      * **Tán phase** — target already wears the mark. Strip it immediately
        and add ``scatter_bonus_base_dmg`` to base_dmg for this cast. No
        after-callback (mark is gone, nothing to defer).
      * **Tụ phase** — target lacks the mark. Damage lands as a normal
        magical hit; the after-callback applies the mark for
        ``apply_duration`` turns once the strike resolves (so the apply
        sits next to other on-hit effects in the log).

    Spec shape:
        {"mark_effect": "DebuffXyz",
         "scatter_bonus_base_dmg": 3500,
         "apply_duration": 3}

    Generic enough that any future self-cycling consume skill can reuse
    it by naming a different ``mark_effect``. The mark itself should be
    a stat-less debuff (pure state marker) so cycling doesn't bleed
    side effects across casts.
    """
    mark_key = spec.get("mark_effect")
    if not mark_key:
        return None
    scatter_bonus = int(spec.get("scatter_bonus_base_dmg", 0))
    apply_dur = int(spec.get("apply_duration", 3))

    if ctx.target.has_effect(mark_key):
        # Tán phase — strip the mark right now so the main hit reads it
        # as already consumed (no spillover into post-damage hooks that
        # might inspect target.effects).
        ctx.target.effects.pop(mark_key, None)
        ctx.target.effect_overrides.pop(mark_key, None)
        if scatter_bonus > 0:
            ctx.base_dmg += scatter_bonus
            ctx.skill_data = {**ctx.skill_data, "base_dmg": ctx.base_dmg}
        ctx.session.log.append(
            f"    🌪️ *Tán* — Lưu Sa kết tụ bùng nổ, +{scatter_bonus:,} ST nền"
        )
        return None

    # Tụ phase — apply mark in the after-callback so the main hit lands
    # first and the apply log sits with other on-hit lines.
    def after(ctx2: CastContext, _final_dmg: int) -> None:
        if not ctx2.target.is_alive():
            return
        ctx2.target.apply_effect(mark_key, apply_dur)
        ctx2.session.log.append(
            f"    🌪️ *Tụ* — cát Lưu Sa kết tụ trên **{ctx2.target.name}** ({apply_dur}t)"
        )
    return after


@register_pre_damage_consumer("self_hp_cost_pct")
def _self_hp_cost_berserker(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Địa Sát Trảm — spend caster HP/shield to fuel bonus base_dmg.

    Pre-damage only (no after-callback): pay ``spec`` fraction of the
    caster's current HP as self-cost. Shield absorbs first (consistent
    with the standard damage pipeline), then HP. The total spent is
    converted to additive base_dmg at ``bonus_base_dmg_per_hp_spent`` ST
    per 1 unit of HP/shield consumed.

    Direct mutation of ``actor.hp`` / ``actor.shield`` (not via
    ``take_damage``) so on-damage-taken reactions — Lưu Quang stack
    bumps, Bất Tử checks, reactive damage procs — don't fire on the
    self-payment. Safety cap leaves caster at ≥ 1 HP so the cost can
    never self-kill.

    Spec is a flat float (the HP fraction). Companion field
    ``bonus_base_dmg_per_hp_spent`` lives at the same skill_data level.
    Returns ``None`` — there's no deferred post-damage phase.
    """
    pct = float(spec or 0.0)
    per_hp = float(ctx.skill_data.get("bonus_base_dmg_per_hp_spent", 0.0))
    if pct <= 0 or per_hp <= 0:
        return None
    # Cost based on current HP. Safety cap: never reduce caster below 1 HP
    # (so a low-HP berserker click can't self-KO).
    cost = max(1, int(ctx.actor.hp * pct))
    cost = min(cost, max(1, ctx.actor.hp + ctx.actor.shield - 1))
    shield_loss = min(ctx.actor.shield, cost)
    ctx.actor.shield -= shield_loss
    hp_loss = cost - shield_loss
    ctx.actor.hp = max(1, ctx.actor.hp - hp_loss)
    bonus = int(cost * per_hp)
    if bonus > 0:
        ctx.base_dmg += bonus
        ctx.skill_data = {**ctx.skill_data, "base_dmg": ctx.base_dmg}
    ctx.session.log.append(
        f"    🩸 *Địa Sát Phẫn Nộ* — chi {cost:,} sinh khí "
        f"(HP -{hp_loss:,}, Thuẫn -{shield_loss:,}) → +{bonus:,} ST nền"
    )
    return None


@register_pre_damage_consumer("element_pen_self")
def _element_pen_self_consumer(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Thủy Long Ngâm-style self-pen — this cast carries +X% pen of its
    own element. Folds into ``actor.element_pen[elem]`` only for the
    damage roll via the shared helper; the ``finally`` restores it.
    """
    delta = float(spec or 0.0)
    if delta <= 0:
        return None
    mutate_actor_element_pen(ctx, delta)
    return None


@register_pre_damage_consumer("scaling_per_target_stack")
def _target_stack_scale_consumer(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Hỏa Vân Sậu Thiên-style per-stack scaling — read the target's named
    stack count and apply per-stack bonuses to this cast: ``crit_dmg_rating``
    (additive int on actor), ``element_pen`` (additive float on actor's
    matching element). Mutations are one-shot via ``mutate_actor_*``; the
    ``finally`` restores both.
    """
    if not isinstance(spec, dict):
        return None
    stack_kind = spec.get("stack")
    if not stack_kind:
        return None
    count = int(getattr(ctx.target, f"{stack_kind}_stacks", 0))
    if count <= 0:
        return None
    per_cd = int(spec.get("crit_dmg_rating", 0))
    if per_cd:
        mutate_actor_crit_dmg_rating(ctx, per_cd * count)
    per_pen = float(spec.get("element_pen", 0.0))
    if per_pen:
        mutate_actor_element_pen(ctx, per_pen * count)
    return None


@register_pre_damage_consumer("same_element_streak_scaling")
def _same_element_streak_consumer(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Lôi Tù Cấm Ngục — reward an unbroken same-element cast chain.

    ``actor.same_element_streak`` is bumped in ``cast_skill`` BEFORE this
    runs and already INCLUDES the current cast (first cast of the element →
    streak 1; a cross-element / auto-fired cast resets it). So the bonus is
    keyed off ``(streak - 1)``: the first cast gets nothing, each additional
    same-element cast adds another tier up to ``streak_cap``.

    Pre-damage only (no after-callback):
      * ``per_streak_base_dmg`` × (streak-1) → additive base_dmg.
      * ``per_streak_element_pen_pct`` × (streak-1) → self element_pen for
        the roll (via ``mutate_actor_element_pen`` so the ``finally``
        restores it — mirrors ``element_pen_self`` / Canh Kim).
      * at ``streak >= guaranteed_shock_at``, force the skill's
        ``effect_chances[<shock_effect>]`` to 1.0 by rewriting the cast's
        ``ctx.skill_data`` copy (read back into ``apply_skill_effects``).

    Spec shape:
        {"per_streak_base_dmg": 240, "per_streak_element_pen_pct": 0.04,
         "streak_cap": 5, "guaranteed_shock_at": 3,
         "shock_effect": "DebuffSocDien"}
    """
    if not isinstance(spec, dict):
        return None
    cap = int(spec.get("streak_cap", 0))
    streak = int(getattr(ctx.actor, "same_element_streak", 0))
    if cap > 0:
        streak = min(streak, cap)
    tiers = max(0, streak - 1)

    per_base = int(spec.get("per_streak_base_dmg", 0))
    if tiers > 0 and per_base > 0:
        bonus = per_base * tiers
        ctx.base_dmg += bonus
        ctx.skill_data = {**ctx.skill_data, "base_dmg": ctx.base_dmg}
        ctx.session.log.append(
            f"    ⛓️ *Lôi Tù Cấm Ngục* — chuỗi Lôi ×{streak} "
            f"(+{bonus:,} ST nền)"
        )

    per_pen = float(spec.get("per_streak_element_pen_pct", 0.0))
    if tiers > 0 and per_pen > 0:
        mutate_actor_element_pen(ctx, per_pen * tiers)

    shock_at = int(spec.get("guaranteed_shock_at", 0))
    if shock_at > 0 and streak >= shock_at:
        shock_key = spec.get("shock_effect", "DebuffSocDien")
        # Rewrite the effect_chances on the cast's skill_data copy so
        # ``apply_skill_effects`` (which reads ``ctx.skill_data`` back) rolls
        # the shock at 100%. Copy-on-write — never mutate the registry dict.
        new_chances = {**ctx.skill_data.get("effect_chances", {}), shock_key: 1.0}
        ctx.skill_data = {**ctx.skill_data, "effect_chances": new_chances}
        ctx.session.log.append(
            f"    ⚡ *Lôi Tù* — chuỗi ≥{shock_at}, chắc chắn **Sốc Điện**"
        )
    return None


@register_pre_damage_consumer("blood_offering")
def _blood_offering_consumer(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Huyết Tế Chú (Âm B3) — HP-sacrifice power amp + lifesteal.

    Pre-damage:
      * Pay ``self_hp_cost_pct`` of the caster's CURRENT HP. Shield absorbs
        first (mirrors ``_self_hp_cost_berserker`` / Địa Sát Trảm), then HP.
        Direct ``hp`` / ``shield`` mutation (not ``take_damage``) so the
        self-payment can't arm on-damage reactions. Safety cap leaves the
        caster at ≥ 1 HP — the cost can NEVER self-kill.
      * Per 1% of HP actually spent, add
        ``final_dmg_pct_per_pct_hp_spent`` to ``final_dmg_bonus`` for the
        roll (via the shared first-writer-captures helper so the damage-roll
        ``finally`` restores it), capped at ``max_final_dmg_bonus``.

    Post-damage (after-callback): heal the caster ``lifesteal_pct_of_dmg``
    of the damage that landed, routed through ``session._apply_heal`` so
    bleed-heal-reduce / heal-can-crit apply uniformly.

    ``self_hp_cost_pct`` is shared with Địa Sát Trảm's consumer, but that
    one early-returns when ``bonus_base_dmg_per_hp_spent`` is unset (which
    B3 leaves unset), so the two never double-charge the same cast.
    """
    if not isinstance(spec, dict):
        return None
    cost_pct = float(ctx.skill_data.get("self_hp_cost_pct", 0.0))
    if cost_pct <= 0:
        return None
    # Cost based on current HP. Shield-absorbs-first; safety cap keeps the
    # caster at ≥ 1 HP so the offering can't self-KO.
    cost = max(1, int(ctx.actor.hp * cost_pct))
    cost = min(cost, max(1, ctx.actor.hp + ctx.actor.shield - 1))
    shield_loss = min(ctx.actor.shield, cost)
    ctx.actor.shield -= shield_loss
    hp_loss = cost - shield_loss
    ctx.actor.hp = max(1, ctx.actor.hp - hp_loss)

    # +X% final dmg per 1% of hp_max actually spent, capped.
    spent_pct = (cost / ctx.actor.hp_max) if ctx.actor.hp_max > 0 else 0.0
    per_pct_unit = float(spec.get("final_dmg_pct_per_pct_hp_spent", 0.0))
    max_fdb = float(spec.get("max_final_dmg_bonus", 0.0))
    fdb = (spent_pct * 100.0) * per_pct_unit
    if max_fdb > 0:
        fdb = min(fdb, max_fdb)
    if fdb > 0:
        mutate_actor_final_dmg_bonus(ctx, fdb)
    ctx.session.log.append(
        f"    🩸 *Huyết Tế Chú* — chi {cost:,} sinh khí "
        f"(HP -{hp_loss:,}, Thuẫn -{shield_loss:,}) → +{int(fdb * 100)}% ST cuối"
    )

    lifesteal = float(spec.get("lifesteal_pct_of_dmg", 0.0))
    if lifesteal <= 0:
        return None

    def after(ctx2: CastContext, final_dmg: int) -> None:
        if final_dmg <= 0 or not ctx2.actor.is_alive():
            return
        requested = max(1, int(final_dmg * lifesteal))
        healed = ctx2.session._apply_heal(ctx2.actor, requested)
        if healed > 0:
            ctx2.session.log.append(
                f"    🩸 **{ctx2.actor.name}** Huyết Tế hồi sinh "
                f"+{healed:,} HP ({int(lifesteal * 100)}% sát thương)"
            )

    return after


@register_pre_damage_consumer("overload_recoil")
def _overload_recoil_consumer(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Quá Tải Lôi Bạo (Lôi B4) phase-1 — shock-stack consumer finisher.

    Pre-damage:
      * snapshot the target's ``shock_stacks``; if any, add
        ``per_shock_stack_final_dmg_pct × n`` to ``final_dmg_bonus`` and
        ``per_shock_stack_element_pen × n`` to the actor's Lôi element_pen
        for the roll (both via the shared first-writer-captures helpers so
        the damage-roll ``finally`` restores them).

    Post-damage (after-callback): CONSUME the snapshotted shock stacks via
    the standard ``consume_stacks`` path. Consumed after the roll so the
    amp reads the full pre-consume count.

    Phase-2 (the self-cooldown recoil) lives in ``cast_consumers.py`` keyed
    on the same ``overload_recoil`` block — post-cast so it runs after the
    casting skill's own cooldown is set.
    """
    if not isinstance(spec, dict) or not spec.get("consume_target_shock_stacks"):
        return None
    n = int(getattr(ctx.target, "shock_stacks", 0))
    if n <= 0:
        return None
    per_fdb = float(spec.get("per_shock_stack_final_dmg_pct", 0.0))
    if per_fdb > 0:
        mutate_actor_final_dmg_bonus(ctx, per_fdb * n)
    per_pen = float(spec.get("per_shock_stack_element_pen", 0.0))
    if per_pen > 0:
        mutate_actor_element_pen(ctx, per_pen * n)
    ctx.session.log.append(
        f"    ⚡ *Quá Tải Lôi Bạo* — nuốt {n} tầng Sốc Điện "
        f"(+{int(per_fdb * n * 100)}% ST cuối, +{int(per_pen * n * 100)}% xuyên Kháng Lôi)"
    )

    def after(ctx2: CastContext, _final_dmg: int) -> None:
        ctx2.target.consume_stacks("shock")

    return after


@register_pre_damage_consumer("consume_target_bleed_on_cast")
def _bleed_consumer(
    spec: Any, ctx: CastContext,
) -> Optional[AfterDamageFn]:
    """Canh Kim — eat the target's bleed for base-dmg + element-pen amp.

    Pre-damage:
      * snapshot ``target.bleed_stacks``
      * if ``dmg_pct_of_atk_per_stack`` > 0, grow ``ctx.base_dmg`` by
        ``atk × pct × stacks`` and refresh skill_data mirror
      * if ``element_pen_per_stack`` > 0 and skill has an element,
        bump ``actor.element_pen[elem]`` for the roll (via the shared
        helper so the finally restores it)

    Post-damage (after-callback): drain the bleed stacks. Always runs
    when stacks > 0 — the consumer doesn't gate on whether the bonus
    fired, matching the original behavior.
    """
    consume_bleed_n = int(ctx.target.bleed_stacks)
    if consume_bleed_n <= 0:
        return None

    atk_pct = float(spec.get("dmg_pct_of_atk_per_stack", 0.0))
    if atk_pct > 0:
        bonus = int(ctx.actor.atk * atk_pct * consume_bleed_n)
        if bonus > 0:
            ctx.base_dmg += bonus
            ctx.skill_data = {**ctx.skill_data, "base_dmg": ctx.base_dmg}
            ctx.session.log.append(
                f"    🩸 *Canh Kim* — nuốt {consume_bleed_n} tầng "
                f"Chảy Máu (+{bonus:,} ST nền)"
            )

    pen_pct = float(spec.get("element_pen_per_stack", 0.0))
    if pen_pct > 0:
        mutate_actor_element_pen(ctx, pen_pct * consume_bleed_n)

    def after(ctx2: CastContext, _final_dmg: int) -> None:
        ctx2.target.consume_stacks("bleed")

    return after
