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
from src.game.constants.grades import GRADE_LABELS, Grade
from src.game.constants.realms import FORMATION_REALMS, realm_label
from src.game.systems.character_stats import active_formation_gem_map
from src.game.systems.cultivation import (
    formation_path_multiplier,
    formation_reserve_reduction,
    gem_slot_unlock_realm,
    get_active_formations,
    max_formation_slots,
    max_unlocked_gem_slots,
    set_active_formations,
)
from src.game.systems.formation import (
    compute_active_formation_bonuses,
    gem_element,
)
from src.game.systems.skills import formation_activation_would_exceed_cap
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.utils.pagination import PAGE_SIZE, add_page_controls, page_slice, total_pages
from src.utils.discord_safe import safe_defer

log = logging.getLogger(__name__)

_GEM_ELEMENT_VI = {
    "kim": "Kim", "moc": "Mộc", "thuy": "Thủy", "hoa": "Hỏa",
    "tho": "Thổ", "loi": "Lôi", "phong": "Phong", "quang": "Quang", "am": "Âm",
}

_GEM_EMOJI = {
    "kim": "⚙️", "moc": "🌿", "thuy": "💧", "hoa": "🔥",
    "tho": "🪨", "loi": "⚡", "phong": "🌬️", "quang": "☀️", "am": "🌑",
}

# Stat keys whose value is a fraction (0.18 → "18.0%"). Anything not listed is
# treated as a flat integer rating (formatted as "+1,234").
_PCT_STAT_KEYS = frozenset({
    # Core damage / defense
    "hp_pct", "mp_pct", "final_dmg_bonus", "final_dmg_reduce",
    "hp_regen_pct", "mp_regen_pct", "cooldown_reduce",
    "heal_pct", "true_dmg_pct", "debuff_immune_pct",
    "all_element_bonus", "res_all",
    # On-hit / proc chances
    "burn_on_hit_pct", "slow_on_hit_pct", "mark_on_hit_pct",
    "bleed_on_hit_pct", "shock_on_hit_pct", "stun_on_hit_pct",
    "soul_drain_on_hit_pct", "stat_steal_on_hit_pct",
    "silence_on_crit_pct", "cleanse_on_turn_pct", "heal_reduce_on_hit_pct",
    "freeze_on_skill_chance",
    # Damage-of-DoT scaling. The per-kind ``dot_dmg_bonus_by_kind`` dict
    # fans out into separate lines via _format_bonus_lines; only the
    # cross-kind ``dot_dmg_bonus`` scalar lives in this set.
    "dot_dmg_bonus",
    # Stack / shield modifiers
    "shock_per_stack_pct_bonus", "shield_regen_pct", "shield_max_pct",
    # Thorn / reflect / leech
    "thorn_pct", "reflect_pct", "mp_leech_pct",
    # Phong build (mark / evasion synergy)
    "damage_bonus_from_evasion_pct",
    "damage_bonus_from_hp_pct", "damage_bonus_from_mp_pct",
    # Misc
    "turn_steal_pct",
})

