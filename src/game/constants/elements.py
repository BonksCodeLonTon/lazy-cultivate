"""9-element system — mirrors the 9 Linh Căn types."""
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


ELEMENT_LABELS: dict[Element, tuple[str, str]] = {
    Element.KIM:   ("Kim",   "Metal"),
    Element.MOC:   ("Mộc",   "Wood"),
    Element.THUY:  ("Thủy",  "Water"),
    Element.HOA:   ("Hỏa",   "Fire"),
    Element.THO:   ("Thổ",   "Earth"),
    Element.LOI:   ("Lôi",   "Thunder"),
    Element.PHONG: ("Phong", "Wind"),
    Element.QUANG: ("Quang", "Light"),
    Element.AM:    ("Ám",    "Dark"),
}

# Resistance stat keys — map Element → CharacterStats field name
RESISTANCE_KEYS: dict[Element, str] = {
    Element.KIM:   "ResKim",
    Element.MOC:   "ResMoc",
    Element.THUY:  "ResThuy",
    Element.HOA:   "ResHoa",
    Element.THO:   "ResTho",
    Element.LOI:   "ResLoi",
    Element.PHONG: "ResPhong",
    Element.QUANG: "ResQuang",
    Element.AM:    "ResAm",
}
