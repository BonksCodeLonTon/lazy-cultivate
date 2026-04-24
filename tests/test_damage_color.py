"""Tests for the element-aware damage-coloring helper."""
from __future__ import annotations

from src.game.engine.damage.color import (
    ELEMENT_ANSI,
    TRUE_DMG_ANSI,
    PHYSICAL_ANSI,
    colorize_damage,
    to_ansi_block,
)

_ESC = "\x1b"
_RESET = f"{_ESC}[0m"
_BOLD = f"{_ESC}[1m"


def test_colorize_fire_damage_wraps_with_red_and_bold():
    s = colorize_damage("-1,234 HP", "hoa")
    assert s.startswith(_BOLD + ELEMENT_ANSI["hoa"])
    assert s.endswith(_RESET)
    assert "-1,234 HP" in s


def test_colorize_physical_damage_when_no_element():
    s = colorize_damage("-42 HP", None)
    assert PHYSICAL_ANSI in s
    assert _RESET in s


def test_colorize_true_damage_uses_bold_yellow():
    s = colorize_damage("-500 HP", None, true_dmg=True)
    assert TRUE_DMG_ANSI in s
    # true_dmg=True shouldn't double-bold — the TRUE_DMG_ANSI sequence
    # already bakes bold (1;33m) so we skip the extra bold prefix.
    assert not s.startswith(_BOLD + TRUE_DMG_ANSI)


def test_colorize_unknown_element_falls_back_to_physical():
    s = colorize_damage("-1 HP", "bogus_element")
    assert PHYSICAL_ANSI in s


def test_to_ansi_block_wraps_and_converts_markdown():
    out = to_ansi_block("**Actor** dùng *Skill* → -100 HP")
    assert out.startswith("```ansi\n")
    assert out.endswith("\n```")
    # Markdown bold asterisks stripped and replaced by ANSI bold
    assert "**Actor**" not in out
    assert f"{_BOLD}Actor{_RESET}" in out


def test_to_ansi_block_handles_empty_input():
    assert to_ansi_block("") == ""
    assert to_ansi_block("   ") == "   "


def test_all_nine_elements_have_palette_entries():
    for elem in ("kim", "moc", "thuy", "hoa", "tho",
                 "loi", "phong", "am", "quang"):
        assert elem in ELEMENT_ANSI, f"missing ANSI entry for {elem}"
