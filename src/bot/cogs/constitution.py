"""Thể Chất Bảng — interactive constitution roster + activation UI.

Entry points:
  * ``render_the_chat_hub(interaction, discord_id, back_fn)`` —
    called from the ``/status`` view to open the hub.

Multi-slot rules (see ``src.game.systems.the_chat``):
  - Non-Thể Tu paths: 1 slot total; activation replaces the single entry.
  - Thể Tu: ``1 + body_realm`` slots (capped at 8). Activation adds into
    the next free slot; players must remove an entry from the hub before
    activating when slots are full.
  - Hỗn Độn Đạo Thể: special 9th slot; activation gated by
    ``requires_all_legendary_equipped`` + ``requires_all_dao_ti``.

Activation always rolls a success chance (rarity-based, with a Thể Tu
bonus). On failure, materials + merit are consumed but nothing is equipped
— matches gacha-style attempt economics.
"""
from __future__ import annotations

import logging
import random

import discord

from src.data.registry import registry
from src.utils import emojis
from src.db.connection import get_session
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.elements import ELEMENT_LABELS_VI
from src.game.constants.grades import Grade
from src.game.systems.the_chat import (
    HON_DON_KEY,
    activation_chance,
    add_to_tracker,
    check_requirements,
    get_constitutions,
    get_tracker,
    is_the_tu,
    max_slots,
    remove_from_tracker,
    roll_activation,
    set_constitutions,
)

PHAM_THE_KEY = "ConstitutionPhamThe"
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.utils.discord_safe import safe_defer

log = logging.getLogger(__name__)

DAO_COT_KEY = "MatDaoCotTinh"
DAO_COT_GRADE = Grade.THIEN


def _activation_cost(const_data: dict) -> tuple[int, int]:
    """Return ``(merit, stones)`` from the constitution's ``cost`` block."""
    cost = const_data.get("cost") or {}
    return int(cost.get("merit", 0)), int(cost.get("stones", 0))

# Fallback material list when a constitution JSON entry is missing
# ``materials`` for some reason. Always cost at least one Đạo Cốt Tinh.
_FALLBACK_MATERIALS: dict[str, int] = {DAO_COT_KEY: 1}


def _required_materials(const_data: dict) -> dict[str, int]:
    """Return {item_key: qty} for activating this constitution."""
    mats = const_data.get("materials")
    if isinstance(mats, dict) and mats:
        return {k: int(v) for k, v in mats.items() if int(v) > 0}
    return dict(_FALLBACK_MATERIALS)


async def _inventory_counts(irepo: InventoryRepository, player_id: int, keys) -> dict[str, int]:
    """Return {item_key: owned_qty} summed across every grade row.

    Constitution materials may sit at multiple grade rows so a single-grade
    lookup misses partial stacks. Counting every row keyed by ``item_key``
    mirrors how the alchemy/forge consume paths total their stacks before
    deducting.
    """
    keyset = set(keys)
    counts: dict[str, int] = {k: 0 for k in keyset}
    for row in await irepo.get_all(player_id):
        if row.item_key in keyset:
            counts[row.item_key] += row.quantity
    return counts


def _format_materials(
    materials: dict[str, int], owned: dict[str, int] | None = None,
) -> str:
    """Render materials as bullet lines: `✅ Đạo Cốt Tinh ×2 (có 5)`."""
    lines: list[str] = []
    for key, need in materials.items():
        item = registry.get_item(key)
        name = item["vi"] if item else key
        have = (owned or {}).get(key, 0)
        mark = "✅" if have >= need else "❌"
        owned_part = f" (có **{have}**)" if owned is not None else ""
        lines.append(f"{mark} {name} ×**{need}**{owned_part}")
    return "\n".join(lines) if lines else "*(không có)*"

_RARITY_META: dict[str, dict] = {
    "common":     {"vi": "Phổ Thông",     "emoji": emojis.for_rarity("common"),    "order": 0, "color": 0x95A5A6},
    "uncommon":   {"vi": "Khá",           "emoji": emojis.for_rarity("uncommon"),  "order": 1, "color": 0x2ECC71},
    "rare":       {"vi": "Hiếm",          "emoji": emojis.for_rarity("rare"),      "order": 2, "color": 0x3498DB},
    "epic":       {"vi": "Sử Thi",        "emoji": emojis.for_rarity("epic"),      "order": 3, "color": 0x9B59B6},
    "legendary":  {"vi": "Truyền Thuyết", "emoji": emojis.for_rarity("legendary"), "order": 4, "color": 0xF1C40F},
}

