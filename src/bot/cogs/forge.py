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
from src.game.constants.grades import Grade
from src.game.systems.forge import (
    QUALITY_LABELS,
    check_forge_requirements,
    describe_recipe,
    forge_equipment,
    get_material_grade,
    get_recipe,
    max_affix_total,
)
from src.utils.embed_builder import base_embed, error_embed

log = logging.getLogger(__name__)

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


async def _nav_material(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    grade: int,
    hub_back_fn,
) -> None:
    loaded = await _load_forge_bag(discord_id)
    if loaded is None:
        await interaction.edit_original_response(
            embed=error_embed("Không tìm thấy nhân vật."), view=None
        )
        return
    char, mats_in_bag, _ = loaded

    recipe = get_recipe(grade)
    required_qty = max_affix_total(grade)
    required_grades = sorted({r["mat_grade"] for opt in (recipe or {}).get("options", []) for r in opt["materials"]})

    # Materials in bag with qty >= required_qty, whose grade is one of the
    # recipe's accepted grades. Each entry is a valid forge pick.
    eligible: list[tuple[str, int]] = [
        (key, qty) for key, qty in mats_in_bag.items()
        if qty >= required_qty and get_material_grade(key) in required_grades
    ]

    embed = discord.Embed(
        title=f"⚒️ Chọn Nguyên Liệu Rèn — Cấp {grade}",
        description=(
            f"Cần **{required_qty}x** nguyên liệu cùng loại "
            f"(phẩm {', '.join(str(g) for g in required_grades)}).\n"
            "Chọn đúng loại nguyên liệu bạn muốn dùng."
        ),
        color=0xB8860B,
    )
    if not eligible:
        embed.add_field(
            name="⚠️ Thiếu Nguyên Liệu",
            value="Không có loại nguyên liệu nào đạt yêu cầu. Hãy tích trữ thêm.",
            inline=False,
        )

    view = _MaterialView(discord_id, slot, base_key, grade, hub_back_fn,
                         eligible=eligible, required_qty=required_qty)
    await interaction.edit_original_response(embed=embed, view=view)


