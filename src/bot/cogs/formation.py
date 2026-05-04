"""Trận Pháp (formation) — interactive formation switch + gem socket manager."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.formation import FORMATION_GEM_SLOTS
from src.db.repositories.formation_repo import FormationRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.grades import Grade
from src.game.constants.realms import FORMATION_REALMS, realm_label
from src.game.systems.character_stats import active_formation_gem_map
from src.game.systems.cultivation import (
    formation_path_multiplier,
    formation_reserve_reduction,
    get_active_formations,
    max_formation_slots,
    set_active_formations,
)
from src.game.systems.formation import (
    compute_active_formation_bonuses,
    gem_element,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

_GEM_ELEMENT_VI = {
    "kim": "Kim", "moc": "Mộc", "thuy": "Thủy", "hoa": "Hỏa",
    "tho": "Thổ", "loi": "Lôi", "phong": "Phong", "quang": "Quang", "am": "Âm",
    "bang": "Băng",
}

_GEM_EMOJI = {
    "kim": "⚙️", "moc": "🌿", "thuy": "💧", "hoa": "🔥",
    "tho": "🪨", "loi": "⚡", "phong": "🌬️", "quang": "☀️", "am": "🌑",
    "bang": "❄️",
}


def _gem_display(gem_key: str | None) -> str:
    if not gem_key:
        return "⬜ *(trống)*"
    item = registry.get_item(gem_key) or {}
    elem = gem_element(gem_key) or ""
    emoji = _GEM_EMOJI.get(elem, "💠")
    return f"{emoji} {item.get('vi', gem_key)}"


# ── Embeds ────────────────────────────────────────────────────────────────────

def _formation_hub_embed(player, active_forms: list[dict], form_bonuses: dict, gem_map: dict) -> discord.Embed:
    """Hub embed — renders every active formation slot plus aggregate bonuses.

    - When no formations are active: zero-state card.
    - When one is active: legacy single-formation detail view (unchanged shape
      for existing users).
    - When multiple are active: multi-slot summary card listing each slot.
    """
    stages = player.formation_realm * 9 + player.formation_level
    path_mult = formation_path_multiplier(stages)
    path_label = realm_label("formation", player.formation_realm, player.formation_xp)
    slot_cap = max_formation_slots(
        player.body_realm, player.qi_realm, player.formation_realm,
    )

    if not active_forms:
        desc = (
            f"Chưa kích hoạt trận pháp nào. ({len(active_forms)}/{slot_cap} ổ)\n"
            "Dùng nút **🔯 Đổi Trận** để chọn trận pháp phù hợp với lộ trình tu luyện."
        )
        embed = base_embed("🔯 Trận Pháp", desc, color=0x555555)
        embed.add_field(
            name="Trận Đạo",
            value=f"**{path_label}** · Hệ số trận pháp **×{path_mult:.2f}**",
            inline=False,
        )
        return embed

    if len(active_forms) == 1:
        return _formation_detail_embed(player, active_forms[0], form_bonuses)

    return _formation_multi_embed(player, active_forms, form_bonuses, gem_map, slot_cap)


def _formation_multi_embed(
    player, active_forms: list[dict], form_bonuses: dict,
    gem_map: dict, slot_cap: int,
) -> discord.Embed:
    """Multi-slot summary: one line per active formation + aggregate bonuses."""
    from src.db.models.formation import FORMATION_GEM_SLOTS as MAX
    stages = player.formation_realm * 9 + player.formation_level
    path_mult = formation_path_multiplier(stages)
    path_label = realm_label("formation", player.formation_realm, player.formation_xp)
    total_reserve = form_bonuses.get("_mp_reserve_pct", 0.0) * 100
    reserve_mult = formation_reserve_reduction(stages)
    reserve_note = (
        f" *(giảm xuống {reserve_mult * 100:.0f}% / trận nhờ Trận Đạo)*"
        if reserve_mult < 0.999
        else ""
    )

    embed = base_embed(
        f"🔯 Tổ Hợp Trận Pháp ({len(active_forms)}/{slot_cap} ổ)",
        f"🏯 **Trận Đạo**: {path_label} · Hệ số **×{path_mult:.2f}**\n"
        f"🔒 **Tổng MP trấn giữ**: {total_reserve:.1f}%{reserve_note}",
        color=0x9B59B6,
    )

    # One field per active slot
    for idx, fd in enumerate(active_forms):
        elem = fd.get("element") or "—"
        elem_vi = _GEM_ELEMENT_VI.get(elem, elem.title())
        gems = gem_map.get(fd["key"], [])
        thresholds = [int(t) for t in (fd.get("gem_threshold_bonuses") or {}).keys()]
        next_t = next((t for t in sorted(thresholds) if t > len(gems)), None)
        th_note = f"Ngưỡng tiếp: **{next_t}** ngọc" if next_t else "✨ Đạt ngưỡng cao nhất"
        embed.add_field(
            name=f"#{idx + 1}  🔯 {fd['vi']}",
            value=(
                f"Hệ: **{elem_vi}** · Ngọc: **{len(gems)}/{MAX}**\n"
                f"{th_note} · Skill: `{fd.get('formation_skill_key', '—')}`"
            ),
            inline=False,
        )

    # Aggregated bonus summary — re-uses the single-formation formatter logic
    lines: list[str] = []
    pct_keys = {
        "hp_pct", "mp_pct", "final_dmg_bonus", "final_dmg_reduce",
        "hp_regen_pct", "mp_regen_pct", "cooldown_reduce",
        "burn_on_hit_pct", "slow_on_hit_pct",
    }
    name_map = {
        "hp_pct": "HP", "mp_pct": "MP",
        "final_dmg_bonus": "Tăng ST", "final_dmg_reduce": "Giảm ST",
        "hp_regen_pct": "Hồi HP%", "mp_regen_pct": "Hồi MP%",
        "cooldown_reduce": "Hồi Chiêu-",
        "crit_rating": "Bạo Kích",
        "crit_dmg_rating": "Bạo Thương",
        "crit_res_rating": "Kháng Bạo",
        "evasion_rating": "Né",
        "res_element": "Kháng Hệ",
        "res_all": "Kháng TN",
        "spd_bonus": "Tốc Độ",
    }
    for k, v in form_bonuses.items():
        if k.startswith("_") or k == "note":
            continue
        if isinstance(v, bool):
            if v:
                lines.append(f"✨ **{name_map.get(k, k)}**")
            continue
        if not isinstance(v, (int, float)) or v == 0:
            continue
        label = name_map.get(k, k)
        if k in pct_keys:
            lines.append(f"• {label}: **+{v * 100:.1f}%**")
        else:
            lines.append(f"• {label}: **+{int(v):,}**")
    embed.add_field(
        name="📊 Hiệu Ứng Tổng",
        value="\n".join(lines) if lines else "*(không có)*",
        inline=False,
    )
    return embed


def _formation_detail_embed(player, form_data: dict, form_bonuses: dict) -> discord.Embed:
    """Primary embed for the hub — shows active formation + bonuses + slot grid."""
    from src.db.models.formation import FORMATION_GEM_SLOTS as MAX
    stages = player.formation_realm * 9 + player.formation_level
    path_mult = formation_path_multiplier(stages)
    path_label = realm_label("formation", player.formation_realm, player.formation_xp)

    elem = form_data.get("element") or "—"
    elem_vi = _GEM_ELEMENT_VI.get(elem, elem.title())
    reserve_pct = form_bonuses.get("_mp_reserve_pct", 0.0) * 100
    reserve_mult = formation_reserve_reduction(stages)
    reserve_note = (
        f" *(giảm xuống {reserve_mult * 100:.0f}% nhờ Trận Đạo)*"
        if reserve_mult < 0.999
        else ""
    )

    # Bonus summary — skip meta + bool flags
    lines: list[str] = []
    pct_keys = {
        "hp_pct", "mp_pct", "final_dmg_bonus", "final_dmg_reduce",
        "hp_regen_pct", "mp_regen_pct", "cooldown_reduce",
        "burn_on_hit_pct", "slow_on_hit_pct",
    }
    name_map = {
        "hp_pct": "HP", "mp_pct": "MP",
        "final_dmg_bonus": "Tăng ST", "final_dmg_reduce": "Giảm ST",
        "hp_regen_pct": "Hồi HP%", "mp_regen_pct": "Hồi MP%",
        "cooldown_reduce": "Hồi Chiêu-",
        "crit_rating": "Bạo Kích",
        "crit_dmg_rating": "Bạo Thương",
        "crit_res_rating": "Kháng Bạo",
        "evasion_rating": "Né",
        "res_element": "Kháng Hệ",
        "res_all": "Kháng TN",
        "spd_bonus": "Tốc Độ",
        "burn_on_hit_pct": "Thiêu Đốt",
        "slow_on_hit_pct": "Làm Chậm",
    }
    for k, v in form_bonuses.items():
        if k.startswith("_") or k == "note":
            continue
        if isinstance(v, bool):
            if v:
                lines.append(f"✨ **{name_map.get(k, k)}**")
            continue
        if not isinstance(v, (int, float)) or v == 0:
            continue
        label = name_map.get(k, k)
        if k in pct_keys:
            lines.append(f"• {label}: **+{v * 100:.1f}%**")
        else:
            lines.append(f"• {label}: **+{int(v):,}**")

    bonus_text = "\n".join(lines) if lines else "*(không có)*"

    embed = base_embed(
        f"🔯 {form_data['vi']}",
        f"*Hệ: {elem_vi}*\n"
        f"🏯 **Trận Đạo**: {path_label} · Hệ số **×{path_mult:.2f}**\n"
        f"🔒 **Trấn giữ MP**: {reserve_pct:.1f}%{reserve_note}",
        color=0x9B59B6,
    )
    embed.add_field(name="📊 Hiệu Ứng Hiện Tại", value=bonus_text, inline=False)

    # Gem socket grid
    slots = player.formations
    active = next((f for f in slots if f.formation_key == form_data["key"]), None) if slots else None
    gem_slots = active.gem_slots if active else {}
    grid_lines: list[str] = []
    for i in range(MAX):
        gem_key = gem_slots.get(str(i))
        grid_lines.append(f"`[{i}]` {_gem_display(gem_key)}")
    embed.add_field(
        name=f"💎 Ổ Khảm ({len(gem_slots)}/{MAX})",
        value="\n".join(grid_lines),
        inline=False,
    )

    # Thresholds summary
    thresholds = form_data.get("gem_threshold_bonuses", {})
    thr_lines = []
    for t_str, data in sorted(thresholds.items(), key=lambda x: int(x[0])):
        t = int(t_str)
        done = "✅" if len(gem_slots) >= t else "⬜"
        note = data.get("note", "")
        thr_lines.append(f"{done} **{t} ngọc** — {note}")
    if thr_lines:
        embed.add_field(
            name="🎯 Ngưỡng Ngọc",
            value="\n".join(thr_lines),
            inline=False,
        )

    return embed


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _load_formation_view_state(discord_id: int):
    """Return (player, active_form_data_list, aggregated_form_bonuses, gem_map).

    The bonus aggregation (per-slot merge + skill-reservation fold-in + cap)
    lives in ``compute_active_formation_bonuses``; this helper only handles
    the DB load and registry lookup for active form data.
    """
    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is None:
            return None, [], {}, {}

        active_keys = get_active_formations(player.active_formation)
        active_form_data = [
            fd for fd in (registry.get_formation(k) for k in active_keys) if fd
        ]
        gem_map = active_formation_gem_map(player)
        form_bonuses = compute_active_formation_bonuses(player)
    return player, active_form_data, form_bonuses, gem_map


async def _player_gem_inventory(player_db_id: int) -> list[dict]:
    """Return a list of gem items from the player's inventory with metadata."""
    async with get_session() as session:
        irepo = InventoryRepository(session)
        items = await irepo.get_all(player_db_id)
    gems: list[dict] = []
    for inv in items:
        data = registry.get_item(inv.item_key)
        if data and data.get("type") == "gem":
            gems.append({
                "key": inv.item_key,
                "grade": inv.grade,
                "qty": inv.quantity,
                "name": data.get("vi", inv.item_key),
                "element": gem_element(inv.item_key),
            })
    return gems