_BONUS_FORMATTERS: list[tuple[str, str]] = [
    ("hp_pct",                        "❤️ HP +{pct:.0f}%"),
    ("hp_flat_per_realm",             "❤️ HP +{flat}/Cảnh Giới"),
    ("mp_pct",                        "💙 MP +{pct:.0f}%"),
    ("cultivation_speed_bonus",       "⚡ Tốc độ tu luyện +{pct:.0f}%"),
    ("final_dmg_bonus",               "⚔️ ST cuối +{pct:.0f}%"),
    ("final_dmg_reduce",              "🛡️ Giảm ST +{pct:.0f}%"),
    ("crit_rating",                   "💥 Bạo Kích Rating +{flat}"),
    ("crit_dmg_rating",               "💥 Bạo Kích DMG Rating +{flat}"),
    ("evasion_rating",                "🌀 Né Tránh Rating +{flat}"),
    ("crit_res_rating",               "🛡️ Kháng Bạo Rating +{flat}"),
    ("res_all",                       "🛡️ Kháng nguyên tố +{flat}"),
    ("spd_bonus",                     "⚡ Tốc độ +{flat}"),
    ("cooldown_reduce",               "⏱️ Giảm Hồi Chiêu {pct:.0f}%"),
    ("hp_regen_pct",                  "💚 Hồi HP {pct2:.1f}%/lượt"),
    ("hp_regen_flat",                 "💚 Hồi HP +{flat}/lượt"),
    ("mp_regen_pct",                  "💧 Hồi MP {pct2:.1f}%/lượt"),
    ("mp_regen_flat",                 "💧 Hồi MP +{flat}/lượt"),
    ("heal_pct",                      "✨ Trị liệu +{pct:.0f}%"),
    ("burn_on_hit_pct",               "🔥 Thiêu Đốt +{pct:.0f}%"),
    ("bleed_on_hit_pct",              "🩸 Chảy Máu +{pct:.0f}%"),
    ("shock_on_hit_pct",              "⚡ Sốc Điện +{pct:.0f}%"),
    ("mark_on_hit_pct",               "🎯 Ấn Phong +{pct:.0f}%"),
    ("blind_on_hit_pct",              "🌫️ Lóa Mắt +{pct:.0f}%"),
    ("soul_drain_on_hit_pct",         "💀 Hút Hồn +{pct:.0f}%"),
    ("stat_steal_on_hit_pct",         "💠 Cướp Chỉ Số +{pct:.0f}%"),
    ("silence_on_crit_pct",           "🤐 Cấm Phép +{pct:.0f}%"),
    ("heal_reduce_on_hit_pct",        "🚫 Giảm Hồi {pct:.0f}%"),
    ("cleanse_on_turn_pct",           "✨ Thanh Tẩy +{pct:.0f}%"),
    ("true_dmg_pct",                  "🗡️ ST Chuẩn {pct:.0f}%"),
    ("life_steal_pct",                "🩸 Hút Máu +{pct:.0f}% ST gây ra"),
    ("crit_dmg_rating_to_dmg_pct",    "💥 Hủy Diệt Hóa Hình: +{pct:.0f}% Bạo Kích DMG Rating → ST cuối"),
    ("armor_pen_pct",                 "🗡️ Xuyên Giáp +{pct:.0f}%"),
    ("thorn_pct",                     "🌵 Phản +{pct:.0f}% lại kẻ địch"),
    ("shield_regen_pct",              "🛡️ Hồi Khiên {pct:.1f}% Khiên/lượt"),
    ("shield_regen_flat",             "🛡️ Hồi Khiên +{flat}/lượt"),
    ("shield_max_base",               "🛡️ Khiên Gốc +{flat}"),
    ("shield_max_flat",               "🛡️ Khiên Tối Đa +{flat}"),
    ("shield_max_pct",                "🛡️ Khiên Tối Đa +{pct:.0f}%"),
    ("hp_to_shield_pct",              "🔄 Chuyển {pct:.0f}% HP → Khiên"),
    ("matk_from_shield_pct",          "🔮 Pháp Công +{pct:.0f}% Khiên hiện tại"),
    ("atk_from_shield_pct",           "⚔️ Công +{pct:.0f}% Khiên hiện tại"),
    ("endure_threshold_pct",          "🌿 Cội Nguồn Bất Tận: sống sót ở {pct:.0f}% HP khi tử vong"),
    ("endure_cooldown",               "🌿 Hồi chiêu Cội Nguồn: {flat} lượt"),
    ("cleanse_heal_pct",              "💚 Liên Hoa Tịnh Hóa: +{pct:.0f}% HP mỗi lần Thanh Tẩy"),
    ("cleanse_retaliate_dmg_pct",     "☀️ Tịnh Hóa Phản Đòn: ST = {pct:.0f}% Pháp Công mỗi lần Thanh Tẩy"),
    ("kill_buff_per_kill_pct",        "🗡️ Sát Khí Đại Thành: +{pct:.0f}% ST cuối / mạng giết"),
    ("kill_buff_cap",                 "🗡️ Cap Sát Khí: {flat} mạng"),
    ("multi_strike_pct",              "✨ Liên Kích: {pct:.0f}% mỗi đòn đánh thêm 1 lượt"),
    ("multi_strike_dmg_pct",          "✨ ST Liên Kích: {pct:.0f}% ST gốc"),
    ("damage_bonus_from_hp_pct",      "💪 ST theo HP +{pct:.0f}%"),
    ("damage_bonus_from_mp_pct",      "🔷 ST theo MP +{pct:.0f}%"),
    ("damage_bonus_from_shield_pct",  "🧱 ST theo Khiên +{pct:.0f}%"),
    ("damage_bonus_from_evasion_pct", "🌪️ ST theo Né +{pct:.0f}%"),
    ("turn_steal_pct",                "⏩ Cướp Lượt +{pct:.0f}%"),
    ("reflect_pct",                   "🪞 Phản ST +{pct:.0f}%"),
    ("debuff_immune_pct",             "🪬 Miễn Debuff {pct:.0f}%"),
    # NOTE: per-element penetration is read from the generic ``element_pen``
    # dict and rendered dynamically below — don't add unicode-emoji formatters
    # for individual elements here.
    ("burn_dmg_bonus",                "🔥 ST Thiêu Đốt +{pct:.0f}%"),
    ("solar_aura_pct",                "☀️ Hào Quang Lửa: {pct:.1f}% HP/lượt"),
    ("wither_aura_pct",               "🌿 Hấp Linh Khí Tràng: {pct:.1f}% HP/lượt"),
    ("stat_drain_aura_pct",           "🌑 Thôn Thiên Ma Khí: cướp {pct:.0f}% chỉ số đầu trận"),
    ("damage_defer_pct",              "💧 Hoãn Trả: {pct:.0f}% sát thương trải đều"),
    ("damage_defer_turns",            "💧 Số lượt trải đều: {flat}"),
    ("fortify_per_turn_pct",          "🛡️ Hào Quang Củng Cố: +{pct:.1f}% ST cuối + Giảm ST mỗi tầng"),
    ("fortify_stack_cap",             "🛡️ Cap tầng Củng Cố: {flat}"),
    ("fortify_post_hit_dr_pct",       "🛡️ Phòng Ngự Hậu-Thương: +{pct:.0f}% Giảm ST sau khi nhận đòn (1 lượt)"),
    ("loot_qty_bonus",                "🎁 Số lượng vật phẩm rớt +{pct:.0f}%"),
    ("loot_luck_bonus",               "🍀 Cơ hội rớt vật phẩm +{pct:.0f}%"),
    # NOTE: per-element ``res_<elem>`` is rendered dynamically below using
    # the central emoji registry — don't add unicode-emoji formatters here.
    ("phoenix_revive_pct",            "🔥🦅 Niết Bàn Trùng Sinh: hồi {pct:.0f}% HP khi tử vong"),
    ("phoenix_revive_buff_pct",       "🔥 Buff sau Niết Bàn: +{pct:.0f}% chỉ số chiến đấu"),
    ("bleed_dmg_bonus",               "🩸 ST Chảy Máu +{pct:.0f}%"),
    ("poison_dmg_bonus",              "☠️ ST Độc +{pct:.0f}%"),
    ("all_passives_multiplier",       "🌌 Khuếch đại mọi passive ×{flat2:.1f}"),
]
_BOOL_FLAGS: list[tuple[str, str]] = [
    ("dot_can_crit",        "🔥 DoT có thể bạo kích"),
    ("heal_can_crit",       "💚 Trị liệu có thể bạo kích"),
    ("barrier_on_cleanse",  "🛡️ Thanh tẩy tạo khiên"),
    ("thorn_from_shield",   "🌵 Phản đòn từ khiên"),
    ("reflect_applies_effects", "🪞 Phản đòn áp dụng hiệu ứng"),
    ("paralysis_on_crit",   "⚡ Bạo kích có thể gây Tê Liệt"),
    ("poison_immunity",     "🪬 Miễn nhiễm Trúng Độc"),
]


