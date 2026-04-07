"""Discord embed builder helpers."""
from __future__ import annotations

import discord

from src.utils.assets import (
    BAR_EMPTY,
    BAR_FILLED,
    AXIS_ICONS,
    CURRENCY_ICONS,
    ELEMENT_COLORS,
    FOOTER_TEXT,
    GRADE_COLORS,
    REALM_TIER_ICONS,
    SEPARATOR,
    STAT_ICONS,
    THUMBNAIL_URL,
)

# Re-export so callers that import ELEMENT_COLORS / GRADE_COLORS from here still work.
__all__ = [
    "ELEMENT_COLORS",
    "GRADE_COLORS",
    "progress_bar",
    "battle_embed",
    "base_embed",
    "character_embed",
    "error_embed",
    "success_embed",
]


# ── Utility ───────────────────────────────────────────────────────────────────

def progress_bar(current: int | float, maximum: int | float, length: int = 12) -> str:
    """Return a Unicode block progress bar string.

    Example (length=12, 5/9):  ▓▓▓▓▓▓▓░░░░░
    """
    if maximum <= 0:
        return BAR_EMPTY * length
    ratio = max(0.0, min(1.0, current / maximum))
    filled = round(ratio * length)
    return BAR_FILLED * filled + BAR_EMPTY * (length - filled)


def _pct(current: int | float, maximum: int | float) -> str:
    if maximum <= 0:
        return "0%"
    return f"{current / maximum * 100:.0f}%"


# ── Base builders ─────────────────────────────────────────────────────────────

def battle_embed(
    wave_label: str,
    wave_idx: int,
    total_waves: int,
    player_name: str,
    player_hp: int,
    player_hp_max: int,
    player_mp: int,
    player_mp_max: int,
    enemy_name: str,
    enemy_hp: int,
    enemy_hp_max: int,
    turn: int,
    turn_log: list[str],
) -> discord.Embed:
    """Real-time battle embed — updated each turn."""
    p_hp_bar = progress_bar(player_hp, player_hp_max, 10)
    p_mp_bar = progress_bar(player_mp, player_mp_max, 10)
    e_hp_bar = progress_bar(enemy_hp, enemy_hp_max, 10)

    wave_header = f"Wave {wave_idx + 1}/{total_waves}" if total_waves > 1 else "Chiến Đấu"
    lines = [
        f"**{wave_header}** — {wave_label}",
        "",
        f"❤️ `{p_hp_bar}` {player_hp:,}/{player_hp_max:,}  **{player_name}**",
        f"💙 `{p_mp_bar}` {player_mp:,}/{player_mp_max:,} MP",
        "─" * 24,
        f"👹 `{e_hp_bar}` {enemy_hp:,}/{enemy_hp_max:,}  **{enemy_name}**",
    ]
    if turn:
        lines.append(f"\n*Lượt {turn}*")

    log_section = "\n".join(turn_log[-10:]) if turn_log else ""
    description = "\n".join(lines) + ("\n\n" + log_section if log_section else "")

    return discord.Embed(title="⚔️ Chiến Đấu", description=description, color=0x3498DB)


