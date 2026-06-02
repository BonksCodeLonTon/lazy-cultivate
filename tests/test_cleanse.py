"""Cleanse mechanic tests — Quang Thanh Tẩy + ``EffectMeta.cleansable``.

Covers the data-driven cleanse refactor:
  * ``EffectMeta.cleansable`` defaults track the effect's ``kind``
    (DEBUFF / CC → True, BUFF → False) without any per-entry boilerplate.
  * The ``EffectNgungDong`` regression — its key starts with ``Effect``
    instead of ``Debuff``, so the legacy substring filter silently
    skipped it. The kind-based default now catches it.
  * ``try_cleanse`` consults ``meta.cleansable`` (no string filter) and
    picks a *random* cleansable effect (not "first-applied"), so debuff
    application order can no longer be gamed as a buffer.
  * Buffs are never cleansed.
  * The associated ``effect_overrides`` entry is dropped alongside the
    effect itself so re-application starts from the meta default.
"""
from __future__ import annotations

import random
from collections import Counter

import pytest

from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS, EffectKind
from src.game.engine.linh_can_effects.quang import try_cleanse
from src.game.systems.combatant import Combatant


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_quang_actor(level: int = 9, force_chance: float = 0.95) -> Combatant:
    """Build a dummy combatant with maxed Quang so ``try_cleanse`` always fires.

    ``cleanse_on_turn_pct`` stacks additively on top of the base 15 % chance
    and the per-level bonus, so 0.95 effectively guarantees a cleanse roll
    succeeds in tests (chance is clamped at the gate via < comparison).
    """
    c = Combatant(
        key="t", name="T", hp=10_000, hp_max=10_000,
        mp=200, mp_max=200, spd=10, element=None, atk=100, matk=100,
    )
    c.linh_can = ["quang"]
    c.linh_can_levels = {"quang": level}
    c.cleanse_on_turn_pct = force_chance
    return c


# ── 1. Meta-default invariants ────────────────────────────────────────────────

class TestCleansableDefaults:
    """``EffectMeta.cleansable`` must follow the kind-based rule for every
    entry in the registry — without any per-effect boilerplate. Exceptions
    are intentional opt-outs (e.g. ``DebuffTanDiet`` is permanent by design)
    and are listed in ``_UNCLEANSABLE_DEBUFF_EXCEPTIONS``.
    """

    # Debuffs / CCs that intentionally set ``cleansable=False`` so they
    # can't be removed by Quang Thanh Tẩy. Adding to this set requires a
    # design rationale (i.e. the effect represents a permanent / structural
    # change rather than a clearable status).
    _UNCLEANSABLE_DEBUFF_EXCEPTIONS = {
        EffectKey.DEBUFF_TAN_DIET.value,  # Tận Diệt — irreversible HP-max shrink
        EffectKey.DEBUFF_THIEN_MA_POST.value,  # Thiên Ma Hậu Di Chứng — locked into the auto-cycle, can't be cleansed away
        EffectKey.DEBUFF_NGHIEP_HOA_HONG_LIEN.value,  # Hồng Liên Nghiệp Hỏa — karmic mark, "không thể giải trừ" by design
        EffectKey.DEBUFF_PHONG_DO_MA.value,  # Phong Đô Ma Khí — formation aura, lives as long as the trận is active
        EffectKey.DEBUFF_XICH_LUYEN_TOA_HON.value,  # Xích Luyện Tỏa Hồn — formation aura, lives as long as the trận is active
        EffectKey.DEBUFF_THO_NGUYEN_TRAN_MA.value,  # Thổ Nguyên Trấn Ma — Hộ Pháp Trận aura, lives as long as the trận is active
        "DebuffKimPheGiap",  # Phệ Giáp — armor devoured by Kim Phệ Giáp, "không thể giải" structural -25% DEF for the fight
    }

    def test_every_effect_has_correct_default_for_its_kind(self):
        for key, meta in EFFECTS.items():
            if meta.kind == EffectKind.BUFF:
                assert meta.cleansable is False, (
                    f"Buff {key} unexpectedly has cleansable=True"
                )
            elif key in self._UNCLEANSABLE_DEBUFF_EXCEPTIONS:
                assert meta.cleansable is False, (
                    f"{meta.kind.value} {key} listed as exception "
                    f"but has cleansable=True"
                )
            else:  # DEBUFF or CC
                assert meta.cleansable is True, (
                    f"{meta.kind.value} {key} unexpectedly has cleansable=False "
                    f"(if intentional, add it to _UNCLEANSABLE_DEBUFF_EXCEPTIONS)"
                )

    def test_every_effectkey_enum_entry_has_a_meta(self):
        # Sister invariant: the registry covers the enum (regression for the
        # earlier audit that found HpRegen / MpRegen missing).
        for key in EffectKey:
            assert key.value in EFFECTS, f"EffectKey.{key.name} missing from EFFECTS"