def _rarity_label(rarity: str) -> str:
    meta = _RARITY_META.get(rarity, {"vi": rarity, "emoji": "❔"})
    return f"{meta['emoji']} {meta['vi']}"


_DOT_KIND_LABEL_VI: dict[str, str] = {"burn": "Thiêu Đốt", "bleed": "Chảy Máu", "poison": "Trúng Độc"}
_CRIT_STATE_LABEL_VI: dict[str, str] = {"bleed": "Chảy Máu", "marked": "Đ.Dấu", "drained": "Hút Hồn"}


def _format_bonus_lines(bonuses: dict) -> list[str]:
    lines: list[str] = []
    for key, template in _BONUS_FORMATTERS:
        val = bonuses.get(key)
        if not val:
            continue
        if isinstance(val, bool):
            continue
        if "{pct2" in template:
            lines.append(template.format(pct2=val * 100))
        elif "{pct" in template:
            lines.append(template.format(pct=val * 100))
        elif "{flat2" in template:
            lines.append(template.format(flat2=val))
        else:
            lines.append(template.format(flat=val))
    for key, label in _BOOL_FLAGS:
        if bonuses.get(key):
            lines.append(label)

    # ── Per-element bonuses (resolved against the central emoji registry) ──
    # ``res_<elem>`` lives as a flat key; pen + damage bonus + convert all
    # come from generic dicts (single source of truth, multi-element friendly).
    elem_dmg   = bonuses.get("element_dmg_bonus") or {}
    elem_pen   = bonuses.get("element_pen") or {}
    convert    = bonuses.get("damage_taken_convert_pct") or {}
    for elem, vi in ELEMENT_LABELS_VI.items():
        emoji = emojis.for_element(elem)
        if (r := bonuses.get(f"res_{elem}", 0)):
            lines.append(f"{emoji} Kháng {vi} +{r * 100:.0f}%")
        if (p := elem_pen.get(elem, 0)):
            lines.append(f"{emoji} Xuyên Kháng {vi} {p * 100:.0f}%")
        if (d := elem_dmg.get(elem, 0)):
            lines.append(f"{emoji} ST {vi} +{d * 100:.0f}%")
        if (c := convert.get(elem, 0)):
            lines.append(
                f"{emoji} Hóa Thân {vi}: {c * 100:.0f}% ST nhận vào → ST {vi} "
                f"(chịu Kháng {vi})"
            )

    # ── Grouped DoT-kind / crit-vs-state dicts (post-2026-05 migration) ─────
    # Authors now declare ``dot_dmg_bonus_by_kind: {burn: 0.15}`` etc.; fan
    # each sub-entry out into a labelled line. Mirrors the same per-kind
    # display the pre-migration flat keys used to render.
    for kind, label in _DOT_KIND_LABEL_VI.items():
        if (v := (bonuses.get("dot_dmg_bonus_by_kind") or {}).get(kind, 0)):
            lines.append(f"🔥 ST {label} +{v * 100:.0f}%")
        if (v := (bonuses.get("dot_stack_cap_bonus") or {}).get(kind, 0)):
            lines.append(f"🔥 Cap {label} +{int(v)}")
        if (v := (bonuses.get("dot_per_stack_pct_bonus") or {}).get(kind, 0)):
            lines.append(f"🔥 {label}/Stack +{v * 100:.2f}%")
    for state, amps in (bonuses.get("crit_amp_vs") or {}).items():
        state_label = _CRIT_STATE_LABEL_VI.get(state, state)
        if (r := (amps or {}).get("rating", 0)):
            lines.append(f"🎯 Bạo Kích vs {state_label} +{int(r)}")
        if (d := (amps or {}).get("dmg", 0)):
            lines.append(f"🎯 Bạo Thương vs {state_label} +{int(d)}")
    return lines


# ── Embeds ────────────────────────────────────────────────────────────────────


_HUB_KEY_MATERIALS = ("MatDaoCotTinh", "MatThienDaoTuy", "MatHonNguyenCot")


def _render_equipped_lines(equipped: list[str]) -> str:
    if not equipped:
        return "*(trống)*"
    lines = []
    for i, key in enumerate(equipped, start=1):
        c = registry.get_constitution(key)
        if not c:
            lines.append(f"`[{i}]` {key}")
            continue
        meta = _RARITY_META.get(c.get("rarity", "common"), _RARITY_META["common"])
        lines.append(f"`[{i}]` {meta['emoji']} **{c['vi']}**")
    return "\n".join(lines)


