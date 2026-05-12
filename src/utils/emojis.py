"""Central emoji registry for Discord display.

All emojis surfaced in embeds, buttons, and text live here so:

  • Discord custom emojis (``<:name:id>`` / ``<a:name:id>``) can be added by
    pasting the ID once instead of editing every cog.
  • Unicode fallbacks remain the default — overriding is opt-in per key.
  • Resolvers walk a fallback chain: per-entity key → category → unicode.

Format reminder — Discord custom emoji syntax is plain text:

    "<:gem_kim_1:123456789012345678>"        # static
    "<a:loot_burst:123456789012345678>"      # animated

Just paste those strings into the maps below; no Discord API call needed.
"""
from __future__ import annotations

from typing import Mapping

from src.game.constants.elements import Element
from src.utils import assets

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY-LEVEL FALLBACKS
# These are the defaults when no per-entity override is set. Replacing a value
# here flips the emoji everywhere a category fallback fires.
# ─────────────────────────────────────────────────────────────────────────────

# ── Custom Discord emojis (guild-uploaded) ──────────────────────────────────
# Centralised so the IDs live in one place; the category maps below reference
# these. To rotate or replace an emoji, edit the ID here once.
EMOJI_KIM             = "<:Kim:1500910148980310056>"
EMOJI_MOC             = "<:Moc:1500910154382577705>"
EMOJI_THUY            = "<:Thuy:1500910171323498727>"
EMOJI_HOA             = "<:Hoa:1500910145494974464>"
EMOJI_THO             = "<:Tho:1500910169180213250>"
EMOJI_LOI             = "<:Loi:1500910150674944071>"
EMOJI_PHONG           = "<:Phong:1500910160674160851>"
EMOJI_QUANG           = "<:Quang:1500910162838421504>"
EMOJI_AM              = "<:Am:1500910141586014398>"

EMOJI_CONG_DUC        = "<:CongDuc:1500910143640961104>"
EMOJI_NGHIEP_LUC      = "<:NghiepLuc:1500910156735582258>"
EMOJI_HON_NGUYEN      = "<:HonNguyenThach:1500910147130884207>"

EMOJI_NGOC_GIAN       = "<:NgocGian:1500910158614888509>"
EMOJI_LUYEN_DAN       = "<:LuyenDan:1500910152721633421>"
EMOJI_THIEN_CONG      = "<:ThienCongPhuong:1500910167615864913>"

EMOJI_TRAN_PHAP       = "<:TranPhap:1500910173462462474>"
EMOJI_THE_TU          = "<:Thetu:1500910164813942904>"
EMOJI_TU_DAO          = "<:TuDao:1500910175140319473>"

EMOJI_RARITY_COMMON    = "<:common:1501071691659346040>"
EMOJI_RARITY_UNCOMMON  = "<:uncommon:1501071700786417786>"
EMOJI_RARITY_RARE      = "<:rare:1501071698697523250>"
EMOJI_RARITY_EPIC      = "<:epic:1501071693681004625>"
EMOJI_RARITY_LEGENDARY = "<:legendary:1501071696231399514>"


ITEM_TYPE_EMOJI: dict[str, str] = {
    "forge_material":         EMOJI_THIEN_CONG,
    "constitution_material":  "🪨",
    "super_material":         "💠",
    "gem":                    "💠",
    "scroll":                 EMOJI_NGOC_GIAN,
    "chest":                  "📦",
    "pill":                   EMOJI_LUYEN_DAN,
    "herb":                   "🌿",
    "furnace":                "🏺",
    "special":                "⭐",
    "artifact":               "🗡️",
    "equipment":              "🗡️",
}

SKILL_CATEGORY_EMOJI: dict[str, str] = {
    "attack":    "⚔️",
    "defense":   "🛡️",
    "movement":  "🏃",
    "support":   "💫",
    "passive":   "✨",
    "formation": EMOJI_TRAN_PHAP,
}

