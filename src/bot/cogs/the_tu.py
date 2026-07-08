"""Bách Thể Chú Linh commands — Thể Tu body-part infusion UI.

``/thetu`` shows the nine Bộ Vị Cơ Thể (one per Luyện Thể realm), their
lock status, and any infused Tinh Huyết. Selecting an unlocked part opens
a detail view where a vital essence from the inventory can be infused
(consuming ``infusion_cost`` copies) or the current infusion removed.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.systems.body_parts import (
    ARCHETYPE_VI,
    BODY_PARTS,
    HOA_HINH_TRIGGER_HP_PCT,
    PART_AFFINITY,
    RESONANCE_T1_COUNT,
    RESONANCE_T2_COUNT,
    BodyPart,
    hoa_hinh_form,
    awaken_threshold,
    encode_infusions,
    essence_item,
    get_part,
    infusion_cost,
    is_awakened,
    is_part_unlocked,
    is_the_tu,
    parse_infusions,
    part_affinity_matches,
    part_power_mult,
    resonance_count,
    scaled_essence_bonuses,
)
from src.utils.discord_safe import safe_defer
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

_COLOR = 0x8B4513

# Rarity badge per essence_tier (normal → mythic ladder).
_TIER_BADGES: dict[str, str] = {
    "normal":    "⚪",
    "magic":     "🔵",
    "rare":      "🟣",
    "legendary": "🟠",
    "mythic":    "🌟",
}
# Sort order for essence listings: rarity descending, then name.
_TIER_ORDER: dict[str, int] = {
    "mythic": 0, "legendary": 1, "rare": 2, "magic": 3, "normal": 4,
}


def _essence_badge(item: dict) -> str:
    return _TIER_BADGES.get(item.get("essence_tier", "normal"), "⚪")


def _sorted_essences(essence_counts: dict[str, int]) -> list[tuple[dict, int]]:
    """Owned essences as (item, qty), best rarity first."""
    out: list[tuple[dict, int]] = []
    for key, qty in essence_counts.items():
        item = registry.get_item(key)
        if item:
            out.append((item, qty))
    out.sort(key=lambda pair: (
        _TIER_ORDER.get(pair[0].get("essence_tier", "normal"), 9),
        pair[0].get("vi", ""),
    ))
    return out

# Human labels for every stat key the vital essences can carry.
_STAT_LABELS: dict[str, str] = {
    "atk_pct":                 "⚔️ Công kích +{:.0%}",
    "matk_pct":                "🔮 Pháp công +{:.0%}",
    "hp_pct":                  "❤️ Sinh lực +{:.0%}",
    "def_bonus":               "🛡️ Phòng ngự +{:.0f}",
    "crit_rating":             "💥 Bạo kích +{:.0f}",
    "evasion_rating":          "💨 Né tránh +{:.0f}",
    "spd_bonus":               "👟 Tốc độ +{:.0f}",
    "mp_regen_pct":            "💙 Hồi linh lực +{:.1%}/lượt",
    "hp_regen_pct":            "💚 Hồi sinh lực +{:.1%}/lượt",
    "poison_on_hit_pct":       "☠️ {:.0%} tẩm độc khi đánh",
    "true_dmg_pct":            "🗡️ Chân thương +{:.1%}",
    "final_dmg_bonus":         "🔥 Tổng sát thương +{:.1%}",
    "final_dmg_reduce":        "🧿 Giảm sát thương nhận +{:.1%}",
    "phoenix_revive_pct":      "🐦‍🔥 Niết Bàn: hồi sinh với {:.0%} HP (1 lần/trận)",
    "phoenix_revive_buff_pct": "🐦‍🔥 Sau hồi sinh +{:.0%} sát thương",
    "heal_pct":                "💗 Hiệu quả hồi phục +{:.0%}",
    "cleanse_on_turn_pct":     "🌟 {:.0%} thanh tẩy debuff mỗi lượt",
    "res_all":                 "🌈 Kháng toàn nguyên tố +{:.1%}",
    "loot_luck_bonus":         "🍀 Vận may chiến lợi phẩm +{:.0%}",
    "shield_max_pct":          "🐢 Lá chắn tối đa +{:.0%}",
    "reflect_pct":             "🔁 Phản chấn {:.0%} sát thương",
    "crit_dmg_rating":         "💢 Sát thương bạo kích +{:.0f}",
    "burn_on_hit_pct":         "🔥 {:.0%} thiêu đốt khi đánh",
    "bleed_on_hit_pct":        "🩸 {:.0%} chảy máu khi đánh",
    "shock_on_hit_pct":        "⚡ {:.0%} sốc điện khi đánh",
    "slow_on_hit_pct":         "🐌 {:.0%} làm chậm khi đánh",
    "mark_on_hit_pct":         "🎯 {:.0%} in dấu khi đánh",
    "stun_on_hit_pct":         "💫 {:.0%} choáng khi đánh",
    "freeze_on_skill_chance":  "🧊 {:.0%} đóng băng khi thi triển",
    "life_steal_pct":          "🧛 Hút máu {:.0%}",
    "mp_leech_pct":            "💙 Hút linh lực {:.0%}",
    "dot_leech_pct":           "☠️ Độc tố hút sinh lực {:.0%}",
    "multi_strike_pct":        "⚔️ {:.0%} đánh bồi",
    "bonus_dmg_vs_burn":       "🔥 +{:.0%} sát thương lên mục tiêu cháy",
    "damage_bonus_from_evasion_pct": "💨 Né tránh cộng sát thương +{:.1%}",
    "damage_bonus_from_hp_pct":      "❤️ Sinh lực cộng sát thương +{:.1%}",
    "damage_bonus_from_mp_pct":      "💙 Linh lực cộng sát thương +{:.1%}",
    "soul_drain_on_hit_pct":   "👻 {:.0%} rút hồn khi đánh",
    "stat_steal_on_hit_pct":   "🌀 {:.0%} đoạt chỉ số khi đánh",
    "shield_regen_pct":        "🛡️ Hồi lá chắn +{:.1%}/lượt",
    "thorn_pct":               "🌵 Gai phản chấn {:.0%}",
    "endure_threshold_pct":    "🐢 Quy Tức: gồng chí mạng ở {:.0%} HP",
    "endure_cooldown":         "⏳ Hồi Quy Tức: {:.0f} lượt",
    "overheal_to_shield_pct":  "🦌 Hồi dư → lá chắn {:.0%}",
    "bonus_base_dmg_per_self_def_pct":    "🗿 +{:.0%} DEF cộng vào sát thương gốc",
    "bonus_base_dmg_per_self_shield_pct": "🛡️ +{:.0%} lá chắn cộng vào sát thương gốc",
}

# Bool-flag effects — formatted as on/off lines, never numbers.
_FLAG_LABELS: dict[str, str] = {
    "heal_can_crit":      "💖 Hồi phục có thể BẠO HỒI",
    "dot_can_crit":       "🔥 Sát thương độc/đốt có thể bạo kích",
    "paralysis_on_crit":  "⚡ Bạo kích gây tê liệt",
    "barrier_on_cleanse": "🌟 Thanh tẩy sinh kết giới hộ thân",
}


def _format_bonus_lines(bonuses: dict) -> list[str]:
    lines: list[str] = []
    for key, val in bonuses.items():
        if isinstance(val, bool):
            if val:
                lines.append(_FLAG_LABELS.get(key, f"✨ {key}"))
            continue
        if isinstance(val, dict):
            if key == "element_dmg_bonus":
                for elem, v in val.items():
                    lines.append(f"🜁 Sát thương hệ {elem.capitalize()} +{v:.0%}")
            elif key in ("dot_dmg_bonus_by_kind", "dot_per_stack_pct_bonus"):
                suffix = " mỗi tầng" if key == "dot_per_stack_pct_bonus" else ""
                for kind, v in val.items():
                    lines.append(f"☠️ Sát thương {kind}{suffix} +{v:.0%}")
            continue
        template = _STAT_LABELS.get(key)
        if template:
            lines.append(template.format(val))
        elif isinstance(val, (int, float)):
            lines.append(f"• {key}: +{val:g}")
    return lines


async def _load_essence_counts(player_db_id: int) -> dict[str, int]:
    """Aggregate owned vital-essence quantities by item_key (grade-fungible)."""
    counts: dict[str, int] = {}
    async with get_session() as session:
        irepo = InventoryRepository(session)
        for inv in await irepo.get_all(player_db_id):
            item = registry.get_item(inv.item_key)
            if item and item.get("type") == "vital_essence":
                counts[inv.item_key] = counts.get(inv.item_key, 0) + inv.quantity
    return counts


def _hub_embed(player, essence_counts: dict[str, int]) -> discord.Embed:
    infusions = parse_infusions(player.body_part_infusions)
    the_tu = is_the_tu(player.active_axis)

    part_lines: list[str] = []
    for part in BODY_PARTS:
        if not is_part_unlocked(part, player.body_realm):
            part_lines.append(
                f"{part.emoji} **{part.vi}** — 🔒 mở khóa tại **{part.realm_vi}**"
            )
            continue
        entry = infusions.get(part.key)
        if entry:
            item = essence_item(entry["essence"])
            name = f"{_essence_badge(item)} {item['vi']}" if item else entry["essence"]
            if item and part_affinity_matches(part, item):
                name += " 💞"
            if item and is_awakened(part, entry):
                name += " ⚡"
            elif item:
                name += f" ({entry['fed']}/{awaken_threshold(part, item)})"
            part_lines.append(
                f"{part.emoji} **{part.vi}** (×{part_power_mult(part.tier):.2f}) — {name}"
            )
        else:
            part_lines.append(
                f"{part.emoji} **{part.vi}** (×{part_power_mult(part.tier):.2f}) — *trống*"
            )

    ess_lines = [
        f"{_essence_badge(item)} {item['vi']}: **{qty:,}**"
        for item, qty in _sorted_essences(essence_counts)
    ]
    ess_block = "\n".join(ess_lines) or "*(chưa có Tinh Huyết — săn yêu thú tại 🏔️ Thập Vạn Đại Sơn)*"

    path_note = (
        "🥋 Đang là **Thể Tu** — mọi chú nhập đều phát huy hiệu lực."
        if the_tu else
        "⚠️ Chỉ **Thể Tu** (trục Luyện Thể) mới nhận hiệu ứng chú nhập. "
        "Chuyển trục tu luyện để kích hoạt."
    )
    form = hoa_hinh_form(infusions, player.active_axis, player.body_realm)
    if form is not None:
        _, beast_vi = form
        path_note += (
            f"\n🐲 **HÓA HÌNH sẵn sàng — {beast_vi}!** Trong chiến đấu, khi HP "
            f"tụt dưới {HOA_HINH_TRIGGER_HP_PCT:.0%}, huyết mạch bùng nổ hóa thân "
            f"(1 lần/trận)."
        )

    desc = (
        f"Mỗi đại cảnh giới **Luyện Thể** khai mở một **Bộ Vị Cơ Thể**. "
        f"Chú nhập **Tinh Huyết** yêu thú để nhận sức mạnh theo chủng loài — "
        f"bộ vị càng cao, hiệu lực càng lớn. Hấp thụ đủ Tinh Huyết cùng loại "
        f"sẽ **⚡ Giác Tỉnh**, khai mở bí thuật đặc biệt của chủng loài đó.\n\n"
        + "\n".join(part_lines)
        + f"\n\n**Tinh Huyết sở hữu:**\n{ess_block}\n\n{path_note}"
    )
    return base_embed("🏔️ Bách Thể Chú Linh", desc, color=_COLOR)


def _part_embed(player, part: BodyPart, essence_counts: dict[str, int]) -> discord.Embed:
    infusions = parse_infusions(player.body_part_infusions)
    entry = infusions.get(part.key)
    favored = ARCHETYPE_VI.get(PART_AFFINITY.get(part.key, ""), "?")
    lines = [
        f"Bộ vị thứ {part.tier + 1} — tôi luyện tại cảnh giới **{part.realm_vi}**.",
        f"Hệ số bộ vị: **×{part_power_mult(part.tier):.2f}** · "
        f"Tương thích: **{favored}** (Tinh Huyết cùng thiên hướng +25% hiệu lực)",
    ]
    if entry:
        item = essence_item(entry["essence"])
        name = f"{_essence_badge(item)} {item['vi']}" if item else entry["essence"]
        awakened = bool(item) and is_awakened(part, entry)
        affinity = bool(item) and part_affinity_matches(part, item)
        resonance = resonance_count(infusions, player.body_realm, entry["essence"])
        lines.append(f"\n**Đang chú nhập:** {name}" + (" ⚡ **ĐÃ GIÁC TỈNH**" if awakened else ""))
        if affinity:
            lines.append("💞 **Tương thích bộ vị** — hiệu lực ×1.25")
        if resonance >= RESONANCE_T2_COUNT:
            lines.append(
                f"🩸 **Huyết Mạch Cộng Hưởng {resonance}/9** — hiệu lực ×1.15, "
                f"bí thuật Giác Tỉnh ×1.5"
            )
        elif resonance >= RESONANCE_T1_COUNT:
            lines.append(f"🩸 **Huyết Mạch Cộng Hưởng {resonance}/9** — hiệu lực ×1.15")
        if item:
            awakening = item.get("awakening") or {}
            if not awakened:
                threshold = awaken_threshold(part, item)
                lines.append(
                    f"🔮 Giác Tỉnh: **{entry['fed']}/{threshold}** Tinh Huyết đã hấp thụ — "
                    f"tiếp tục chú nhập cùng loại để thức tỉnh."
                )
            if awakening.get("desc_vi"):
                state = "⚡" if awakened else "🔒"
                lines.append(f"{state} *Bí thuật: {awakening['desc_vi']}*")
            skill_key = awakening.get("granted_skill")
            if skill_key:
                skill = registry.get_skill(skill_key)
                skill_name = skill["vi"] if skill else skill_key
                state = "⚡" if awakened else "🔒"
                lines.append(f"{state} *Kỹ năng ban tặng: **{skill_name}***")
        bonus_lines = _format_bonus_lines(scaled_essence_bonuses(
            entry["essence"], part.tier, awakened=awakened,
            affinity=affinity, resonance=resonance,
        ))
        if bonus_lines:
            lines.append("\n".join(bonus_lines))
    else:
        lines.append("\n*Chưa chú nhập Tinh Huyết nào.*")
    lines.append(
        "\nChọn Tinh Huyết bên dưới để chú nhập. Cùng loại sẽ **tích lũy** về "
        "phía Giác Tỉnh; đổi loại khác sẽ **thay thế** và tính lại từ đầu "
        "(không hoàn lại nguyên liệu)."
    )
    return base_embed(f"{part.emoji} {part.vi}", "\n".join(lines), color=_COLOR)


class PartSelect(discord.ui.Select):
    def __init__(self, view_ref: TheTuView) -> None:
        self._view_ref = view_ref
        options = [
            discord.SelectOption(
                label=f"{p.vi} — {p.realm_vi}",
                value=p.key,
                emoji=p.emoji,
                description=f"Hệ số ×{part_power_mult(p.tier):.2f}",
            )
            for p in BODY_PARTS
            if is_part_unlocked(p, view_ref.player_body_realm)
        ]
        if not options:
            options = [discord.SelectOption(label="(Chưa mở khóa bộ vị nào)", value="__none__")]
        super().__init__(placeholder="🩸 Chọn Bộ Vị Cơ Thể...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._view_ref.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        if self.values[0] == "__none__":
            if not await safe_defer(interaction):
                return
            return
        await self._view_ref.show_part(interaction, self.values[0])


class EssenceSelect(discord.ui.Select):
    def __init__(self, view_ref: TheTuView, part: BodyPart) -> None:
        self._view_ref = view_ref
        self._part = part
        options: list[discord.SelectOption] = []
        for item, qty in _sorted_essences(view_ref.essence_counts):
            cost = infusion_cost(part, item)
            options.append(discord.SelectOption(
                label=f"{_essence_badge(item)} {item['vi']}"[:100],
                value=item["key"],
                description=f"Sở hữu {qty} | Cần {cost} để chú nhập"[:100],
            ))
        if not options:
            options = [discord.SelectOption(label="(Không có Tinh Huyết)", value="__none__")]
        super().__init__(placeholder="✨ Chọn Tinh Huyết để chú nhập...", options=options[:25], row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._view_ref.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        if self.values[0] == "__none__":
            if not await safe_defer(interaction):
                return
            return
        await self._view_ref.infuse(interaction, self._part, self.values[0])


class TheTuView(discord.ui.View):
    """Hub view — part select on row 0; after picking a part, an essence
    select on row 1 plus remove/back buttons."""

    def __init__(
        self,
        discord_id: int,
        player_db_id: int,
        player_body_realm: int,
        essence_counts: dict[str, int],
    ) -> None:
        super().__init__(timeout=180)
        self.discord_id = discord_id
        self.player_db_id = player_db_id
        self.player_body_realm = player_body_realm
        self.essence_counts = essence_counts
        self.current_part: BodyPart | None = None
        self._build()

    def _build(self) -> None:
        self.clear_items()
        self.add_item(PartSelect(self))
        if self.current_part is not None:
            self.add_item(EssenceSelect(self, self.current_part))
            remove_btn = discord.ui.Button(
                label="🗑️ Gỡ Chú Nhập", style=discord.ButtonStyle.danger, row=2,
            )
            remove_btn.callback = self._remove_cb
            self.add_item(remove_btn)
            back_btn = discord.ui.Button(
                label="◀ Tổng Quan", style=discord.ButtonStyle.secondary, row=2,
            )
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    async def _fetch_player(self):
        async with get_session() as session:
            repo = PlayerRepository(session)
            return await repo.get_by_discord_id(self.discord_id)

    async def show_part(self, interaction: discord.Interaction, part_key: str) -> None:
        part = get_part(part_key)
        if part is None:
            if not await safe_defer(interaction):
                return
            return
        player = await self._fetch_player()
        if player is None:
            await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
            return
        self.current_part = part
        self.essence_counts = await _load_essence_counts(self.player_db_id)
        self._build()
        await interaction.response.edit_message(
            embed=_part_embed(player, part, self.essence_counts), view=self,
        )

    async def show_hub(self, interaction: discord.Interaction) -> None:
        player = await self._fetch_player()
        if player is None:
            await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
            return
        self.current_part = None
        self.essence_counts = await _load_essence_counts(self.player_db_id)
        self.player_body_realm = player.body_realm
        self._build()
        await interaction.response.edit_message(
            embed=_hub_embed(player, self.essence_counts), view=self,
        )

    async def infuse(
        self, interaction: discord.Interaction, part: BodyPart, item_key: str,
    ) -> None:
        item = registry.get_item(item_key)
        if not item or item.get("type") != "vital_essence":
            if not await safe_defer(interaction):
                return
            return
        cost = infusion_cost(part, item)

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(self.discord_id)
            if player is None:
                await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            if not is_part_unlocked(part, player.body_realm):
                await interaction.response.edit_message(
                    embed=error_embed(f"Bộ vị **{part.vi}** chưa mở khóa (cần {part.realm_vi})."),
                    view=self,
                )
                return
            irepo = InventoryRepository(session)
            removed = await irepo.remove_any_grade(player.id, item_key, cost)
            if not removed:
                owned = sum(
                    inv.quantity for inv in await irepo.get_all(player.id)
                    if inv.item_key == item_key
                )
                await interaction.response.edit_message(
                    embed=error_embed(
                        f"Không đủ **{item['vi']}** — cần {cost}, hiện có {owned}."
                    ),
                    view=self,
                )
                return
            infusions = parse_infusions(player.body_part_infusions)
            prev = infusions.get(part.key)
            was_awakened = is_awakened(part, prev) if prev else False
            if prev and prev.get("essence") == item["essence_key"]:
                # Same type: feeding accumulates toward Giác Tỉnh.
                entry = {"essence": item["essence_key"],
                         "fed": int(prev.get("fed", 0)) + cost}
            else:
                # New type replaces the old — progress restarts.
                entry = {"essence": item["essence_key"], "fed": cost}
            infusions[part.key] = entry
            player.body_part_infusions = encode_infusions(infusions)
            await repo.save(player)

        self.essence_counts = await _load_essence_counts(self.player_db_id)
        self._build()
        awakened = is_awakened(part, entry)
        just_awakened = awakened and not (
            was_awakened and prev.get("essence") == item["essence_key"]
        )
        affinity = part_affinity_matches(part, item)
        resonance = resonance_count(infusions, player.body_realm, item["essence_key"])
        bonus_lines = _format_bonus_lines(scaled_essence_bonuses(
            item["essence_key"], part.tier, awakened=awakened,
            affinity=affinity, resonance=resonance,
        ))
        if affinity:
            bonus_lines.insert(0, "💞 Tương thích bộ vị — hiệu lực ×1.25")
        if resonance >= RESONANCE_T1_COUNT:
            bonus_lines.insert(0, f"🩸 Huyết Mạch Cộng Hưởng {resonance}/9")
        threshold = awaken_threshold(part, item)
        awakening = item.get("awakening") or {}
        msg = (
            f"🩸 Đã chú nhập **{item['vi']}** vào {part.emoji} **{part.vi}** "
            f"(tiêu hao {cost})."
        )
        if awakened and just_awakened:
            msg += f"\n\n⚡ **GIÁC TỈNH!** {awakening.get('desc_vi', '')}"
            skill_key = awakening.get("granted_skill")
            if skill_key:
                skill = registry.get_skill(skill_key)
                skill_name = skill["vi"] if skill else skill_key
                msg += f"\n🗡️ Lĩnh ngộ kỹ năng mới: **{skill_name}**"
        elif not awakened and threshold > 0:
            msg += f"\n🔮 Giác Tỉnh: **{entry['fed']}/{threshold}** Tinh Huyết đã hấp thụ."
        embed = success_embed(msg + "\n\n" + "\n".join(bonus_lines))
        embed.title = "⚡ GIÁC TỈNH — Bí Thuật Khai Mở" if just_awakened else "✨ Chú Nhập Thành Công"
        await interaction.response.edit_message(embed=embed, view=self)

    async def _remove_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        part = self.current_part
        if part is None:
            if not await safe_defer(interaction):
                return
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(self.discord_id)
            if player is None:
                await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            infusions = parse_infusions(player.body_part_infusions)
            if part.key not in infusions:
                await interaction.response.edit_message(
                    embed=_part_embed(player, part, self.essence_counts), view=self,
                )
                return
            infusions.pop(part.key, None)
            player.body_part_infusions = encode_infusions(infusions)
            await repo.save(player)
            await interaction.response.edit_message(
                embed=_part_embed(player, part, self.essence_counts), view=self,
            )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        await self.show_hub(interaction)

    async def on_timeout(self) -> None:
        pass


class TheTuCog(commands.Cog, name="TheTu"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="thetu",
        description="Bách Thể Chú Linh — chú nhập Tinh Huyết vào Bộ Vị Cơ Thể (Thể Tu)",
    )
    async def thetu(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật. Dùng `/register` để bắt đầu."),
            )
            return
        essence_counts = await _load_essence_counts(player.id)
        view = TheTuView(interaction.user.id, player.id, player.body_realm, essence_counts)
        await interaction.edit_original_response(
            embed=_hub_embed(player, essence_counts), view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TheTuCog(bot))