def _hub_embed(player, key_counts: dict[str, int]) -> discord.Embed:
    equipped = get_constitutions(player.constitution_type)
    tracker = get_tracker(player.constitution_tracker)
    the_tu = is_the_tu(player.active_axis)
    slot_cap = max_slots(player.active_axis, player.body_realm)

    mat_lines = []
    for k in _HUB_KEY_MATERIALS:
        item = registry.get_item(k)
        if not item:
            continue
        mat_lines.append(f"🦴 {item['vi']}: **{key_counts.get(k, 0):,}**")
    mat_block = "\n".join(mat_lines)

    path_tag = "🥋 **Thể Tu**" if the_tu else "📿 Khí Tu / Trận Tu"
    standard_equipped = [k for k in equipped if k != HON_DON_KEY]
    hon_don = HON_DON_KEY in equipped

    slot_line = (
        f"🔢 Slot trang bị: **{len(standard_equipped)}/{slot_cap}**"
        + (" + 🌌 Hỗn Độn" if hon_don else "")
    )
    # Tracker count excludes Phàm Thể (the default "no constitution" state) so
    # the displayed number matches the player's earned roster.
    tracker_count = len([k for k in tracker if k != PHAM_THE_KEY])
    tracker_line = f"📚 Đã lĩnh ngộ: **{tracker_count}** Thể Chất"
    path_hint = (
        "Thể Tu mở khóa thêm 1 slot mỗi khi đột phá Luyện Thể, tối đa 8 slot. "
        "Khi đủ 8 slot đều là Truyền Thuyết có thể khai mở **Hỗn Độn Đạo Thể**."
        if the_tu else
        "Chỉ Thể Tu mới có nhiều slot. Con đường Khí Tu / Trận Tu chỉ có thể "
        "mang **1 Thể Chất** duy nhất."
    )
    swap_hint = (
        "💡 Đã lĩnh ngộ rồi thì có thể trang bị / gỡ tự do qua dropdown "
        "**📚 Đã lĩnh ngộ** mà không tốn nguyên liệu."
    )

    desc = (
        f"{path_tag}   ·   {slot_line}\n"
        f"{tracker_line}   ·   "
        f"{emojis.for_currency('merit')} Công Đức: **{player.merit:,}**\n\n"
        f"**Đang trang bị:**\n{_render_equipped_lines(equipped)}\n\n"
        f"{mat_block}\n\n"
        f"{path_hint}\n{swap_hint}"
    )
    return base_embed("🧬 Thể Chất Bảng", desc, color=0xB8860B)


def _detail_embed(
    player, const_data: dict, owned_materials: dict[str, int],
) -> discord.Embed:
    rarity = const_data.get("rarity", "common")
    meta = _RARITY_META.get(rarity, _RARITY_META["common"])
    elem = const_data.get("element")

    title = f"{meta['emoji']} {const_data['vi']}"
    desc_parts: list[str] = [const_data.get("passive_description_vi", "")]

    bonus_lines = _format_bonus_lines(const_data.get("stat_bonuses", {}))
    if bonus_lines:
        desc_parts.append("\n**Chỉ số:**\n" + "\n".join(bonus_lines))

    cost_merit, cost_stones = _activation_cost(const_data)
    tags = [_rarity_label(rarity)]
    if elem:
        tags.append(f"🜁 Hệ {elem.capitalize()}")
    reqs = const_data.get("special_requirements")
    if reqs:
        tags.append(f"🔒 {reqs}")

    desc_parts.append("\n" + " • ".join(tags))

    # ── Success chance preview (only meaningful for first-time activation) ──
    the_tu = is_the_tu(player.active_axis)
    if const_data["key"] not in get_tracker(player.constitution_tracker):
        chance = activation_chance(const_data, player.active_axis)
        bonus_tag = " (có +20% Thể Tu)" if the_tu else ""
        desc_parts.append(f"\n🎲 **Tỉ lệ thành công:** {chance * 100:.0f}%{bonus_tag}")

    # ── Slot / ownership status ──────────────────────────────────────────
    equipped = get_constitutions(player.constitution_type)
    tracker = get_tracker(player.constitution_tracker)
    slot_cap = max_slots(player.active_axis, player.body_realm)
    standard_equipped = [k for k in equipped if k != HON_DON_KEY]
    already = const_data["key"] in equipped
    in_tracker = const_data["key"] in tracker
    is_hon_don = const_data["key"] == HON_DON_KEY
    if already:
        desc_parts.append("\n✅ *Đang trang bị — bấm **Gỡ Bỏ** để tháo ra.*")
    elif in_tracker:
        if is_hon_don:
            desc_parts.append("\n📚 *Đã lĩnh ngộ — sẽ gắn vào slot đặc biệt thứ 9 (Hỗn Độn).*")
        elif the_tu:
            if len(standard_equipped) < slot_cap:
                desc_parts.append(
                    "\n📚 *Đã lĩnh ngộ — sẽ trang bị vào slot trống "
                    f"({len(standard_equipped) + 1}/{slot_cap}). Miễn phí.*"
                )
            else:
                desc_parts.append(
                    f"\n🚫 *Đã đầy {slot_cap}/{slot_cap} slot — gỡ bớt một Thể Chất trước.*"
                )
        else:
            if standard_equipped:
                cur = registry.get_constitution(standard_equipped[0]) or {}
                desc_parts.append(
                    "\n📚 *Đã lĩnh ngộ — sẽ thay thế Thể Chất hiện tại: "
                    f"{cur.get('vi', standard_equipped[0])}. Miễn phí.*"
                )
            else:
                desc_parts.append("\n📚 *Đã lĩnh ngộ — sẵn sàng trang bị. Miễn phí.*")
    elif is_hon_don:
        desc_parts.append("\n🌌 *Kích hoạt vào slot đặc biệt thứ 9 (Hỗn Độn).*")
    elif the_tu:
        if len(standard_equipped) < slot_cap:
            desc_parts.append(
                f"\n➕ *Sẽ gắn vào slot trống ({len(standard_equipped) + 1}/{slot_cap}).*"
            )
        else:
            desc_parts.append(
                f"\n🚫 *Đã đầy {slot_cap}/{slot_cap} slot — gỡ bớt một Thể Chất trước.*"
            )
    else:
        if standard_equipped:
            cur = registry.get_constitution(standard_equipped[0]) or {}
            desc_parts.append(
                f"\n🔄 *Sẽ thay thế Thể Chất hiện tại: {cur.get('vi', standard_equipped[0])}.*"
            )

    # Materials/cost are only relevant for first-time activation. Once a Thể
    # Chất is in the tracker, equip/unequip is free, so showing the cost block
    # would be misleading.
    if not in_tracker:
        materials = _required_materials(const_data)
        desc_parts.append(
            "\n**Nguyên liệu cần:**\n" + _format_materials(materials, owned_materials)
        )
        if cost_stones > 0:
            desc_parts.append(
                f"\n**Hỗn Nguyên Thạch:** {emojis.for_currency('primordial_stones')} "
                f"{cost_stones:,} (hiện có: {player.primordial_stones:,})"
            )
        if cost_merit > 0 or cost_stones <= 0:
            desc_parts.append(
                f"\n**Công Đức:** {emojis.for_currency('merit')} {cost_merit:,} (hiện có: {player.merit:,})"
            )

    return base_embed(title, "\n".join(p for p in desc_parts if p), color=meta["color"])