ELEMENT_EMOJI: dict[Element, str] = {
    Element.KIM:   EMOJI_KIM,
    Element.MOC:   EMOJI_MOC,
    Element.THUY:  EMOJI_THUY,
    Element.HOA:   EMOJI_HOA,
    Element.THO:   EMOJI_THO,
    Element.LOI:   EMOJI_LOI,
    Element.PHONG: EMOJI_PHONG,
    Element.QUANG: EMOJI_QUANG,
    Element.AM:    EMOJI_AM,
}
assert set(ELEMENT_EMOJI) == set(Element), "ELEMENT_EMOJI missing element entries"

# Item grades reuse the rarity emoji palette: Hoàng < Huyền < Địa < Thiên
# maps to common < uncommon < rare < legendary (epic is skipped).
GRADE_EMOJI: dict[int, str] = {
    1: EMOJI_RARITY_COMMON,
    2: EMOJI_RARITY_UNCOMMON,
    3: EMOJI_RARITY_RARE,
    4: EMOJI_RARITY_LEGENDARY,
}

GRADE_EMOJI_BY_KEY: dict[str, str] = {
    "hoang": EMOJI_RARITY_COMMON,
    "huyen": EMOJI_RARITY_UNCOMMON,
    "dia":   EMOJI_RARITY_RARE,
    "thien": EMOJI_RARITY_LEGENDARY,
}

# Item-level quality (forge / loot roll outcome) reuses the rarity palette,
# keyed by the quality keys used in src/game/engine/quality.py.
QUALITY_EMOJI: dict[str, str] = {
    "hoan":  EMOJI_RARITY_COMMON,
    "huyen": EMOJI_RARITY_UNCOMMON,
    "dia":   EMOJI_RARITY_RARE,
    "thien": EMOJI_RARITY_LEGENDARY,
}

CURRENCY_EMOJI: dict[str, str] = {
    "merit":             EMOJI_CONG_DUC,
    "karma_accum":       EMOJI_NGHIEP_LUC,
    "karma_usable":      EMOJI_NGHIEP_LUC,
    "primordial_stones": EMOJI_HON_NGUYEN,
}

RARITY_EMOJI: dict[str, str] = {
    "common":    EMOJI_RARITY_COMMON,
    "uncommon":  EMOJI_RARITY_UNCOMMON,
    "rare":      EMOJI_RARITY_RARE,
    "epic":      EMOJI_RARITY_EPIC,
    "legendary": EMOJI_RARITY_LEGENDARY,
}

STAT_EMOJI: dict[str, str] = dict(assets.STAT_ICONS)

AXIS_EMOJI: dict[str, str] = {
    "body":      EMOJI_THE_TU,
    "qi":        EMOJI_TU_DAO,
    "formation": EMOJI_TRAN_PHAP,
}

SLOT_EMOJI: dict[str, str] = {
    "weapon":   "🗡️",
    "off_hand": "🛡️",
    "armor":    "🥋",
    "helmet":   "🪖",
    "glove":    "🧤",
    "belt":     "➰",
    "boot":     "🥾",
    "ring":     "💍",
    "amulet":   "📿",
}


# ─────────────────────────────────────────────────────────────────────────────
# PER-ENTITY OVERRIDES
# Add Discord custom-emoji strings here, keyed by the entity's ``key`` field.
# Anything missing falls through to the category fallback above.
# ─────────────────────────────────────────────────────────────────────────────

ITEM_EMOJI: dict[str, str] = {
    # "GemKim_1": "<:gem_kim_1:123456789012345678>",
}

SKILL_EMOJI: dict[str, str] = {
    # "SkillAtkHoa1": "<:fire_blast:123456789012345678>",
}

CONSTITUTION_EMOJI: dict[str, str] = {
    # "ConstitutionHoaDiem_Com": "<:hoa_diem:123456789012345678>",
}

FORMATION_EMOJI: dict[str, str] = {
    # "NhatNguyenKim": "<:nhatnguyen_kim:123456789012345678>",
}

LINH_CAN_EMOJI: dict[Element, str] = dict(ELEMENT_EMOJI)