# ── Views ─────────────────────────────────────────────────────────────────────

async def _render_hub(interaction: discord.Interaction, discord_id: int, back_fn=None) -> None:
    """Re-read state and render the formation hub on the current message."""
    player, active_forms, form_bonuses, gem_map = await _load_formation_view_state(discord_id)
    if player is None:
        await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
        return
    embed = _formation_hub_embed(player, active_forms, form_bonuses, gem_map)
    view = FormationHubView(
        discord_id,
        has_active=bool(active_forms),
        back_fn=back_fn,
    )
    await interaction.edit_original_response(embed=embed, view=view)


class FormationHubView(discord.ui.View):
    """Root formation hub: switch formation, manage sockets, refresh, back."""

    def __init__(self, discord_id: int, has_active: bool, back_fn=None) -> None:
        super().__init__(timeout=300)
        self.discord_id = discord_id
        self._back_fn = back_fn

        switch_btn = discord.ui.Button(label="🔯 Đổi Trận", style=discord.ButtonStyle.blurple, row=0)
        switch_btn.callback = self._on_switch
        self.add_item(switch_btn)

        sockets_btn = discord.ui.Button(
            label="💎 Khảm Ngọc",
            style=discord.ButtonStyle.primary,
            disabled=not has_active,
            row=0,
        )
        sockets_btn.callback = self._on_sockets
        self.add_item(sockets_btn)

        refresh_btn = discord.ui.Button(label="🔄 Làm Mới", style=discord.ButtonStyle.secondary, row=0)
        refresh_btn.callback = self._on_refresh
        self.add_item(refresh_btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(label="◀ Trở Về", style=discord.ButtonStyle.secondary, row=0)
            back_btn.callback = self._on_back
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.discord_id

    async def _on_switch(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _render_formation_picker(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_sockets(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _render_socket_manager(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _render_hub(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


# ── Formation picker ──────────────────────────────────────────────────────────

async def _render_formation_picker(interaction: discord.Interaction, discord_id: int, back_fn=None) -> None:
    all_forms = sorted(registry.formations.values(), key=lambda f: f.get("vi", ""))
    player, _, _, _ = await _load_formation_view_state(discord_id)
    active_keys = set(get_active_formations(player.active_formation)) if player else set()
    slot_cap = (
        max_formation_slots(player.body_realm, player.qi_realm, player.formation_realm)
        if player else 1
    )

    desc_lines = [
        f"Chọn **tới {slot_cap} trận pháp** muốn kích hoạt đồng thời. "
        f"Mỗi trận chiếm một ổ và cộng MP trấn giữ (tổng cap 50%).",
        f"Trận đã chọn: **{len(active_keys)}/{slot_cap}**.",
    ]
    if active_keys:
        names = [registry.get_formation(k) or {} for k in active_keys]
        names_str = ", ".join(n.get("vi", "?") for n in names)
        desc_lines.append(f"\n🔯 Hiện đang dùng: **{names_str}**")
    embed = base_embed("🔯 Chọn Trận Pháp", "\n".join(desc_lines), color=0x9B59B6)

    for f in all_forms[:10]:
        elem = f.get("element") or "—"
        elem_vi = _GEM_ELEMENT_VI.get(elem, elem.title())
        marker = " ◀ Đang dùng" if f["key"] in active_keys else ""
        embed.add_field(
            name=f"{f['vi']}{marker}",
            value=f"Hệ: {elem_vi} · Skill: `{f.get('formation_skill_key', '—')}`",
            inline=False,
        )

    view = FormationPickerView(
        discord_id, all_forms, active_keys=active_keys,
        slot_cap=slot_cap, back_fn=back_fn,
    )
    await interaction.edit_original_response(embed=embed, view=view)


class FormationPickerView(discord.ui.View):
    """Multi-select picker. User chooses up to ``slot_cap`` formations at once;
    the selection replaces ``active_formation`` (comma-separated list)."""

    def __init__(
        self, discord_id: int, forms: list[dict],
        active_keys: set[str], slot_cap: int, back_fn=None,
    ) -> None:
        super().__init__(timeout=180)
        self.discord_id = discord_id
        self._back_fn = back_fn
        self._slot_cap = slot_cap

        options = [
            discord.SelectOption(
                label=f["vi"][:100],
                value=f["key"],
                description=f"Hệ: {_GEM_ELEMENT_VI.get(f.get('element') or '', '—')}"[:100],
                emoji="🔯",
                default=f["key"] in active_keys,
            )
            for f in forms[:25]
        ]
        select = discord.ui.Select(
            placeholder=f"Chọn tối đa {slot_cap} trận pháp...",
            options=options,
            min_values=0,
            max_values=min(slot_cap, len(options)),
            row=0,
        )
        select.callback = self._on_pick
        self.add_item(select)

        back_btn = discord.ui.Button(label="◀ Quay lại", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._on_back
        self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.discord_id

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        picked = list(interaction.data["values"])[: self._slot_cap]
        async with get_session() as session:
            prepo = PlayerRepository(session)
            frepo = FormationRepository(session)
            player = await prepo.get_by_discord_id(self.discord_id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            # Ensure a PlayerFormation row exists for every newly selected slot
            # so gem state is preserved across activate/deactivate cycles.
            for k in picked:
                await frepo.get_or_create(player.id, k)
            player.active_formation = set_active_formations(picked)
            await prepo.save(player)
        await interaction.response.defer()
        await _render_hub(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _render_hub(interaction, self.discord_id, back_fn=self._back_fn)


# ── Socket manager ────────────────────────────────────────────────────────────

async def _render_socket_manager(
    interaction: discord.Interaction, discord_id: int, back_fn=None,
    target_formation_key: str | None = None,
) -> None:
    """Render the socket manager. When multiple formations are active,
    ``target_formation_key`` specifies which one to manage — defaults to the
    first active slot when not provided.
    """
    player, active_forms, _, _ = await _load_formation_view_state(discord_id)
    if player is None or not active_forms:
        await interaction.edit_original_response(
            embed=error_embed("Chưa kích hoạt trận pháp nào."), view=None,
        )
        return

    # Pick which formation to manage gems for.
    if target_formation_key is None:
        target_formation_key = active_forms[0]["key"]
    form_data = next(
        (fd for fd in active_forms if fd["key"] == target_formation_key),
        active_forms[0],
    )

    active_form = next(
        (f for f in (player.formations or []) if f.formation_key == form_data["key"]),
        None,
    )
    gem_slots = active_form.gem_slots if active_form else {}

    gems_inv = await _player_gem_inventory(player.id)

    lines = [
        f"Khảm ngọc để mở ngưỡng **1 / 3 / 5 / 7 / {FORMATION_GEM_SLOTS}**.",
        f"**Trận đang khảm**: {form_data['vi']}",
    ]
    if len(active_forms) > 1:
        lines.append(
            f"*(Đang kích hoạt {len(active_forms)} trận — chọn dropdown trên "
            f"để đổi trận cần khảm.)*"
        )
    lines.append("")
    for i in range(FORMATION_GEM_SLOTS):
        gem_key = gem_slots.get(str(i))
        lines.append(f"`[{i}]` {_gem_display(gem_key)}")

    if gems_inv:
        lines.append("\n**Ngọc có trong túi đồ:**")
        for g in gems_inv[:15]:
            lines.append(f"  {_GEM_EMOJI.get(g['element'] or '', '💠')} `{g['name']}` ×{g['qty']}")
    else:
        lines.append("\n*(Không có ngọc nào trong túi)*")

    embed = base_embed(
        f"💎 Ổ Khảm Ngọc — {form_data['vi']}",
        "\n".join(lines),
        color=0x9B59B6,
    )

    view = SocketManagerView(
        discord_id, gem_slots, gems_inv, back_fn=back_fn,
        active_forms=active_forms, target_formation_key=form_data["key"],
    )
    await interaction.edit_original_response(embed=embed, view=view)


class SocketManagerView(discord.ui.View):
    """Pick a slot + (optionally) a gem → Khảm / Gỡ.

    When multiple formations are active the view also shows a top-row
    formation selector so the user can choose which formation to manage gems
    for without leaving the socket hub.
    """

    def __init__(
        self,
        discord_id: int,
        gem_slots: dict,
        gems_inv: list[dict],
        back_fn=None,
        active_forms: list[dict] | None = None,
        target_formation_key: str | None = None,
    ) -> None:
        super().__init__(timeout=240)
        self.discord_id = discord_id
        self._gem_slots = dict(gem_slots)
        self._gems_inv = list(gems_inv)
        self._back_fn = back_fn
        self._selected_slot: int | None = None
        self._selected_gem_key: str | None = None
        self._target_formation_key = target_formation_key
        self._active_forms = active_forms or []

        # Row 0: formation selector (only shown when ≥2 active slots).
        # Keeps simple single-formation UI unchanged for normal players.
        row_offset = 0
        if len(self._active_forms) >= 2 and target_formation_key:
            form_opts = [
                discord.SelectOption(
                    label=fd["vi"][:100],
                    value=fd["key"],
                    default=(fd["key"] == target_formation_key),
                    emoji="🔯",
                )
                for fd in self._active_forms
            ]
            self._form_select = discord.ui.Select(
                placeholder="🔯 Chọn trận pháp cần khảm...",
                options=form_opts,
                row=0,
            )
            self._form_select.callback = self._on_form_pick
            self.add_item(self._form_select)
            row_offset = 1

        # Slot select — all 10 slots, showing whether occupied
        slot_opts = []
        for i in range(FORMATION_GEM_SLOTS):
            gk = gem_slots.get(str(i))
            if gk:
                data = registry.get_item(gk) or {}
                label = f"[{i}] {data.get('vi', gk)[:80]}"
                desc = "Đã khảm — chọn để gỡ hoặc thay"
            else:
                label = f"[{i}] (trống)"
                desc = "Slot trống"
            slot_opts.append(discord.SelectOption(
                label=label[:100],
                value=str(i),
                description=desc[:100],
                emoji="💎" if gk else "⬜",
            ))
        self._slot_select = discord.ui.Select(
            placeholder="🎯 Chọn slot...",
            options=slot_opts,
            row=row_offset,
        )
        self._slot_select.callback = self._on_slot_pick
        self.add_item(self._slot_select)

        # Gem select from inventory (only when gems exist)
        if gems_inv:
            gem_opts = []
            for g in gems_inv[:25]:
                elem = g["element"] or ""
                emoji = _GEM_EMOJI.get(elem, "💠")
                gem_opts.append(discord.SelectOption(
                    label=f"{g['name']} ×{g['qty']}"[:100],
                    value=g["key"],
                    description=f"Hệ: {_GEM_ELEMENT_VI.get(elem, '—')}"[:100],
                    emoji=emoji,
                ))
            self._gem_select = discord.ui.Select(
                placeholder="💠 Chọn ngọc để khảm...",
                options=gem_opts,
                row=row_offset + 1,
            )
            self._gem_select.callback = self._on_gem_pick
            self.add_item(self._gem_select)
        else:
            self._gem_select = None

        # Action buttons on the last row (Discord caps at row=4).
        action_row = min(4, row_offset + 2)
        inlay_btn = discord.ui.Button(label="✨ Khảm", style=discord.ButtonStyle.green, row=action_row)
        inlay_btn.callback = self._on_inlay
        self.add_item(inlay_btn)

        remove_btn = discord.ui.Button(label="🗑️ Gỡ Ngọc", style=discord.ButtonStyle.red, row=action_row)
        remove_btn.callback = self._on_remove
        self.add_item(remove_btn)

        back_btn = discord.ui.Button(label="◀ Trở Lại Trận", style=discord.ButtonStyle.secondary, row=action_row)
        back_btn.callback = self._on_back
        self.add_item(back_btn)

    async def _on_form_pick(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        picked = interaction.data["values"][0]
        await _render_socket_manager(
            interaction, self.discord_id, back_fn=self._back_fn,
            target_formation_key=picked,
        )

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.discord_id

    async def _on_slot_pick(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        self._selected_slot = int(interaction.data["values"][0])
        await interaction.response.defer()

    async def _on_gem_pick(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        self._selected_gem_key = interaction.data["values"][0]
        await interaction.response.defer()

    async def _on_inlay(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if self._selected_slot is None:
            await interaction.response.send_message(
                embed=error_embed("Chọn slot trước khi khảm."), ephemeral=True,
            )
            return
        if not self._selected_gem_key:
            await interaction.response.send_message(
                embed=error_embed("Chọn ngọc muốn khảm trước."), ephemeral=True,
            )
            return

        await interaction.response.defer()

        async with get_session() as session:
            prepo = PlayerRepository(session)
            frepo = FormationRepository(session)
            irepo = InventoryRepository(session)
            player = await prepo.get_by_discord_id(self.discord_id)
            if player is None or not self._target_formation_key:
                await interaction.followup.send(
                    embed=error_embed("Chưa kích hoạt trận pháp."), ephemeral=True,
                )
                return
            target = self._target_formation_key

            gem_data = registry.get_item(self._selected_gem_key) or {}
            grade = Grade(gem_data.get("grade", 1))
            if not await irepo.has_item(player.id, self._selected_gem_key, grade):
                await interaction.followup.send(
                    embed=error_embed(f"Không đủ **{gem_data.get('vi', self._selected_gem_key)}** trong túi đồ."),
                    ephemeral=True,
                )
                return

            # If slot was occupied, return the old gem to inventory first
            existing = await frepo.get(player.id, target)
            old_key = None
            if existing:
                old_key = existing.gem_slots.get(str(self._selected_slot))
            if old_key:
                old_data = registry.get_item(old_key) or {}
                old_grade = Grade(old_data.get("grade", 1))
                await irepo.add_item(player.id, old_key, old_grade, 1)

            await frepo.inlay_gem(
                player.id, target,
                self._selected_slot, self._selected_gem_key,
            )
            await irepo.remove_item(player.id, self._selected_gem_key, grade, 1)

        await _render_socket_manager(
            interaction, self.discord_id, back_fn=self._back_fn,
            target_formation_key=self._target_formation_key,
        )

    async def _on_remove(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if self._selected_slot is None:
            await interaction.response.send_message(
                embed=error_embed("Chọn slot cần gỡ trước."), ephemeral=True,
            )
            return

        await interaction.response.defer()

        async with get_session() as session:
            prepo = PlayerRepository(session)
            frepo = FormationRepository(session)
            irepo = InventoryRepository(session)
            player = await prepo.get_by_discord_id(self.discord_id)
            if player is None or not self._target_formation_key:
                await interaction.followup.send(
                    embed=error_embed("Chưa kích hoạt trận pháp."), ephemeral=True,
                )
                return
            target = self._target_formation_key

            existing = await frepo.get(player.id, target)
            old_key = existing.gem_slots.get(str(self._selected_slot)) if existing else None
            if not old_key:
                await interaction.followup.send(
                    embed=error_embed("Slot này không có ngọc."), ephemeral=True,
                )
                return

            old_data = registry.get_item(old_key) or {}
            old_grade = Grade(old_data.get("grade", 1))
            await irepo.add_item(player.id, old_key, old_grade, 1)
            await frepo.remove_gem(player.id, target, self._selected_slot)

        await _render_socket_manager(
            interaction, self.discord_id, back_fn=self._back_fn,
            target_formation_key=self._target_formation_key,
        )

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _render_hub(interaction, self.discord_id, back_fn=self._back_fn)


# ── Cog ───────────────────────────────────────────────────────────────────────

class FormationCog(commands.Cog, name="Formation"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="formation_hub", description="Quản lý trận pháp và khảm ngọc (UI)")
    async def formation_hub(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _render_hub(interaction, interaction.user.id, back_fn=None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FormationCog(bot))
