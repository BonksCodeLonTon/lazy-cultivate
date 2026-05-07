"""Cẩm Nang — in-game handbook explaining mechanics to players.

Pure-static reference cog: no player lookup, no DB writes. Each chapter
is a single embed assembled from constants in ``src/game/constants/`` so
the numbers stay in sync with the live ruleset.

Entry points:
  * ``/camnang`` slash command — opens the handbook directly.
  * Module-level ``render_handbook_hub(interaction, discord_id, back_fn)``
    — called from the ``/status`` view's 📖 Cẩm Nang button so the same
    UI is reachable both ways.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from src.game.constants.balance import (
    FORMATION_MAX_RESERVE_PCT,
    GEM_ELEMENT_BASE_BONUS,
    HEAL_CRIT_CHANCE,
    HEAL_CRIT_MULT,
    MAX_CRIT_CHANCE,
    MAX_FINAL_DMG_REDUCE,
    MAX_PHYS_REDUCTION,
    RATING_K,
    REALM_POWER_BONUS_PER_STAGE,
    SOUL_DRAIN_CAP_PCT,
    STAT_STEAL_CAP_PCT,
    TRUE_DMG_PCT_CAP,
)
from src.game.constants.currencies import (
    BONUS_TURNS,
    KARMA_ACCUM_CAP,
    KARMA_PER_NORMAL_TURN,
    KARMA_TITLE_THRESHOLDS,
    MARKET_LISTING_HOURS,
    MARKET_MAX_LISTINGS,
    FORMATION_EXP_PER_MERIT_BY_REALM,
    MERIT_PER_BONUS_TURN,
    MERIT_PER_NORMAL_TURN,
    MERIT_TITLE_THRESHOLDS,
    SECONDS_PER_TURN,
    TRADE_FEE_RATE,
    TURNS_PER_DAY,
)
from src.game.constants.grades import GRADE_LABELS, Grade
from src.game.constants.linh_can import ALL_LINH_CAN, LINH_CAN_DATA, LINH_CAN_MAX_LEVEL
from src.game.constants.realms import (
    BODY_REALMS,
    FORMATION_REALMS,
    LEVELS_PER_REALM,
    QI_REALMS,
)
from src.utils import emojis
from src.utils.embed_builder import base_embed

BackFn = Callable[[discord.Interaction], Awaitable[None]]


# ── Chapter registry ─────────────────────────────────────────────────────────
# Order = display order in the dropdown. Each builder returns a fresh
# ``discord.Embed`` so chapters are cheap to switch between.

def _chapter_overview() -> discord.Embed:
    embed = base_embed(
        "📖 Cẩm Nang — Tổng Quan",
        (
            "**Lazy Cultivate** là một game tu tiên AFK theo lượt. "
            "Nhân vật của bạn tự động tu luyện theo thời gian thực — "
            f"1 lượt = {SECONDS_PER_TURN}s, mỗi ngày có **{TURNS_PER_DAY:,}** lượt.\n\n"
            "Tu luyện gồm **3 trục**, mỗi trục có **9 cảnh giới × 9 bậc**. "
            "Hành trình từ phàm nhân tới đỉnh thường mất **6 tháng – 1 năm**, "
            "không có đường tắt ngoài đan dược hiếm."
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="3 Trục Tu Luyện",
        value=(
            "🔥 **Luyện Thể** — Thể Tu, sinh lực cao, phòng thủ vững.\n"
            "💧 **Luyện Khí** — Khí Tu, cân bằng, cộng hưởng Linh Căn.\n"
            "🔯 **Trận Đạo** — Trận Tu, linh lực cao, sát thương phép."
        ),
        inline=False,
    )
    embed.add_field(
        name="Vòng Lặp Cốt Lõi",
        value=(
            "1. `/cultivate` — Tu luyện AFK để dồn EXP & Công Đức.\n"
            "2. `/breakthrough` — Đột phá khi đạt Bậc 9 của một cảnh giới.\n"
            "3. `/fight` & `/dungeon` — Tham chiến, săn rơi vật phẩm.\n"
            "4. `/equip`, `/inlay`, `/learn` — Trang bị, khảm ngọc, học kỹ năng.\n"
            "5. `/shop`, `/market` — Mua bán nguyên liệu hiếm."
        ),
        inline=False,
    )
    embed.add_field(
        name="Mở Bảng Điều Hướng",
        value="Dùng `/status` để mở bảng nhân vật + nút điều hướng tới mọi tính năng.",
        inline=False,
    )
    return embed


def _chapter_cultivation() -> discord.Embed:
    body_first = BODY_REALMS[0].vi
    body_last = BODY_REALMS[-1].vi
    qi_first = QI_REALMS[0].vi
    qi_last = QI_REALMS[-1].vi
    form_first = FORMATION_REALMS[0].vi
    form_last = FORMATION_REALMS[-1].vi

    embed = base_embed(
        "🌀 Tu Luyện & Đột Phá",
        "Mỗi trục tu luyện độc lập, có cảnh giới và lộ trình riêng.",
        color=0x3498DB,
    )
    embed.add_field(
        name=f"🔥 Luyện Thể — {body_first} → {body_last}",
        value="Nhánh thể tu (Body). HP rất cao, Phòng Thủ tốt, ATK cao.",
        inline=False,
    )
    embed.add_field(
        name=f"💧 Luyện Khí — {qi_first} → {qi_last}",
        value="Nhánh khí tu (Qi). Cân bằng. Mở giới hạn cấp Linh Căn.",
        inline=False,
    )
    embed.add_field(
        name=f"🔯 Trận Đạo — {form_first} → {form_last}",
        value="Nhánh trận tu (Formation). MP cao, MATK mạnh, mở Trận Pháp & Ngọc Khảm.",
        inline=False,
    )
    from math import ceil
    rate_lo = FORMATION_EXP_PER_MERIT_BY_REALM[0]
    rate_hi = FORMATION_EXP_PER_MERIT_BY_REALM[-1]

    def _fmt_rate(rate: float) -> str:
        if rate >= 1.0:
            return f"1 Công Đức = {rate:g} EXP"
        return f"{ceil(1.0 / rate):,} Công Đức = 1 EXP"

    embed.add_field(
        name="📊 Tốc Độ Tu Luyện",
        value=(
            f"• Luyện Thể & Luyện Khí: tự động tăng theo lượt cày (trục có "
            f"`base_exp_rate` riêng cho từng cảnh giới).\n"
            f"• Trận Đạo: **không** tự tăng theo lượt — đổi Công Đức thành "
            f"EXP qua **📘 Học Trận**. Tỷ lệ giảm dần theo cảnh giới: "
            f"**{_fmt_rate(rate_lo)}** ở Khai Huyền, "
            f"**{_fmt_rate(rate_hi)}** ở Đế Trận.\n"
            f"_({LEVELS_PER_REALM} bậc × {len(BODY_REALMS)} cảnh giới = "
            f"{LEVELS_PER_REALM * len(BODY_REALMS)} bậc tổng / trục)_"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚡ Đột Phá",
        value=(
            "Khi đạt **Bậc 9** của một cảnh giới, dùng `/breakthrough` (hoặc nút Đột Phá ở `/status`) "
            "để vượt cảnh giới. Đột phá tốn nguyên liệu chuyên dụng và có thể có tỷ lệ thất bại "
            "ở các cảnh giới cao — chuẩn bị đan dược hỗ trợ trước khi thử."
        ),
        inline=False,
    )
    embed.add_field(
        name="💪 Realm Power",
        value=(
            f"Mỗi bậc tu luyện cộng dồn cộng **+{REALM_POWER_BONUS_PER_STAGE * 100:.1f}%** "
            "sát thương cuối. Tu càng cao, mỗi đòn càng nặng."
        ),
        inline=False,
    )
    return embed


def _chapter_combat() -> discord.Embed:
    embed = base_embed(
        "⚔️ Chiến Đấu",
        (
            "Combat theo lượt, dựa hoàn toàn trên kỹ năng (skill). "
            "Không có ATK/DEF mặc định — sát thương đến từ kỹ năng học được."
        ),
        color=0xE74C3C,
    )
    embed.add_field(
        name="🧮 Công Thức Sát Thương",
        value=(
            "```\n"
            "Raw  = (BaseSkill + MPCost + ATK·s_atk + MATK·s_matk)\n"
            "       × variance(0.85–1.15)\n"
            "DMG  = Raw\n"
            "       × CritMult            (nếu bạo kích)\n"
            "       × (1 − PhysReduction) (chỉ vật công)\n"
            "       × (1 − ElemRes)       (nếu cùng hệ)\n"
            "       × (1 + FinalDmgBonus)\n"
            "```\n"
            "**`s_atk` / `s_matk`** = hệ số scale của skill với ATK / MATK (tuỳ skill).\n"
            "**`FinalDmgBonus`** cộng dồn từ Realm Power, trang bị, buff, Linh Căn, Thể Chất.\n"
            "Đường ống: **Né Tránh → Roll → Bạo Kích → Phòng Thủ → Nguyên Tố → Bonus Cuối**."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎲 Hệ Thống Rating",
        value=(
            f"```\n% = Rating / (Rating + {RATING_K})\n```\n"
            "Áp dụng cho: 💥 Bạo Kích · 💥 Sát Thương Bạo · 🌀 Né Tránh · 🛡️ Kháng Bạo.\n"
            f"Ví dụ: 1000 Crit Rating ≈ **{1000 / (1000 + RATING_K) * 100:.1f}%** bạo kích "
            "(trên nền 5%)."
        ),
        inline=False,
    )
    embed.add_field(
        name="📐 Trần Quan Trọng",
        value=(
            f"• Bạo Kích tối đa: **{MAX_CRIT_CHANCE * 100:.0f}%**\n"
            f"• Giảm Sát Thương Cuối tối đa: **{MAX_FINAL_DMG_REDUCE * 100:.0f}%**\n"
            f"• Giảm Sát Thương Vật Lý (DEF) tối đa: **{MAX_PHYS_REDUCTION * 100:.0f}%**\n"
            f"• Sát Thương Chân Thực / đòn: **{TRUE_DMG_PCT_CAP * 100:.0f}%** HP tối đa\n"
            f"• Hồn Phệ (Âm) / trận: **{SOUL_DRAIN_CAP_PCT * 100:.0f}%** HP gốc\n"
            f"• Cướp Chỉ Số (Âm) / chỉ số: **{STAT_STEAL_CAP_PCT * 100:.0f}%**\n"
            f"• Bạo Kích Hồi Máu (Mộc/Quang): **{HEAL_CRIT_CHANCE * 100:.0f}%** chance, "
            f"x{HEAL_CRIT_MULT}"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚡ Tốc Độ & Lượt Đánh",
        value=(
            "Đánh trước thuộc về SPD cao hơn. Chênh SPD lớn → có cơ hội đánh thêm lượt phụ. "
            "SPD vượt baseline cũng cộng vào Né Tránh — luôn là lá chắn phòng thủ."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 Kiểu Skill",
        value=(
            "**Thiên** — Tấn công\n"
            "**Địa** — Phòng thủ\n"
            "**Nhân** — Hỗ trợ / khống chế\n"
            "**Trận Pháp** — Buff toàn cục, tốn MP duy trì."
        ),
        inline=False,
    )
    return embed


def _chapter_linh_can() -> discord.Embed:
    embed = base_embed(
        "🌿 Linh Căn (Spiritual Roots)",
        (
            "Mỗi nhân vật được random 1-3 Linh Căn khi tạo. "
            "Linh Căn cho **passive bonus** + **proc kỹ năng nguyên tố** trong combat."
        ),
        color=0x9B59B6,
    )

    elem_lines = []
    for key in ALL_LINH_CAN:
        d = LINH_CAN_DATA[key]
        elem_lines.append(f"{d['emoji']} **{d['vi']}** — {d.get('description', '')}")
    embed.add_field(
        name=f"🔱 {len(ALL_LINH_CAN)} Hệ Linh Căn",
        value="\n".join(elem_lines),
        inline=False,
    )
    embed.add_field(
        name="📈 Nâng Cấp",
        value=(
            f"Mỗi Linh Căn đã sở hữu có thể nâng từ Lv1 → Lv{LINH_CAN_MAX_LEVEL}. "
            "Cấp tối đa hiện thời = **cảnh giới Luyện Khí + 1** — ép bạn đầu tư trục Khí "
            "nếu muốn unlock effect cao cấp.\n\n"
            "Mỗi Linh Căn có các **mốc unlock** ở Lv3/5/7/9 — mỗi mốc thêm hiệu ứng mới "
            "(crit, on-hit proc, miễn nhiễm…)."
        ),
        inline=False,
    )
    embed.add_field(
        name="🌌 Khí Tu Cộng Hưởng",
        value=(
            "Nếu Luyện Khí > Luyện Thể & Trận Đạo (Khí Tu), mọi Linh Căn ≥ Lv5 sẽ "
            "**khuếch đại** passive theo số lượng. Đa hệ là lựa chọn của Khí Tu."
        ),
        inline=False,
    )
    embed.add_field(
        name="📦 Nguồn Nguyên Liệu",
        value=(
            "Linh Mạch Bí Cảnh (`/dungeon` chọn loại Linh Căn) là nguồn rớt chính. "
            "Có thể giao dịch ngọc/linh mạch qua `/market` hoặc `/trade` trực tiếp."
        ),
        inline=False,
    )
    return embed


def _chapter_constitution() -> discord.Embed:
    embed = base_embed(
        "🧬 Thể Chất (Constitution)",
        (
            "**Thể Chất** là đặc tính bẩm sinh, được **rút ngẫu nhiên** khi tạo nhân vật. "
            "Pool roll lọc theo Linh Căn của bạn (universal + cùng hệ); "
            "Có thể đổi Thể Chất bằng cách **kích hoạt** một bộ "
            "khác sau này."
        ),
        color=0xE67E22,
    )
    embed.add_field(
        name="📚 Bộ Thể Chất",
        value=(
            "**8 Bát Quái nguyên tố** + **Thái Dương** + **Thái Âm** + **Vạn Tượng** "
            "+ **Hỗn Độn Đạo Thể** (endgame).\n\n"
            f"Mỗi Thể Chất có 5 mức hiếm: {emojis.for_rarity('common')} Phổ Thông → "
            f"{emojis.for_rarity('uncommon')} Khá → "
            f"{emojis.for_rarity('rare')} Quý → "
            f"{emojis.for_rarity('epic')} Sử Thi → "
            f"{emojis.for_rarity('legendary')} Truyền Thuyết. "
            "Bậc càng cao, buff càng mạnh nhưng tỷ lệ kích hoạt thấp."
        ),
        inline=False,
    )
    embed.add_field(
        name="💠 Thể Tu Đa Slot",
        value=(
            "Người bình thường giữ **1** Thể Chất. **Thể Tu** "
            "(Luyện Thể ≥ Luyện Khí và Trận Đạo) mở thêm **1 slot / cảnh giới Thể** "
            "theo công thức `1 + body_realm` (tối đa **8** slot). Mỗi slot là một "
            "Thể Chất riêng, buff cộng dồn."
        ),
        inline=False,
    )
    embed.add_field(
        name="🔓 Đạo Thể (Body 9)",
        value=(
            "Đột phá Luyện Thể đến **Nhập Thánh Cấp 9** mở khoá **Đạo Thể**. "
            "Đây là gate cho các Thể Chất đặc biệt:\n"
            "• **Thái Dương / Thái Âm** — yêu cầu Đạo Thể.\n"
            "• **Hỗn Độn Đạo Thể** — slot thứ 9, yêu cầu Đạo Thể + tất cả 8 slot "
            "đang giữ Thể Chất Truyền Thuyết."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎲 Tỷ Lệ Kích Hoạt",
        value=(
            "Mỗi lần thử kích hoạt cần **Đạo Cốt Tinh** + Công Đức + nguyên liệu riêng. "
            "Thất bại vẫn tiêu nguyên liệu (gacha-style). Thể Tu được **+20%** chance."
        ),
        inline=False,
    )
    return embed


def _chapter_formation() -> discord.Embed:
    from src.db.models.formation import FORMATION_GEM_SLOTS

    embed = base_embed(
        "🔯 Trận Pháp & Ngọc Khảm",
        (
            "Trận Pháp là buff toàn cục — kích hoạt tốn **% MP duy trì**. "
            "Mỗi trận pháp có ô khảm để gắn ngọc nguyên tố."
        ),
        color=0x1ABC9C,
    )
    embed.add_field(
        name="📜 10 Trận Pháp",
        value=(
            "**8 Nhất Nguyên** (1 trận / hệ nguyên tố) + "
            "**Kiếm Trận** + **Cửu Cung Bát Quái Trận**.\n\n"
            "Đổi trận: `/formation <key>`. Trận Tu (formation > body & qi) có thể giữ "
            "nhiều trận đồng thời."
        ),
        inline=False,
    )
    embed.add_field(
        name=f"💎 Khảm Ngọc — {FORMATION_GEM_SLOTS} Ô / Trận",
        value=(
            f"Mỗi trận có **{FORMATION_GEM_SLOTS} ô** khảm. Ngọc cùng hệ với trận sẽ "
            "kích hoạt thêm threshold bonus theo số ô khảm.\n"
            "Khảm bằng `/inlay <slot> <gem>`."
        ),
        inline=False,
    )

    gem_lines = []
    for elem, bonuses in GEM_ELEMENT_BASE_BONUS.items():
        d = LINH_CAN_DATA.get(elem, {})
        emoji = d.get("emoji", "💎")
        vi = d.get("vi", elem.title())
        bonus_strs = []
        for stat, val in bonuses.items():
            if "pct" in stat or "bonus" in stat and isinstance(val, float) and val < 1:
                bonus_strs.append(f"{stat} +{val * 100:.2f}%")
            else:
                bonus_strs.append(f"{stat} +{val}")
        gem_lines.append(f"{emoji} **{vi}** — {', '.join(bonus_strs)} / ngọc")
    embed.add_field(
        name="🪨 Bonus Theo Hệ Ngọc (mỗi viên × cấp ngọc)",
        value="\n".join(gem_lines),
        inline=False,
    )
    embed.add_field(
        name="🔒 Khoá MP",
        value=(
            f"Trận pháp tốn **% MP** để duy trì (gồm phí kỹ năng trận + phí mỗi viên ngọc). "
            f"Trần khoá: **{FORMATION_MAX_RESERVE_PCT * 100:.0f}%** MP. "
            "Tu Trận Đạo càng cao, chi phí khoá càng giảm — endgame chỉ còn ~30%."
        ),
        inline=False,
    )
    embed.add_field(
        name="🏆 Lộ Trình Tinh Thông",
        value="Chân Nhân → Chân Quân → Tiên Tôn → **Đạo Tổ** (max).",
        inline=False,
    )
    return embed


def _chapter_currency() -> discord.Embed:
    embed = base_embed(
        "💰 Tiền Tệ & Nghiệp Lực",
        f"Một ngày có **{TURNS_PER_DAY:,}** lượt — tiền nhận tuỳ giai đoạn.",
        color=0xF1C40F,
    )
    embed.add_field(
        name="📅 Cơ Chế Lượt",
        value=(
            f"• **{BONUS_TURNS:,} lượt đầu / ngày**: nhận **{MERIT_PER_BONUS_TURN}× Công Đức**, "
            "**0 Nghiệp Lực** — incentive đăng nhập hằng ngày.\n"
            f"• Lượt còn lại: **{MERIT_PER_NORMAL_TURN}** Công Đức + "
            f"**{KARMA_PER_NORMAL_TURN}** Nghiệp Lực Tích Lũy / lượt."
        ),
        inline=False,
    )
    embed.add_field(
        name=f"{emojis.for_currency('merit')} Công Đức (Merit)",
        value="Tiêu chính: đột phá, học skill, mua shop, kích hoạt Thể Chất.",
        inline=True,
    )
    embed.add_field(
        name=f"{emojis.for_currency('karma_accum')} Nghiệp Lực (Karma)",
        value=(
            f"Hai pool:\n"
            f"• **Tích Lũy** — chỉ tăng (cap **{KARMA_ACCUM_CAP:,}**), unlock title ác.\n"
            f"• **Khả Dụng** — tiêu được ở Quỷ Thị (Dark Market)."
        ),
        inline=True,
    )
    embed.add_field(
        name=f"{emojis.for_currency('primordial_stones')} Hỗn Nguyên Thạch",
        value="Premium currency — chỉ rơi từ legendary boss. Dùng ở chợ Thiên Phẩm.",
        inline=True,
    )

    merit_lines = [
        f"• **{thr:,}** → `{key}`"
        for thr, key in sorted(MERIT_TITLE_THRESHOLDS.items())
    ]
    karma_lines = [
        f"• **{thr:,}** → `{key}`"
        for thr, key in sorted(KARMA_TITLE_THRESHOLDS.items())
    ]
    embed.add_field(
        name="👑 Tước Hiệu Thiện (Công Đức)",
        value="\n".join(merit_lines),
        inline=True,
    )
    embed.add_field(
        name="💀 Tước Hiệu Ác (Nghiệp Lực)",
        value="\n".join(karma_lines),
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=True)  # spacer
    return embed


def _chapter_items() -> discord.Embed:
    embed = base_embed(
        "🎒 Vật Phẩm & Cấp Bậc",
        "Tất cả vật phẩm được phân theo cấp bậc và kiểu sử dụng.",
        color=0x95A5A6,
    )
    grade_lines = []
    for g in (Grade.HOANG, Grade.HUYEN, Grade.DIA, Grade.THIEN):
        vi, en = GRADE_LABELS[g]
        grade_lines.append(f"**{int(g)}.** {vi} _({en})_")
    embed.add_field(
        name="📊 Cấp Bậc Vật Phẩm (thấp → cao)",
        value="\n".join(grade_lines),
        inline=False,
    )
    embed.add_field(
        name="📦 Loại Vật Phẩm",
        value=(
            "📜 **Ngọc Giản (Scrolls)** — duy nhất để học kỹ năng (`/learn`).\n"
            "🗡️ **Pháp Bảo (Artifacts)** — 3 slot: Kiếm / Giáp / Pháp Bảo.\n"
            "💊 **Đan Dược (Elixirs)** — hồi HP/MP, buff, giảm Nghiệp.\n"
            "💎 **Ngọc Khảm** — 9 hệ + đặc biệt, gắn vào trận pháp.\n"
            "🪨 **Nguyên Liệu** — Đạo Cốt Tinh, Linh Mạch, ore, herb…\n"
            "🎁 **Rương** — `/use` để mở, ra vật phẩm random."
        ),
        inline=False,
    )
    embed.add_field(
        name="📤 Giao Dịch P2P",
        value=(
            f"`/market` — niêm yết vật phẩm cùng cấp bậc, phí **{TRADE_FEE_RATE * 100:.0f}%** "
            "chống lạm phát. "
            f"Mỗi người tối đa **{MARKET_MAX_LISTINGS}** listing, hết hạn sau "
            f"**{MARKET_LISTING_HOURS}h**.\n"
            "`/trade` — trao đổi trực tiếp 1-1, không qua chợ."
        ),
        inline=False,
    )
    embed.add_field(
        name="🏪 Shop",
        value=(
            "**Đạo Thương** — fixed shop + 6h rotating slots + khu Hỗn Nguyên Thạch.\n"
            "**Quỷ Thị** — Dark Market, hàng độc, FOMO 4–8h reset, dùng Nghiệp Khả Dụng."
        ),
        inline=False,
    )
    return embed


def _chapter_skills() -> discord.Embed:
    embed = base_embed(
        "🎯 Kỹ Năng & Tàng Kinh Các",
        "Nhân vật có **6 slot kỹ năng** để mang vào combat.",
        color=0x8E44AD,
    )
    embed.add_field(
        name="📜 Học Kỹ Năng",
        value=(
            "Cách **duy nhất** học là dùng **Ngọc Giản (scroll)**:\n"
            "`/learn <scroll> <skill> [slot]`\n\n"
            "Ngọc Giản kiếm từ shop, drop bí cảnh, market, trade. "
            "Học xong, scroll bị tiêu — không hoàn lại."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎰 4 Loại Kỹ Năng",
        value=(
            "**Thiên** — Tấn công đơn / AoE.\n"
            "**Địa** — Phòng thủ, shield, hồi máu, miễn nhiễm.\n"
            "**Nhân** — Hỗ trợ / CC: stun, freeze, silence, slow.\n"
            "**Trận Pháp** — Buff toàn cục cho team, MP-reservation."
        ),
        inline=False,
    )
    embed.add_field(
        name="🌿 Linh Căn Là Gating",
        value=(
            "Mỗi skill có **yêu cầu Linh Căn** (ví dụ skill Hỏa cần Hỏa Linh Căn ≥ Lv2). "
            "Mở Tàng Kinh Các (`/status` → 📚) để xem **mọi skill bạn đủ điều kiện học**, "
            "tô sáng những skill có scroll trong túi."
        ),
        inline=False,
    )
    embed.add_field(
        name="🔄 Đổi Slot",
        value=(
            "`/forget <slot>` — gỡ skill khỏi 1 trong 6 ô.\n"
            "Học lại bằng `/learn` nhưng cần scroll mới — không hoàn skill cũ."
        ),
        inline=False,
    )
    return embed


# Order matters — controls dropdown order.
_CHAPTERS: list[tuple[str, str, str, Callable[[], discord.Embed]]] = [
    ("overview",     "Tổng Quan",                 "📖", _chapter_overview),
    ("cultivation",  "Tu Luyện & Đột Phá",        "🌀", _chapter_cultivation),
    ("combat",       "Chiến Đấu",                 "⚔️", _chapter_combat),
    ("linh_can",     "Linh Căn",                  "🌿", _chapter_linh_can),
    ("constitution", "Thể Chất",                  "🧬", _chapter_constitution),
    ("formation",    "Trận Pháp & Ngọc Khảm",     "🔯", _chapter_formation),
    ("currency",     "Tiền Tệ & Nghiệp Lực",      "💰", _chapter_currency),
    ("items",        "Vật Phẩm & Giao Dịch",      "🎒", _chapter_items),
    ("skills",       "Kỹ Năng & Tàng Kinh",       "🎯", _chapter_skills),
]


def _build_chapter_embed(key: str) -> discord.Embed:
    for k, _vi, _emoji, builder in _CHAPTERS:
        if k == key:
            return builder()
    return _chapter_overview()


# ── View ─────────────────────────────────────────────────────────────────────

class HandbookView(discord.ui.View):
    """Single-screen handbook navigation: dropdown of chapters + back button."""

    def __init__(self, discord_id: int, current_key: str, back_fn: BackFn | None) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._current = current_key
        self._back_fn = back_fn

        select = discord.ui.Select(
            placeholder="📖 Chọn chương để đọc…",
            min_values=1,
            max_values=1,
            row=0,
        )
        for key, vi, emoji, _builder in _CHAPTERS:
            select.add_option(
                label=vi,
                value=key,
                emoji=emoji,
                default=(key == current_key),
            )
        select.callback = self._on_select
        self.add_item(select)

        if back_fn is not None:
            back_btn = discord.ui.Button(
                label="◀ Quay Lại",
                style=discord.ButtonStyle.danger,
                row=1,
            )
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        # Select.values is the chosen list — we set max_values=1 so take first.
        chosen = interaction.data["values"][0] if interaction.data else self._current  # type: ignore[index]
        embed = _build_chapter_embed(chosen)
        view = HandbookView(self._discord_id, chosen, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        if self._back_fn is not None:
            await self._back_fn(interaction)


# ── Hub entry points ─────────────────────────────────────────────────────────

async def render_handbook_hub(
    interaction: discord.Interaction,
    discord_id: int,
    *,
    back_fn: BackFn | None = None,
) -> None:
    """Open / refresh the handbook in the current message.

    Caller must have already deferred (uses ``edit_original_response``).
    Pass ``back_fn`` from the status cog to enable the ◀ Quay Lại button.
    """
    embed = _build_chapter_embed("overview")
    view = HandbookView(discord_id, "overview", back_fn)
    await interaction.edit_original_response(embed=embed, view=view)


# ── Cog (slash command) ─────────────────────────────────────────────────────

class HandbookCog(commands.Cog, name="Handbook"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="camnang",
        description="Mở Cẩm Nang — hướng dẫn cơ chế và tính năng",
    )
    async def camnang(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        embed = _build_chapter_embed("overview")
        view = HandbookView(interaction.user.id, "overview", back_fn=None)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HandbookCog(bot))