# ── Views ─────────────────────────────────────────────────────────────────────


_ELEMENT_FILTER_TABS: tuple[tuple[str, str], ...] = (
    ("universal", "Phổ Quát (Trung)"),
    ("kim",       "Hệ Kim"),
    ("moc",       "Hệ Mộc"),
    ("thuy",      "Hệ Thủy"),
    ("hoa",       "Hệ Hỏa"),
    ("tho",       "Hệ Thổ"),
    ("loi",       "Hệ Lôi"),
    ("phong",     "Hệ Phong"),
    ("quang",     "Hệ Quang"),
    ("am",        "Hệ Ám"),
)


class _ElementFilterSelect(discord.ui.Select):
    """Element sub-filter shown on every rarity tab.

    Every rarity bucket is over Discord's 25-option Select cap when shown
    raw (common 41 / uncommon 40 / rare 40 / epic 40 / legendary 61), so
    the constitution dropdown only renders entries matching the selected
    element. ``"universal"`` means ``element is None`` — bodies not bound
    to any of the 9 elements.
    """

    def __init__(
        self, discord_id: int, rarity: str, current_element: str, back_fn,
    ) -> None:
        self._discord_id = discord_id
        self._rarity = rarity
        self._back_fn = back_fn
        options = [
            discord.SelectOption(
                label=label, value=key, default=(key == current_element),
            )
            for key, label in _ELEMENT_FILTER_TABS
        ]
        super().__init__(placeholder="🜁 Lọc theo hệ...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _open_hub(
            interaction, self._discord_id, self._back_fn,
            rarity=self._rarity, element_filter=self.values[0],
        )


