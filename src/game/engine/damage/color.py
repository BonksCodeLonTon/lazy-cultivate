"""Element-aware damage coloring for combat logs.

Discord renders ANSI escape codes inside ``ansi``-tagged code blocks — that
is the only built-in way to put colored text in a Discord embed. We use it
to tag damage numbers with their element, so a Hỏa skill paints its damage
red, a Thủy skill paints it blue, etc.

Public API
----------
- ``ELEMENT_ANSI``      : element key → ANSI sequence
- ``colorize_damage``   : wrap a damage substring with element color + reset
- ``to_ansi_block``     : wrap a full log block in an ``ansi`` fence and
                          convert Markdown bold/italic to ANSI equivalents so
                          existing combat logs don't lose emphasis inside
                          the code block.

The combat engine only needs ``colorize_damage``; the Discord cog calls
``to_ansi_block`` once per embed chunk.
"""
from __future__ import annotations

import re

from src.game.constants.elements import Element

# ── ANSI palette (Discord-supported 30-37 foreground codes) ──────────────
_ESC = "\x1b"
_RESET = f"{_ESC}[0m"
_BOLD = f"{_ESC}[1m"

# Element → ANSI foreground code. Colors picked to be readable on Discord's
# dark/light themes and evocative of each element. Keyed by ``Element`` so
# adding a 10th element forces every entry to be filled in here.
ELEMENT_ANSI: dict[Element, str] = {
    Element.HOA:   f"{_ESC}[31m",   # red — fire
    Element.MOC:   f"{_ESC}[32m",   # green — wood
    Element.THUY:  f"{_ESC}[34m",   # blue — water
    Element.KIM:   f"{_ESC}[33m",   # yellow — gold/metal
    Element.THO:   f"{_ESC}[33m",   # yellow — earth (shares with kim, adequate)
    Element.LOI:   f"{_ESC}[35m",   # pink — lightning
    Element.PHONG: f"{_ESC}[36m",   # cyan — wind
    Element.AM:    f"{_ESC}[30m",   # gray — shadow
    Element.QUANG: f"{_ESC}[37m",   # white — light
}
assert set(ELEMENT_ANSI) == set(Element), "ELEMENT_ANSI missing element entries"
# "physical" (non-elemental) and "true" (Chân Thương) damage lanes.
PHYSICAL_ANSI = f"{_ESC}[37m"     # plain white — generic
TRUE_DMG_ANSI = f"{_ESC}[1;33m"   # bold yellow — unblockable marker


def colorize_damage(dmg_text: str, element: str | None, *,
                    bold: bool = True, true_dmg: bool = False) -> str:
    """Wrap ``dmg_text`` (e.g. ``"-1,234 HP"``) in an ANSI color sequence.

    ``element`` is the attack's element key (``hoa``/``kim``/…) or ``None``
    for physical. ``true_dmg=True`` overrides the element palette with a
    bold-yellow tag to mark Chân Thương unblockable damage.
    """
    if true_dmg:
        prefix = TRUE_DMG_ANSI
    elif element and element in ELEMENT_ANSI:
        prefix = ELEMENT_ANSI[element]
    else:
        prefix = PHYSICAL_ANSI
    if bold and not true_dmg:
        prefix = _BOLD + prefix
    return f"{prefix}{dmg_text}{_RESET}"


# Discord's ansi block strips markdown, so ``**actor.name**`` renders with
# literal asterisks. Convert them to ANSI bold when wrapping a log chunk.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)


def to_ansi_block(log_text: str) -> str:
    """Wrap a combat-log chunk in ```ansi``` so colors render in Discord.

    Converts Markdown bold/italic to ANSI equivalents so existing combat
    formatting survives the code block (which otherwise prints asterisks
    literally). Returns the original text unchanged if empty.
    """
    if not log_text.strip():
        return log_text
    converted = _MD_BOLD_RE.sub(lambda m: f"{_BOLD}{m.group(1)}{_RESET}", log_text)
    # ansi code block has no italic — use underline as the closest signal.
    converted = _MD_ITALIC_RE.sub(
        lambda m: f"{_ESC}[4m{m.group(1)}{_RESET}", converted,
    )
    return f"```ansi\n{converted}\n```"