# ── 2. The original bug: EffectNgungDong now cleansable ───────────────────────

class TestEffectNgungDongRegression:
    """The Thuy Linh Căn debuff ``EffectNgungDong`` (Ngưng Đọng / Stagnation,
    −25% SPD) used a key starting with ``Effect``, not ``Debuff``. The old
    substring filter silently skipped it; the new flag must catch it.
    """

    def test_meta_marks_it_cleansable(self):
        assert EFFECTS["EffectNgungDong"].cleansable is True

    def test_buff_bat_tu_remains_non_cleansable(self):
        # Spot-check: a one-turn protective buff with an unusual short
        # duration should still NOT be cleansable.
        assert EFFECTS["BuffBatTu"].cleansable is False

    def test_try_cleanse_actually_removes_it(self):
        actor = _make_quang_actor()
        actor.effects = {"EffectNgungDong": 3}
        try_cleanse(actor, random.Random(0), [], opponent=None)
        assert "EffectNgungDong" not in actor.effects


# ── 3. Buff-vs-debuff selection ───────────────────────────────────────────────

class TestCleanseSelection:
    """``try_cleanse`` must pick from cleansable effects only and leave
    buffs alone, regardless of how the effects dict is laid out.
    """

    def test_buffs_are_never_cleansed(self):
        # Run many trials with a buff sitting alongside multiple debuffs;
        # the buff must survive every single trial.
        survived = 0
        trials = 200
        for seed in range(trials):
            actor = _make_quang_actor()
            actor.effects = {
                "BuffKiemKhi": 3,        # not cleansable
                "DebuffThieuDot": 3,     # cleansable
                "EffectNgungDong": 3,    # cleansable
                "DebuffLamCham": 3,      # cleansable
            }
            try_cleanse(actor, random.Random(seed), [], opponent=None)
            if "BuffKiemKhi" in actor.effects:
                survived += 1
        assert survived == trials, (
            f"BuffKiemKhi was cleansed in {trials - survived}/{trials} trials"
        )

    def test_only_one_effect_removed_per_pulse(self):
        # The cleanse contract is "one effect per successful pulse" — we
        # only ever lose exactly one entry from the dict per call.
        actor = _make_quang_actor()
        actor.effects = {
            "DebuffThieuDot": 3,
            "DebuffLamCham": 3,
            "DebuffChayMau": 3,
        }
        before = set(actor.effects)
        try_cleanse(actor, random.Random(0), [], opponent=None)
        after = set(actor.effects)
        assert len(before - after) == 1, (
            f"Expected exactly 1 removal, got {before - after}"
        )

    def test_no_effects_no_op(self):
        # Empty dict → cleanse does nothing, doesn't crash.
        actor = _make_quang_actor()
        actor.effects = {}
        try_cleanse(actor, random.Random(0), [], opponent=None)
        assert actor.effects == {}

    def test_only_buffs_no_debuff_removed(self):
        # All-buff state: cleanse may still roll, but nothing should be
        # removed because no entry is cleansable.
        actor = _make_quang_actor()
        actor.effects = {"BuffKiemKhi": 3, "BuffSinhCo": 3}
        before = dict(actor.effects)
        try_cleanse(actor, random.Random(0), [], opponent=None)
        assert actor.effects == before


# ── 4. Random pick (not "first-applied") ──────────────────────────────────────

