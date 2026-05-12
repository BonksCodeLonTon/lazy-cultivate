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

from src.game.systems.combatant import Combatant

if TYPE_CHECKING:
    from .session import CombatSession


# ── 2. Charge bonus ──────────────────────────────────────────────────────────

def apply_charge_bonus(
    session: "CombatSession", actor: Combatant, target: Combatant,
    skill_key: str, skill_data: dict, base_dmg_dealt: int,
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
    if not trigger(actor, target, parent_skill_key, spec, ctx or {}):
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
    session.log.append(
        f"    🔗 **{actor.name}** liên hoàn → **{chain_data.get('vi', chain_key)}** "
        f"({pct * 100:.0f}% ST)"
    )
    # Lazy import to break the cycle: skill_extras ← casting ← skill_extras.
    from .casting import cast_skill
    cast_skill(session, actor, target, chain_key, scaled, mp_cost=0)


# ── 4. Auto-cast on stacks ───────────────────────────────────────────────────

_STACK_FIELD: dict[str, str] = {
    "burn": "burn_stacks",
    "bleed": "bleed_stacks",
    "shock": "shock_stacks",
    "hoa_van": "hoa_van_stacks",
}


def _stack_count(target: Combatant, stack_name: str) -> int:
    field = _STACK_FIELD.get(stack_name)
    return int(getattr(target, field, 0)) if field else 0


def _consume_stacks(target: Combatant, stack_name: str) -> int:
    """Zero the stack counter and return the count that was consumed."""
    field = _STACK_FIELD.get(stack_name)
    if not field:
        return 0
    n = int(getattr(target, field, 0))
    setattr(target, field, 0)
    return n


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
    for _ in range(count):
        summon = {
            "vi": spec.get("vi", "Triệu Hồi Vật"),
            "emoji": spec.get("emoji", "✨"),
            "element": spec.get("element"),
            "dmg": dmg,
            "turns": turns,
        }
        if aura and aura.get("key"):
            summon["aura_buff_key"] = aura["key"]
            summon["aura_buff_overrides"] = aura.get("overrides")
        # Optional ``on_hit_debuff`` lets a summon stamp / refresh a debuff
        # on the target every tick — used by curse-class summons whose
        # flavor is "the demon marks you" rather than "the demon hits you".
        if on_hit_debuff and on_hit_debuff.get("key"):
            summon["on_hit_debuff"] = on_hit_debuff
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
    for s in actor.summons:
        if target.is_alive():
            target.take_damage(s["dmg"])
            tag = colorize_damage(f"-{s['dmg']:,} HP", s.get("element"))
            session.log.append(
                f"    {s['emoji']} **{s['vi']}** đánh **{target.name}** {tag}"
            )
            # Curse-class summons stamp a debuff on the target each tick.
            # Re-application refreshes duration; the debuff's stat_bonus
            # caps via ``get_combat_modifiers`` aggregation, so multiple
            # summons sharing the same key never exceed the meta magnitude.
            on_hit = s.get("on_hit_debuff") or {}
            on_hit_key = on_hit.get("key")
            if on_hit_key and EFFECTS.get(on_hit_key):
                from src.game.systems.combat.casting import inflict_debuff
                inflict_debuff(
                    session,
                    on_hit_key,
                    EFFECTS[on_hit_key],
                    target,
                    actor=actor,
                    overrides=on_hit.get("overrides"),
                )
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
    actor.summons = survivors
