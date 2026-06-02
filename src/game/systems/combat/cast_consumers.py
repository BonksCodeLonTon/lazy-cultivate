"""Post-cast consumer registry.

These hooks fire at the *very end* of ``cast_skill`` (after damage, after
effect application, after summon spawning) and read a count field on
``skill_data`` that specifies how many effects to strip from the target.
Used by finisher-class skills: Cửu U Thần Trảo eats N debuffs the target
was carrying (the debuffs already contributed their amp to the cast just
landed), Thánh Quang Phán Quyết randomly tears N buffs off the target.

Before this module, each consumer lived as its own 20-line block inside
``cast_skill``: read int, gate on count + target alive, filter effects
by kind, pop entries, log. The registry collapses the boilerplate into a
small handler per consumer — see the two below for templates.

Each consumer is a function decorated with
``@register_post_cast_consumer("skill_field_name")`` that receives the
field value, actor, target, and session. The dispatcher
``apply_post_cast_consumers`` walks the registry once per cast and only
invokes handlers whose ``skill_data`` field is truthy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant

    from .session import CombatSession


# Consumer signature: (field_value, skill_data, actor, target, session) → None.
# All side effects happen inside the handler. ``skill_data`` is the full
# spec dict for the casting skill — consumers that need to exclude the
# casting skill from their target set (e.g. self-cooldown reducers that
# must not reset their own cd) read ``skill_data.get("key")`` for the id.
PostCastConsumerFn = Callable[
    [Any, dict, "Combatant", "Combatant", "CombatSession"],
    None,
]


@dataclass(frozen=True)
class PostCastConsumer:
    """One registered consumer: the ``skill_data`` field it watches + handler."""
    skill_field: str
    fn: PostCastConsumerFn


_POST_CAST_CONSUMERS: list[PostCastConsumer] = []


def register_post_cast_consumer(
    skill_field: str,
) -> Callable[[PostCastConsumerFn], PostCastConsumerFn]:
    """Decorator — register ``fn`` as the consumer for ``skill_data[skill_field]``.

    The dispatcher only invokes the handler when the field is truthy
    (count > 0 / non-empty dict), so handlers don't need to re-check
    presence — but they DO own the "target alive" gate since some future
    consumers might want to fire on dead targets.
    """
    def deco(fn: PostCastConsumerFn) -> PostCastConsumerFn:
        _POST_CAST_CONSUMERS.append(
            PostCastConsumer(skill_field=skill_field, fn=fn)
        )
        return fn
    return deco


def apply_post_cast_consumers(
    skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Walk every registered post-cast consumer; invoke each truthy match.

    Consumers run in registration order. A consumer that strips effects
    is responsible for popping both ``target.effects`` and
    ``target.effect_overrides`` to keep the per-instance override map
    consistent with the active-effect set.
    """
    for consumer in _POST_CAST_CONSUMERS:
        value = skill_data.get(consumer.skill_field)
        if not value:
            continue
        consumer.fn(value, skill_data, actor, target, session)


# ── Built-in consumers ─────────────────────────────────────────────────
# Byte-identical log strings to the former inline blocks in cast_skill.