# Vietnamese labels for every stat formations (or sockets) can emit. Keys not
# in this map fall through ``_STAT_NAME_VI.get(k, k)`` to the raw key — that
# was the source of the "raw text" rows the user saw.
_STAT_NAME_VI = {
    # Core
    "hp_pct": "HP", "mp_pct": "MP",
    "final_dmg_bonus": "Tăng ST", "final_dmg_reduce": "Giảm ST",
    "hp_regen_pct": "Hồi HP%", "mp_regen_pct": "Hồi MP%",
    "hp_regen_flat": "Hồi HP", "mp_regen_flat": "Hồi MP",
    "cooldown_reduce": "Hồi Chiêu-",
    "heal_pct": "Trị Liệu", "true_dmg_pct": "ST Chuẩn",
    "debuff_immune_pct": "Miễn Debuff",
    # Combat ratings
    "crit_rating": "Bạo Kích",
    "crit_dmg_rating": "Bạo Thương",
    "crit_res_rating": "Kháng Bạo",
    "evasion_rating": "Né",
    "spd_bonus": "Tốc Độ",
    "dmg_reduce_flat": "Giảm ST Cố Định",
    # Resistances
    "res_element": "Kháng Hệ",
    "res_all": "Kháng TN",
    "all_element_bonus": "ST Mọi Hệ",
    # On-hit / proc chances
    "burn_on_hit_pct": "Thiêu Đốt",
    "slow_on_hit_pct": "Làm Chậm",
    "mark_on_hit_pct": "Tỉ Lệ Đánh Dấu",
    "bleed_on_hit_pct": "Chảy Máu",
    "shock_on_hit_pct": "Sốc Điện",
    "stun_on_hit_pct": "Choáng",
    "soul_drain_on_hit_pct": "Hút Hồn",
    "stat_steal_on_hit_pct": "Cướp Chỉ Số",
    "silence_on_crit_pct": "Cấm Phép (BK)",
    "cleanse_on_turn_pct": "Thanh Tẩy",
    "heal_reduce_on_hit_pct": "Giảm Hồi Phục",
    "freeze_on_skill_chance": "Đóng Băng (Skill)",
    # DoT damage modifiers (per-kind ``dot_dmg_bonus_by_kind`` /
    # ``dot_stack_cap_bonus`` / ``dot_per_stack_pct_bonus`` dicts are
    # rendered by their own grouped-dict handlers in _format_bonus_lines).
    "dot_dmg_bonus": "ST DoT",
    # Stacks / cap bumps
    "shock_per_stack_pct_bonus": "Sốc Điện/Stack",
    "shock_stack_cap_bonus": "Cap Sốc Điện",
    # Shield / barrier
    "shield_regen_pct": "Hồi Khiên%",
    "shield_regen_flat": "Hồi Khiên",
    "shield_max_pct": "Khiên Tối Đa",
    # Defensive procs
    "thorn_pct": "Phản Sát Thương",
    "reflect_pct": "Phản Đòn",
    "mp_leech_pct": "Hút MP",
    # Phong build (mark / evasion synergy) — per-state crit amps now flow
    # through ``crit_amp_vs`` and render via the grouped-dict handler.
    "damage_bonus_from_evasion_pct": "ST Theo Né",
    "damage_bonus_from_hp_pct": "ST Theo HP",
    "damage_bonus_from_mp_pct": "ST Theo MP",
    # Misc
    "turn_steal_pct": "Cướp Lượt",
}

# Bool-style flags — emitted by formations and unique gems alike. Rendered as
# "✨ Label" lines (no value), skipped silently when False.
_BOOL_STAT_LABELS = {
    "dot_can_crit":       "🔥 DoT Có Thể Bạo Kích",
    "heal_can_crit":      "💚 Trị Liệu Có Thể Bạo Kích",
    "barrier_on_cleanse": "🛡️ Thanh Tẩy Tạo Khiên",
    "thorn_from_shield":  "🌵 Phản Đòn Từ Khiên",
    "paralysis_on_crit":  "⚡ Bạo Kích Gây Tê Liệt",
    "poison_immunity":    "🪬 Miễn Nhiễm Độc",
    "sword_aura":         "🗡️ Kiếm Khí",
}