_FALLBACK = "❓"


# ─────────────────────────────────────────────────────────────────────────────
# RESOLVERS
# Each takes either an entity dict (preferred, single arg) or a bare key.
# Resolution order: per-entity override → category fallback → unicode default.
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_entity(entity: Mapping | str | None) -> tuple[str, Mapping]:
    """Return ``(key, entity_dict)`` for either input form."""
    if entity is None:
        return "", {}
    if isinstance(entity, str):
        return entity, {}
    return entity.get("key", ""), entity


def for_item(entity: Mapping | str | None) -> str:
    """Resolve emoji for an item (registry dict or bare key)."""
    key, data = _coerce_entity(entity)
    if key and key in ITEM_EMOJI:
        return ITEM_EMOJI[key]
    return ITEM_TYPE_EMOJI.get(data.get("type", ""), _FALLBACK)


def for_skill(entity: Mapping | str | None) -> str:
    """Resolve emoji for a skill — key → category → element."""
    key, data = _coerce_entity(entity)
    if key and key in SKILL_EMOJI:
        return SKILL_EMOJI[key]
    if cat := data.get("category"):
        if cat in SKILL_CATEGORY_EMOJI:
            return SKILL_CATEGORY_EMOJI[cat]
    if elem := data.get("element"):
        if elem in ELEMENT_EMOJI:
            return ELEMENT_EMOJI[elem]
    return _FALLBACK


def for_constitution(entity: Mapping | str | None) -> str:
    """Resolve emoji for a constitution — key → element."""
    key, data = _coerce_entity(entity)
    if key and key in CONSTITUTION_EMOJI:
        return CONSTITUTION_EMOJI[key]
    return ELEMENT_EMOJI.get(data.get("element", ""), _FALLBACK)


def for_formation(entity: Mapping | str | None) -> str:
    """Resolve emoji for a formation — key → element."""
    key, data = _coerce_entity(entity)
    if key and key in FORMATION_EMOJI:
        return FORMATION_EMOJI[key]
    return ELEMENT_EMOJI.get(data.get("element", ""), _FALLBACK)


def for_linh_can(element: str) -> str:
    """Resolve emoji for a Linh Căn element."""
    if element in LINH_CAN_EMOJI:
        return LINH_CAN_EMOJI[element]
    return ELEMENT_EMOJI.get(element, _FALLBACK)


def for_element(element: str) -> str:
    """Resolve emoji for an element key (kim/moc/thuy/...)."""
    return ELEMENT_EMOJI.get(element, _FALLBACK)


def for_grade(grade: int | str) -> str:
    """Resolve emoji for an item grade (1..4 numeric or 'hoang'/'huyen'/...)."""
    if isinstance(grade, int):
        return GRADE_EMOJI.get(grade, _FALLBACK)
    return GRADE_EMOJI_BY_KEY.get(grade, _FALLBACK)


def for_quality(quality: str | None) -> str:
    """Resolve emoji for an item quality tier (hoan/huyen/dia/thien)."""
    return QUALITY_EMOJI.get(quality or "hoan", _FALLBACK)


def for_currency(currency: str) -> str:
    """Resolve emoji for a currency key (merit/karma_usable/...)."""
    return CURRENCY_EMOJI.get(currency, "💰")


def for_rarity(rarity: str) -> str:
    """Resolve emoji for a rarity tier (common/uncommon/rare/epic/legendary)."""
    return RARITY_EMOJI.get(rarity, _FALLBACK)


def for_stat(stat: str) -> str:
    """Resolve emoji for a stat key (hp/mp/spd/crit/...)."""
    return STAT_EMOJI.get(stat, "")


def for_axis(axis: str) -> str:
    """Resolve emoji for a cultivation axis (body/qi/formation)."""
    return AXIS_EMOJI.get(axis, _FALLBACK)


def for_slot(slot: str) -> str:
    """Resolve emoji for an equipment slot (weapon/armor/...)."""
    return SLOT_EMOJI.get(slot, _FALLBACK)