def base_embed(title: str, description: str = "", color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def error_embed(message: str) -> discord.Embed:
    return base_embed("❌ Lỗi", message, color=0xED4245)


def success_embed(message: str) -> discord.Embed:
    return base_embed("✅ Thành Công", message, color=0x57F287)


# ── Character status embed ────────────────────────────────────────────────────

def character_embed(player_name: str, stats: dict, avatar_url: str | None = None) -> discord.Embed:
    """Clean character status embed.

    Expected keys in *stats*:
        hp_current, hp_max, mp_current, mp_max, spd
        body_realm, body_level, body_xp, body_realm_label
        qi_realm,   qi_level,   qi_xp,  qi_realm_label
        formation_realm, formation_level, formation_xp, formation_realm_label
        active_axis
        merit, karma_accum, primordial_stones
        constitution (display name)
        active_formation (display name or None)
        gem_count (int)
    """
    from src.game.constants.currencies import TURNS_PER_CULT_LEVEL

    LEVELS_PER_REALM = 9
    embed = discord.Embed(title=f"⚔️  {player_name}", color=0x5865F2)
    thumb = avatar_url or THUMBNAIL_URL
    if thumb:
        embed.set_thumbnail(url=thumb)

    # ── Vitals (3 inline) ────────────────────────────────────────────────────
    hp_cur = stats.get("hp_current", 0)
    hp_max = stats.get("hp_max", 1)
    mp_cur = stats.get("mp_current", 0)
    mp_max = stats.get("mp_max", 1)
    spd    = stats.get("spd", 0)

    embed.add_field(
        name=f"{STAT_ICONS['hp']} Sinh Lực",
        value=f"`{progress_bar(hp_cur, hp_max, 8)}`\n{hp_cur:,} / {hp_max:,}",
        inline=True,
    )
    embed.add_field(
        name=f"{STAT_ICONS['mp']} Linh Lực",
        value=f"`{progress_bar(mp_cur, mp_max, 8)}`\n{mp_cur:,} / {mp_max:,}",
        inline=True,
    )
    embed.add_field(
        name=f"{STAT_ICONS['spd']} Tốc Độ",
        value=f"**{spd}**",
        inline=True,
    )

    # ── Cultivation (single block) ────────────────────────────────────────────
    active_axis = stats.get("active_axis", "qi")
    axes = [
        ("body",      "body_realm",      "body_level",      "body_xp",      "body_realm_label"),
        ("qi",        "qi_realm",        "qi_level",        "qi_xp",        "qi_realm_label"),
        ("formation", "formation_realm", "formation_level", "formation_xp", "formation_realm_label"),
    ]

    cult_lines: list[str] = []
    for axis_key, realm_k, level_k, xp_k, label_k in axes:
        icon      = AXIS_ICONS[axis_key]
        realm_idx = stats.get(realm_k, 0)
        level     = stats.get(level_k, 1)
        xp        = stats.get(xp_k, 0)
        label     = stats.get(label_k, "Chưa Tu Luyện")
        tier_icon = REALM_TIER_ICONS[min(realm_idx, len(REALM_TIER_ICONS) - 1)]
        xp_max    = TURNS_PER_CULT_LEVEL[axis_key]
        at_max    = level >= LEVELS_PER_REALM
        marker    = " **◀**" if axis_key == active_axis else ""

        header = f"{icon} {tier_icon} **{label}** · Cấp {level}/{LEVELS_PER_REALM}{marker}"
        if at_max:
            body = "  ⚡ *Sẵn sàng đột phá!*"
        else:
            body = f"  `{progress_bar(xp, xp_max, 12)}` {xp:,}/{xp_max:,}"
        cult_lines.append(f"{header}\n{body}")

    embed.add_field(
        name="✨ Tu Luyện",
        value="\n\n".join(cult_lines),
        inline=False,
    )

    # ── Resources (3 inline) ─────────────────────────────────────────────────
    merit  = stats.get("merit", 0)
    karma  = stats.get("karma_accum", 0)
    stones = stats.get("primordial_stones", 0)

    embed.add_field(name=f"{CURRENCY_ICONS['merit']} Công Đức",            value=f"{merit:,}",  inline=True)
    embed.add_field(name=f"{CURRENCY_ICONS['karma_accum']} Nghiệp Lực",    value=f"{karma:,}",  inline=True)
    embed.add_field(name=f"{CURRENCY_ICONS['primordial_stones']} Hỗn Nguyên", value=f"{stones:,}", inline=True)

    # ── Constitution & Formation (compact) ────────────────────────────────────
    constitution = stats.get("constitution") or "Vạn Tượng"
    active_form  = stats.get("active_formation")
    gem_count    = stats.get("gem_count", 0)

    if active_form:
        detail = (
            f"🧬 **{constitution}**\n"
            f"🔯 **{active_form}**  `{progress_bar(gem_count, 81, 8)}` {gem_count}/81 ngọc"
        )
    else:
        detail = f"🧬 **{constitution}**\n🔯 *(chưa kích hoạt trận pháp)*"

    embed.add_field(name="Thể Chất & Trận Pháp", value=detail, inline=False)

    embed.set_footer(text=FOOTER_TEXT)
    return embed