class _RaritySelect(discord.ui.Select):
    """Dropdown listing constitutions for the active (rarity, element) tab.

    Every rarity bucket is over Discord's 25-option Select cap, so the pool
    is sliced by ``element_filter`` (``"universal"`` → element=None,
    otherwise exact element match). Per-element sub-buckets are ≤6 entries
    everywhere; ``universal`` is the largest at 21 (legendary) — still
    under cap. ``element_filter=None`` falls back to no slicing, matching
    the old behaviour for any caller that hasn't migrated.
    """

    def __init__(
        self,
        discord_id: int,
        rarity: str,
        back_fn,
        element_filter: str | None = None,
        row: int = 2,
    ) -> None:
        self._discord_id = discord_id
        self._rarity = rarity
        self._back_fn = back_fn

        pool = [
            c for c in registry.constitutions.values()
            if c.get("rarity") == rarity
        ]
        if element_filter:
            target_elem = None if element_filter == "universal" else element_filter
            pool = [c for c in pool if c.get("element") == target_elem]
        pool.sort(key=lambda c: (c.get("element") or "zz_none", c["vi"]))

        options: list[discord.SelectOption] = []
        for c in pool[:25]:
            elem = c.get("element")
            elem_label = f"[{elem.capitalize()}]" if elem else "[Trung]"
            req = c.get("special_requirements")
            emoji = "🔒" if req else _RARITY_META.get(rarity, {}).get("emoji", "❔")
            options.append(discord.SelectOption(
                label=c["vi"][:100],
                value=c["key"],
                description=f"{elem_label} {c.get('passive_description_vi', '')[:80]}"[:100],
                emoji=emoji,
            ))
        if not options:
            options = [discord.SelectOption(label="(Không có)", value="__none__")]

        # Select placeholder is plain text — custom Discord emojis don't render
        # there, so use the Vietnamese label only.
        rarity_vi = _RARITY_META.get(rarity, {}).get("vi", rarity)
        placeholder = f"{rarity_vi} — chọn Thể Chất..."
        super().__init__(placeholder=placeholder, options=options, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        key = self.values[0]
        if key == "__none__":
            if not await safe_defer(interaction):
                return
            return
        await _open_detail(interaction, self._discord_id, key, self._back_fn)


class _TrackerSelect(discord.ui.Select):
    """Owned-Thể-Chất dropdown — lists everything in the activation tracker.

    Selecting an entry routes to the detail view, where the action button
    becomes context-aware (Equip if owned-but-unequipped, Unequip if
    currently equipped). Capped at 25 options by Discord's Select limit; if
    the player owns more, the rarity-filtered browse dropdown still surfaces
    them via the standard navigation.
    """

    def __init__(
        self, discord_id: int, tracker: list[str], equipped: list[str],
        back_fn, row: int = 3,
    ) -> None:
        self._discord_id = discord_id
        self._back_fn = back_fn
        # Hide Phàm Thể from the tracker — it's the "no constitution" default
        # and doesn't represent a real unlock.
        visible = [k for k in tracker if k != PHAM_THE_KEY]
        options: list[discord.SelectOption] = []
        for k in visible[:25]:
            c = registry.get_constitution(k)
            if not c:
                options.append(discord.SelectOption(label=k[:100], value=k))
                continue
            meta = _RARITY_META.get(c.get("rarity", "common"), _RARITY_META["common"])
            is_eq = k in equipped
            label = c["vi"][:100]
            description = ("⚙️ Đang trang bị" if is_eq else "📚 Đã lĩnh ngộ")
            options.append(discord.SelectOption(
                label=label, value=k,
                description=description,
                emoji=meta["emoji"],
            ))
        if not options:
            options = [discord.SelectOption(label="(chưa lĩnh ngộ Thể Chất nào)", value="__none__")]
        super().__init__(
            placeholder="📚 Đã lĩnh ngộ — chọn để trang bị / gỡ...",
            options=options, row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        key = self.values[0]
        if key == "__none__":
            if not await safe_defer(interaction):
                return
            return
        await _open_detail(interaction, self._discord_id, key, self._back_fn)


class TheChatHubView(discord.ui.View):
    """Main roster — rarity tabs + browse select + tracker select.

    The tracker dropdown surfaces every Thể Chất the player has activated so
    they can equip / unequip without re-paying. It's hidden when the player
    has no real unlocks (only the default Phàm Thể).
    """

    def __init__(
        self, discord_id: int, rarity: str, back_fn,
        equipped: list[str] | None = None,
        tracker: list[str] | None = None,
        element_filter: str | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._rarity = rarity
        self._back_fn = back_fn
        self._element_filter = element_filter or "universal"

        # Layout (uniform across all rarity tabs):
        #   row 0: rarity tabs            (5 buttons)
        #   row 1: element sub-filter     (Select)
        #   row 2: constitution dropdown  (Select)
        #   row 3: tracker dropdown       (Select, hidden when no unlocks)
        #   row 4: back button
        for r in ("common", "uncommon", "rare", "epic", "legendary"):
            meta = _RARITY_META[r]
            style = discord.ButtonStyle.primary if r == rarity else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=meta["vi"], emoji=meta["emoji"], style=style, row=0)
            btn.callback = self._make_tab_cb(r)
            self.add_item(btn)

        self.add_item(_ElementFilterSelect(
            discord_id, rarity, self._element_filter, back_fn,
        ))
        self.add_item(_RaritySelect(
            discord_id, rarity, back_fn,
            element_filter=self._element_filter, row=2,
        ))

        tracker = tracker or []
        equipped = equipped or []
        # Show the tracker dropdown whenever the player has unlocked anything
        # beyond the default Phàm Thể.
        has_unlocks = any(k for k in tracker if k != PHAM_THE_KEY)
        if has_unlocks:
            self.add_item(_TrackerSelect(
                discord_id, tracker, equipped, back_fn, row=3,
            ))

        if back_fn:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=4)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _make_tab_cb(self, rarity: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            # Must ack the interaction BEFORE _open_hub calls edit_original_response —
            # otherwise the original-response webhook is 404 "Unknown Webhook".
            if not await safe_defer(interaction):
                return
            # Preserve the element sub-filter across rarity switches so the
            # user doesn't have to re-pick their element each time.
            await _open_hub(
                interaction, self._discord_id, self._back_fn,
                rarity=rarity, element_filter=self._element_filter,
            )
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)


class ConstitutionDetailView(discord.ui.View):
    """Detail view — primary action is context-aware:

    - Equipped:         🔻 Gỡ Bỏ (free)
    - Owned (tracker):  ⚙️ Trang Bị (free)
    - Otherwise:        ✨ Kích Hoạt (pays cost + rolls activation chance)
    """

    def __init__(
        self, discord_id: int, const_key: str, back_fn,
        is_equipped: bool = False, in_tracker: bool = False,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._const_key = const_key
        self._back_fn = back_fn

        if is_equipped:
            primary = discord.ui.Button(
                label="🔻 Gỡ Bỏ", style=discord.ButtonStyle.danger, row=0,
            )
            primary.callback = self._unequip_cb
        elif in_tracker:
            primary = discord.ui.Button(
                label="⚙️ Trang Bị", style=discord.ButtonStyle.success, row=0,
            )
            primary.callback = self._equip_cb
        else:
            primary = discord.ui.Button(
                label="✨ Kích Hoạt", style=discord.ButtonStyle.success, row=0,
            )
            primary.callback = self._activate_cb
        self.add_item(primary)

        back_btn = discord.ui.Button(
            label="◀ Danh sách", style=discord.ButtonStyle.secondary, row=0,
        )
        back_btn.callback = self._back_to_hub_cb
        self.add_item(back_btn)

    async def _equip_cb(self, interaction: discord.Interaction) -> None:
        """Equip an already-unlocked Thể Chất (free, no roll)."""
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        const_data = registry.get_constitution(self._const_key)
        if not const_data:
            await interaction.edit_original_response(
                embed=error_embed("Thể Chất không tồn tại."), view=None,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return

            tracker = get_tracker(player.constitution_tracker)
            if self._const_key not in tracker:
                await interaction.edit_original_response(
                    embed=error_embed(
                        "Chưa lĩnh ngộ Thể Chất này — cần kích hoạt trước."
                    ),
                )
                return

            equipped = get_constitutions(player.constitution_type)
            if self._const_key in equipped:
                await interaction.edit_original_response(
                    embed=error_embed("Đã trang bị Thể Chất này rồi."),
                )
                return

            the_tu = is_the_tu(player.active_axis)
            slot_cap = max_slots(player.active_axis, player.body_realm)
            standard_equipped = [k for k in equipped if k != HON_DON_KEY]
            is_hon_don = self._const_key == HON_DON_KEY

            if the_tu and not is_hon_don and len(standard_equipped) >= slot_cap:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Đã đầy {slot_cap}/{slot_cap} slot Thể Chất. "
                        "Gỡ một Thể Chất khỏi bảng trước khi trang bị."
                    ),
                )
                return

            if the_tu and not is_hon_don:
                new_equipped = list(equipped) + [self._const_key]
            elif is_hon_don:
                new_equipped = [k for k in equipped if k != HON_DON_KEY] + [HON_DON_KEY]
            else:
                # Non-Thể Tu: replace standard slot, preserve Hỗn Độn (9th slot).
                new_equipped = (
                    [k for k in equipped if k == HON_DON_KEY] + [self._const_key]
                )
            player.constitution_type = set_constitutions(new_equipped)
            await prepo.save(player)

        await _open_hub(
            interaction, self._discord_id, self._back_fn,
            rarity=const_data.get("rarity", "common"),
            element_filter=const_data.get("element") or "universal",
        )

    async def _unequip_cb(self, interaction: discord.Interaction) -> None:
        """Unequip a currently-equipped Thể Chất. Stays in the tracker."""
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        const_data = registry.get_constitution(self._const_key) or {}

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return

            equipped = get_constitutions(player.constitution_type)
            if self._const_key not in equipped:
                await interaction.edit_original_response(
                    embed=error_embed("Thể Chất này không còn được trang bị."),
                )
                return

            equipped.remove(self._const_key)
            # Phàm Thể is the implicit "no constitution" state. Single-slot
            # paths fall back to it when fully unequipped so combat code
            # never sees a nameless slot.
            if not equipped:
                equipped = [PHAM_THE_KEY]
            player.constitution_type = set_constitutions(equipped)
            await prepo.save(player)

        await _open_hub(
            interaction, self._discord_id, self._back_fn,
            rarity=const_data.get("rarity", "common"),
            element_filter=const_data.get("element") or "universal",
        )

    async def _activate_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        const_data = registry.get_constitution(self._const_key)
        if not const_data:
            await interaction.edit_original_response(
                embed=error_embed("Thể Chất không tồn tại."), view=None,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return

            equipped = get_constitutions(player.constitution_type)
            if self._const_key in equipped:
                await interaction.edit_original_response(
                    embed=error_embed("Ngươi đã trang bị Thể Chất này rồi."),
                )
                return

            # ── Slot rules ───────────────────────────────────────────────
            the_tu = is_the_tu(player.active_axis)
            slot_cap = max_slots(player.active_axis, player.body_realm)
            standard_equipped = [k for k in equipped if k != HON_DON_KEY]
            is_hon_don = self._const_key == HON_DON_KEY
            # Progression chains (e.g. Hoang Cổ Thánh Thể tier 1 → 2) consume
            # the predecessor on success, so a full-slot Thể Tu can still
            # upgrade in place — net slot count is unchanged. Skip the
            # slot-full guard when the predecessor is currently equipped.
            progress_from = const_data.get("progresses_from")
            replaces_in_place = bool(
                progress_from and progress_from in standard_equipped
            )

            if (
                the_tu
                and not is_hon_don
                and not replaces_in_place
                and len(standard_equipped) >= slot_cap
            ):
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Đã đầy {slot_cap}/{slot_cap} slot Thể Chất. "
                        "Gỡ một Thể Chất khỏi Bảng trước khi kích hoạt cái mới."
                    ),
                )
                return

            # ── Requirements (incl. all-legendary gate for Hỗn Độn) ──────
            err = check_requirements(player, const_data, registry.constitutions)
            if err:
                await interaction.edit_original_response(embed=error_embed(err))
                return

            cost_merit, cost_stones = _activation_cost(const_data)
            if cost_merit > player.merit:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Không đủ Công Đức. Cần {emojis.for_currency('merit')} **{cost_merit:,}**, có **{player.merit:,}**."
                    ),
                )
                return
            if cost_stones > player.primordial_stones:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Không đủ Hỗn Nguyên Thạch. Cần "
                        f"{emojis.for_currency('primordial_stones')} **{cost_stones:,}**, "
                        f"có **{player.primordial_stones:,}**.\n"
                        f"💡 Hỗn Nguyên Thạch rớt từ Bí Cảnh thường (R3+), Dược Viên (R3+), và Thần Cốt Địa (R3+)."
                    ),
                )
                return

            irepo = InventoryRepository(session)
            materials = _required_materials(const_data)
            owned = await _inventory_counts(irepo, player.id, materials.keys())
            missing = [
                (k, need, owned.get(k, 0))
                for k, need in materials.items() if owned.get(k, 0) < need
            ]
            if missing:
                lines = []
                for k, need, have in missing:
                    item = registry.get_item(k)
                    name = item["vi"] if item else k
                    lines.append(f"❌ Thiếu {name}: cần **{need}**, có **{have}**")
                lines.append(
                    "\n👑 **Hỗn Nguyên Cốt** chỉ rớt từ **Thần Cốt Địa Đỉnh Phong (R9)**."
                )
                await interaction.edit_original_response(
                    embed=error_embed("\n".join(lines)),
                )
                return

            # ── Deduct cost first (attempt economy — pay even on failure) ──
            for k, need in materials.items():
                await irepo.remove_any_grade(player.id, k, need)
            if cost_merit > 0:
                player.merit -= cost_merit
            if cost_stones > 0:
                player.primordial_stones -= cost_stones

            # ── Roll the activation chance ────────────────────────────────
            chance = activation_chance(const_data, player.active_axis)
            succeeded = roll_activation(const_data, player.active_axis)

            if succeeded:
                # Progression chain: a Thể Chất can declare ``progresses_from``
                # so activation consumes the predecessor (e.g. Thôn Thiên Ma
                # Tâm → Thôn Thiên Ma Thể). The predecessor is unequipped AND
                # removed from the tracker so the upgrade doesn't leave the
                # earlier form re-equippable. ``progress_from`` was resolved
                # in the slot-rules section above so the slot-full guard can
                # treat in-place upgrades correctly.
                base_equipped = (
                    [k for k in equipped if k != progress_from]
                    if progress_from else list(equipped)
                )
                if the_tu and not is_hon_don:
                    # Append to equipped list (slot already validated above)
                    new_equipped = base_equipped + [self._const_key]
                else:
                    # Non-Thể Tu: replace standard slot. Hỗn Độn: append.
                    if is_hon_don:
                        new_equipped = base_equipped + [HON_DON_KEY]
                    else:
                        new_equipped = [
                            k for k in base_equipped if k == HON_DON_KEY
                        ] + [self._const_key]
                player.constitution_type = set_constitutions(new_equipped)
                # Tracker bookkeeping: record the new unlock; drop the
                # predecessor since it transformed into this entry.
                tracker_raw = player.constitution_tracker
                if progress_from:
                    tracker_raw = remove_from_tracker(tracker_raw, progress_from)
                player.constitution_tracker = add_to_tracker(
                    tracker_raw, self._const_key,
                )
            await prepo.save(player)

        mat_summary_lines = []
        for k, need in materials.items():
            item = registry.get_item(k)
            name = item["vi"] if item else k
            mat_summary_lines.append(f"🦴 -{need} {name}")
        if cost_merit > 0:
            mat_summary_lines.append(f"{emojis.for_currency('merit')} -{cost_merit:,} Công Đức")
        if cost_stones > 0:
            mat_summary_lines.append(f"{emojis.for_currency('primordial_stones')} -{cost_stones:,} Hỗn Nguyên Thạch")

        if succeeded:
            bonus_lines = _format_bonus_lines(const_data.get("stat_bonuses", {}))
            msg = (
                f"🎉 **Kích hoạt thành công!** 🎉 (tỉ lệ {chance * 100:.0f}%)\n"
                f"Đã trang bị **{const_data['vi']}**.\n"
                + "\n".join(mat_summary_lines)
                + "\n\n" + const_data.get("passive_description_vi", "")
                + (("\n\n**Chỉ số:**\n" + "\n".join(bonus_lines)) if bonus_lines else "")
            )
            embed = success_embed(msg)
        else:
            msg = (
                f"💨 **Kích hoạt thất bại!** (tỉ lệ {chance * 100:.0f}%)\n"
                f"Thể Chất **{const_data['vi']}** không thành hình — nguyên "
                f"liệu tiêu hao, Thể Chất đang trang bị giữ nguyên.\n"
                + "\n".join(mat_summary_lines)
            )
            embed = error_embed(msg)

        # Pass post-save tracker/equipped so the tracker dropdown immediately
        # reflects the new unlock without an extra DB roundtrip.
        await interaction.edit_original_response(
            embed=embed,
            view=TheChatHubView(
                self._discord_id, const_data.get("rarity", "common"), self._back_fn,
                equipped=get_constitutions(player.constitution_type),
                tracker=get_tracker(player.constitution_tracker),
                element_filter=const_data.get("element") or "universal",
            ),
        )

    async def _back_to_hub_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        const_data = registry.get_constitution(self._const_key) or {}
        rarity = const_data.get("rarity", "common")
        # Land back on the same element bucket the user was browsing — for an
        # element-locked body, that's its own element; for a universal one,
        # the universal bucket. Avoids a confusing "I clicked into Quang and
        # came back to a different element" jump.
        elem = const_data.get("element") or "universal"
        await _open_hub(
            interaction, self._discord_id, self._back_fn,
            rarity=rarity, element_filter=elem,
        )