@register_post_cast_consumer("consume_target_debuffs")
def _consume_debuffs(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Cửu U Thần Trảo finisher — strip N DEBUFFs from target post-cast.

    Counts only ``EffectKind.DEBUFF`` entries (CC and buffs are untouched).
    Removed AFTER damage + apply_skill_effects so the consumed debuffs
    already contributed their amp to this cast — the strip is a flavor
    payoff, not a damage prerequisite. Picks debuffs in insertion order
    (first N entries on the target).
    """
    from src.game.engine.effects import EFFECTS, EffectKind

    consume_n = int(value)
    if consume_n <= 0 or not target.is_alive():
        return
    debuff_keys = [
        k for k in list(target.effects)
        if (m := EFFECTS.get(k)) and m.kind is EffectKind.DEBUFF
    ]
    consumed: list[str] = []
    for k in debuff_keys[:consume_n]:
        target.effects.pop(k, None)
        target.effect_overrides.pop(k, None)
        meta = EFFECTS.get(k)
        consumed.append(meta.vi if meta else k)
    if consumed:
        session.log.append(
            f"    🩸 **{actor.name}** Cửu U nuốt chửng {len(consumed)} trạng thái: "
            f"{', '.join(consumed)}"
        )


@register_post_cast_consumer("compress_target_buff_duration")
def _compress_buffs(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Trấn Sơn Chi Lực — shorten every buff on target by N turns; pop expired.

    Spec shape (dict OR plain int for shorthand):
        {"turns": 2}   →   2-turn compression
        2              →   same

    Counts only ``EffectKind.BUFF`` entries (debuffs / CC untouched).
    Buffs whose duration drops to ``<= 0`` are popped immediately along
    with their per-instance overrides — same teardown the natural-expiry
    path in ``tick_effects`` does. Logs which buffs were shortened vs
    fully expired; silent when no buffs were on the target.
    """
    from src.game.engine.effects import EFFECTS, EffectKind

    if isinstance(value, dict):
        turns = int(value.get("turns", 0))
    else:
        turns = int(value)
    if turns <= 0 or not target.is_alive():
        return
    expired: list[str] = []
    shortened: list[str] = []
    for k in list(target.effects):
        meta = EFFECTS.get(k)
        if not meta or meta.kind is not EffectKind.BUFF:
            continue
        new_dur = target.effects[k] - turns
        if new_dur <= 0:
            target.effects.pop(k, None)
            target.effect_overrides.pop(k, None)
            expired.append(meta.vi)
        else:
            target.effects[k] = new_dur
            shortened.append(meta.vi)
    if not (expired or shortened):
        return
    parts: list[str] = []
    if shortened:
        parts.append(f"rút ngắn {', '.join(shortened)}")
    if expired:
        parts.append(f"tan biến {', '.join(expired)}")
    session.log.append(
        f"    🗿 **{actor.name}** Trấn Sơn ép — {' • '.join(parts)}"
    )


@register_post_cast_consumer("reduce_self_cooldowns_by_element")
def _reduce_self_cooldowns_by_element(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Súc Địa Thành Thốn — flat -N turns on caster's own cooldowns, element-filtered.

    Spec shape:
        {"turns": 2, "element": "tho"}

    Walks ``actor.cooldowns``, looks up each on-cd skill's element via the
    registry, and only reduces entries whose ``skill["element"]`` matches.
    Floors at 0. Distinct from the existing ``reduce_self_cooldowns_by``
    field (Phong Thần Thối) which is flat across ALL skills — this one
    narrows scope to a single element so the player's off-element rotation
    can't piggyback on the compression.

    The currently-casting skill is excluded explicitly via
    ``skill_data["key"]`` — ``set_cooldown`` runs *before* this consumer
    in cast_skill, so the casting skill would otherwise be included and
    instantly shave 2 turns off its own freshly-set cd.
    """
    from src.data.registry import registry

    if not isinstance(value, dict):
        return
    turns = int(value.get("turns", 0))
    target_elem = value.get("element")
    if turns <= 0 or not target_elem:
        return
    self_key = skill_data.get("key")
    reduced: list[str] = []
    for k, cd in list(actor.cooldowns.items()):
        if cd <= 0 or k == self_key:
            continue
        sk = registry.get_skill(k)
        if not sk or sk.get("element") != target_elem:
            continue
        new_cd = max(0, cd - turns)
        if new_cd == cd:
            continue
        actor.cooldowns[k] = new_cd
        reduced.append(sk.get("vi", k))
    if not reduced:
        return
    head = ", ".join(reduced[:3])
    tail = f" + {len(reduced) - 3} chiêu" if len(reduced) > 3 else ""
    session.log.append(
        f"    🌀 **{actor.name}** Súc Địa ép — {len(reduced)} chiêu "
        f"{target_elem.upper()} rút {turns} lượt: {head}{tail}"
    )


@register_post_cast_consumer("extend_target_cooldowns")
def _extend_cooldowns(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Trọng Lực Áp Chế — gravity delays target's skill recovery by N turns.

    Spec shape (dict OR plain int for shorthand):
        {"turns": 2}   →   +2 turns to every cooldown the target has
        2              →   same

    Walks ``target.cooldowns`` and bumps every positive entry by ``turns``.
    Skills already off-cooldown (entry missing or zero) are not affected —
    the extension only delays SKILLS THE TARGET JUST USED. Silently does
    nothing when the target has nothing on cooldown, so a vanilla cast on
    a fresh enemy pays no overhead.

    The log truncates the extended-skill list to 3 names plus a count for
    the rest so a boss with a fat rotation doesn't bloat the combat log.
    """
    from src.data.registry import registry

    if isinstance(value, dict):
        turns = int(value.get("turns", 0))
    else:
        turns = int(value)
    if turns <= 0 or not target.is_alive():
        return
    extended: list[str] = []
    for k, cd in list(target.cooldowns.items()):
        if cd <= 0:
            continue
        target.cooldowns[k] = cd + turns
        skill = registry.get_skill(k)
        extended.append(skill.get("vi", k) if skill else k)
    if not extended:
        return
    head = ", ".join(extended[:3])
    tail = f" + {len(extended) - 3} chiêu khác" if len(extended) > 3 else ""
    session.log.append(
        f"    🌀 **{actor.name}** Trọng Lực ép — {len(extended)} chiêu trễ "
        f"{turns} lượt: {head}{tail}"
    )


@register_post_cast_consumer("tide_charge")
def _tide_charge_discharge(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Trào Tịch Tích Lãng (Thủy B2) — discharge the tide reservoir every Nth cast.

    The STORE side rides the post-hit path in ``cast_skill`` (mirrors the
    overheal-reservoir capture): each cast banks ``store_pct_of_dmg_dealt``
    of the damage dealt into ``actor.thuy_tide`` (clamped to
    ``reservoir_cap_matk_scale × actor.matk``) and bumps
    ``actor.thuy_tide_casts``. This consumer fires post-cast and, on every
    ``release_every_n_casts``-th cast, emits a Thủy strike for
    ``thuy_tide × release_pct`` through the standard mitigation math
    (mirrors ``overheal_release``), then resets the reservoir + counter.

    Inert for enemies: they never cast the skill, so the counter never
    advances and the reservoir stays 0.
    """
    if not isinstance(value, dict):
        return
    n = int(value.get("release_every_n_casts", 0))
    if n <= 0 or actor.thuy_tide_casts <= 0 or actor.thuy_tide_casts % n != 0:
        return
    reservoir = int(actor.thuy_tide)
    if reservoir <= 0 or not target.is_alive():
        # Counter still resets so the cadence stays aligned to the cast count.
        actor.thuy_tide = 0
        actor.thuy_tide_casts = 0
        return
    from src.game.constants.balance import MAX_ELEMENTAL_RES
    from src.game.engine.damage.color import colorize_damage

    release_pct = float(value.get("release_pct", 1.0))
    released = max(1, int(reservoir * release_pct))
    element = str(value.get("element", "thuy"))
    target_res = max(
        0.0,
        min(
            MAX_ELEMENTAL_RES,
            target.resistances.get(element, 0.0)
            - actor.element_pen.get(element, 0.0),
        ),
    )
    mult = 1.0 + actor.final_dmg_bonus
    strike = max(1, int(released * mult * (1.0 - target_res)))
    target.take_damage(strike)
    actor.thuy_tide = 0
    actor.thuy_tide_casts = 0
    label = str(value.get("release_label", "Hồi Triều"))
    emoji = str(value.get("release_emoji", "🌊"))
    tag = colorize_damage(f"-{strike:,} HP", element)
    session.log.append(
        f"  {emoji} **{actor.name}** **{label}** — dội Triều Khố "
        f"({released:,}) → **{target.name}** {tag} "
        f"| {target.name}: {target.hp:,}/{target.hp_max:,} HP"
    )


@register_post_cast_consumer("overload_recoil")
def _overload_recoil_lockout(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Quá Tải Lôi Bạo (Lôi B4) phase-2 — self-cooldown recoil.

    The INVERSE of ``reduce_self_cooldowns_by_element``: ADD
    ``self_cooldown_lockout_turns`` to every OTHER ``lockout_scope``-element
    skill the caster knows. Phase-1 (shock-stack consume amp) lives in
    ``two_phase_consumers.py`` keyed on the same ``overload_recoil`` block.

    Two ordering facts make the recoil stick:
      * ``set_cooldown`` for THIS skill already ran earlier in ``cast_skill``,
        so the casting skill is present in ``actor.cooldowns`` at full CD —
        it is excluded by ``skill_data["key"]`` so it never lengthens its own
        cd, and it locks the OTHER Lôi skills (off-cd ones too, by stamping a
        bare lockout cd on them).
      * This consumer fires at the very end of ``cast_skill``, after every
        chain / auto-cast the cast itself could trigger. B4 declares no
        ``chain_skill`` / cast_on hooks, so nothing it fires can wipe the
        lockout the same turn. A LATER-turn cooldown reset (e.g. Nghịch Lưu
        Phản Phệ on a subsequent turn) CAN undo it — that is intended
        cross-turn counterplay, not a same-turn bypass.

    Inert for enemies: a non-player Combatant's Lôi skill set is whatever
    its JSON declares; the block only fires when the skill is actually cast,
    and the magnitude is data-driven so a 0-turn lockout is a no-op.
    """
    from src.data.registry import registry

    if not isinstance(value, dict):
        return
    turns = int(value.get("self_cooldown_lockout_turns", 0))
    scope = value.get("lockout_scope")
    if turns <= 0 or not scope:
        return
    self_key = skill_data.get("key")
    locked: list[str] = []
    for k in actor.skill_keys:
        if k == self_key:
            continue
        sk = registry.get_skill(k)
        if not sk or sk.get("element") != scope:
            continue
        before = actor.cooldowns.get(k, 0)
        actor.cooldowns[k] = before + turns
        locked.append(sk.get("vi", k))
    if not locked:
        return
    head = ", ".join(locked[:3])
    tail = f" + {len(locked) - 3} chiêu" if len(locked) > 3 else ""
    session.log.append(
        f"    🔌 **{actor.name}** Quá Tải phản phệ — {len(locked)} chiêu "
        f"{str(scope).upper()} khóa thêm {turns} lượt: {head}{tail}"
    )


@register_post_cast_consumer("cooldown_reservoir")
def _cooldown_reservoir(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Nghịch Lưu Phản Phệ — convert remaining cooldown turns into shield + dmg.

    Post-cast (fires for 0-dmg defense skills too, unlike the pre-damage
    consumer registry which only runs inside the ``base_dmg > 0`` block).
    ``set_cooldown`` for THIS skill already ran earlier in ``cast_skill``, so
    the casting skill's own key is present in ``actor.cooldowns`` at full CD —
    it MUST be excluded from both the conversion sum and the wipe (otherwise
    the skill would reset its own freshly-set cooldown). Exclusion is by
    ``skill_data["key"]``.

    Sum every OTHER skill's remaining cooldown turns → ``R``. Then:
      * grant ``min(shield_cap, R × shield_per_cd_turn)`` shield via the real
        ``add_shield`` (respects ``shield_cap()`` / ``shield_taken_reduce``);
      * stamp ``final_dmg_bonus = min(buff_fdb_cap, R × buff_fdb_per_cd_turn)``
        copy-on-write onto the actor's live ``effect_overrides["BuffNghichLuu"]``
        stat_bonus (the buff was applied by ``apply_support_skill`` already),
        so the buff's magnitude is per-cast;
      * set every OTHER skill's cooldown to ``wipe_to``.

    Spec shape:
        {"shield_per_cd_turn": int, "shield_cap": int, "wipe_to": int,
         "buff_fdb_per_cd_turn": float, "buff_fdb_cap": float}
    """
    if not isinstance(value, dict):
        return
    own_key = skill_data.get("key")
    R = sum(
        max(0, v) for k, v in actor.cooldowns.items()
        if k != own_key and v > 0
    )
    if R <= 0:
        return

    shield_per = int(value.get("shield_per_cd_turn", 0))
    shield_cap = int(value.get("shield_cap", 0))
    shield_want = min(shield_cap, R * shield_per) if shield_per > 0 else 0
    gained = actor.add_shield(shield_want) if shield_want > 0 else 0

    fdb_per = float(value.get("buff_fdb_per_cd_turn", 0.0))
    fdb_cap = float(value.get("buff_fdb_cap", 0.0))
    fdb = min(fdb_cap, R * fdb_per) if fdb_per > 0 else 0.0
    if fdb > 0 and actor.has_effect("BuffNghichLuu"):
        ovr = actor.effect_overrides.setdefault("BuffNghichLuu", {})
        sb = dict(ovr.get("stat_bonus") or {})
        sb["final_dmg_bonus"] = fdb
        ovr["stat_bonus"] = sb

    wipe_to = int(value.get("wipe_to", 1))
    wiped = 0
    for k in list(actor.cooldowns.keys()):
        if k != own_key and actor.cooldowns[k] > wipe_to:
            actor.cooldowns[k] = wipe_to
            wiped += 1

    session.log.append(
        f"    🔄 **{actor.name}** Nghịch Lưu Phản Phệ — quy đổi {R} lượt "
        f"hồi chiêu → +{gained:,} Khiên, +{int(fdb * 100)}% ST "
        f"(reset {wiped} kỹ năng về {wipe_to}t)"
    )


@register_post_cast_consumer("strip_target_buffs")
def _strip_buffs(
    value: Any, skill_data: dict, actor: "Combatant", target: "Combatant",
    session: "CombatSession",
) -> None:
    """Phán-Quyết-class — randomly tear N BUFFs off target post-cast.

    Counts only ``EffectKind.BUFF`` so debuffs on the target are
    untouched. Random selection (via ``session.rng``) so the player can't
    game application order to shield the most valuable buff.
    """
    from src.game.engine.effects import EFFECTS, EffectKind

    strip_n = int(value)
    if strip_n <= 0 or not target.is_alive():
        return
    buff_keys = [
        k for k in list(target.effects)
        if (m := EFFECTS.get(k)) and m.kind is EffectKind.BUFF
    ]
    if not buff_keys:
        return
    picks = session.rng.sample(buff_keys, min(strip_n, len(buff_keys)))
    stripped: list[str] = []
    for k in picks:
        target.effects.pop(k, None)
        target.effect_overrides.pop(k, None)
        meta = EFFECTS.get(k)
        stripped.append(meta.vi if meta else k)
    if stripped:
        session.log.append(
            f"    ☀️ **{actor.name}** Thánh Quang tước đoạt {len(stripped)} buff: "
            f"{', '.join(stripped)}"
        )