class TestCleanseRandomness:
    """The pick is ``rng.choice(cleansable_keys)`` — over many seeds every
    cleansable key must show up at least once and the distribution should
    be roughly even.
    """

    def test_every_cleansable_key_picked_at_least_once(self):
        # 200 trials with 3 cleansable effects → each key should appear
        # at least once with overwhelming probability under a uniform pick.
        keys = ("DebuffThieuDot", "EffectNgungDong", "DebuffLamCham")
        counts: Counter[str] = Counter()
        for seed in range(200):
            actor = _make_quang_actor()
            actor.effects = {k: 3 for k in keys}
            before = set(actor.effects)
            try_cleanse(actor, random.Random(seed), [], opponent=None)
            removed = before - set(actor.effects)
            if removed:
                counts[next(iter(removed))] += 1
        for k in keys:
            assert counts[k] > 0, f"{k} was never picked over 200 seeds"

    def test_distribution_is_roughly_uniform(self):
        # Soft check: each of the three cleansable keys should land in
        # the [20%, 50%] band over a 300-trial sample. Wide bands keep
        # the test stable across reasonable seed batches.
        keys = ("DebuffThieuDot", "EffectNgungDong", "DebuffLamCham")
        counts: Counter[str] = Counter()
        trials = 300
        for seed in range(trials):
            actor = _make_quang_actor()
            actor.effects = {k: 3 for k in keys}
            before = set(actor.effects)
            try_cleanse(actor, random.Random(seed), [], opponent=None)
            removed = before - set(actor.effects)
            if removed:
                counts[next(iter(removed))] += 1
        total = sum(counts.values())
        for k in keys:
            pct = counts[k] / total
            assert 0.20 <= pct <= 0.50, (
                f"{k} picked at {pct:.0%} (expected ~33%, allowed 20–50%) "
                f"— distribution may be biased"
            )


# ── 5. State cleanup on cleanse ───────────────────────────────────────────────

class TestCleanseStateCleanup:
    """The cleanse path must drop the matching ``effect_overrides`` entry
    too — same contract as natural expiry in ``Combatant.tick_effects`` —
    so a re-application later in the fight starts from the meta default.
    """

    def test_effect_overrides_dropped_on_cleanse(self):
        actor = _make_quang_actor()
        actor.effects = {"DebuffThieuDot": 3}
        actor.effect_overrides = {
            "DebuffThieuDot": {"dot_pct": 0.20},  # heavy stamp
        }
        try_cleanse(actor, random.Random(0), [], opponent=None)
        assert "DebuffThieuDot" not in actor.effects
        assert "DebuffThieuDot" not in actor.effect_overrides

    def test_unrelated_overrides_preserved(self):
        # Cleansing one effect must NOT wipe overrides on a different
        # effect that's still active.
        actor = _make_quang_actor()
        # Force the only-cleansable case so the pick is deterministic.
        actor.effects = {"DebuffThieuDot": 3, "DebuffLamCham": 3}
        actor.effect_overrides = {
            "DebuffThieuDot": {"dot_pct": 0.20},
            "DebuffLamCham":  {"stat_bonus": {"spd_pct": -0.50}},
        }
        try_cleanse(actor, random.Random(0), [], opponent=None)
        # Whichever effect survived must still carry its override.
        survivor = next(iter(actor.effects))
        assert survivor in actor.effect_overrides, (
            f"Survivor {survivor} lost its override unexpectedly"
        )
        # The cleansed effect's override must be gone.
        cleansed = "DebuffLamCham" if survivor == "DebuffThieuDot" else "DebuffThieuDot"
        assert cleansed not in actor.effect_overrides


# ── 6. Quang gating ───────────────────────────────────────────────────────────

class TestCleanseGating:
    """``try_cleanse`` is a Quang Linh Căn passive — non-Quang actors
    must short-circuit immediately and not touch any state.
    """

    def test_non_quang_actor_skips_cleanse(self):
        actor = _make_quang_actor()
        actor.linh_can = []          # no quang
        actor.linh_can_levels = {}
        actor.effects = {"DebuffThieuDot": 3}
        try_cleanse(actor, random.Random(0), [], opponent=None)
        # Debuff still present — cleanse never ran.
        assert actor.effects == {"DebuffThieuDot": 3}

    def test_quang_low_chance_can_skip(self):
        # With chance ~0 the cleanse should mostly *not* fire.
        actor = _make_quang_actor(level=1, force_chance=0.0)
        actor.effects = {"DebuffThieuDot": 3}
        # Use a seed that the base 15% chance won't clear.
        # Random(1).random() ≈ 0.134... which IS below 0.15 — so we
        # explicitly need a seed whose first random() > 0.15. Pick one:
        for seed in (3, 5, 7, 9, 11):
            r = random.Random(seed)
            roll = r.random()
            if roll >= 0.15:
                actor.effects = {"DebuffThieuDot": 3}
                try_cleanse(actor, random.Random(seed), [], opponent=None)
                assert actor.effects == {"DebuffThieuDot": 3}, (
                    f"Cleanse fired at seed={seed} with roll={roll}"
                )
                return
        pytest.skip("No usable seed found for the low-chance branch")
