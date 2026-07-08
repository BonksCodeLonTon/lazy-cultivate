"""Five extra skill mechanics opt-in via ``skill_data`` JSON fields.

Each is independent and skill-local — none of these change anything when the
field is absent, so existing skills keep working unchanged.

1. ``hit_count``: Multi-hit single attack — runs the damage block N times,
   each subject to its own crit/evade roll. Effects (debuffs, on-hit procs)
   fire per-hit, but stamped skill effects (debuff application via
   ``effects: [...]``) only land on the LAST hit so duration timers don't
   refresh N times in one cast.

2. ``charge_bonus``: After every Nth cast of this skill, deal a flat bonus
   damage chunk on top of the base hit. State lives on
   ``actor.skill_cast_counts`` so each skill charges independently.

       "charge_bonus": {"every": 3, "amount": 800}

3. ``chain_skill``: After the main cast, automatically cast another skill
   for ``pct`` of its base damage. The chained skill keeps its full effect
   list (effects/debuffs apply normally) but pays no MP and ignores
   cooldown. Single-step only — chained skills don't chain again.

       "chain_skill": {"key": "SkillFollowUp", "pct": 0.5}

   Optional ``cast_on: <trigger>`` gates when the follow-up fires — see
   ``_CAST_ON_TRIGGERS`` for registered triggers (always / every_n_casts
   / crit / kill / shield_break). Counter for ``every_n_casts`` lives on
   ``actor.skill_chain_counts`` so each skill charges independently,
   separate from ``charge_bonus``'s counter. The chained skill itself can
   also set ``chain_only: true`` to opt out of being picked as a normal
   turn action by ``_choose_skill``.

       "chain_skill": {"key": "SkillFinisher", "pct": 1.0,
                       "cast_on": "every_n_casts", "n": 4}

4. ``auto_cast_on_stacks``: At the start of the actor's turn, if the
   target carries ≥ ``threshold`` of ``stack`` (one of "burn"/"bleed"/
   "shock"), force-cast this skill instead of the actor's normal pick,
   then optionally consume all stacks. Bypasses cooldown but still pays MP.

       "auto_cast_on_stacks": {
           "stack": "burn", "threshold": 5, "consume": true
       }

5. ``summon_spec``: After casting, append a summon to ``actor.summons``.
   Each summon ticks once per round in ``_process_periodic`` — does ``dmg``
   to the opponent, decrements ``turns``, expires at 0.

       "summon_spec": {
           "vi": "Kiếm Hồn", "emoji": "🗡️", "element": "kim",
           "dmg_pct_of_matk": 0.4, "turns": 3
       }
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from src.game.engine.damage.elemental import apply_elemental
from src.game.engine.effects import get_combat_modifiers
from src.game.systems.combatant import Combatant

if TYPE_CHECKING:
    from .session import CombatSession


def _apply_defender_elemental(
    actor: Combatant, target: Combatant, element: str | None, dmg: int,
) -> int:
    """Run a frozen attacker-baked summon hit through the element pipeline.

    Routes through ``apply_elemental`` so defender ``res_<elem>`` (base +
    debuff shred), defender ``dmg_taken_bonus_<elem>`` (per-stack amps),
    attacker ``element_pen[<elem>]``, and the ``MAX_ELEMENTAL_RES`` clamp
    all apply. Does NOT pass ``attacker_element_amp`` because summon dmg
    was already amped at spawn time (``maybe_spawn_summon`` folds in
    ``element_dmg_bonus`` + ``dmg_bonus_<elem>`` + dmg_amp_field). Passing
    them again here would double-amp.
    """
    if not element:
        return dmg
    target_mods = get_combat_modifiers(target)
    defender_res = {
        element: float(target.resistances.get(element, 0.0))
        + float(target_mods.get(f"res_{element}", 0.0)),
    }
    defender_amp = float(target_mods.get(f"dmg_taken_bonus_{element}", 0.0))
    pen = float(actor.element_pen.get(element, 0.0))
    return apply_elemental(
        dmg, element, defender_res, pen_pct=pen,
        damage_taken_by_element={element: defender_amp} if defender_amp else None,
    )


# ── 2. Charge bonus ──────────────────────────────────────────────────────────

def apply_charge_bonus(
    session: "CombatSession", actor: Combatant, target: Combatant,
    skill_key: str, skill_data: dict,
) -> None:
    """Increment per-skill cast count and detonate the bonus on every Nth cast."""
    spec = skill_data.get("charge_bonus")
    if not spec or not target.is_alive():
        return
    every = max(1, int(spec.get("every", 0)))
    amount = int(spec.get("amount", 0))
    if every <= 1 or amount <= 0:
        return
    actor.skill_cast_counts[skill_key] = actor.skill_cast_counts.get(skill_key, 0) + 1
    if actor.skill_cast_counts[skill_key] < every:
        return
    actor.skill_cast_counts[skill_key] = 0
    target.take_damage(amount)
    session.log.append(
        f"    💢 **{actor.name}** Tích Lũy đủ {every} lần niệm — bùng phát "
        f"-{amount:,} HP | {target.name}: {target.hp:,}/{target.hp_max:,} HP"
    )


# ── 3. Chain skill ───────────────────────────────────────────────────────────

# Generic trigger dispatcher for ``chain_skill.cast_on``. Each entry takes
# ``(actor, target, parent_skill_key, spec, ctx)`` and returns True when the
# trigger fires this cast. ``spec`` is the chain_skill dict (so triggers can
# read their own params like ``n`` for every_n_casts), ``ctx`` is the cast
# context dict assembled in cast_skill (carries ``is_crit``, ``killed``,
# ``shield_broken``, ``dealt`` flags). Add a new trigger by registering it
# here — no other engine plumbing needed.
_CAST_ON_TRIGGERS: dict[str, "Callable[..., bool]"] = {}


def _register_cast_on(name: str):
    def deco(fn):
        _CAST_ON_TRIGGERS[name] = fn
        return fn
    return deco


@_register_cast_on("always")
def _trigger_always(actor, target, parent_key, spec, ctx) -> bool:
    """Default — fires on every parent cast. Equivalent to old ``chain_skill``
    with no ``every`` / ``cast_on``."""
    return True


@_register_cast_on("every_n_casts")
def _trigger_every_n_casts(actor, target, parent_key, spec, ctx) -> bool:
    """Fires once every Nth top-level cast of the parent skill. Counter
    lives on ``actor.skill_chain_counts[parent_key]`` so each chained pair
    cycles independently. ``n`` defaults to 1 (always)."""
    n = max(1, int(spec.get("n", 1)))
    if n <= 1:
        return True
    cur = actor.skill_chain_counts.get(parent_key, 0) + 1
    if cur < n:
        actor.skill_chain_counts[parent_key] = cur
        return False
    actor.skill_chain_counts[parent_key] = 0
    return True


@_register_cast_on("crit")
def _trigger_on_crit(actor, target, parent_key, spec, ctx) -> bool:
    """Fires only when the parent cast crit. Useful for "burst on crit"
    finisher chains."""
    return bool(ctx.get("is_crit"))


@_register_cast_on("kill")
def _trigger_on_kill(actor, target, parent_key, spec, ctx) -> bool:
    """Fires only when the parent cast killed the target. Note: target is
    already dead by the time this fires, so the chain skill needs to be
    self-targeted or aoe — single-target damage chains will silently fizzle
    at the ``target.is_alive()`` guard inside cast_chain_skill."""
    return bool(ctx.get("killed"))


@_register_cast_on("shield_break")
def _trigger_on_shield_break(actor, target, parent_key, spec, ctx) -> bool:
    """Fires only when the parent cast emptied the target's shield this
    strike (i.e., shield_before > 0 and target.shield == 0 after)."""
    return bool(ctx.get("shield_broken"))


@_register_cast_on("target_stacks_at_least")
def _trigger_target_stacks(actor, target, parent_key, spec, ctx) -> bool:
    """Fires when the target carries ≥ ``threshold`` of the named ``stack``.

    Optionally consumes ALL stacks when ``consume`` is true; the consumed
    count is written to ``ctx["consumed_stacks"]`` so the chain handler can
    scale the follow-up cast per-stack (e.g. Ngũ Lôi Oanh Đỉnh → Lôi Đình
    Chấn Thiên Sát: +10% loi dmg, +3% loi pen, +500 base per consumed
    Sốc Điện stack).

    Spec shape:
        "chain_skill": {
            "key": "SkillLoiDinhChanThienSat",
            "cast_on": "target_stacks_at_least",
            "stack": "shock", "threshold": 5, "consume": true,
            "per_stack_base_dmg_bonus": 500,
            "per_stack_element_pen_pct": 0.03,
            "per_stack_element_dmg_pct": 0.10
        }
    """
    stack_name = spec.get("stack")
    threshold = int(spec.get("threshold", 0))
    if not stack_name or threshold <= 0:
        return False
    count = _stack_count(target, stack_name)
    if count < threshold:
        return False
    if spec.get("consume", False):
        consumed = _consume_stacks(target, stack_name)
        ctx["consumed_stacks"] = consumed
    else:
        ctx["consumed_stacks"] = count
    return True


def cast_chain_skill(
    session: "CombatSession", actor: Combatant, target: Combatant,
    parent_skill_key: str, skill_data: dict,
    ctx: dict | None = None,
) -> None:
    """Cast a follow-up skill at scaled damage. Recursion-guarded (one step).

    Trigger dispatch is driven by ``spec.cast_on`` (default ``"always"``).
    Examples:

        # Fire every Nth cast — counter on actor.skill_chain_counts
        "chain_skill": {"key": "Finisher", "pct": 1.0,
                        "cast_on": "every_n_casts", "n": 4}

        # Fire only when the parent cast crit
        "chain_skill": {"key": "BurstHit", "pct": 0.5, "cast_on": "crit"}

        # Fire only when the parent cast killed the target (chain runs on
        # the corpse — meaningful when the chain skill is self-targeted /
        # buff application; damage chains will silently fizzle).
        "chain_skill": {"key": "ExecuteMark", "cast_on": "kill"}

        # Fire only when the parent cast broke the target's shield
        "chain_skill": {"key": "ShieldBreak", "cast_on": "shield_break"}

    Add a new trigger by registering it in ``_CAST_ON_TRIGGERS`` — no
    cast_skill / engine changes needed.
    """
    spec = skill_data.get("chain_skill")
    if not spec or not target.is_alive():
        return
    chain_key = spec.get("key")
    if not chain_key:
        return
    # Equipping gate — the chained-to skill must be in the actor's skill_keys.
    # Lets a player who only knows the parent skill (without its combo target)
    # still benefit from the parent's other mechanics, while the chain payoff
    # is locked behind equipping both. Bypassable with ``require_equipped:
    # false`` for legacy / formation-style chains that should always fire.
    if spec.get("require_equipped", True) and chain_key not in actor.skill_keys:
        return
    cast_on = spec.get("cast_on", "always")
    trigger = _CAST_ON_TRIGGERS.get(cast_on)
    if trigger is None:
        return  # unknown trigger — silently skip rather than crash
    # Use a stable ctx dict so triggers can write back into it
    # (e.g. target_stacks_at_least stores consumed_stacks for scaling).
    trigger_ctx = ctx if ctx is not None else {}
    if not trigger(actor, target, parent_skill_key, spec, trigger_ctx):
        return
    from src.data.registry import registry
    chain_data = registry.get_skill(chain_key)
    if not chain_data:
        return
    pct = float(spec.get("pct", 1.0))
    # Stamp scaled base_dmg + strip nested chain to cap recursion at one step.
    scaled = dict(chain_data)
    scaled["base_dmg"] = max(1, int(chain_data.get("base_dmg", 0) * pct))
    scaled.pop("chain_skill", None)
    scaled.pop("auto_cast_on_stacks", None)  # don't loop the auto-cast guard

    # Per-stack scaling — when the trigger stored ``consumed_stacks`` (e.g.
    # target_stacks_at_least with consume=true), scale the chain cast per
    # consumed unit. Three lanes supported, all optional:
    #   * per_stack_base_dmg_bonus    — flat add to scaled base_dmg
    #   * per_stack_element_pen_pct   — folds into scaled.element_pen_self
    #   * per_stack_element_dmg_pct   — stamps a 1-turn transient buff on
    #     the actor carrying ``dmg_bonus_<element>`` so the chain cast
    #     reads it through normal actor_mods aggregation
    consumed = int(trigger_ctx.get("consumed_stacks", 0))
    transient_buff_key: str | None = None
    if consumed > 0:
        base_bonus = consumed * int(spec.get("per_stack_base_dmg_bonus", 0))
        if base_bonus > 0:
            scaled["base_dmg"] = scaled["base_dmg"] + base_bonus
        pen_bonus = consumed * float(spec.get("per_stack_element_pen_pct", 0.0))
        if pen_bonus > 0:
            scaled["element_pen_self"] = (
                float(scaled.get("element_pen_self", 0.0)) + pen_bonus
            )
        dmg_bonus_pct = consumed * float(spec.get("per_stack_element_dmg_pct", 0.0))
        chain_element = chain_data.get("element")
        if dmg_bonus_pct > 0 and chain_element:
            transient_buff_key = str(
                spec.get("per_stack_buff_key", "BuffNguLoiCharged")
            )
            actor.apply_effect(
                transient_buff_key,
                duration=1,
                overrides={
                    "stat_bonus": {f"dmg_bonus_{chain_element}": dmg_bonus_pct}
                },
            )
        if base_bonus > 0 or pen_bonus > 0 or dmg_bonus_pct > 0:
            session.log.append(
                f"    ⚡ **{actor.name}** dồn nén {consumed} tầng "
                f"→ +{base_bonus:,} ST nền / +{pen_bonus * 100:.0f}% xuyên "
                f"/ +{dmg_bonus_pct * 100:.0f}% ST {chain_element or 'nguyên tố'}"
            )

    session.log.append(
        f"    🔗 **{actor.name}** liên hoàn → **{chain_data.get('vi', chain_key)}** "
        f"({pct * 100:.0f}% ST)"
    )
    # Lazy import to break the cycle: skill_extras ← casting ← skill_extras.
    from .casting import cast_skill
    try:
        cast_skill(session, actor, target, chain_key, scaled, mp_cost=0)
    finally:
        # Clean up the transient buff so it can't carry into next turn.
        # Engine duration=1 also expires it at periodic phase, but the
        # explicit removal makes the consume-on-cast intent obvious and
        # avoids edge cases when the chain skill itself reads/refreshes
        # actor effects.
        if transient_buff_key:
            actor.effects.pop(transient_buff_key, None)
            actor.effect_overrides.pop(transient_buff_key, None)


# ── 4. Auto-cast on stacks ───────────────────────────────────────────────────

# Whitelist of stack kinds that skill JSON ``auto_cast_on_stacks.stack``
# may reference. Acts as a gate so a typo in the JSON doesn't silently
# read an unrelated combatant attribute. New stack kinds get one line here.
_STACK_KINDS: frozenset[str] = frozenset({"burn", "bleed", "shock", "hoa_van"})


def _stack_count(target: Combatant, stack_name: str) -> int:
    if stack_name not in _STACK_KINDS:
        return 0
    return int(getattr(target, f"{stack_name}_stacks", 0))


def _consume_stacks(target: Combatant, stack_name: str) -> int:
    """Zero the stack counter and return the count that was consumed.

    Thin gate around ``Combatant.consume_stacks`` that restricts the
    skill-JSON-reachable stack kinds to the ``_STACK_KINDS`` whitelist.
    Engine code that already knows the kind can call
    ``combatant.consume_stacks(kind)`` directly.
    """
    if stack_name not in _STACK_KINDS:
        return 0
    return target.consume_stacks(stack_name)


def find_auto_cast_skill(
    actor: Combatant, target: Combatant,
) -> tuple[str | None, dict | None]:
    """Return ``(skill_key, skill_data)`` for the highest-priority auto-cast
    skill the actor knows whose trigger condition is currently met, or
    ``(None, None)``. Highest threshold wins so a 9-stack finisher fires
    over a 3-stack one when both qualify.

    Two trigger types are supported:

    1. ``auto_cast_on_stacks`` — target carries ≥ ``threshold`` of the
       named stack ("burn"/"bleed"/"shock"). Skill stays selectable in
       the normal rotation as a fallback.
    2. ``auto_cast_on_buff_count`` — target carries > ``threshold`` BUFFs
       (strict greater-than; CC and debuffs don't count). Pure reactive —
       these skills are filtered out of ``_choose_skill`` so they only
       ever fire from this path.
    """
    from src.data.registry import registry
    from src.game.engine.effects import EFFECTS, EffectKind
    best_key = None
    best_data = None
    best_threshold = -1

    # Cache target buff count once so the loop doesn't re-walk effects per skill.
    target_buff_count = sum(
        1 for k in target.effects
        if (m := EFFECTS.get(k)) is not None and m.kind is EffectKind.BUFF
    )

    for skill_key in actor.skill_keys:
        data = registry.get_skill(skill_key)
        if not data:
            continue

        spec = data.get("auto_cast_on_stacks")
        if spec:
            stack_name = spec.get("stack")
            threshold = int(spec.get("threshold", 0))
            if threshold > 0 and _stack_count(target, stack_name) >= threshold:
                if threshold > best_threshold:
                    best_threshold = threshold
                    best_key, best_data = skill_key, data
            continue

        spec_buffs = data.get("auto_cast_on_buff_count")
        if spec_buffs:
            threshold = int(spec_buffs.get("threshold", 0))
            if threshold >= 0 and target_buff_count > threshold:
                if threshold > best_threshold:
                    best_threshold = threshold
                    best_key, best_data = skill_key, data
            continue

    return best_key, best_data


def consume_auto_cast_stacks(
    session: "CombatSession", actor: Combatant, target: Combatant, skill_data: dict,
) -> None:
    """Strip stacks if the auto-cast spec requested it. Safe to call after
    the skill resolved — by then the damage / effects already used the
    higher stack count via the normal ``actor`` paths.
    """
    spec = skill_data.get("auto_cast_on_stacks")
    if not spec or not spec.get("consume"):
        return
    consumed = _consume_stacks(target, spec.get("stack", ""))
    if consumed > 0:
        session.log.append(
            f"    🌀 Tiêu thụ {consumed} tầng {spec['stack']} trên **{target.name}**."
        )


# ── 5. Summon ────────────────────────────────────────────────────────────────

def maybe_spawn_summon(
    session: "CombatSession", actor: Combatant, skill_data: dict,
) -> None:
    """Append a new summon entry to ``actor.summons`` if the skill defines one.

    A summon may optionally carry an ``aura_buff`` block:

        "aura_buff": {"key": "BuffSinhCo", "overrides": {...}}

    Auras stack additively across all active summons that share an aura
    key — three Mộc summons each carrying ``BuffSinhCo`` together grant 3×
    the hp regen. Aggregation is computed on demand by ``aura_stat_bonus``
    and folded into ``get_combat_modifiers``; nothing is stamped onto
    ``actor.effects`` so the bonus is bound to the summon's lifetime
    (drops the moment the summon expires).
    """
    spec = skill_data.get("summon_spec")
    if not spec:
        return
    # ``limit`` (optional) caps how many copies of this same summon can
    # coexist on the actor — keyed by ``vi`` (display name) so different
    # summons don't share quotas. ``summon_limit_bonus`` on the actor (e.g.
    # from Thiên Giới Thẩm Phán's tier-10 gem threshold) bumps the cap up.
    # A re-cast while at the cap is silently skipped (caller's MP + cooldown
    # still spent — the formation skill paid for an aura refresh, not a
    # duplicate spawn).
    base_limit = int(spec.get("limit", 0))
    summon_vi = spec.get("vi", "Triệu Hồi Vật")
    if base_limit > 0:
        effective_limit = base_limit + max(0, int(getattr(actor, "summon_limit_bonus", 0)))
        already = sum(1 for s in actor.summons if s.get("vi") == summon_vi)
        if already >= effective_limit:
            session.log.append(
                f"    {spec.get('emoji', '✨')} **{summon_vi}** đã hiện diện "
                f"({already}/{effective_limit}) — không triệu hồi thêm."
            )
            return
    dmg = int(spec.get("dmg", 0))
    pct_matk = float(spec.get("dmg_pct_of_matk", 0.0))
    pct_atk = float(spec.get("dmg_pct_of_atk", 0.0))
    if pct_matk > 0:
        dmg += int(actor.matk * pct_matk)
    if pct_atk > 0:
        dmg += int(actor.atk * pct_atk)
    # ``dmg_amp_field`` (optional) names a Combatant float attribute whose
    # value multiplicatively amps this summon's damage (× 1+amp). Applied
    # AFTER base+pct so the bonus rides through the element-amp step too.
    # Data-driven replacement for the old ``vi == "Vạn Kiếm"`` name check —
    # Vạn Kiếm summons set it to ``sword_summon_dmg_amp`` (Kiếm Tâm Thông
    # Minh). Absent / zero amp → no change for every other summon kind.
    amp_field = spec.get("dmg_amp_field")
    if amp_field:
        _amp = float(getattr(actor, amp_field, 0.0))
        if _amp > 0:
            dmg = int(dmg * (1.0 + _amp))
    # Element-bonus folding — a quang summon should benefit from the
    # caster's ``dmg_bonus_quang`` (formation passive) and permanent
    # ``element_dmg_bonus`` (constitutions / equipment) the same way a
    # quang skill cast does. Read both at spawn time so the summon's
    # damage reflects the caster's build at the moment of summoning.
    elem = spec.get("element")
    if elem:
        from src.game.engine.effects import get_combat_modifiers
        elem_bonus = float(get_combat_modifiers(actor).get(f"dmg_bonus_{elem}", 0.0))
        elem_bonus += float(actor.element_dmg_bonus.get(elem, 0.0))
        if elem_bonus > 0:
            dmg = int(dmg * (1.0 + elem_bonus))
    dmg = max(1, dmg)
    turns = max(1, int(spec.get("turns", 3)))
    # ``count`` (default 1) lets one cast spawn a swarm of identical summons —
    # used by Vạn Quỷ Phệ Tâm-style skills that summon multiple tiểu quỷ. Each
    # ticks independently, so a swarm of N delivers N hits + N debuff
    # refreshes per round.
    count = max(1, int(spec.get("count", 1)))
    aura = spec.get("aura_buff")
    on_hit_debuff = spec.get("on_hit_debuff")
    # Optional actor-attribute name to read for per-summon overrides (e.g.
    # ``cuu_long``). Lets a formation declare a static summon spec while the
    # gem-threshold-driven actor dict tunes crit_rating / stun_chance /
    # expire_finisher_pct / dmg_pct_bonus at spawn time. Apply BEFORE the
    # summon list is filled so all ``count`` copies share the same overrides.
    override_key = spec.get("actor_overrides")
    actor_ov = getattr(actor, override_key, None) if override_key else None
    if isinstance(actor_ov, dict):
        # dmg_pct_bonus — extra fraction multiplier on top of the spawn dmg.
        _pct_bonus = float(actor_ov.get("dmg_pct_bonus", 0.0))
        if _pct_bonus:
            dmg = max(1, int(dmg * (1.0 + _pct_bonus)))
    for _ in range(count):
        summon = {
            "vi": spec.get("vi", "Triệu Hồi Vật"),
            "emoji": spec.get("emoji", "✨"),
            "element": spec.get("element"),
            "dmg": dmg,
            "turns": turns,
        }
        # Carry the amp field forward so the consume-at-count burst can
        # apply the same multiplier the per-tick swing got at spawn.
        if amp_field:
            summon["dmg_amp_field"] = amp_field
        # Opt-in: per-tick swing routes through the elemental pipeline so
        # defender ``res_<elem>`` + ``dmg_taken_bonus_<elem>`` apply. Vanilla
        # summons (Cửu Long, curse-class) keep the legacy raw-take_damage path
        # so this flag stays purely additive — no existing balance change.
        if spec.get("respect_target_res"):
            summon["respect_target_res"] = True
        if aura and aura.get("key"):
            summon["aura_buff_key"] = aura["key"]
            summon["aura_buff_overrides"] = aura.get("overrides")
        # Optional ``on_hit_debuff`` lets a summon stamp / refresh a debuff
        # on the target every tick — used by curse-class summons whose
        # flavor is "the demon marks you" rather than "the demon hits you".
        if on_hit_debuff and on_hit_debuff.get("key"):
            summon["on_hit_debuff"] = on_hit_debuff
        # Optional ``consume_spec`` — when the swarm count for this ``vi``
        # reaches ``at_count``, every copy is consumed and the spec's
        # damage / stack-gain fires (see consume-at-count pass in
        # ``tick_summons``). Stamped per-summon so each carries the same
        # spec; the consume pass reads it once per group.
        _consume = spec.get("consume_spec")
        if _consume and int(_consume.get("at_count", 0)) > 0:
            summon["consume_spec"] = _consume
        # Per-summon crit / stun / on-expire finisher — populated from the
        # actor's override dict (e.g. cuu_long). Optional; absent fields stay
        # at zero so the tick path treats the summon as a vanilla swarm.
        if isinstance(actor_ov, dict):
            _cr = int(actor_ov.get("crit_rating", 0))
            _cdr = int(actor_ov.get("crit_dmg_rating", 0))
            if _cr or _cdr:
                summon["crit_rating"] = _cr
                summon["crit_dmg_rating"] = _cdr
            _stun = float(actor_ov.get("stun_chance", 0.0))
            if _stun:
                summon["stun_chance"] = _stun
            _fin_pct = float(actor_ov.get("finisher_pct", 0.0))
            if _fin_pct:
                summon["expire_finisher"] = {
                    "pct": _fin_pct,
                    "element": spec.get("element"),
                    "emoji": spec.get("finisher_emoji", "💥"),
                    "vi": spec.get("finisher_vi", spec.get("vi", "Hỏa Long") + " Tận Sát"),
                }
        # Reset the per-batch damage accumulator on the first spawn of a new
        # batch. Idempotent across the swarm — popping a missing key is fine.
        actor.summon_dmg_dealt.pop(summon["vi"], None)
        actor.summons.append(summon)
    aura_tag = f" + Aura {aura['key']}" if aura and aura.get("key") else ""
    swarm_tag = f"×{count} " if count > 1 else ""
    session.log.append(
        f"    {spec.get('emoji', '✨')} **{actor.name}** triệu hồi "
        f"{swarm_tag}**{spec.get('vi', 'Triệu Hồi Vật')}** "
        f"({turns}t · {dmg:,}/lượt{aura_tag})"
    )


def aura_stat_bonus(combatant: Combatant) -> dict[str, float]:
    """Return the summed stat-bonus dict from every distinct active aura.

    Auras stack only across **different** aura keys — duplicate copies of
    the same aura don't compound. Three Mộc summons all carrying
    ``BuffSinhCo`` therefore grant +5 % HP regen total (one copy of
    BuffSinhCo), but mixing in a Quang summon with ``BuffNhietTinh`` adds
    its full payload on top. Build diversity is rewarded; spam isn't.

    When the same aura key shows up on multiple summons with different
    ``overrides``, the first one encountered (oldest summon) wins — the
    aura is "claimed" for the fight and later duplicates are ignored.
    Per-summon override stats win per-stat over the meta default exactly
    like ``get_combat_modifiers`` does for normal effect overrides.
    """
    if not combatant.summons:
        return {}
    from src.game.engine.effects import EFFECTS
    result: dict[str, float] = {}
    seen: set[str] = set()
    for s in combatant.summons:
        key = s.get("aura_buff_key")
        if not key or key in seen:
            continue
        meta = EFFECTS.get(key)
        if not meta:
            continue
        seen.add(key)
        override_stats = (s.get("aura_buff_overrides") or {}).get("stat_bonus") or {}
        for stat, val in meta.stat_bonus.items():
            effective = override_stats.get(stat, val)
            if isinstance(effective, (int, float)):
                result[stat] = result.get(stat, 0.0) + float(effective)
        # Override-only stats (not in meta) also contribute.
        for stat, val in override_stats.items():
            if stat not in meta.stat_bonus and isinstance(val, (int, float)):
                result[stat] = result.get(stat, 0.0) + float(val)
    return result


def tick_summons(
    session: "CombatSession", actor: Combatant, target: Combatant,
) -> None:
    """Run every active summon owned by ``actor`` for one tick.

    Each summon strikes the opponent for its ``dmg`` (scaled at spawn time),
    its turn counter decrements, and expired entries are removed. Auras
    don't need refreshing — ``aura_stat_bonus`` is read on demand by
    ``get_combat_modifiers``, so the bonus tracks summon lifetime exactly
    and stacks naturally across same-key summons. Damage routes through
    ``take_damage`` so shield/Endure/Fortify still apply, but the hit
    bypasses the full skill pipeline (no crit/evade).
    """
    if not actor.summons:
        return
    survivors: list[dict] = []
    from src.game.engine.damage.color import colorize_damage
    from src.game.engine.effects import EFFECTS

    logged_auras: set[str] = set()
    # Track finishers to fire ONCE after the loop, keyed by summon ``vi``
    # so multi-copy swarms (e.g. 9 Cửu Long) burst as one combined hit
    # instead of each dragon nuking a freshly-popped accumulator.
    pending_finishers: dict[str, dict] = {}
    for s in actor.summons:
        if target.is_alive():
            # Per-tick crit roll when the summon carries ``crit_rating`` (set
            # at spawn by an ``actor_overrides`` source, e.g. Cửu Long Thần
            # Hỏa Trận). Uses the same rating→pct curve as the main damage
            # pipeline. Skipped silently when crit_rating is 0.
            hit_dmg = int(s["dmg"])
            is_crit = False
            _cr = int(s.get("crit_rating", 0))
            if _cr > 0:
                crit_chance = _cr / (_cr + target.crit_res_rating + 3000)
                if session.rng.random() < crit_chance:
                    is_crit = True
                    _cdr = int(s.get("crit_dmg_rating", 0))
                    crit_mult = 1.5 + _cdr / (_cdr + 3000)
                    hit_dmg = max(1, int(hit_dmg * crit_mult))
            # Opt-in defender-side elemental pipeline (set via summon_spec's
            # ``respect_target_res``). Today: Vạn Kiếm (so Kim res / Phá Giáp
            # / per-stack debuff shred all apply to each sword swing).
            if s.get("respect_target_res"):
                hit_dmg = _apply_defender_elemental(
                    actor, target, s.get("element"), hit_dmg,
                )
            target.take_damage(hit_dmg)
            actor.summon_dmg_dealt[s["vi"]] = (
                actor.summon_dmg_dealt.get(s["vi"], 0) + hit_dmg
            )
            tag = colorize_damage(f"-{hit_dmg:,} HP", s.get("element"))
            crit_tag = " 💥" if is_crit else ""
            session.log.append(
                f"    {s['emoji']} **{s['vi']}** đánh **{target.name}** {tag}{crit_tag}"
            )
            # Curse-class summons stamp a debuff on the target each tick.
            # Re-application refreshes duration; the debuff's stat_bonus
            # caps via ``get_combat_modifiers`` aggregation, so multiple
            # summons sharing the same key never exceed the meta magnitude.
            on_hit = s.get("on_hit_debuff") or {}
            on_hit_key = on_hit.get("key")
            if on_hit_key and EFFECTS.get(on_hit_key):
                # Optional ``chance`` gate — when omitted the debuff always
                # applies (back-compat with existing curse summons).
                _chance = float(on_hit.get("chance", 1.0))
                if _chance >= 1.0 or session.rng.random() < _chance:
                    from src.game.systems.combat.casting import inflict_debuff
                    inflict_debuff(
                        session,
                        on_hit_key,
                        EFFECTS[on_hit_key],
                        target,
                        actor=actor,
                        overrides=on_hit.get("overrides"),
                    )
            # Per-hit CCStun roll — independent of on_hit_debuff so a summon
            # can stamp both a flavor debuff (e.g. burn) and a CC roll.
            _stun_chance = float(s.get("stun_chance", 0.0))
            if _stun_chance > 0 and (
                _stun_chance >= 1.0 or session.rng.random() < _stun_chance
            ):
                from src.game.systems.combat.casting import inflict_debuff
                _stun_meta = EFFECTS.get("CCStun")
                if _stun_meta is not None:
                    inflict_debuff(session, "CCStun", _stun_meta, target, actor=actor)
        # Log each distinct aura once per tick. Duplicate auras (same key
        # from multiple summons) don't stack — see ``aura_stat_bonus``.
        aura_key = s.get("aura_buff_key")
        if aura_key and aura_key not in logged_auras and EFFECTS.get(aura_key):
            meta = EFFECTS[aura_key]
            session.log.append(
                f"    {meta.emoji} Hào quang **{meta.vi}** đang bao phủ "
                f"**{actor.name}**"
            )
            logged_auras.add(aura_key)
        s["turns"] -= 1
        if s["turns"] > 0:
            survivors.append(s)
        else:
            session.log.append(
                f"    {s['emoji']} **{s['vi']}** kết thúc triệu hồi."
            )
            # Stash the finisher spec; firing happens after the loop so the
            # burst sees the FULL batch accumulator (every dragon's tick this
            # turn included) and pops it exactly once per group.
            finisher = s.get("expire_finisher") or {}
            if finisher:
                pending_finishers.setdefault(s["vi"], finisher)
    actor.summons = survivors

    # ── On-expire finisher pass ────────────────────────────────────────────
    # Fire one burst per summon-group whose last copy expired this tick.
    # Survivors with the same ``vi`` cancel the burst (a partial expiry
    # leaves the batch alive). Accumulator popped so a re-summon starts fresh.
    surviving_vis = {s["vi"] for s in survivors}
    for vi, finisher in pending_finishers.items():
        if vi in surviving_vis:
            continue
        if not target.is_alive():
            actor.summon_dmg_dealt.pop(vi, None)
            continue
        accum = actor.summon_dmg_dealt.pop(vi, 0)
        burst = max(0, int(accum * float(finisher.get("pct", 0.0))))
        if burst <= 0:
            continue
        target.take_damage(burst)
        tag = colorize_damage(f"-{burst:,} HP", finisher.get("element"))
        session.log.append(
            f"    {finisher.get('emoji', '💥')} "
            f"**{finisher.get('vi', vi + ' Tận Sát')}** "
            f"bùng nổ trên **{target.name}** {tag}"
        )

    # ── Consume-at-count pass ──────────────────────────────────────────────
    # Some swarms (e.g. Vạn Kiếm Quy Tông's 10 swords) opt into a "consume
    # when the group is full" burst: every summon in the group is removed,
    # a scaled hit lands on the target, and an optional Combatant counter
    # field (Sword Heart stacks) gets incremented. The trigger is data-only
    # via the ``consume`` sub-dict on the summon spec — vanilla summons
    # without ``consume_at_count`` are untouched.
    if not actor.summons or not target.is_alive():
        return
    consume_by_vi: dict[str, dict] = {}
    counts_by_vi: dict[str, int] = {}
    amp_field_by_vi: dict[str, str] = {}
    for s in actor.summons:
        cs = s.get("consume_spec")
        if cs and cs.get("at_count"):
            consume_by_vi.setdefault(s["vi"], cs)
            counts_by_vi[s["vi"]] = counts_by_vi.get(s["vi"], 0) + 1
            if s.get("dmg_amp_field"):
                amp_field_by_vi.setdefault(s["vi"], s["dmg_amp_field"])
    fired_vis: set[str] = set()
    for vi, cs in consume_by_vi.items():
        threshold = int(cs.get("at_count", 0))
        if counts_by_vi.get(vi, 0) < threshold:
            continue
        # Optional gate: when the spec sets ``requires_formation_skill``,
        # the burst only fires if the actor carries that formation skill
        # in ``formation_skill_keys`` (set at build time from the active
        # formation's ``formation_skill_key``). Lets a defensive skill
        # spawn the same summon swarm without auto-granting the formation's
        # signature burst — e.g. Hộ Thể Kiếm Cương stacks Vạn Kiếm but the
        # 600 % ATK Quy Tông burst requires the Vạn Kiếm Quy Tông formation
        # to actually be running. Vanilla consume_specs without this field
        # always fire (back-compat).
        req_formation = cs.get("requires_formation_skill")
        if req_formation and req_formation not in getattr(actor, "formation_skill_keys", []):
            continue
        # Compute the burst — scales off the actor's current stats so it
        # tracks buffs in play at consume time.
        burst = int(cs.get("dmg", 0))
        burst += int(actor.atk * float(cs.get("dmg_pct_of_atk", 0.0)))
        burst += int(actor.matk * float(cs.get("dmg_pct_of_matk", 0.0)))
        # Same ``dmg_amp_field`` the per-turn swing used (carried on the
        # summon at spawn), applied to the burst so the consume payoff
        # scales identically with the passive — e.g. Kiếm Tâm Thông Minh.
        _amp_field = amp_field_by_vi.get(vi)
        if _amp_field:
            _amp = float(getattr(actor, _amp_field, 0.0))
            if _amp > 0:
                burst = int(burst * (1.0 + _amp))
        # Optional ``element`` bonus (folds element_dmg_bonus + buff dmg)
        # so a Kim burst sees the actor's full kim build-up.
        _elem = cs.get("element")
        if _elem:
            _bonus = float(get_combat_modifiers(actor).get(f"dmg_bonus_{_elem}", 0.0))
            _bonus += float(actor.element_dmg_bonus.get(_elem, 0.0))
            # Sword-Heart kim amp folds in too so the consume already
            # benefits from the stacks it's about to top up.
            if _elem == "kim" and actor.sword_heart_stacks > 0:
                _bonus += 0.05 * actor.sword_heart_stacks
            if _bonus:
                burst = int(burst * (1.0 + _bonus))
        burst = max(1, burst)
        # Opt-in defender-side elemental pipeline for the consume burst —
        # same gate as the per-tick swing (set via consume_spec's
        # ``respect_target_res``). Today: Vạn Kiếm Quy Tông's 10-stack burst.
        if cs.get("respect_target_res"):
            burst = _apply_defender_elemental(actor, target, _elem, burst)
        target.take_damage(burst)
        tag = colorize_damage(f"-{burst:,} HP", _elem)
        session.log.append(
            f"    {cs.get('emoji', '⚔️')} "
            f"**{cs.get('vi', vi + ' Quy Tông')}** "
            f"thi triển trên **{target.name}** {tag}"
        )
        # Stack-gain: bump a named Combatant counter (Sword Heart etc.) by
        # ``gain``, clamped to ``cap``. Safe no-op when the field is missing.
        stack_field = cs.get("stack_field")
        stack_gain = int(cs.get("stack_gain", 0))
        stack_cap = int(cs.get("stack_cap", 0))
        if stack_field and stack_gain and hasattr(actor, stack_field):
            current = int(getattr(actor, stack_field, 0))
            new_val = current + stack_gain
            if stack_cap > 0:
                new_val = min(new_val, stack_cap)
            setattr(actor, stack_field, new_val)
            session.log.append(
                f"    ✨ **{actor.name}** đạt **{new_val}** "
                f"{cs.get('stack_vi', stack_field)}."
            )
        fired_vis.add(vi)
    if fired_vis:
        actor.summons = [s for s in actor.summons if s["vi"] not in fired_vis]
        # Reset accumulators for fired groups so a re-summon next turn
        # starts fresh.
        for vi in fired_vis:
            actor.summon_dmg_dealt.pop(vi, None)


# ── 8. Vital-essence awakening riders (Bách Thể Chú Linh) ────────────────────
# One opt-in ``vital_rider`` dict per awakening skill. Every sub-field is
# independent and optional so each granted skill declares only its own
# signature mechanic. Bonus-damage branches all route through the shared
# ``_deal_capped_true_dmg`` tail (capped at a % of target max HP) so no rider
# can one-shot a boss; heals route through ``session._apply_heal`` so heal
# reductions / crits / overheal conversion apply normally.
#
#     "vital_rider": {
#         "label": "🍖 Thôn Phệ",
#         "heal_pct_of_dmg": 0.30,          # devour: heal % of damage dealt
#         "execute_below_pct": 0.30,        # target under X% HP after the hit →
#         "execute_bonus_pct": 1.0,         #   bonus true dmg = dealt × pct
#         "true_dmg_pct_of_dmg": 0.30,      # portion re-dealt as true damage
#         "detonate_stack": "burn",         # per-stack instant burst:
#         "detonate_pct_per_stack": 0.15,   #   dealt × pct × stacks
#         "detonate_stack_cap": 8,
#         "mp_surge_pct_of_current": 0.25,  # burn extra MP after the cast →
#         "mp_surge_dmg_per_mp": 2.0,       #   bonus true dmg = drained × f
#         "mp_refund_on_kill_pct": 0.5,     #   refund a share if the cast killed
#         "self_heal_pct_max_hp": 0.12,
#         "cleanse_count": 1,               # strip N random debuffs from self
#         "revenge_after_revive_pct": 0.40, # bonus dmg once phoenix revive spent
#         "shield_pct_max_hp": 0.15,        # raise a shield (add_shield clamp)
#         "per_debuff_bonus_pct": 0.08,     # dealt × pct × target debuff count
#         "per_debuff_cap": 6,
#     }

_DETONATE_STACK_ATTRS: dict[str, str] = {
    "burn":   "burn_stacks",
    "bleed":  "bleed_stacks",
    "poison": "poison_stacks",
    "shock":  "shock_stacks",
}


def _count_cleansable(combatant: Combatant) -> int:
    """Number of cleansable (debuff/CC) effects currently on ``combatant``."""
    from src.game.engine.effects import EFFECTS
    return sum(
        1 for k in combatant.effects
        if (m := EFFECTS.get(k)) is not None and m.cleansable
    )


def apply_vital_rider(
    session: "CombatSession", actor: Combatant, target: Combatant,
    skill_data: dict, dealt_total: int, cast_ctx: dict,
) -> None:
    """Run the ``vital_rider`` spec after a damaging cast (top-level only).

    Called after ``apply_skill_effects`` so debuffs stamped by THIS cast
    count for the detonate / per-debuff branches. Self-directed branches
    (heal, cleanse, shield) fire even when the target died to the main hit;
    bonus-damage branches gate on ``target.is_alive()`` and ``dealt > 0``.
    """
    spec = skill_data.get("vital_rider")
    if not spec:
        return
    from .casting import _deal_capped_true_dmg

    label = spec.get("label", "✨ Bí Thuật")
    dealt = max(0, int(dealt_total))

    # ── Devour heal (Thao Thiết) ─────────────────────────────────────────
    heal_pct = float(spec.get("heal_pct_of_dmg", 0.0))
    if heal_pct > 0 and dealt > 0 and actor.is_alive():
        healed = session._apply_heal(actor, int(dealt * heal_pct))
        if healed > 0:
            session.log.append(
                f"    {label} — nuốt chửng huyết khí: +{healed:,} HP"
            )

    # ── Execute (Thao Thiết: ăn tươi nuốt sống) ──────────────────────────
    exec_below = float(spec.get("execute_below_pct", 0.0))
    exec_bonus = float(spec.get("execute_bonus_pct", 0.0))
    if (
        exec_below > 0 and exec_bonus > 0 and dealt > 0
        and target.is_alive() and target.hp_max > 0
        and target.hp / target.hp_max < exec_below
    ):
        _deal_capped_true_dmg(
            session, target, int(dealt * exec_bonus), f"{label} (kết liễu)",
        )

    # ── True-damage portion (Chân Long: Long Uy) ─────────────────────────
    true_pct = float(spec.get("true_dmg_pct_of_dmg", 0.0))
    if true_pct > 0 and dealt > 0 and target.is_alive():
        _deal_capped_true_dmg(session, target, int(dealt * true_pct), label)

    # ── DoT detonation (Tất Phương: burn burst) ──────────────────────────
    det_kind = spec.get("detonate_stack")
    det_pct = float(spec.get("detonate_pct_per_stack", 0.0))
    if det_kind in _DETONATE_STACK_ATTRS and det_pct > 0 and dealt > 0 \
            and target.is_alive():
        stacks = int(getattr(target, _DETONATE_STACK_ATTRS[det_kind], 0))
        stacks = min(stacks, int(spec.get("detonate_stack_cap", 8)))
        if stacks > 0:
            _deal_capped_true_dmg(
                session, target, int(dealt * det_pct * stacks),
                f"{label} ×{stacks} tầng",
            )

    # ── MP surge (Cửu Anh: damage bought with linh lực) ──────────────────
    surge_pct = float(spec.get("mp_surge_pct_of_current", 0.0))
    surge_factor = float(spec.get("mp_surge_dmg_per_mp", 0.0))
    if surge_pct > 0 and surge_factor > 0 and dealt > 0:
        drained = int(actor.mp * surge_pct)
        if drained > 0:
            actor.mp -= drained
            if target.is_alive():
                _deal_capped_true_dmg(
                    session, target, int(drained * surge_factor),
                    f"{label} (-{drained:,} MP)",
                )
            refund_pct = float(spec.get("mp_refund_on_kill_pct", 0.0))
            if refund_pct > 0 and cast_ctx.get("killed"):
                refund = int(drained * refund_pct)
                actor.mp = min(actor.mp_max, actor.mp + refund)
                session.log.append(
                    f"    {label} — kết liễu hoàn trả +{refund:,} MP"
                )

    # ── Self heal + cleanse (Phượng Hoàng) ───────────────────────────────
    self_heal = float(spec.get("self_heal_pct_max_hp", 0.0))
    if self_heal > 0 and actor.is_alive():
        healed = session._apply_heal(actor, int(actor.hp_max * self_heal))
        if healed > 0:
            session.log.append(f"    {label} — lửa tái sinh: +{healed:,} HP")
    cleanse_n = int(spec.get("cleanse_count", 0))
    if cleanse_n > 0 and actor.is_alive():
        from src.game.engine.effects import EFFECTS
        for _ in range(cleanse_n):
            cleansable = [
                k for k in list(actor.effects)
                if (m := EFFECTS.get(k)) is not None and m.cleansable
            ]
            if not cleansable:
                break
            removed = session.rng.choice(cleansable)
            del actor.effects[removed]
            actor.effect_overrides.pop(removed, None)
            session.log.append(f"    {label} — thanh tẩy *{removed}*")

    # ── Post-revive vengeance (Phượng Hoàng) ─────────────────────────────
    revenge_pct = float(spec.get("revenge_after_revive_pct", 0.0))
    if revenge_pct > 0 and dealt > 0 and target.is_alive() \
            and actor.phoenix_revive_used:
        _deal_capped_true_dmg(
            session, target, int(dealt * revenge_pct), f"{label} (phục hận)",
        )

    # ── Shield raise (Kỳ Lân) ────────────────────────────────────────────
    shield_pct = float(spec.get("shield_pct_max_hp", 0.0))
    if shield_pct > 0 and actor.is_alive():
        gained = actor.add_shield(int(actor.hp_max * shield_pct))
        if gained > 0:
            session.log.append(f"    {label} — kết giới thụy quang +{gained:,} 🛡️")

    # ── Per-debuff amp (Kỳ Lân: smite the corrupted) ─────────────────────
    per_debuff = float(spec.get("per_debuff_bonus_pct", 0.0))
    if per_debuff > 0 and dealt > 0 and target.is_alive():
        count = min(_count_cleansable(target), int(spec.get("per_debuff_cap", 6)))
        if count > 0:
            _deal_capped_true_dmg(
                session, target, int(dealt * per_debuff * count),
                f"{label} ×{count} tà khí",
            )
