"""Forge — interactive button flow for crafting equipment."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.engine.equipment import STAT_LABELS, format_stat
from src.game.systems.forge import (
    QUALITY_LABELS,
    check_forge_requirements,
    describe_recipe,
    distribute_qty,
    forge_equipment,
    get_material_grade,
    get_recipe,
    max_affix_total,
)
from src.utils import emojis
from src.utils.discord_safe import safe_defer
from src.utils.embed_builder import base_embed, error_embed
from src.utils.pagination import PAGE_SIZE, add_page_controls, page_slice, total_pages

log = logging.getLogger(__name__)


def _format_affix_bias(material_key: str, *, max_chars: int | None = None) -> str:
    """Render a material's ``affix_bias`` list as a comma-joined Vietnamese
    stat-label string. Returns an empty string if the material has no bias.

    Each entry shows the underlying stat (``STAT_LABELS`` — e.g. ``Công``,
    ``Pháp Công``) plus a ``(Tiền)`` / ``(Hậu)`` tag so players can tell
    a prefix-variant material apart from a suffix-variant one when both
    exist for the same stat (e.g. ``pfx_matk`` ``Pháp Lực`` vs ``sfx_matk``
    ``Pháp Tủy`` — both render as ``Pháp Công`` without the tag).

    Distinct affix keys may still resolve to the same final label after
    tagging — we dedupe order-preservingly. ``max_chars`` truncates with
    an ellipsis to fit Discord's 100-char ``SelectOption.description``.
    """
    item = registry.get_item(material_key) or {}
    keys = item.get("affix_bias") or []
    if not keys:
        return ""
    seen: set[str] = set()
    labels: list[str] = []
    for k in keys:
        affix = registry.get_affix(k) or {}
        stat = affix.get("stat")
        base = STAT_LABELS.get(stat) if stat else None
        if not base:
            base = affix.get("vi", k)
        kind = affix.get("type")
        if kind == "prefix":
            label = f"{base} (Tiền)"
        elif kind == "suffix":
            label = f"{base} (Hậu)"
        else:
            label = base
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    joined = ", ".join(labels)
    if max_chars is not None and len(joined) > max_chars:
        joined = joined[: max(0, max_chars - 1)].rstrip(", ") + "…"
    return joined


SLOT_VI: dict[str, str] = {
    "weapon":   "Vũ Khí",
    "off_hand": "Phụ Thủ",
    "armor":    "Giáp",
    "helmet":   "Mũ Giáp",
    "glove":    "Găng Tay",
    "belt":     "Đai Lưng",
    "boot":     "Giày",
    "ring":     "Nhẫn",
    "amulet":   "Bùa Hộ Mệnh",
}

QUALITY_COLORS: dict[str, discord.Color] = {
    "hoan":  discord.Color.from_str("#B8860B"),
    "huyen": discord.Color.from_str("#8A2BE2"),
    "dia":   discord.Color.from_str("#1A5276"),
    "thien": discord.Color.from_str("#C0392B"),
}


def _bases_by_slot() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for b in registry.bases.values():
        grouped.setdefault(b["slot"], []).append(b)
    return grouped


def _forge_hub_embed() -> discord.Embed:
    embed = base_embed(
        "⚒️ Luyện Công Phường",
        "Chào mừng đến Luyện Công Phường! Chọn chức năng bên dưới.",
        color=0xB8860B,
    )
    embed.add_field(name="⚒️ Rèn Trang Bị",      value="Rèn trang bị từ nguyên liệu trong kho.", inline=False)
    embed.add_field(name="📋 Danh Sách Trang Bị", value="Xem các loại trang bị có thể rèn.", inline=False)
    embed.add_field(name="📜 Công Thức Rèn",      value="Xem yêu cầu nguyên liệu và tỷ lệ phẩm chất.", inline=False)
    return embed


def _guard(interaction: discord.Interaction, discord_id: int) -> bool:
    return interaction.user.id == discord_id


# ── Navigation helpers (edit in-place) ───────────────────────────────────────

async def _nav_hub(interaction: discord.Interaction, discord_id: int, hub_back_fn) -> None:
    await interaction.edit_original_response(
        embed=_forge_hub_embed(),
        view=ForgeHubView(discord_id, hub_back_fn),
    )


async def _nav_slot(interaction: discord.Interaction, discord_id: int, hub_back_fn) -> None:
    embed = discord.Embed(
        title="⚒️ Rèn Trang Bị — Chọn Vị Trí Trang Bị",
        description="Chọn vị trí trang bị muốn rèn.",
        color=0xB8860B,
    )
    await interaction.edit_original_response(embed=embed, view=_SlotView(discord_id, hub_back_fn))


async def _nav_base(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    hub_back_fn,
) -> None:
    slot_name = SLOT_VI.get(slot, slot)
    embed = discord.Embed(
        title=f"⚒️ Chọn Loại Trang Bị — {slot_name}",
        description="Chọn loại trang bị muốn rèn.",
        color=0xB8860B,
    )
    await interaction.edit_original_response(embed=embed, view=_BaseView(discord_id, slot, hub_back_fn))


async def _nav_grade(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    hub_back_fn,
) -> None:
    base_data = registry.get_base(base_key)
    embed = discord.Embed(
        title=f"⚒️ Chọn Cấp Rèn — {base_data['vi'] if base_data else base_key}",
        description="Chọn cấp trang bị muốn rèn (1 = thấp nhất, 9 = cao nhất).",
        color=0xB8860B,
    )
    await interaction.edit_original_response(embed=embed, view=_GradeView(discord_id, slot, base_key, hub_back_fn))


async def _load_forge_bag(discord_id: int):
    """Fetch character + inventory slices needed for the forge flow.

    Returns (char, all_items, mats_in_bag, super_mats_in_bag) or None when
    the player record is missing.
    """
    async with get_session() as session:
        player_repo = PlayerRepository(session)
        inv_repo    = InventoryRepository(session)

        player = await player_repo.get_by_discord_id(discord_id)
        if not player:
            return None
        char = _player_to_model(player)

        all_items   = await inv_repo.get_all(player.id)
        mats_in_bag = {
            it.item_key: it.quantity
            for it in all_items
            if get_material_grade(it.item_key) is not None
        }
        super_mats_in_bag = {
            it.item_key: it.quantity
            for it in all_items
            if (registry.get_item(it.item_key) or {}).get("type") == "super_material"
        }
    return char, mats_in_bag, super_mats_in_bag


_MATERIAL_PAGE_SIZE = 10


def _material_embed(
    grade: int,
    eligible: list[tuple[str, int]],
    required_qty: int,
    page: int,
    picks: set[str] | None = None,
) -> discord.Embed:
    """Build the material picker embed for a given page.

    Bias listing and dropdown share ``_MATERIAL_PAGE_SIZE`` so the embed
    text always describes exactly the materials shown in the current
    dropdown slice. Page controls (``◀ / ▶``) re-render this embed.
    ``picks`` is the cross-page accumulator — surfaced as an "Đã chọn"
    summary so the player can see selections made on other pages.
    """
    pages = total_pages(len(eligible), per_page=_MATERIAL_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    total_owned = sum(qty for _, qty in eligible)
    picks = picks or set()

    description = (
        f"Cần tổng cộng **{required_qty}** vật liệu luyện khí "
        "(bất kỳ phẩm). Có thể trộn nhiều loại — vật liệu phẩm cao kéo "
        "chỉ số mạnh hơn: **3× / 4× / 5× / 6×** cho phẩm 1 / 2 / 3 / 4.\n"
        f"Đang có: **{total_owned}** vật liệu trong kho."
    )
    if pages > 1:
        description += f"\n*Trang {page + 1} / {pages}*"
    embed = discord.Embed(
        title=f"⚒️ Chọn Nguyên Liệu Rèn — Cấp {grade}",
        description=description,
        color=0xB8860B,
    )
    if total_owned < required_qty:
        embed.add_field(
            name="⚠️ Thiếu Nguyên Liệu",
            value=f"Tổng nguyên liệu hợp lệ chưa đủ {required_qty}. Hãy tích trữ thêm.",
            inline=False,
        )
    if eligible:
        bias_lines: list[str] = []
        for key, qty in page_slice(eligible, page, per_page=_MATERIAL_PAGE_SIZE):
            item = registry.get_item(key) or {}
            name = item.get("vi", key)
            bias = _format_affix_bias(key)
            tail = (
                f" — Tăng tỉ lệ ra chỉ số: {bias}"
                if bias else " — *(không có ưu tiên chỉ số)*"
            )
            marker = "✅ " if key in picks else ""
            bias_lines.append(f"• {marker}**{name}** ×{qty}{tail}")
        embed.add_field(
            name="🎯 Tăng Tỉ Lệ Ra Chỉ Số Theo Nguyên Liệu",
            value="\n".join(bias_lines),
            inline=False,
        )
    if picks:
        # Cross-page accumulator summary — lets the player see selections
        # made on other pages without flipping back to verify.
        owned_map = dict(eligible)
        chosen_lines: list[str] = []
        for key in sorted(picks):
            data = registry.get_item(key) or {}
            name = data.get("vi", key)
            chosen_lines.append(f"• **{name}** (có {owned_map.get(key, 0)})")
        embed.add_field(
            name=f"🧺 Đã Chọn ({len(picks)})",
            value="\n".join(chosen_lines)[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="🧺 Đã Chọn (0)",
            value="*Chưa chọn nguyên liệu nào — chọn từ danh sách rồi nhấn ✅ Xác Nhận.*",
            inline=False,
        )
    return embed


async def _nav_material(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    grade: int,
    hub_back_fn,
    *,
    page: int = 0,
    picks: set[str] | None = None,
) -> None:
    loaded = await _load_forge_bag(discord_id)
    if loaded is None:
        await interaction.edit_original_response(
            embed=error_embed("Không tìm thấy nhân vật."), view=None
        )
        return
    char, mats_in_bag, _ = loaded

    required_qty = max_affix_total(grade)

    # Any owned forge_material is now eligible — no per-grade gate. The
    # cog-level filter in ``_load_forge_bag`` already restricts the bag
    # to ``type == "forge_material"`` rows. Mix-and-match is allowed;
    # total qty is checked at confirm time.
    eligible: list[tuple[str, int]] = sorted(
        (
            (key, qty) for key, qty in mats_in_bag.items()
            if qty >= 1
        ),
        key=lambda kv: -kv[1],
    )

    # Drop stale picks for materials no longer in the bag (drained between
    # page flips by another action).
    valid_keys = {k for k, _ in eligible}
    picks = {p for p in (picks or set()) if p in valid_keys}

    embed = _material_embed(grade, eligible, required_qty, page, picks)
    view = _MaterialView(
        discord_id, slot, base_key, grade, hub_back_fn,
        eligible=eligible, required_qty=required_qty,
        page=page, picks=picks,
    )
    await interaction.edit_original_response(embed=embed, view=view)


async def _nav_super(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    grade: int,
    selected_mat_keys: list[str],
    hub_back_fn,
) -> None:
    loaded = await _load_forge_bag(discord_id)
    if loaded is None:
        await interaction.edit_original_response(
            embed=error_embed("Không tìm thấy nhân vật."), view=None
        )
        return
    _, _, super_mats_in_bag = loaded

    # Super materials with min_item_grade ≤ forge grade and at least 1 owned
    eligible: list[tuple[str, dict]] = []
    for key, qty in super_mats_in_bag.items():
        if qty <= 0:
            continue
        spec = registry.get_super_material(key)
        if not spec:
            continue
        min_grade = int(spec.get("min_item_grade", spec.get("grade", 1)))
        if min_grade <= grade:
            eligible.append((key, spec))

    mat_names = [
        (registry.get_item(k) or {}).get("vi", k) for k in selected_mat_keys
    ]
    if len(mat_names) == 1:
        primary_line = f"Nguyên liệu chính: **{mat_names[0]}**."
    else:
        primary_line = "Nguyên liệu đã chọn: " + ", ".join(f"**{n}**" for n in mat_names) + "."
    embed = discord.Embed(
        title="✨ Chọn Vật Liệu Siêu Hiếm (Tùy Chọn)",
        description=(
            f"{primary_line}\n"
            "Có thể đính kèm **1** vật liệu siêu hiếm để ban hiệu ứng đặc biệt, "
            "hoặc bỏ qua để rèn thường."
        ),
        color=0xB8860B,
    )
    if not eligible:
        embed.add_field(
            name="Không Có Lựa Chọn",
            value="Bạn chưa sở hữu vật liệu siêu hiếm phù hợp với cấp này.",
            inline=False,
        )

    view = _SuperMaterialView(
        discord_id, slot, base_key, grade, hub_back_fn,
        selected_mat_keys=selected_mat_keys,
        eligible=eligible,
    )
    await interaction.edit_original_response(embed=embed, view=view)


async def _nav_confirm(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    grade: int,
    selected_mat_keys: list[str],
    selected_super_key: str | None,
    hub_back_fn,
) -> None:
    loaded = await _load_forge_bag(discord_id)
    if loaded is None:
        await interaction.edit_original_response(
            embed=error_embed("Không tìm thấy nhân vật."), view=None
        )
        return
    char, mats_in_bag, _ = loaded

    view = _ConfirmView(
        discord_id, slot, base_key, grade, hub_back_fn,
        char=char,
        mats_in_bag=mats_in_bag,
        selected_mat_keys=selected_mat_keys,
        selected_super_key=selected_super_key,
    )
    embed = _build_confirm_embed(
        base_key, grade, char, mats_in_bag,
        selected_mat_keys=selected_mat_keys,
        selected_super_key=selected_super_key,
    )
    await interaction.edit_original_response(embed=embed, view=view)


# ── Confirm embed builder ─────────────────────────────────────────────────────

def _build_confirm_embed(
    base_key: str,
    grade: int,
    char,
    mats_in_bag: dict[str, int],
    selected_mat_keys: list[str] | None = None,
    selected_super_key: str | None = None,
) -> discord.Embed:
    base_data = registry.get_base(base_key)
    recipe    = get_recipe(grade)
    slot_vi   = SLOT_VI.get(base_data["slot"], base_data["slot"]) if base_data else "?"

    ok, msg, _ = check_forge_requirements(char, grade, mats_in_bag)

    embed = discord.Embed(
        title="⚒️ Xác Nhận Rèn Luyện",
        color=discord.Color.gold() if ok else discord.Color.red(),
    )
    embed.add_field(
        name="Trang Bị",
        value=f"**{base_data['vi']}** ({slot_vi}) · Cấp {grade}",
        inline=False,
    )

    if recipe:
        merit_icon = "✅" if char.merit >= recipe["cost_cong_duc"] else "❌"
        embed.add_field(
            name="Chi Phí Công Đức",
            value=f"{merit_icon} {recipe['cost_cong_duc']:,} {emojis.for_currency('merit')} (đang có: {char.merit:,})",
            inline=False,
        )

        required_qty = max_affix_total(grade)
        if selected_mat_keys:
            picks = [(k, mats_in_bag.get(k, 0)) for k in selected_mat_keys]
            split = distribute_qty(picks, required_qty)
            mat_lines: list[str] = []
            if split is None:
                total_owned = sum(q for _, q in picks)
                mat_lines.append(
                    f"❌ Tổng chỉ có **{total_owned}** — cần **{required_qty}**."
                )
                for k, owned in picks:
                    item = registry.get_item(k) or {}
                    mat_lines.append(f"  • **{item.get('vi', k)}** (có: {owned})")
            else:
                for k, owned in picks:
                    take = split[k]
                    item = registry.get_item(k) or {}
                    icon = "✅" if owned >= take else "❌"
                    mat_lines.append(
                        f"{icon} {take}x **{item.get('vi', k)}** (có: {owned})"
                    )
            embed.add_field(
                name=f"Nguyên Liệu Đã Chọn (tổng {required_qty})",
                value="\n".join(mat_lines),
                inline=False,
            )
        else:
            owned_total = sum(qty for _, qty in mats_in_bag.items())
            icon = "✅" if owned_total >= required_qty else "❌"
            embed.add_field(
                name="Nguyên Liệu",
                value=f"{icon} {required_qty}x Vật liệu luyện khí (có: {owned_total})",
                inline=False,
            )

        if selected_super_key:
            super_spec = registry.get_super_material(selected_super_key) or {}
            embed.add_field(
                name="Vật Liệu Siêu Hiếm",
                value=f"✨ **{super_spec.get('vi', selected_super_key)}** — ban hiệu ứng đặc biệt",
                inline=False,
            )

        c = recipe["quality_chances"]
        embed.add_field(
            name="Tỷ Lệ Phẩm Chất",
            value=(
                f"Hoàng **{c['hoan']*100:.0f}%** · "
                f"Huyền **{c['huyen']*100:.0f}%** · "
                f"Địa **{c['dia']*100:.0f}%** · "
                f"Thiên **{c['thien']*100:.0f}%**"
            ),
            inline=False,
        )

    if not ok:
        embed.add_field(name="⚠️ Không Đủ Điều Kiện", value=msg, inline=False)

    return embed


# ── Views ─────────────────────────────────────────────────────────────────────

class ForgeHubView(discord.ui.View):
    """Hub shown from the status page Forge button or /forge command."""

    def __init__(self, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=300)
        self._discord_id  = discord_id
        self._back_fn     = back_fn

        configs = [
            ("⚒️ Rèn Trang Bị",      discord.ButtonStyle.primary,   self._craft_cb,   0),
            ("♻️ Phân Giải",          discord.ButtonStyle.danger,    self._recycle_cb, 0),
            ("📋 Danh Sách Trang Bị", discord.ButtonStyle.secondary, self._list_cb,    1),
            ("📜 Công Thức Rèn",      discord.ButtonStyle.secondary, self._recipe_cb,  1),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return _guard(interaction, self._discord_id)

    async def _craft_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_slot(interaction, self._discord_id, self._back_fn)

    async def _recycle_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        # Late import — recycle imports the forge cog indirectly via shared
        # state util modules, so a top-level import would cycle.
        from src.bot.cogs.recycle import RecycleView, _recycle_embed

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return
            erepo = EquipmentRepository(session)
            bag = await erepo.get_bag(player.id)
        embed = _recycle_embed(player.name, bag)
        view = RecycleView(self._discord_id, player.name, bag)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _list_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return

        embed = discord.Embed(title="⚒️ Danh Sách Trang Bị Có Thể Rèn", color=discord.Color.gold())
        for slot, bases in sorted(_bases_by_slot().items()):
            names = ", ".join(f"{b['vi']} (`{b['key']}`)" for b in bases)
            embed.add_field(name=SLOT_VI.get(slot, slot), value=names, inline=False)
        embed.set_footer(text="Dùng /forge recipe <cấp> để xem yêu cầu chi tiết.")

        back_view = discord.ui.View(timeout=120)
        back_btn  = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
        async def _back(inter: discord.Interaction) -> None:
            if not await safe_defer(inter):
                return
            await _nav_hub(inter, self._discord_id, self._back_fn)
        back_btn.callback = _back
        back_view.add_item(back_btn)
        await interaction.edit_original_response(embed=embed, view=back_view)

    async def _recipe_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        embed = discord.Embed(
            title="📜 Công Thức Rèn — Chọn Cấp",
            description="Nhấn nút để xem công thức rèn theo cấp trang bị.",
            color=discord.Color.blue(),
        )
        await interaction.edit_original_response(embed=embed, view=_RecipeSelectView(self._discord_id, self._back_fn))

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)


class _SlotView(discord.ui.View):
    """Step 1 — choose an equipment slot."""

    def __init__(self, discord_id: int, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._hub_back_fn = hub_back_fn

        slots = sorted(_bases_by_slot().keys())
        for i, slot in enumerate(slots):
            btn = discord.ui.Button(
                label=SLOT_VI.get(slot, slot),
                style=discord.ButtonStyle.primary,
                row=i // 4,
            )
            btn.callback = self._make_slot_cb(slot)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_slot_cb(self, slot: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            await _nav_base(interaction, self._discord_id, slot, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_hub(interaction, self._discord_id, self._hub_back_fn)


class _BaseView(discord.ui.View):
    """Step 2 — choose a base item within the selected slot."""

    def __init__(self, discord_id: int, slot: str, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._slot        = slot
        self._hub_back_fn = hub_back_fn

        bases = _bases_by_slot().get(slot, [])
        for i, base in enumerate(bases):
            btn = discord.ui.Button(
                label=base["vi"],
                style=discord.ButtonStyle.primary,
                row=i // 4,
            )
            btn.callback = self._make_base_cb(base["key"])
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_base_cb(self, base_key: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            await _nav_grade(interaction, self._discord_id, self._slot, base_key, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_slot(interaction, self._discord_id, self._hub_back_fn)


class _GradeView(discord.ui.View):
    """Step 3 — choose a craft grade (1-9)."""

    def __init__(self, discord_id: int, slot: str, base_key: str, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._slot        = slot
        self._base_key    = base_key
        self._hub_back_fn = hub_back_fn

        for grade in range(1, 10):
            btn = discord.ui.Button(
                label=f"Cấp {grade}",
                style=discord.ButtonStyle.primary,
                row=(grade - 1) // 5,
            )
            btn.callback = self._make_grade_cb(grade)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_grade_cb(self, grade: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            await _nav_material(interaction, self._discord_id, self._slot, self._base_key, grade, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_base(interaction, self._discord_id, self._slot, self._hub_back_fn)


class _MaterialView(discord.ui.View):
    """Step 4 — pick one or more normal materials from the bag.

    Multi-select: choose 1 to ``required_qty`` distinct materials. Each
    material's ``affix_bias`` is unioned at forge time, so any biased key
    from any pick gets the 3× roll weight. Total quantity is split across
    picks at confirm time (each pick gets at least 1, remainder fills the
    most-owned picks first).
    """

    def __init__(
        self,
        discord_id: int,
        slot: str,
        base_key: str,
        grade: int,
        hub_back_fn,
        *,
        eligible: list[tuple[str, int]],
        required_qty: int,
        page: int = 0,
        picks: set[str] | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._slot        = slot
        self._base_key    = base_key
        self._grade       = grade
        self._hub_back_fn = hub_back_fn
        self._eligible    = list(eligible)
        self._required_qty = required_qty
        pages = total_pages(len(self._eligible), per_page=_MATERIAL_PAGE_SIZE)
        self._page = max(0, min(page, pages - 1))
        self._picks: set[str] = set(picks or set())
        # Track current-page keys so the dropdown callback knows which
        # entries to overwrite vs which to leave untouched (keeps picks
        # made on other pages alive after a re-selection).
        self._current_page_keys: list[str] = []

        if self._eligible:
            options: list[discord.SelectOption] = []
            for key, qty in page_slice(self._eligible, self._page, per_page=_MATERIAL_PAGE_SIZE):
                self._current_page_keys.append(key)
                item = registry.get_item(key) or {}
                label = item.get("vi", key)
                mat_g = get_material_grade(key)
                # Reserve room in the 100-char description for the prefix
                # ("Phẩm N · có M · Tăng tỉ lệ: ") so the bias names don't
                # overflow Discord's SelectOption.description hard limit.
                # Embed-body label uses the full "Tăng tỉ lệ ra chỉ số"; in
                # the cramped dropdown row the trailing words are dropped.
                head = f"Phẩm {mat_g} · có {qty}"
                bias = _format_affix_bias(key, max_chars=100 - len(head) - len(" · Tăng tỉ lệ: "))
                desc = f"{head} · Tăng tỉ lệ: {bias}" if bias else head
                options.append(discord.SelectOption(
                    label=label[:100],
                    value=key,
                    description=desc[:100],
                    default=(key in self._picks),
                ))
            max_picks = min(len(options), _MATERIAL_PAGE_SIZE)
            placeholder = "🔨 Chọn nguyên liệu cho trang này…"
            if pages > 1:
                placeholder = f"🔨 Chọn nguyên liệu (Trang {self._page + 1}/{pages})…"
            select = discord.ui.Select(
                placeholder=placeholder,
                options=options,
                # min_values=0 lets the player deselect every option on the
                # current page without losing picks made on other pages.
                min_values=0,
                max_values=max_picks,
                row=0,
            )
            select.callback = self._make_pick_cb(select)
            self.add_item(select)

        confirm_btn = discord.ui.Button(
            label=f"✅ Xác Nhận ({len(self._picks)})",
            style=discord.ButtonStyle.success,
            row=1,
            disabled=not self._picks,
        )
        confirm_btn.callback = self._on_confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

        add_page_controls(
            self,
            page=self._page,
            total=len(self._eligible),
            on_change=self._on_page_change,
            row=2,
            per_page=_MATERIAL_PAGE_SIZE,
        )

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        embed = _material_embed(
            self._grade, self._eligible, self._required_qty, new_page, self._picks,
        )
        view = _MaterialView(
            self._discord_id, self._slot, self._base_key, self._grade,
            self._hub_back_fn,
            eligible=self._eligible,
            required_qty=self._required_qty,
            page=new_page, picks=self._picks,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    def _make_pick_cb(self, select: discord.ui.Select):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            # Replace only the current-page slice of the accumulator —
            # picks made on other pages stay intact so the user can build
            # up a multi-page selection.
            new_picks = (self._picks - set(self._current_page_keys)) | set(select.values)
            self._picks = new_picks
            embed = _material_embed(
                self._grade, self._eligible, self._required_qty,
                self._page, self._picks,
            )
            view = _MaterialView(
                self._discord_id, self._slot, self._base_key, self._grade,
                self._hub_back_fn,
                eligible=self._eligible,
                required_qty=self._required_qty,
                page=self._page, picks=self._picks,
            )
            await interaction.edit_original_response(embed=embed, view=view)
        return _cb

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not self._picks:
            await interaction.response.send_message(
                embed=error_embed("Chưa chọn nguyên liệu nào."), ephemeral=True,
            )
            return
        if not await safe_defer(interaction):
            return
        await _nav_super(
            interaction, self._discord_id, self._slot, self._base_key, self._grade,
            selected_mat_keys=list(self._picks), hub_back_fn=self._hub_back_fn,
        )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_grade(interaction, self._discord_id, self._slot, self._base_key, self._hub_back_fn)


class _SuperMaterialView(discord.ui.View):
    """Step 5 — pick an optional super-rare material or skip.

    Only one super material can be attached per forge — enforced by the
    singular select + a skip button that forwards no key.
    """

    def __init__(
        self,
        discord_id: int,
        slot: str,
        base_key: str,
        grade: int,
        hub_back_fn,
        *,
        selected_mat_keys: list[str],
        eligible: list[tuple[str, dict]],
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id       = discord_id
        self._slot             = slot
        self._base_key         = base_key
        self._grade            = grade
        self._hub_back_fn      = hub_back_fn
        self._selected_mat_keys = selected_mat_keys
        self._eligible         = list(eligible)
        pages = total_pages(len(self._eligible), per_page=PAGE_SIZE)
        self._page = max(0, min(page, pages - 1))

        if self._eligible:
            options: list[discord.SelectOption] = []
            for key, spec in page_slice(self._eligible, self._page, per_page=PAGE_SIZE):
                label = spec.get("vi", key)
                min_g = spec.get("min_item_grade", spec.get("grade", 1))
                options.append(discord.SelectOption(
                    label=label[:100],
                    value=key,
                    description=f"Tối thiểu cấp {min_g}"[:100],
                ))
            placeholder = "✨ Chọn vật liệu siêu hiếm…"
            if pages > 1:
                placeholder = f"✨ Chọn vật liệu siêu hiếm (Trang {self._page + 1}/{pages})…"
            select = discord.ui.Select(
                placeholder=placeholder,
                options=options,
                row=0,
            )
            select.callback = self._make_pick_cb(select)
            self.add_item(select)

        skip_btn = discord.ui.Button(label="⏭️ Bỏ Qua", style=discord.ButtonStyle.primary, row=1)
        skip_btn.callback = self._skip_cb
        self.add_item(skip_btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

        add_page_controls(
            self,
            page=self._page,
            total=len(self._eligible),
            on_change=self._on_page_change,
            row=2,
        )

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        view = _SuperMaterialView(
            self._discord_id, self._slot, self._base_key, self._grade,
            self._hub_back_fn,
            selected_mat_keys=self._selected_mat_keys,
            eligible=self._eligible,
            page=new_page,
        )
        await interaction.edit_original_response(view=view)

    def _make_pick_cb(self, select: discord.ui.Select):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            picked = select.values[0]
            await _nav_confirm(
                interaction, self._discord_id, self._slot, self._base_key, self._grade,
                selected_mat_keys=self._selected_mat_keys,
                selected_super_key=picked,
                hub_back_fn=self._hub_back_fn,
            )
        return _cb

    async def _skip_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_confirm(
            interaction, self._discord_id, self._slot, self._base_key, self._grade,
            selected_mat_keys=self._selected_mat_keys,
            selected_super_key=None,
            hub_back_fn=self._hub_back_fn,
        )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_material(interaction, self._discord_id, self._slot, self._base_key, self._grade, self._hub_back_fn)


class _ConfirmView(discord.ui.View):
    """Step 6 — confirm requirements, then execute forge."""

    def __init__(
        self,
        discord_id: int,
        slot: str,
        base_key: str,
        grade: int,
        hub_back_fn,
        *,
        char,
        mats_in_bag: dict[str, int],
        selected_mat_keys: list[str],
        selected_super_key: str | None,
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id         = discord_id
        self._slot               = slot
        self._base_key           = base_key
        self._grade              = grade
        self._hub_back_fn        = hub_back_fn
        self._selected_mat_keys  = selected_mat_keys
        self._selected_super_key = selected_super_key

        required_qty = max_affix_total(grade)
        picks = [(k, mats_in_bag.get(k, 0)) for k in selected_mat_keys]
        mat_ok = distribute_qty(picks, required_qty) is not None
        recipe = get_recipe(grade)
        merit_ok = bool(recipe) and char.merit >= recipe["cost_cong_duc"]
        ok = mat_ok and merit_ok

        confirm_btn = discord.ui.Button(
            label="✅ Xác Nhận Rèn",
            style=discord.ButtonStyle.success,
            disabled=not ok,
            row=0,
        )
        confirm_btn.callback = self._confirm_cb
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label="◀ Hủy", style=discord.ButtonStyle.secondary, row=0)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    async def _confirm_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return

        required_qty = max_affix_total(self._grade)

        async with get_session() as session:
            player_repo = PlayerRepository(session)
            inv_repo    = InventoryRepository(session)
            equip_repo  = EquipmentRepository(session)

            player = await player_repo.get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Không tìm thấy nhân vật."), view=None)
                return

            char = _player_to_model(player)

            # Re-verify each picked material still has its allocated share.
            all_items = await inv_repo.get_all(player.id)
            owned_lookup: dict[str, int] = {}
            for k in self._selected_mat_keys:
                it = next((x for x in all_items if x.item_key == k), None)
                owned_lookup[k] = it.quantity if it else 0

            picks = [(k, owned_lookup[k]) for k in self._selected_mat_keys]
            split = distribute_qty(picks, required_qty)
            if split is None:
                await interaction.edit_original_response(
                    embed=error_embed("Không đủ nguyên liệu — kho đã thay đổi."), view=None
                )
                return

            recipe = get_recipe(self._grade)
            if not recipe:
                await interaction.edit_original_response(embed=error_embed("Không tìm thấy công thức rèn."), view=None)
                return

            # Every pick must still be a forge_material (defends against
            # stale dropdown values pointing at non-forge items). Recipe
            # grade no longer gates eligibility.
            for k in self._selected_mat_keys:
                if get_material_grade(k) is None:
                    await interaction.edit_original_response(
                        embed=error_embed("Có nguyên liệu không hợp lệ."), view=None
                    )
                    return

            if char.merit < recipe["cost_cong_duc"]:
                await interaction.edit_original_response(
                    embed=error_embed("Không đủ Công Đức."), view=None
                )
                return

            # Verify super material is still owned (if chosen)
            if self._selected_super_key:
                owned_super = next(
                    (it for it in all_items if it.item_key == self._selected_super_key),
                    None,
                )
                if not owned_super or owned_super.quantity < 1:
                    await interaction.edit_original_response(
                        embed=error_embed("Không còn vật liệu siêu hiếm đã chọn."), view=None
                    )
                    return

            # Consume each picked material by its allocated share. ``remove_any_grade``
            # iterates rows ascending-by-grade so split stacks at different
            # grades all drain into the same recipe slot.
            consumed: list[tuple[str, int]] = []
            for k, take in split.items():
                if take <= 0:
                    continue
                if get_material_grade(k) is None:
                    await interaction.edit_original_response(
                        embed=error_embed("Nguyên liệu không hợp lệ."), view=None
                    )
                    return
                ok_remove = await inv_repo.remove_any_grade(player.id, k, take)
                if not ok_remove:
                    await interaction.edit_original_response(
                        embed=error_embed("Không thể tiêu hao nguyên liệu."), view=None
                    )
                    return
                consumed.append((k, take))

            # Consume the super material (at most one, singular arg)
            if self._selected_super_key:
                await inv_repo.remove_any_grade(
                    player.id, self._selected_super_key, 1,
                )

            result = forge_equipment(
                char, self._base_key, self._grade, consumed,
                super_material_key=self._selected_super_key,
            )
            if not result.success:
                await interaction.edit_original_response(embed=error_embed(result.message), view=None)
                return

            # Write mutated merit back to ORM and save
            player.merit = char.merit
            await player_repo.save(player)
            inst = await equip_repo.add_to_bag(player.id, result.item_data)

        quality = result.item_data["quality"]
        embed = discord.Embed(
            title="⚒️ Rèn Luyện Thành Công!",
            description=result.message,
            color=QUALITY_COLORS[quality],
        )
        # Render implicit base stats through format_stat so labels and pct
        # formatting stay consistent with /equipment and /inventory.
        implicit_lines = [
            f"• {format_stat(k, v)}"
            for k, v in result.item_data["implicit_stats"].items()
        ]
        embed.add_field(
            name="Chỉ Số Cơ Bản",
            value="\n".join(implicit_lines) or "—",
            inline=True,
        )
        if result.item_data["affixes"]:
            # Each affix carries (key, stat, value, type). Display the underlying
            # stat with format_stat (gives "+0.7% Tăng ST") and append the affix
            # vi name from the registry as flavor (e.g. "Siêu Việt").
            affix_lines: list[str] = []
            for a in result.item_data["affixes"]:
                stat_str = format_stat(a["stat"], a["value"])
                aff_def = registry.get_affix(a["key"]) or {}
                vi_name = aff_def.get("vi")
                affix_lines.append(
                    f"• {stat_str} *({vi_name})*" if vi_name else f"• {stat_str}"
                )
            embed.add_field(
                name="Thuộc Tính",
                value="\n".join(affix_lines),
                inline=True,
            )
        embed.set_footer(
            text=f"ID: {inst.id} | Phẩm: {QUALITY_LABELS[quality]} | Còn {char.merit:,} Công Đức ✨"
        )

        back_view = discord.ui.View(timeout=120)
        back_btn  = discord.ui.Button(label="◀ Về Luyện Công Phường", style=discord.ButtonStyle.secondary)
        async def _to_hub(inter: discord.Interaction) -> None:
            if not await safe_defer(inter):
                return
            await _nav_hub(inter, self._discord_id, self._hub_back_fn)
        back_btn.callback = _to_hub
        back_view.add_item(back_btn)

        await interaction.edit_original_response(embed=embed, view=back_view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_super(
            interaction, self._discord_id, self._slot, self._base_key, self._grade,
            selected_mat_keys=self._selected_mat_keys,
            hub_back_fn=self._hub_back_fn,
        )


class _RecipeSelectView(discord.ui.View):
    """Grade selector for recipe browsing."""

    def __init__(self, discord_id: int, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._hub_back_fn = hub_back_fn

        for grade in range(1, 10):
            btn = discord.ui.Button(
                label=f"Cấp {grade}",
                style=discord.ButtonStyle.primary,
                row=(grade - 1) // 5,
            )
            btn.callback = self._make_grade_cb(grade)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_grade_cb(self, grade: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            embed = discord.Embed(
                title=f"📜 Công Thức Rèn Cấp {grade}",
                description=describe_recipe(grade),
                color=discord.Color.blue(),
            )
            back_view = discord.ui.View(timeout=120)
            back_btn  = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
            async def _back(inter: discord.Interaction) -> None:
                if not await safe_defer(inter):
                    return
                await inter.edit_original_response(
                    embed=discord.Embed(
                        title="📜 Công Thức Rèn — Chọn Cấp",
                        description="Nhấn nút để xem công thức rèn theo cấp trang bị.",
                        color=discord.Color.blue(),
                    ),
                    view=_RecipeSelectView(self._discord_id, self._hub_back_fn),
                )
            back_btn.callback = _back
            back_view.add_item(back_btn)
            await interaction.edit_original_response(embed=embed, view=back_view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_hub(interaction, self._discord_id, self._hub_back_fn)


# ── Cog ──────────────────────────────────────────────────────────────────────

class ForgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="forge", description="Mở Luyện Công Phường — rèn trang bị từ nguyên liệu")
    async def forge_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=_forge_hub_embed(),
            view=ForgeHubView(interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ForgeCog(bot))
