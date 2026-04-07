"""UI asset constants — emoji and icon mappings for Discord embeds."""
from __future__ import annotations

# ── Element & grade color codes (for Discord embed colors) ───────────────────
ELEMENT_COLORS: dict[str, int] = {
    "kim":   0xFFD700,
    "moc":   0x228B22,
    "thuy":  0x1E90FF,
    "hoa":   0xFF4500,
    "tho":   0xA0522D,
    "loi":   0x9400D3,
    "phong": 0x00FA9A,
    "quang": 0xFFFACD,
    "am":    0x2F0047,
}

GRADE_COLORS: dict[str, int] = {
    "hoang": 0xFFD700,
    "huyen": 0x9400D3,
    "dia":   0x6B8E23,
    "thien": 0xFF4500,
}

# ── Cultivation axes ─────────────────────────────────────────────────────────
AXIS_ICONS: dict[str, str] = {
    "body":      "🗡️",   # Luyện Thể
    "qi":        "🔮",   # Luyện Khí
    "formation": "🔯",   # Trận Đạo
}

AXIS_LABELS: dict[str, str] = {
    "body":      "Luyện Thể",
    "qi":        "Luyện Khí",
    "formation": "Trận Đạo",
}

# ── Elements ──────────────────────────────────────────────────────────────────
ELEMENT_ICONS: dict[str, str] = {
    "kim":   "⚙️",   # Kim — Metal
    "moc":   "🌿",   # Mộc — Wood
    "thuy":  "💧",   # Thủy — Water
    "hoa":   "🔥",   # Hỏa — Fire
    "tho":   "🪨",   # Thổ — Earth
    "loi":   "⚡",   # Lôi — Thunder
    "phong": "🌪️",   # Phong — Wind
    "quang": "✨",   # Quang — Light
    "am":    "🌑",   # Ám — Dark
}

ELEMENT_NAMES_VI: dict[str, str] = {
    "kim":   "Kim",
    "moc":   "Mộc",
    "thuy":  "Thủy",
    "hoa":   "Hỏa",
    "tho":   "Thổ",
    "loi":   "Lôi",
    "phong": "Phong",
    "quang": "Quang",
    "am":    "Ám",
}

# ── Item grades ───────────────────────────────────────────────────────────────
GRADE_ICONS: dict[str, str] = {
    "hoang": "🟡",   # Hoàng — Yellow
    "huyen": "🟣",   # Huyền — Purple
    "dia":   "🟢",   # Địa — Green
    "thien": "🔴",   # Thiên — Red
}

GRADE_NAMES_VI: dict[str, str] = {
    "hoang": "Hoàng",
    "huyen": "Huyền",
    "dia":   "Địa",
    "thien": "Thiên",
}

# ── Stats ─────────────────────────────────────────────────────────────────────
STAT_ICONS: dict[str, str] = {
    "hp":          "❤️",
    "mp":          "💙",
    "spd":         "💨",
    "crit":        "⚔️",
    "crit_dmg":    "💥",
    "evasion":     "🌀",
    "crit_res":    "🛡️",
}

# ── Currencies ────────────────────────────────────────────────────────────────
CURRENCY_ICONS: dict[str, str] = {
    "merit":            "✨",   # Công Đức
    "karma_accum":      "☠️",   # Nghiệp Lực Tích Lũy
    "karma_usable":     "🌑",   # Nghiệp Lực Khả Dụng
    "primordial_stones": "💎",  # Hỗn Nguyên Thạch
}

CURRENCY_NAMES_VI: dict[str, str] = {
    "merit":            "Công Đức",
    "karma_accum":      "Nghiệp Lực (Tích)",
    "karma_usable":     "Nghiệp Lực (Dụng)",
    "primordial_stones": "Hỗn Nguyên Thạch",
}

# ── Realm tier icons (by realm index 0-8) ─────────────────────────────────────
REALM_TIER_ICONS: list[str] = [
    "◽",  # 0 — Sơ nhập
    "◾",  # 1
    "🔹",  # 2
    "🔷",  # 3
    "🌟",  # 4
    "💫",  # 5
    "✨",  # 6
    "🌠",  # 7
    "👑",  # 8 — Đỉnh cảnh
]

# ── Progress bar characters ───────────────────────────────────────────────────
BAR_FILLED = "▓"
BAR_EMPTY  = "░"

# ── Decoration ────────────────────────────────────────────────────────────────
SEPARATOR = "─" * 28

# Embed thumbnail URLs (publicly hosted Xianxia art, safe/no-login images)
# Leave empty to skip thumbnail; set via environment variable if desired.
THUMBNAIL_URL: str = ""

# Embed footer text
FOOTER_TEXT = "Tu Tiên Giới • Cultivation Bot"
FOOTER_ICON  = "https://cdn.discordapp.com/emojis/placeholder.png"  # replace with real icon