async def _nav_super(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    grade: int,
    selected_mat_key: str,
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

    mat_name = (registry.get_item(selected_mat_key) or {}).get("vi", selected_mat_key)
    embed = discord.Embed(
        title="✨ Chọn Vật Liệu Siêu Hiếm (Tùy Chọn)",
        description=(
            f"Nguyên liệu chính: **{mat_name}**.\n"
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
        selected_mat_key=selected_mat_key,
        eligible=eligible,
    )
    await interaction.edit_original_response(embed=embed, view=view)


async def _nav_confirm(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    grade: int,
    selected_mat_key: str,
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
        selected_mat_key=selected_mat_key,
        selected_super_key=selected_super_key,
    )
    embed = _build_confirm_embed(
        base_key, grade, char, mats_in_bag,
        selected_mat_key=selected_mat_key,
        selected_super_key=selected_super_key,
    )
    await interaction.edit_original_response(embed=embed, view=view)


# ── Confirm embed builder ─────────────────────────────────────────────────────

def _build_confirm_embed(
    base_key: str,
    grade: int,
    char,
    mats_in_bag: dict[str, int],
    selected_mat_key: str | None = None,
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
            value=f"{merit_icon} {recipe['cost_cong_duc']:,} ✨ (đang có: {char.merit:,})",
            inline=False,
        )

        required_qty = max_affix_total(grade)
        if selected_mat_key:
            mat_item = registry.get_item(selected_mat_key) or {}
            owned = mats_in_bag.get(selected_mat_key, 0)
            icon  = "✅" if owned >= required_qty else "❌"
            embed.add_field(
                name="Nguyên Liệu Đã Chọn",
                value=f"{icon} {required_qty}x **{mat_item.get('vi', selected_mat_key)}** (có: {owned})",
                inline=False,
            )
        else:
            mat_lines: list[str] = []
            for i, opt in enumerate(recipe["options"], 1):
                opt_label = f"[PA {i}] " if len(recipe["options"]) > 1 else ""
                for req in opt["materials"]:
                    owned = sum(qty for k, qty in mats_in_bag.items() if get_material_grade(k) == req["mat_grade"])
                    icon  = "✅" if owned >= required_qty else "❌"
                    mat_lines.append(f"{icon} {opt_label}{required_qty}x Linh Thiết Phẩm {req['mat_grade']} (có: {owned})")
            embed.add_field(name="Nguyên Liệu", value="\n".join(mat_lines) or "—", inline=False)

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
            ("⚒️ Rèn Trang Bị",      discord.ButtonStyle.primary,   self._craft_cb,  0),
            ("📋 Danh Sách Trang Bị", discord.ButtonStyle.secondary,  self._list_cb,   0),
            ("📜 Công Thức Rèn",      discord.ButtonStyle.secondary,  self._recipe_cb, 0),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return _guard(interaction, self._discord_id)

    async def _craft_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_slot(interaction, self._discord_id, self._back_fn)

    async def _list_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        embed = discord.Embed(title="⚒️ Danh Sách Trang Bị Có Thể Rèn", color=discord.Color.gold())
        for slot, bases in sorted(_bases_by_slot().items()):
            names = ", ".join(f"{b['vi']} (`{b['key']}`)" for b in bases)
            embed.add_field(name=SLOT_VI.get(slot, slot), value=names, inline=False)
        embed.set_footer(text="Dùng /forge recipe <cấp> để xem yêu cầu chi tiết.")

        back_view = discord.ui.View(timeout=120)
        back_btn  = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
        async def _back(inter: discord.Interaction) -> None:
            await inter.response.defer()
            await _nav_hub(inter, self._discord_id, self._back_fn)
        back_btn.callback = _back
        back_view.add_item(back_btn)
        await interaction.edit_original_response(embed=embed, view=back_view)

    async def _recipe_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
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
        await interaction.response.defer()
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
            await interaction.response.defer()
            await _nav_base(interaction, self._discord_id, slot, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
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
            await interaction.response.defer()
            await _nav_grade(interaction, self._discord_id, self._slot, base_key, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
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
            await interaction.response.defer()
            await _nav_material(interaction, self._discord_id, self._slot, self._base_key, grade, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_base(interaction, self._discord_id, self._slot, self._hub_back_fn)


class _MaterialView(discord.ui.View):
    """Step 4 — pick a specific normal material from the bag.

    The select menu lists every inventory material whose grade matches one of
    the recipe's accepted grades AND has enough copies for the required qty.
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
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._slot        = slot
        self._base_key    = base_key
        self._grade       = grade
        self._hub_back_fn = hub_back_fn

        if eligible:
            options: list[discord.SelectOption] = []
            for key, qty in eligible[:25]:  # Discord's hard cap
                item = registry.get_item(key) or {}
                label = item.get("vi", key)
                mat_g = get_material_grade(key)
                options.append(discord.SelectOption(
                    label=label[:100],
                    value=key,
                    description=f"Phẩm {mat_g} · có {qty} · cần {required_qty}"[:100],
                ))
            select = discord.ui.Select(
                placeholder="🔨 Chọn nguyên liệu…",
                options=options,
                row=0,
            )
            select.callback = self._make_pick_cb(select)
            self.add_item(select)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_pick_cb(self, select: discord.ui.Select):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            picked = select.values[0]
            await _nav_super(
                interaction, self._discord_id, self._slot, self._base_key, self._grade,
                selected_mat_key=picked, hub_back_fn=self._hub_back_fn,
            )
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
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
        selected_mat_key: str,
        eligible: list[tuple[str, dict]],
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id      = discord_id
        self._slot            = slot
        self._base_key        = base_key
        self._grade           = grade
        self._hub_back_fn     = hub_back_fn
        self._selected_mat_key = selected_mat_key

        if eligible:
            options: list[discord.SelectOption] = []
            for key, spec in eligible[:25]:
                label = spec.get("vi", key)
                min_g = spec.get("min_item_grade", spec.get("grade", 1))
                options.append(discord.SelectOption(
                    label=label[:100],
                    value=key,
                    description=f"Tối thiểu cấp {min_g}"[:100],
                ))
            select = discord.ui.Select(
                placeholder="✨ Chọn vật liệu siêu hiếm…",
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

    def _make_pick_cb(self, select: discord.ui.Select):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            picked = select.values[0]
            await _nav_confirm(
                interaction, self._discord_id, self._slot, self._base_key, self._grade,
                selected_mat_key=self._selected_mat_key,
                selected_super_key=picked,
                hub_back_fn=self._hub_back_fn,
            )
        return _cb

    async def _skip_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_confirm(
            interaction, self._discord_id, self._slot, self._base_key, self._grade,
            selected_mat_key=self._selected_mat_key,
            selected_super_key=None,
            hub_back_fn=self._hub_back_fn,
        )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
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
        selected_mat_key: str,
        selected_super_key: str | None,
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id        = discord_id
        self._slot              = slot
        self._base_key          = base_key
        self._grade             = grade
        self._hub_back_fn       = hub_back_fn
        self._selected_mat_key  = selected_mat_key
        self._selected_super_key = selected_super_key

        required_qty = max_affix_total(grade)
        mat_ok = mats_in_bag.get(selected_mat_key, 0) >= required_qty
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
        await interaction.response.defer()

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

            # Re-verify the user still has the selected material at required qty
            all_items = await inv_repo.get_all(player.id)
            owned_mat = next(
                (it for it in all_items if it.item_key == self._selected_mat_key),
                None,
            )
            if not owned_mat or owned_mat.quantity < required_qty:
                await interaction.edit_original_response(
                    embed=error_embed("Không đủ nguyên liệu đã chọn — kho đã thay đổi."), view=None
                )
                return

            mat_grade = get_material_grade(self._selected_mat_key)
            recipe = get_recipe(self._grade)
            if not recipe or mat_grade is None:
                await interaction.edit_original_response(embed=error_embed("Không tìm thấy công thức rèn."), view=None)
                return
            # Require the recipe to have an option whose mat_grade matches the pick
            if not any(
                any(req["mat_grade"] == mat_grade for req in opt["materials"])
                for opt in recipe["options"]
            ):
                await interaction.edit_original_response(
                    embed=error_embed("Nguyên liệu đã chọn không khớp công thức."), view=None
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

            # Consume the chosen normal material
            removed = await inv_repo.remove_item(
                player.id, self._selected_mat_key, Grade(mat_grade), required_qty,
            )
            if not removed:
                await interaction.edit_original_response(
                    embed=error_embed("Không thể tiêu hao nguyên liệu."), view=None
                )
                return
            consumed = [(self._selected_mat_key, required_qty)]

            # Consume the super material (at most one, singular arg)
            if self._selected_super_key:
                super_spec = registry.get_super_material(self._selected_super_key) or {}
                super_grade = int(super_spec.get("grade", 1))
                await inv_repo.remove_item(
                    player.id, self._selected_super_key, Grade(super_grade), 1,
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
        embed.add_field(
            name="Chỉ Số Cơ Bản",
            value="\n".join(
                f"• {k}: **{v:.3f}**" if isinstance(v, float) and v < 1 else f"• {k}: **{int(v)}**"
                for k, v in result.item_data["implicit_stats"].items()
            ),
            inline=True,
        )
        if result.item_data["affixes"]:
            embed.add_field(
                name="Thuộc Tính",
                value="\n".join(
                    f"• {a['key']} → {a['value']:.3f}"
                    if isinstance(a["value"], float) and a["value"] < 1
                    else f"• {a['key']} → {int(a['value'])}"
                    for a in result.item_data["affixes"]
                ),
                inline=True,
            )
        embed.set_footer(
            text=f"ID: {inst.id} | Phẩm: {QUALITY_LABELS[quality]} | Còn {char.merit:,} Công Đức ✨"
        )

        back_view = discord.ui.View(timeout=120)
        back_btn  = discord.ui.Button(label="◀ Về Luyện Công Phường", style=discord.ButtonStyle.secondary)
        async def _to_hub(inter: discord.Interaction) -> None:
            await inter.response.defer()
            await _nav_hub(inter, self._discord_id, self._hub_back_fn)
        back_btn.callback = _to_hub
        back_view.add_item(back_btn)

        await interaction.edit_original_response(embed=embed, view=back_view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_super(
            interaction, self._discord_id, self._slot, self._base_key, self._grade,
            selected_mat_key=self._selected_mat_key,
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
            await interaction.response.defer()
            embed = discord.Embed(
                title=f"📜 Công Thức Rèn Cấp {grade}",
                description=describe_recipe(grade),
                color=discord.Color.blue(),
            )
            back_view = discord.ui.View(timeout=120)
            back_btn  = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
            async def _back(inter: discord.Interaction) -> None:
                await inter.response.defer()
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
        await interaction.response.defer()
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
