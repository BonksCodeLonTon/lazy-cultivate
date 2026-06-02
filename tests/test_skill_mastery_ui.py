"""Phase 6 tests for the two pure UI helpers in ``src.game.systems.skill_mastery``.

``progress_summary`` and ``breakthrough_preview`` are side-effect-free dict
builders over the locked tables in ``src.game.constants.skill_mastery``. These
tests pin their observable contract (return keys + values); no Discord, no DB.
"""
from __future__ import annotations

from src.game.systems.skill_mastery import breakthrough_preview, progress_summary


# ── progress_summary ──────────────────────────────────────────────────────


def test_progress_summary_band_and_cap():
    # Sơ Khuy band (1-5), visible cap stays at 20.
    early = progress_summary(3, 10)
    assert early["band_vi"] == "Sơ Khuy"
    assert early["cap"] == 20

    # Đại Thành band (11-15).
    mid = progress_summary(13, 0)
    assert mid["band_vi"] == "Đại Thành"

    # At VISIBLE_MAX without the hidden flag: cap stays concealed at 20.
    assert progress_summary(20, 0)["cap"] == 20
    # Hidden flag reveals the 21-25 path: cap becomes MAX_LEVEL 25.
    assert progress_summary(20, 0, hidden_unlocked=True)["cap"] == 25
    # Already past VISIBLE_MAX implies cap 25 even without the flag.
    assert progress_summary(22, 0)["cap"] == 25


def test_progress_summary_xp_next_and_ratio_within_band():
    # Level 1 needs 20 XP to advance; 10/20 = half-full bar, not a ceiling.
    summary = progress_summary(1, 10)
    assert summary["xp_next"] == 20
    assert summary["ratio"] == 0.5
    assert summary["at_ceiling"] is False
    assert summary["level"] == 1
    assert summary["xp"] == 10


def test_progress_summary_ceiling():
    # Gated ceilings 5/10/15/20: no XP transition exists -> full bar, at_ceiling.
    for level in (5, 10, 15, 20):
        summary = progress_summary(level, 0)
        assert summary["xp_next"] is None, level
        assert summary["at_ceiling"] is True, level
        assert summary["ratio"] == 1.0, level


def test_progress_summary_ratio_clamped():
    # Defensive: xp larger than xp_next must not push the bar past full.
    # Level 1 needs 20 XP; feed 9999 -> ratio clamps to 1.0.
    summary = progress_summary(1, 9999)
    assert summary["ratio"] == 1.0
    assert summary["xp_next"] == 20


# ── breakthrough_preview ──────────────────────────────────────────────────


def test_breakthrough_preview_off_gate():
    # Level 7 is not a band ceiling -> only the at_gate flag is reported.
    assert breakthrough_preview(7, 0, 99, False, False) == {"at_gate": False}


def test_breakthrough_preview_at_gate_fields():
    preview = breakthrough_preview(10, 0, 6, False, False)
    assert preview["at_gate"] is True
    assert preview["gate_item_key"] == "MasteryTamDacNgoc"
    assert preview["required_qty"] == 6
    assert preview["owned_qty"] == 6
    assert preview["has_enough"] is True
    assert preview["success_pct"] == 70
    assert preview["is_hidden"] is False


def test_breakthrough_preview_pity_and_talisman_pct():
    # One prior fail adds pity_per_fail (0.08): 70% -> 78%.
    assert breakthrough_preview(10, 1, 6, False, False)["success_pct"] == 78
    # Hộ Đạo Phù adds 0.20: 70% -> 90%.
    assert breakthrough_preview(10, 0, 6, True, False)["success_pct"] == 90


def test_breakthrough_preview_insufficient():
    # Gate 15 requires 10; owning 3 is not enough.
    preview = breakthrough_preview(15, 0, 3, False, False)
    assert preview["required_qty"] == 10
    assert preview["has_enough"] is False


def test_breakthrough_preview_hidden_gate():
    # The 20->21 secret gate: hidden item, single fruit, 35% base.
    preview = breakthrough_preview(20, 0, 1, False, False)
    assert preview["gate_item_key"] == "MasteryThongThienDaoQua"
    assert preview["required_qty"] == 1
    assert preview["is_hidden"] is True
    assert preview["success_pct"] == 35