# ── Entry points ──────────────────────────────────────────────────────────────


async def _open_hub(
    interaction: discord.Interaction,
    discord_id: int,
    back_fn,
    rarity: str = "common",
    element_filter: str | None = None,
) -> None:
    # Defensive ack — if a caller forgot to defer, we self-defer so
    # ``edit_original_response`` below doesn't 404 with "Unknown Webhook".
    if not interaction.response.is_done():
        if not await safe_defer(interaction):
            return
    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật."), view=None,
            )
            return
        irepo = InventoryRepository(session)
        key_counts = await _inventory_counts(irepo, player.id, _HUB_KEY_MATERIALS)

    # The element sub-filter is required on every rarity tab — each bucket
    # is over Discord's 25-option Select cap. Default to ``"universal"``
    # which covers element-agnostic bodies; the user can switch via the
    # sub-select on row 1.
    if element_filter is None:
        element_filter = "universal"

    embed = _hub_embed(player, key_counts)
    equipped = get_constitutions(player.constitution_type)
    tracker = get_tracker(player.constitution_tracker)
    view = TheChatHubView(
        discord_id, rarity, back_fn,
        equipped=equipped, tracker=tracker,
        element_filter=element_filter,
    )
    await interaction.edit_original_response(embed=embed, view=view)


async def _open_detail(
    interaction: discord.Interaction, discord_id: int, const_key: str, back_fn,
) -> None:
    if not interaction.response.is_done():
        if not await safe_defer(interaction):
            return
    const_data = registry.get_constitution(const_key)
    if not const_data:
        await interaction.edit_original_response(
            embed=error_embed("Thể Chất không tồn tại."), view=None,
        )
        return

    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật."), view=None,
            )
            return
        irepo = InventoryRepository(session)
        owned = await _inventory_counts(
            irepo, player.id, _required_materials(const_data).keys(),
        )

    equipped = get_constitutions(player.constitution_type)
    tracker = get_tracker(player.constitution_tracker)
    embed = _detail_embed(player, const_data, owned)
    view = ConstitutionDetailView(
        discord_id, const_key, back_fn,
        is_equipped=(const_key in equipped),
        in_tracker=(const_key in tracker),
    )
    await interaction.edit_original_response(embed=embed, view=view)


async def render_the_chat_hub(
    interaction: discord.Interaction, discord_id: int, back_fn,
) -> None:
    """Open the Thể Chất Bảng hub. Called from the /status navigation."""
    await _open_hub(interaction, discord_id, back_fn)
