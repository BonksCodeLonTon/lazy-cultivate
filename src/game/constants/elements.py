"""9-element system — mirrors the 9 Linh Căn types.

Single source of truth for element identity, iteration order, Vietnamese
display labels, and the resistance-stat field name on ``CharacterStats``.
``Element`` is a ``StrEnum`` so its members compare and hash equal to the
lowercase string keys used throughout the codebase (and JSON data files),
which means existing ``dict[str, ...]`` lookups keep working unchanged.
"""
from __future__ import annotations

from enum import StrEnum


class Element(StrEnum):
    KIM = "kim"
    MOC = "moc"
    THUY = "thuy"
    HOA = "hoa"
    THO = "tho"
    LOI = "loi"
    PHONG = "phong"
    QUANG = "quang"
    AM = "am"


ALL_ELEMENTS: tuple[Element, ...] = tuple(Element)

ELEMENT_LABELS_VI: dict[Element, str] = {
    Element.KIM:   "Kim",
    Element.MOC:   "Mộc",
    Element.THUY:  "Thủy",
    Element.HOA:   "Hỏa",
    Element.THO:   "Thổ",
    Element.LOI:   "Lôi",
    Element.PHONG: "Phong",
    Element.QUANG: "Quang",
    Element.AM:    "Ám",
}

# Element → CharacterStats resistance field (e.g. ``res_kim``).
RESISTANCE_KEYS: dict[Element, str] = {e: f"res_{e}" for e in Element}