def _format_bonus_lines(bonuses: dict) -> list[str]:
    """Render a formation bonus dict as ``"• Label: **+value**"`` lines.

    Handles three value shapes:
      • numeric (int/float) — formatted as percent if key is in
        ``_PCT_STAT_KEYS``, otherwise as a flat ``+1,234`` rating.
      • bool — rendered as ``_BOOL_STAT_LABELS[k]`` when True.
      • dict — only ``element_pen`` is supported (per-element penetration);
        each non-zero entry becomes one line.

    Underscore-prefixed keys (``_mp_reserve_pct``) and ``note`` are private
    metadata and never rendered. Values that round to zero are also skipped
    so the embed doesn't carry "+0%" filler rows.
    """
    # Grouped per-kind / per-state dicts that fan out into multiple labelled
    # lines. Each handler emits ``"• <label>: **+value**"`` for non-zero entries.
    _DOT_KIND_VI = {"burn": "Thiêu Đốt", "bleed": "Chảy Máu", "poison": "Độc"}
    _CRIT_STATE_VI = {"bleed": "Chảy Máu", "marked": "Đ.Dấu", "drained": "Hút Hồn"}

    lines: list[str] = []
    for k, v in bonuses.items():
        if k.startswith("_") or k == "note":
            continue
        # Per-element penetration: dict-of-element → one labelled line each.
        if k == "element_pen" and isinstance(v, dict):
            for elem, pct in v.items():
                if not isinstance(pct, (int, float)) or abs(pct) < 0.0005:
                    continue
                elem_vi = _GEM_ELEMENT_VI.get(elem, str(elem).title())
                lines.append(f"• Xuyên Kháng {elem_vi}: **+{pct * 100:.1f}%**")
            continue
        # Per-element ``<elem>_dmg_taken`` amp dict (Xích Luyện-style).
        if k == "dmg_taken" and isinstance(v, dict):
            for elem, pct in v.items():
                if not isinstance(pct, (int, float)) or abs(pct) < 0.0005:
                    continue
                elem_vi = _GEM_ELEMENT_VI.get(elem, str(elem).title())
                lines.append(f"• ST {elem_vi} Địch Chịu: **+{pct * 100:.1f}%**")
            continue
        # Per-DoT-kind damage / stack-cap / per-stack-pct grouped dicts.
        if k == "dot_dmg_bonus_by_kind" and isinstance(v, dict):
            for kind, pct in v.items():
                if not isinstance(pct, (int, float)) or abs(pct) < 0.0005:
                    continue
                lines.append(f"• ST {_DOT_KIND_VI.get(kind, kind)}: **+{pct * 100:.1f}%**")
            continue
        if k == "dot_stack_cap_bonus" and isinstance(v, dict):
            for kind, n in v.items():
                n = int(n or 0)
                if not n:
                    continue
                lines.append(f"• Cap {_DOT_KIND_VI.get(kind, kind)}: **+{n}**")
            continue
        if k == "dot_per_stack_pct_bonus" and isinstance(v, dict):
            for kind, pct in v.items():
                if not isinstance(pct, (int, float)) or abs(pct) < 0.0005:
                    continue
                lines.append(f"• {_DOT_KIND_VI.get(kind, kind)}/Stack: **+{pct * 100:.2f}%**")
            continue
        # Crit amps vs targets in specific debuff states.
        if k == "crit_amp_vs" and isinstance(v, dict):
            for state, amps in v.items():
                if not isinstance(amps, dict):
                    continue
                state_vi = _CRIT_STATE_VI.get(state, state)
                if (r := int(amps.get("rating", 0) or 0)):
                    lines.append(f"• Bạo Kích vs {state_vi}: **+{r:,}**")
                if (d := int(amps.get("dmg", 0) or 0)):
                    lines.append(f"• Bạo Thương vs {state_vi}: **+{d:,}**")
            continue
        if isinstance(v, bool):
            if v:
                # Prefer the rich bool label table; fall back to the regular
                # Vietnamese map so unmapped keys still get a non-raw label.
                label = _BOOL_STAT_LABELS.get(k) or f"✨ **{_STAT_NAME_VI.get(k, k)}**"
                lines.append(label)
            continue
        if not isinstance(v, (int, float)):
            continue
        # Hide entries whose displayed value would round to zero — covers
        # both exact 0 and tiny fractions on flat-rating stats.
        if k in _PCT_STAT_KEYS:
            if abs(v) < 0.0005:  # less than 0.05% rounds to "+0.0%"
                continue
            lines.append(f"• {_STAT_NAME_VI.get(k, k)}: **+{v * 100:.1f}%**")
        else:
            if int(round(v)) == 0:
                continue
            lines.append(f"• {_STAT_NAME_VI.get(k, k)}: **+{int(round(v)):,}**")
    return lines


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
    - When one is active: single-formation detail view.
    - When multiple are active: multi-slot summary card listing each slot.
    """
    stages = player.formation_realm * 9 + player.formation_level
    path_mult = formation_path_multiplier(stages)
    path_label = realm_label("formation", player.formation_realm, player.formation_xp)
    slot_cap = max_formation_slots(player.active_axis, player.formation_realm)

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

    lines = _format_bonus_lines(form_bonuses)
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

    lines = _format_bonus_lines(form_bonuses)
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


def _unlocked_formation_keys(player) -> set[str]:
    """Set of formation keys the player has unlocked (one CharacterFormation row each)."""
    return {f.formation_key for f in (player.formations or []) if f.formation_key}


async def _find_unique_gem_socket(
    frepo: FormationRepository,
    player_id: int,
    *,
    gem_key: str,
    skip_formation_key: str | None = None,
    skip_slot_index: int | None = None,
) -> tuple[str, int] | None:
    """Return ``(formation_key, slot_index)`` if ``gem_key`` is already inlaid
    in any other slot of any formation row (active or inactive), else ``None``.

    The ``skip_*`` pair lets the caller exclude the slot it's writing to so
    a no-op replace (same key into the same slot) doesn't trip the check.
    """
    for form in await frepo.get_all(player_id):
        for slot_str, k in (form.gem_slots or {}).items():
            if k != gem_key:
                continue
            try:
                slot_idx = int(slot_str)
            except (TypeError, ValueError):
                continue
            if (
                form.formation_key == skip_formation_key
                and slot_idx == skip_slot_index
            ):
                continue
            return form.formation_key, slot_idx
    return None


async def _player_gem_inventory(player_db_id: int) -> list[dict]:
    """Return gem entries from inventory, one per ``(item_key, grade)`` pair.

    Unique gems can land at different grades — players need to pick the
    specific grade they want to socket, and the picker must show each
    grade as a separate option. The select option ``value`` encodes both
    key and grade as ``key|grade`` so Discord's unique-value requirement
    holds even when two rows share an ``item_key``.
    """
    async with get_session() as session:
        irepo = InventoryRepository(session)
        items = await irepo.get_all(player_db_id)
    aggregated: dict[tuple[str, int], dict] = {}
    for inv in items:
        data = registry.get_item(inv.item_key)
        if not data or data.get("type") != "gem":
            continue
        bucket_key = (inv.item_key, inv.grade)
        existing = aggregated.get(bucket_key)
        if existing is not None:
            existing["qty"] += inv.quantity
            continue
        aggregated[bucket_key] = {
            "key": inv.item_key,
            "grade": inv.grade,
            "qty": inv.quantity,
            "name": data.get("vi", inv.item_key),
            "element": gem_element(inv.item_key),
        }
    return sorted(
        aggregated.values(),
        key=lambda g: (g["key"], -g["grade"]),
    )


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
        if not await safe_defer(interaction):
            return
        await _render_formation_picker(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_sockets(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _render_socket_manager(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _render_hub(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)


# ── Formation picker ──────────────────────────────────────────────────────────

async def _render_formation_picker(interaction: discord.Interaction, discord_id: int, back_fn=None) -> None:
    player, _, _, _ = await _load_formation_view_state(discord_id)
    if player is None:
        await interaction.edit_original_response(
            embed=error_embed("Chưa có nhân vật."), view=None,
        )
        return

    unlocked = _unlocked_formation_keys(player)
    unlocked_forms = sorted(
        (registry.get_formation(k) for k in unlocked),
        key=lambda f: (f or {}).get("vi", ""),
    )
    unlocked_forms = [f for f in unlocked_forms if f]
    active_keys = set(get_active_formations(player.active_formation))
    slot_cap = max_formation_slots(player.active_axis, player.formation_realm)

    if not unlocked_forms:
        embed = base_embed(
            "🔯 Chọn Trận Pháp",
            "Bạn chưa mở khoá trận pháp nào.\n"
            "Học **kỹ năng trận pháp** trong `/skilllist` để mở khoá trận tương ứng.",
            color=0x9B59B6,
        )
        view = FormationPickerView(
            discord_id, [], active_keys=set(),
            slot_cap=slot_cap, back_fn=back_fn,
        )
        await interaction.edit_original_response(embed=embed, view=view)
        return

    desc_lines = [
        f"Chọn **tới {slot_cap} trận pháp** muốn kích hoạt đồng thời. "
        f"Mỗi trận chiếm một ổ và cộng MP trấn giữ (tổng cap 50%).",
        f"Trận đã chọn: **{len(active_keys)}/{slot_cap}**.",
        f"Đã mở khoá: **{len(unlocked_forms)}** trận.",
    ]
    if active_keys:
        names = [registry.get_formation(k) or {} for k in active_keys]
        names_str = ", ".join(n.get("vi", "?") for n in names)
        desc_lines.append(f"\n🔯 Hiện đang dùng: **{names_str}**")
    embed = base_embed("🔯 Chọn Trận Pháp", "\n".join(desc_lines), color=0x9B59B6)

    for f in unlocked_forms[:10]:
        elem = f.get("element") or "—"
        elem_vi = _GEM_ELEMENT_VI.get(elem, elem.title())
        marker = " ◀ Đang dùng" if f["key"] in active_keys else ""
        embed.add_field(
            name=f"{f['vi']}{marker}",
            value=f"Hệ: {elem_vi} · Skill: `{f.get('formation_skill_key', '—')}`",
            inline=False,
        )

    view = FormationPickerView(
        discord_id, unlocked_forms, active_keys=active_keys,
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

        if forms:
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

            # Reject any formation key the player hasn't unlocked — guards
            # against stale / tampered selections since formations now flow
            # from learned skills only.
            unlocked = _unlocked_formation_keys(player)
            unauthorized = [k for k in picked if k not in unlocked]
            if unauthorized:
                names = ", ".join(
                    (registry.get_formation(k) or {}).get("vi", k) for k in unauthorized
                )
                await interaction.response.send_message(
                    embed=error_embed(f"Chưa mở khoá trận pháp: **{names}**."),
                    ephemeral=True,
                )
                return

            # Reservation cap moved from learn time to activation time —
            # only active formations cost MP now, so the player can learn
            # freely but must keep their active set under the cap.
            exceeds, projected = formation_activation_would_exceed_cap(player, picked)
            if exceeds:
                from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
                await interaction.response.send_message(
                    embed=error_embed(
                        f"Tổ hợp này khoá **{projected * 100:.1f}%** MP — vượt mức tối đa "
                        f"**{FORMATION_MAX_RESERVE_PCT * 100:.0f}%**.\n"
                        "Tu luyện Trận Đạo để giảm chi phí, hoặc bỏ bớt một trận pháp."
                    ),
                    ephemeral=True,
                )
                return

            for k in picked:
                await frepo.get_or_create(player.id, k)
            player.active_formation = set_active_formations(picked)
            await prepo.save(player)
        if not await safe_defer(interaction):
            return
        await _render_hub(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
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

    unlocked_count = max_unlocked_gem_slots(player.formation_realm)

    lines = [
        f"Khảm ngọc để mở ngưỡng **1 / 3 / 5 / 7 / {FORMATION_GEM_SLOTS}**.",
        f"**Trận đang khảm**: {form_data['vi']}",
        f"🔓 Đã mở: **{unlocked_count}/{FORMATION_GEM_SLOTS}** ổ khảm "
        f"(cảnh giới Trận Đạo hiện tại: {FORMATION_REALMS[player.formation_realm].vi}).",
    ]
    if len(active_forms) > 1:
        lines.append(
            f"*(Đang kích hoạt {len(active_forms)} trận — chọn dropdown trên "
            f"để đổi trận cần khảm.)*"
        )
    lines.append("")
    for i in range(FORMATION_GEM_SLOTS):
        gem_key = gem_slots.get(str(i))
        if i >= unlocked_count:
            req_idx = gem_slot_unlock_realm(i)
            req_vi = FORMATION_REALMS[req_idx].vi
            lines.append(f"`[{i}]` 🔒 *Khóa — cần Trận Đạo {req_vi}*")
        else:
            lines.append(f"`[{i}]` {_gem_display(gem_key)}")

    if gems_inv:
        lines.append("\n**Ngọc có trong túi đồ:**")
        for g in gems_inv[:15]:
            try:
                grade_vi = GRADE_LABELS[Grade(g["grade"])][0]
            except (KeyError, ValueError):
                grade_vi = "—"
            lines.append(
                f"  {_GEM_EMOJI.get(g['element'] or '', '💠')} "
                f"`[{grade_vi}] {g['name']}` ×{g['qty']}"
            )
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
        unlocked_slot_count=unlocked_count,
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
        gem_page: int = 0,
        unlocked_slot_count: int = FORMATION_GEM_SLOTS,
    ) -> None:
        super().__init__(timeout=240)
        self.discord_id = discord_id
        self._gem_slots = dict(gem_slots)
        self._gems_inv = list(gems_inv)
        self._back_fn = back_fn
        self._selected_slot: int | None = None
        self._selected_gem_key: str | None = None
        self._selected_gem_grade: Grade | None = None
        self._target_formation_key = target_formation_key
        self._active_forms = active_forms or []
        self._unlocked_slot_count = max(1, min(FORMATION_GEM_SLOTS, int(unlocked_slot_count)))
        gem_pages = total_pages(len(self._gems_inv), per_page=PAGE_SIZE)
        self._gem_page = max(0, min(gem_page, gem_pages - 1))

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

        # Slot select — all 10 slots, showing whether occupied + lock state.
        # Locked slots stay listed (so players see what's coming) but their
        # description warns that selecting one will fail at inlay time; the
        # _on_inlay handler is the canonical gate.
        slot_opts = []
        for i in range(FORMATION_GEM_SLOTS):
            gk = gem_slots.get(str(i))
            locked = i >= self._unlocked_slot_count
            if locked:
                req_idx = gem_slot_unlock_realm(i)
                req_vi = FORMATION_REALMS[req_idx].vi
                label = f"[{i}] 🔒 (khóa)"
                desc = f"Cần Trận Đạo {req_vi}"
                emoji = "🔒"
            elif gk:
                data = registry.get_item(gk) or {}
                label = f"[{i}] {data.get('vi', gk)[:80]}"
                desc = "Đã khảm — chọn để gỡ hoặc thay"
                emoji = "💎"
            else:
                label = f"[{i}] (trống)"
                desc = "Slot trống"
                emoji = "⬜"
            slot_opts.append(discord.SelectOption(
                label=label[:100],
                value=str(i),
                description=desc[:100],
                emoji=emoji,
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
            for g in page_slice(self._gems_inv, self._gem_page, per_page=PAGE_SIZE):
                elem = g["element"] or ""
                emoji = _GEM_EMOJI.get(elem, "💠")
                try:
                    grade_vi = GRADE_LABELS[Grade(g["grade"])][0]
                except (KeyError, ValueError):
                    grade_vi = "—"
                elem_vi = _GEM_ELEMENT_VI.get(elem, "—")
                gem_opts.append(discord.SelectOption(
                    label=f"[{grade_vi}] {g['name']} ×{g['qty']}"[:100],
                    value=f"{g['key']}|{int(g['grade'])}",
                    description=f"Hệ: {elem_vi}"[:100],
                    emoji=emoji,
                ))
            placeholder = "💠 Chọn ngọc để khảm..."
            if gem_pages > 1:
                placeholder = f"💠 Chọn ngọc để khảm (Trang {self._gem_page + 1}/{gem_pages})..."
            self._gem_select = discord.ui.Select(
                placeholder=placeholder,
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

        # Pagination on the row after the actions when there's space.
        # If we're already at row 4, pagination shares row 4 with the
        # actions — Discord renders both as long as total components ≤5.
        page_row = min(4, action_row + 1)
        add_page_controls(
            self,
            page=self._gem_page,
            total=len(self._gems_inv),
            on_change=self._on_page_change,
            row=page_row,
        )

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        view = SocketManagerView(
            self.discord_id, self._gem_slots, self._gems_inv,
            back_fn=self._back_fn,
            active_forms=self._active_forms,
            target_formation_key=self._target_formation_key,
            gem_page=new_page,
            unlocked_slot_count=self._unlocked_slot_count,
        )
        await interaction.edit_original_response(view=view)

    async def _on_form_pick(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
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
        if not await safe_defer(interaction):
            return
    async def _on_gem_pick(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        raw = interaction.data["values"][0]
        key, sep, grade_str = raw.partition("|")
        self._selected_gem_key = key
        self._selected_gem_grade = None
        if sep and grade_str:
            try:
                self._selected_gem_grade = Grade(int(grade_str))
            except ValueError:
                self._selected_gem_grade = None
        if not await safe_defer(interaction):
            return
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

        if not await safe_defer(interaction):
            return
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

            # Slot-unlock gate — slot index ``i`` requires formation_realm
            # ≥ ``i - 1`` (slot 0 always free, slot 9 needs realm 8 / max).
            unlocked_count = max_unlocked_gem_slots(player.formation_realm)
            if self._selected_slot >= unlocked_count:
                required_realm_idx = gem_slot_unlock_realm(self._selected_slot)
                required_realm = FORMATION_REALMS[required_realm_idx]
                await interaction.followup.send(
                    embed=error_embed(
                        f"Slot `[{self._selected_slot}]` chưa mở khóa — "
                        f"cần đạt **Trận Đạo {required_realm.vi}** "
                        f"(cảnh giới thứ {required_realm_idx + 1})."
                    ),
                    ephemeral=True,
                )
                return

            gem_data = registry.get_item(self._selected_gem_key) or {}
            if self._selected_gem_grade is None:
                await interaction.followup.send(
                    embed=error_embed("Chọn ngọc cần khảm trước."), ephemeral=True,
                )
                return
            # Grade is part of the picker selection now — the dropdown emits
            # one option per (key, grade) so we consume the exact row the
            # player saw, not whichever grade ``remove_any_grade`` happens
            # to drain first.
            picked_row = await irepo.get_item(
                player.id, self._selected_gem_key, self._selected_gem_grade,
            )
            if picked_row is None or picked_row.quantity < 1:
                await interaction.followup.send(
                    embed=error_embed(f"Không đủ **{gem_data.get('vi', self._selected_gem_key)}** trong túi đồ."),
                    ephemeral=True,
                )
                return

            # Unique gems can only occupy one slot at a time across the entire
            # roster — otherwise their bonuses double-stack via
            # ``compute_gem_bonuses``. Scan every formation row (active or
            # not — players can swap formations freely) for the same key,
            # ignoring the slot we're about to write to.
            if gem_data.get("unique"):
                conflict = await _find_unique_gem_socket(
                    frepo, player.id,
                    gem_key=self._selected_gem_key,
                    skip_formation_key=target,
                    skip_slot_index=self._selected_slot,
                )
                if conflict is not None:
                    other_form_key, other_slot = conflict
                    other_form = registry.get_formation(other_form_key) or {}
                    other_form_vi = other_form.get("vi", other_form_key)
                    await interaction.followup.send(
                        embed=error_embed(
                            f"**{gem_data.get('vi', self._selected_gem_key)}** là ngọc "
                            f"độc nhất — đã khảm ở **{other_form_vi}** slot `[{other_slot}]`. "
                            "Hãy gỡ ngọc cũ trước khi khảm vào ổ mới."
                        ),
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
            await irepo.remove_item(
                player.id, self._selected_gem_key, self._selected_gem_grade, 1,
            )

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

        if not await safe_defer(interaction):
            return
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
        if not await safe_defer(interaction):
            return
        await _render_hub(interaction, self.discord_id, back_fn=self._back_fn)


# ── Cog ───────────────────────────────────────────────────────────────────────

class FormationCog(commands.Cog, name="Formation"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="formation_hub", description="Quản lý trận pháp và khảm ngọc (UI)")
    async def formation_hub(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_hub(interaction, interaction.user.id, back_fn=None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FormationCog(bot))
