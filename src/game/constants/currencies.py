"""Currency system constants."""

CURRENCY_CAP = 10_000_000

# Turn system
TURNS_PER_DAY = 1440
BONUS_TURNS = 440           # First 440 turns: merit ×2, 0 karma
MERIT_PER_BONUS_TURN = 2
MERIT_PER_NORMAL_TURN = 3
KARMA_PER_NORMAL_TURN = 7  # Accumulated karma per normal turn

KARMA_ACCUM_CAP = 500_000

# Thresholds for evil titles (Nghiệp Lực Tích Lũy)
KARMA_TITLE_THRESHOLDS = {
    100_000: "van_ac_bat_xa",    # Vạn Ác Bất Xá
    200_000: "vo_gian",          # Vô Gian
    350_000: "cuu_u_ma_ton",     # Cửu U Ma Tôn
    500_000: "diet_the_ma_than", # Diệt Thế Ma Thần (+20% FinalDmg)
}

MERIT_TITLE_THRESHOLDS = {
    50_000: "thien_nhan",        # Thiện Nhân
    200_000: "thanh_nhan",       # Thanh Nhân
    500_000: "tien_nhan",        # Tiên Nhân
    1_000_000: "chung_dao",      # Chứng Đạo Thành Tiên
}

# ── Cultivation progression rates ────────────────────────────────────────────
# Turns needed to gain 1 level on each axis.
# At 1440 turns/day, target ~6 months to max one axis (81 levels).
# body/qi: 259_200 total turns / 81 ≈ 3_200 turns/level
# formation: half the time (1_600) but each level costs Công Đức
TURNS_PER_CULT_LEVEL: dict[str, int] = {
    "body":      720,   # ~12 hrs/level → ~40 days to max axis
    "qi":        720,
    "formation": 360,   # ~6 hrs/level (faster but costs Công Đức per level)
}

# Công Đức cost per level gained on Trận Đạo axis
# Scales with current realm: base × (realm_index + 1)
FORMATION_MERIT_COST_BASE = 1_000

# Shop special item
CELESTIAL_DAO_COST = 99_000    # Thiên Đạo Phù Nghịch (×2 merit 30 days)

# P2P trade fee
TRADE_FEE_RATE = 0.10
MARKET_MAX_LISTINGS = 5
MARKET_LISTING_HOURS = 72
