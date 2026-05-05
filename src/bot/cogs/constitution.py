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
    check_requirements,
    get_constitutions,
    is_the_tu,
    max_slots,
    roll_activation,
    set_constitutions,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

DAO_COT_KEY = "MatDaoCotTinh"
DAO_COT_GRADE = Grade.THIEN

# Fallback material list when a constitution JSON entry is missing
# ``materials`` for some reason. Always cost at least one Đạo Cốt Tinh.
_FALLBACK_MATERIALS: dict[str, int] = {DAO_COT_KEY: 1}


def _required_materials(const_data: dict) -> dict[str, int]:
    """Return {item_key: qty} for activating this constitution."""
    mats = const_data.get("materials")
    if isinstance(mats, dict) and mats:
        return {k: int(v) for k, v in mats.items() if int(v) > 0}
    return dict(_FALLBACK_MATERIALS)


def _item_grade(item_key: str) -> Grade:
    """Resolve an item's stored grade; fall back to HOANG if unknown."""
    data = registry.get_item(item_key)
    if not data:
        return Grade.HOANG
    return Grade(int(data.get("grade", 1)))


async def _inventory_counts(irepo: InventoryRepository, player_id: int, keys) -> dict[str, int]:
    """Return {item_key: owned_qty} for each requested item."""
    counts: dict[str, int] = {}
    for k in keys:
        row = await irepo.get_item(player_id, k, _item_grade(k))
        counts[k] = row.quantity if row else 0
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
    ("true_dmg_pct",                  "🗡️ ST Thật {pct:.0f}%"),
    ("thorn_pct",                     "🌵 Gai +{pct:.0f}%"),
    ("shield_regen_pct",              "🛡️ Hồi Khiên {pct:.1f}% Khiên/lượt"),
    ("shield_regen_flat",             "🛡️ Hồi Khiên +{flat}/lượt"),
    ("shield_max_base",               "🛡️ Khiên Nền +{flat}"),
    ("shield_max_flat",               "🛡️ Khiên Tối Đa +{flat}"),
    ("shield_max_pct",                "🛡️ Khiên Tối Đa +{pct:.0f}%"),
    ("hp_to_shield_pct",              "🔄 Chuyển {pct:.0f}% HP → Khiên"),
    ("matk_from_shield_pct",          "🔮 Pháp Công +{pct:.0f}% Khiên hiện tại"),
    ("atk_from_shield_pct",           "⚔️ Công +{pct:.0f}% Khiên hiện tại"),
    ("damage_bonus_from_hp_pct",      "💪 ST theo HP +{pct:.0f}%"),
    ("damage_bonus_from_mp_pct",      "🔷 ST theo MP +{pct:.0f}%"),
    ("damage_bonus_from_shield_pct",  "🧱 ST theo Khiên +{pct:.0f}%"),
    ("damage_bonus_from_evasion_pct", "🌪️ ST theo Né +{pct:.0f}%"),
    ("turn_steal_pct",                "⏩ Cướp Lượt +{pct:.0f}%"),
    ("reflect_pct",                   "🪞 Phản ST +{pct:.0f}%"),
    ("debuff_immune_pct",             "🪬 Miễn Debuff {pct:.0f}%"),
    # NOTE: per-element shred is read from the generic ``element_res_shred``
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
    # ``res_<elem>`` lives as a flat key; shred + damage bonus + convert all
    # come from generic dicts (single source of truth, multi-element friendly).
    elem_dmg   = bonuses.get("element_dmg_bonus") or {}
    elem_shred = bonuses.get("element_res_shred") or {}
    convert    = bonuses.get("damage_taken_convert_pct") or {}
    for elem, vi in ELEMENT_LABELS_VI.items():
        emoji = emojis.for_element(elem)
        if (r := bonuses.get(f"res_{elem}", 0)):
            lines.append(f"{emoji} Kháng {vi} +{r * 100:.0f}%")
        if (s := elem_shred.get(elem, 0)):
            lines.append(f"{emoji} Xuyên Kháng {vi} {s * 100:.0f}%")
        if (d := elem_dmg.get(elem, 0)):
            lines.append(f"{emoji} ST {vi} +{d * 100:.0f}%")
        if (c := convert.get(elem, 0)):
            lines.append(
                f"{emoji} Hóa Thân {vi}: {c * 100:.0f}% ST nhận vào → ST {vi} "
                f"(chịu Kháng {vi})"
            )
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
    the_tu = is_the_tu(player.body_realm, player.qi_realm, player.formation_realm)
    slot_cap = max_slots(player.body_realm, player.qi_realm, player.formation_realm)

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
    path_hint = (
        "Thể Tu mở khóa thêm 1 slot mỗi khi đột phá Luyện Thể, tối đa 8 slot. "
        "Khi đủ 8 slot đều là Truyền Thuyết có thể khai mở **Hỗn Độn Đạo Thể**."
        if the_tu else
        "Chỉ Thể Tu mới có nhiều slot. Con đường Khí Tu / Trận Tu chỉ có thể "
        "mang **1 Thể Chất** duy nhất."
    )

    desc = (
        f"{path_tag}   ·   {slot_line}\n"
        f"{emojis.for_currency('merit')} Công Đức: **{player.merit:,}**\n\n"
        f"**Đang trang bị:**\n{_render_equipped_lines(equipped)}\n\n"
        f"{mat_block}\n\n"
        f"{path_hint}"
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

    cost_merit = int(const_data.get("cost_merit", 0))
    cost_stones = int(const_data.get("cost_stones", 0))
    tags = [_rarity_label(rarity)]
    if elem:
        tags.append(f"🜁 Hệ {elem.capitalize()}")
    reqs = const_data.get("special_requirements")
    if reqs:
        tags.append(f"🔒 {reqs}")

    desc_parts.append("\n" + " • ".join(tags))

    # ── Success chance preview ────────────────────────────────────────────
    chance = activation_chance(
        const_data, player.body_realm, player.qi_realm, player.formation_realm,
    )
    the_tu = is_the_tu(player.body_realm, player.qi_realm, player.formation_realm)
    bonus_tag = " (có +20% Thể Tu)" if the_tu else ""
    desc_parts.append(f"\n🎲 **Tỉ lệ thành công:** {chance * 100:.0f}%{bonus_tag}")

    # ── Slot status ──────────────────────────────────────────────────────
    equipped = get_constitutions(player.constitution_type)
    slot_cap = max_slots(player.body_realm, player.qi_realm, player.formation_realm)
    standard_equipped = [k for k in equipped if k != HON_DON_KEY]
    already = const_data["key"] in equipped
    is_hon_don = const_data["key"] == HON_DON_KEY
    if is_hon_don:
        desc_parts.append("\n🌌 *Kích hoạt vào slot đặc biệt thứ 9 (Hỗn Độn).*")
    elif already:
        desc_parts.append("\n✅ *Đang trang bị.*")
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


class _RaritySelect(discord.ui.Select):
    """Dropdown listing constitutions — up to 25 per rarity bucket."""

    def __init__(self, discord_id: int, rarity: str, back_fn) -> None:
        self._discord_id = discord_id
        self._rarity = rarity
        self._back_fn = back_fn

        pool = [
            c for c in registry.constitutions.values()
            if c.get("rarity") == rarity
        ]
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
        super().__init__(placeholder=placeholder, options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        key = self.values[0]
        if key == "__none__":
            await interaction.response.defer()
            return
        await _open_detail(interaction, self._discord_id, key, self._back_fn)


class _RemoveSelect(discord.ui.Select):
    """Remove dropdown — lists currently equipped constitutions (Thể Tu only).
    The non-Thể-Tu single-slot is forced to replace-on-activate, so removing
    the sole entry would leave them bare; disabled for that path.
    """

    def __init__(self, discord_id: int, equipped: list[str], back_fn) -> None:
        self._discord_id = discord_id
        self._back_fn = back_fn
        options: list[discord.SelectOption] = []
        for k in equipped[:25]:
            c = registry.get_constitution(k)
            if not c:
                options.append(discord.SelectOption(label=k[:100], value=k))
                continue
            meta = _RARITY_META.get(c.get("rarity", "common"), _RARITY_META["common"])
            options.append(discord.SelectOption(
                label=c["vi"][:100], value=k,
                emoji=meta["emoji"],
            ))
        if not options:
            options = [discord.SelectOption(label="(không có)", value="__none__")]
        super().__init__(placeholder="🗑️ Gỡ một Thể Chất...", options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        key = self.values[0]
        if key == "__none__":
            await interaction.response.defer()
            return
        await interaction.response.defer()

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return
            equipped = get_constitutions(player.constitution_type)
            if key not in equipped:
                await interaction.edit_original_response(
                    embed=error_embed("Thể Chất này không còn được trang bị."),
                )
                return
            if not is_the_tu(
                player.body_realm, player.qi_realm, player.formation_realm,
            ):
                await interaction.edit_original_response(
                    embed=error_embed(
                        "Chỉ Thể Tu mới có thể gỡ Thể Chất thủ công — đường "
                        "Khí Tu / Trận Tu chỉ có 1 slot, hãy kích hoạt cái "
                        "mới để thay thế."
                    ),
                )
                return
            equipped.remove(key)
            if not equipped:
                equipped = ["ConstitutionPhamThe"]
            player.constitution_type = set_constitutions(equipped)
            await prepo.save(player)

        await _open_hub(interaction, self._discord_id, self._back_fn)


class TheChatHubView(discord.ui.View):
    """Main roster — rarity tab buttons + rarity-filtered select + remove.

    The Remove dropdown is only rendered for Thể Tu with standard slots to
    unequip; other paths always replace-on-activate.
    """

    def __init__(
        self, discord_id: int, rarity: str, back_fn,
        equipped: list[str] | None = None, show_remove: bool = False,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._rarity = rarity
        self._back_fn = back_fn

        for r in ("common", "uncommon", "rare", "epic", "legendary"):
            meta = _RARITY_META[r]
            style = discord.ButtonStyle.primary if r == rarity else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=meta["vi"], emoji=meta["emoji"], style=style, row=0)
            btn.callback = self._make_tab_cb(r)
            self.add_item(btn)

        self.add_item(_RaritySelect(discord_id, rarity, back_fn))

        if show_remove and equipped:
            self.add_item(_RemoveSelect(discord_id, equipped, back_fn))

        if back_fn:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=3)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _make_tab_cb(self, rarity: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            # Must ack the interaction BEFORE _open_hub calls edit_original_response —
            # otherwise the original-response webhook is 404 "Unknown Webhook".
            await interaction.response.defer()
            await _open_hub(interaction, self._discord_id, self._back_fn, rarity=rarity)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class ConstitutionDetailView(discord.ui.View):
    def __init__(
        self, discord_id: int, const_key: str, back_fn,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._const_key = const_key
        self._back_fn = back_fn

        activate_btn = discord.ui.Button(
            label="✨ Kích Hoạt", style=discord.ButtonStyle.success, row=0,
        )
        activate_btn.callback = self._activate_cb
        self.add_item(activate_btn)

        back_btn = discord.ui.Button(
            label="◀ Danh sách", style=discord.ButtonStyle.secondary, row=0,
        )
        back_btn.callback = self._back_to_hub_cb
        self.add_item(back_btn)

    async def _activate_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

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
            the_tu = is_the_tu(
                player.body_realm, player.qi_realm, player.formation_realm,
            )
            slot_cap = max_slots(
                player.body_realm, player.qi_realm, player.formation_realm,
            )
            standard_equipped = [k for k in equipped if k != HON_DON_KEY]
            is_hon_don = self._const_key == HON_DON_KEY

            if the_tu and not is_hon_don and len(standard_equipped) >= slot_cap:
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

            cost_merit = int(const_data.get("cost_merit", 0))
            cost_stones = int(const_data.get("cost_stones", 0))
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
                await irepo.remove_item(player.id, k, _item_grade(k), need)
            if cost_merit > 0:
                player.merit -= cost_merit
            if cost_stones > 0:
                player.primordial_stones -= cost_stones

            # ── Roll the activation chance ────────────────────────────────
            chance = activation_chance(
                const_data, player.body_realm, player.qi_realm, player.formation_realm,
            )
            succeeded = roll_activation(
                const_data, player.body_realm, player.qi_realm, player.formation_realm,
            )

            if succeeded:
                # Progression chain: a Thể Chất can declare ``progresses_from``
                # so activation consumes the predecessor (e.g. Thôn Thiên Ma
                # Tâm → Thôn Thiên Ma Thể). The predecessor is unequipped so
                # the upgrade doesn't simultaneously occupy two slots.
                progress_from = const_data.get("progresses_from")
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

        await interaction.edit_original_response(
            embed=embed,
            view=TheChatHubView(
                self._discord_id, const_data.get("rarity", "common"), self._back_fn,
            ),
        )

    async def _back_to_hub_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        const_data = registry.get_constitution(self._const_key) or {}
        rarity = const_data.get("rarity", "common")
        await _open_hub(interaction, self._discord_id, self._back_fn, rarity=rarity)


# ── Entry points ──────────────────────────────────────────────────────────────


async def _open_hub(
    interaction: discord.Interaction, discord_id: int, back_fn, rarity: str = "common",
) -> None:
    # Defensive ack — if a caller forgot to defer, we self-defer so
    # ``edit_original_response`` below doesn't 404 with "Unknown Webhook".
    if not interaction.response.is_done():
        await interaction.response.defer()
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

    embed = _hub_embed(player, key_counts)
    equipped = get_constitutions(player.constitution_type)
    the_tu = is_the_tu(player.body_realm, player.qi_realm, player.formation_realm)
    # Offer the Gỡ dropdown only when Thể Tu has more than one slot worth to
    # manage (single-entry Thể Tu would be back to empty on remove).
    show_remove = the_tu and len(equipped) > 1
    view = TheChatHubView(
        discord_id, rarity, back_fn,
        equipped=equipped, show_remove=show_remove,
    )
    await interaction.edit_original_response(embed=embed, view=view)


async def _open_detail(
    interaction: discord.Interaction, discord_id: int, const_key: str, back_fn,
) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()

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

    embed = _detail_embed(player, const_data, owned)
    view = ConstitutionDetailView(discord_id, const_key, back_fn)
    await interaction.edit_original_response(embed=embed, view=view)


async def render_the_chat_hub(
    interaction: discord.Interaction, discord_id: int, back_fn,
) -> None:
    """Open the Thể Chất Bảng hub. Called from the /status navigation."""
    await _open_hub(interaction, discord_id, back_fn)
