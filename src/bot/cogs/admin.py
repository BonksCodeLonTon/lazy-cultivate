"""Admin commands for development and management."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.skill import CharacterSkill, MAX_SKILL_SLOTS
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.grades import Grade
from src.game.constants.linh_can import (
    ALL_LINH_CAN, LINH_CAN_MAX_LEVEL, format_linh_can, format_linh_can_levels,
    max_linh_can_level,
)
from src.game.systems.the_chat import set_constitutions
from src.utils import emojis
from src.utils.config import settings
from src.utils.embed_builder import error_embed, success_embed
from src.utils.discord_safe import safe_defer

log = logging.getLogger(__name__)


# ── Bot-owner gate ────────────────────────────────────────────────────────────
# Admin slash commands are restricted to the bot owner — NOT every Discord-
# server admin. Two acceptance paths:
#   1. Discord application owner — fetched once via ``application_info`` and
#      cached on the predicate (covers personal bots and team-owned bots
#      where Discord knows the owner).
#   2. ``settings.discord_owner_id`` — explicit override from .env. When
#      non-zero, this user ID also passes. Acts as the canonical fallback
#      when the bot is run by a non-application-owner Discord account.
#
# ``app_commands.default_permissions(administrator=True)`` is kept as a UI
# hint so the command-picker hides commands from server members who lack
# admin in the guild — but the real authentication is the predicate below.

async def _is_bot_owner(interaction: discord.Interaction) -> bool:
    """Predicate that resolves to True iff the invoking user is the bot owner."""
    user_id = interaction.user.id

    # Path 1: explicit override from .env
    if settings.discord_owner_id and user_id == settings.discord_owner_id:
        return True

    client = interaction.client
    # Path 2a: cached application info (populated on bot startup)
    app = getattr(client, "application", None)
    if app is not None and app.owner is not None:
        if user_id == app.owner.id:
            return True
        # Team-owned applications: ``app.team`` lists every team member
        team = getattr(app, "team", None)
        if team is not None and any(m.id == user_id for m in (team.members or [])):
            return True

    # Path 2b: fall back to a fresh fetch (cold start, cache miss)
    try:
        info = await client.application_info()
    except Exception as e:  # noqa: BLE001 — defensive; never block on transient failure
        log.warning("application_info() failed during owner check: %s", e)
        return False
    if info.owner is not None and user_id == info.owner.id:
        return True
    if info.team is not None and any(m.id == user_id for m in (info.team.members or [])):
        return True
    return False


def _owner_only():
    """Decorator that gates a slash command behind ``_is_bot_owner``."""
    return app_commands.check(_is_bot_owner)


# ── Test build presets ────────────────────────────────────────────────────────

# Canonical 8 legendary constitutions that satisfy Hỗn Độn's
# ``requires_all_legendary_equipped`` gate (none carry special_requirements
# that would block the equip).
_LEGENDARY_EIGHT = [
    "ConstitutionPhaTien",
    "ConstitutionHauTo",
    "ConstitutionNgheDinh",
    "ConstitutionTieuDao",
    "ConstitutionKhiHai",
    "ConstitutionBatDiet",
    "ConstitutionTranGioi",
    "ConstitutionThaiCucKim",
]


def _preset_config(preset: str) -> dict:
    """Translate the preset enum into a declarative patch spec consumed by
    ``_apply_testbuild``. Omit keys to leave the current state untouched.
    """
    if preset == "endgame_the_tu":
        return {
            "active_axis": "body",                   # path = Thể Tu (drives slot cap)
            "body_realm": 8, "body_level": 9,
            "qi_realm": 0,   "qi_level": 1,
            "formation_realm": 0, "formation_level": 1,
            "dao_ti_unlocked": True,
            "merit": 10_000_000, "karma_usable": 0,
            "primordial_stones": 1_000_000,
            "linh_can": list(ALL_LINH_CAN),
            "constitutions": list(_LEGENDARY_EIGHT),  # 8 legendaries, Hỗn Độn-ready
            "skills": ["SkillAtkKim1", "SkillAtkHoa1", "SkillAtkLoi1", "SkillDefTo"],
        }
    if preset == "endgame_khi_tu":
        # Khí Tu all-element: active_axis = "qi" so the 1-slot constitution
        # rule + Linh Căn breadth multiplier both fire. All 9 Linh Căn active
        # so every element-specific passive (Kim Xuyên Thấu, Hỏa Bạo Liệt,
        # Quang Thanh Tẩy, etc.) runs side-by-side. Phá Thiên is a neutral
        # legendary so no single element dominates the build. Linh Căn maxed
        # to Lv9 so every threshold effect (Lv3/5/7/9) is active.
        return {
            "active_axis": "qi",                     # path = Khí Tu (breadth gate)
            "body_realm": 1, "body_level": 1,
            "qi_realm": 8,   "qi_level": 9,
            "formation_realm": 4, "formation_level": 5,
            "dao_ti_unlocked": False,
            "merit": 10_000_000,
            "primordial_stones": 500_000,
            "linh_can_levels": {elem: 9 for elem in ALL_LINH_CAN},
            "constitutions": ["ConstitutionPhaTien"],
            "skills": [
                "SkillAtkKim_R8",
                "SkillAtkHoa_R8",
                "SkillAtkLoi_R8",
                "SkillAtkPhong_R8",
                "SkillAtkQuang_R8",
                "SkillAtkAm_R8",
            ],
        }
    if preset == "endgame_tran_tu":
        # Trận Tu: active_axis = "formation" so the multi-formation slot rule
        # fires (formation_realm=8 → 3 slots via max_formation_slots).
        # Legendary Khí Hải for MP/CDR kit.
        # Anchor slot: CuuCungBatQua (neutral, 9 gems covering every element —
        # opens every threshold bonus). Slot 2/3: elemental formations that
        # each contribute their own signature skill + small stat kit; left
        # gem-less so MP reservation stays sane.
        return {
            "active_axis": "formation",              # path = Trận Tu (multi-slot gate)
            "body_realm": 0, "body_level": 1,
            "qi_realm": 6,   "qi_level": 9,
            "formation_realm": 8, "formation_level": 9,
            "dao_ti_unlocked": False,
            "merit": 10_000_000,
            "primordial_stones": 500_000,
            "linh_can_levels": {"kim": 7, "hoa": 7, "loi": 7, "phong": 7, "quang": 7, "am": 7},
            "constitutions": ["ConstitutionKhiHai"],
            "skills": [
                "SkillFrmCuuCung",        # neutral anchor formation skill
                "SkillFrmHoa",            # Hoa formation skill
                "SkillFrmLoi",            # Loi formation skill
                "SkillAtkHoa_R9",         # elemental fallback
                "SkillAtkLoi_R9",
            ],
            "active_formation": [
                "CuuCungBatQua",   # neutral anchor
                "NhatNguyenHoa",   # Hoa offensive formation
                "NhatNguyenLoi",   # Loi offensive formation
            ],
            # Full-build loadout: every active formation has its own 9-gem
            # inlay — 27 grade-3 gems total across 3 formations. Each
            # formation's own thresholds (1/3/5/7 per formation) fire
            # simultaneously, and per-gem elemental bonuses from ALL 27
            # stack into the aggregate bonus dict.
            #
            # Anchor: one of every element (broad per-gem spread).
            # Elemental slots: saturate the element they match plus a few
            # flex slots to help hit the 5/7-gem thresholds.
            "formation_gems": {
                "CuuCungBatQua": {
                    0: "GemKim_3", 1: "GemHoa_3", 2: "GemLoi_3",
                    3: "GemMoc_3", 4: "GemThuy_3", 5: "GemTho_3",
                    6: "GemPhong_3", 7: "GemAm_3", 8: "GemQuang_3",
                },
                "NhatNguyenHoa": {
                    0: "GemHoa_3", 1: "GemHoa_3", 2: "GemHoa_3",
                    3: "GemHoa_3", 4: "GemHoa_3", 5: "GemHoa_3",
                    6: "GemHoa_3", 7: "GemKim_3", 8: "GemLoi_3",
                },
                "NhatNguyenLoi": {
                    0: "GemLoi_3", 1: "GemLoi_3", 2: "GemLoi_3",
                    3: "GemLoi_3", 4: "GemLoi_3", 5: "GemLoi_3",
                    6: "GemLoi_3", 7: "GemKim_3", 8: "GemPhong_3",
                },
            },
        }
    if preset == "the_tu_8leg":
        return {
            "body_realm": 7, "body_level": 9,
            "qi_realm": 2,   "qi_level": 1,
            "formation_realm": 1, "formation_level": 1,
            "dao_ti_unlocked": False,
            "merit": 5_000_000,
            "primordial_stones": 500_000,
            "linh_can": ["kim", "hoa", "loi"],
            "constitutions": list(_LEGENDARY_EIGHT),
            "skills": ["SkillAtkKim1", "SkillAtkHoa1", "SkillAtkLoi1"],
        }
    if preset == "qi_r9_legend":
        return {
            "body_realm": 2, "body_level": 1,
            "qi_realm": 8,   "qi_level": 9,
            "formation_realm": 4, "formation_level": 5,
            "dao_ti_unlocked": False,
            "merit": 2_000_000,
            "primordial_stones": 200_000,
            "linh_can": ["hoa", "loi"],
            "constitutions": ["ConstitutionPhaTien"],
            "skills": ["SkillAtkHoa1", "SkillAtkLoi1"],
        }
    if preset == "qi_r5_rare":
        return {
            "body_realm": 1, "body_level": 1,
            "qi_realm": 4,   "qi_level": 6,
            "formation_realm": 2, "formation_level": 3,
            "merit": 500_000,
            "primordial_stones": 30_000,
            "linh_can": ["hoa"],
            "constitutions": ["ConstitutionLietHoaThe"],
            "skills": ["SkillAtkHoa1"],
        }
    if preset == "starter":
        return {
            "body_realm": 0, "body_level": 1,
            "qi_realm": 0,   "qi_level": 1,
            "formation_realm": 0, "formation_level": 1,
            "body_xp": 0, "qi_xp": 0, "formation_xp": 0,
            "dao_ti_unlocked": False,
            "merit": 0, "karma_accum": 0, "karma_usable": 0,
            "primordial_stones": 0,
            "linh_can": ["hoa"],
            "constitutions": ["ConstitutionPhamThe"],
            "skills": ["SkillAtkHoa1"],
            "active_formation": None,
        }
    if preset == "rich":
        # Only add currencies; don't touch realm/constitution/skills.
        return {
            "merit": 10_000_000,
            "primordial_stones": 1_000_000,
            "karma_usable": 100_000,
        }
    if preset == "dao_ti":
        return {
            "body_realm": 8, "body_level": 9,
            "dao_ti_unlocked": True,
        }
    return {}


_PRESET_CHOICES = [
    Choice(name="Endgame Thể Tu (body 8 + 8 Truyền Thuyết, sẵn sàng Hỗn Độn)", value="endgame_the_tu"),
    Choice(name="Endgame Khí Tu — 9 Linh Căn + Phá Thiên (qi 9)",              value="endgame_khi_tu"),
    Choice(name="Endgame Trận Tu (formation 8 + Cửu Cung 9 gem + Khí Hải)",    value="endgame_tran_tu"),
    Choice(name="Thể Tu 8 Truyền Thuyết (body 7 + 8 legendary)",                value="the_tu_8leg"),
    Choice(name="Khí Tu R9 + Phá Thiên (1 slot)",                              value="qi_r9_legend"),
    Choice(name="Khí Tu R5 + Liệt Hỏa (rare)",                                 value="qi_r5_rare"),
    Choice(name="Fresh starter (reset về zero)",                               value="starter"),
    Choice(name="Rich (10M Công Đức + 1M Hỗn Nguyên, giữ nguyên còn lại)",    value="rich"),
    Choice(name="Unlock Đạo Thể (body 8 cấp 9)",                              value="dao_ti"),
]


# Crit-focused affix preference for the Phá Thiên / R9 admin loadout.
# Order matters — the generator picks top-N from each list, skipping
# anything the slot doesn't allow. Designed to capitalise on the new
# ``crit_dmg_rating_to_dmg_pct`` Phá Thiên grants: every prefix slot
# pumps either crit chance / crit-dmg rating or raw offense. MATK is
# preferred over ATK so the build leans into magic-attack scaling
# (most Phá Thiên / Khí Tu skills scale off MATK).
_PHATIEN_CRIT_PREFIX_PREFERENCE: list[str] = [
    "pfx_crit_dmg",   # Hủy Diệt — feeds the new flat-dmg crit-rating scaling
    "pfx_crit_rate",  # Bạo Liệt
    "pfx_matk",       # Pháp Lực
    "pfx_final_dmg",  # Siêu Việt
    "pfx_hp",         # Cường Thể — sustain
]
_PHATIEN_CRIT_SUFFIX_PREFERENCE: list[str] = [
    "sfx_crit",        # của Huyết Tộc — extra crit_rating
    "sfx_matk",        # Pháp Tủy
    "sfx_dmg_bonus",   # của Sát Thần
    "sfx_hp",          # của Trường Thọ
]


def _gen_crit_thien_r9_item(slot: str, base_key: str) -> dict | None:
    """Build a max-rolled, crit-focused Thiên-quality grade-9 item dict.

    Bypasses the regular forge RNG: implicit + affix values are pinned to
    the per-realm ``hi`` bound so the test character's combat numbers are
    reproducible. Affix choice walks the crit-preference lists and picks
    the first ``n_pre`` / ``n_suf`` entries the slot allows. Returned
    shape matches ``forge_equipment``'s ``item_data`` so the existing
    ``EquipmentRepository.add_to_bag`` path consumes it unchanged.
    """
    from src.game.systems.forge import (
        QUALITY_SPECIAL,
        compute_stats,
        get_affix_count,
        _build_display_name,
    )

    base = registry.get_base(base_key)
    if base is None or "implicit_by_realm" not in base:
        return None

    grade, quality = 9, "thien"
    spec = QUALITY_SPECIAL[quality]
    n_pre, n_suf = get_affix_count(grade, quality)
    if base.get("two_handed", False):
        n_pre *= 2
        n_suf *= 2

    idx = grade - 1

    # Max-roll implicit stats — mirrors roll_implicit_stats but skips the
    # uniform/randint draw so the test build isn't subject to RNG variance.
    mult = spec["implicit_mult"]
    implicit: dict[str, float] = {}
    for stat, ranges in base["implicit_by_realm"].items():
        lo, hi = ranges[idx]
        if isinstance(lo, float) and lo < 1:
            implicit[stat] = round(hi * mult, 5)
        else:
            implicit[stat] = round(int(hi) * mult)

    def _slot_ok(affix: dict) -> bool:
        return "all" in affix["slots"] or slot in affix["slots"]

    def _max_roll(a: dict) -> dict:
        lo, hi = a["by_realm"][idx]
        val = hi if a.get("is_pct") else int(hi)
        return {"key": a["key"], "stat": a["stat"], "value": val, "type": a["type"]}

    def _pick(preference: list[str], pool_type: str, count: int) -> list[dict]:
        chosen: list[dict] = []
        seen_keys: set[str] = set()
        seen_stats: set[str] = set()
        # Walk preferred keys first; fall back to any same-type slot-allowed
        # affix to satisfy the count when preferences run out.
        for key in preference:
            if len(chosen) >= count:
                break
            a = registry.affixes.get(key)
            if not a or a["type"] != pool_type or not _slot_ok(a):
                continue
            if a["key"] in seen_keys or a["stat"] in seen_stats:
                continue
            chosen.append(_max_roll(a))
            seen_keys.add(a["key"])
            seen_stats.add(a["stat"])
        if len(chosen) < count:
            for a in registry.affixes.values():
                if len(chosen) >= count:
                    break
                if a["type"] != pool_type or not _slot_ok(a):
                    continue
                if a["key"] in seen_keys or a["stat"] in seen_stats:
                    continue
                chosen.append(_max_roll(a))
                seen_keys.add(a["key"])
                seen_stats.add(a["stat"])
        return chosen

    affixes = _pick(_PHATIEN_CRIT_PREFIX_PREFERENCE, "prefix", n_pre)
    affixes += _pick(_PHATIEN_CRIT_SUFFIX_PREFERENCE, "suffix", n_suf)

    computed = compute_stats(implicit, affixes)
    name = _build_display_name(base, quality, affixes)

    return {
        "slot": base["slot"],
        "base_key": base_key,
        "grade": grade,
        "quality": quality,
        "special_label": spec["special_label"],
        "implicit_stats": implicit,
        "affixes": affixes,
        "computed_stats": computed,
        "display_name": name,
        "super_material_key": None,
    }


async def _apply_phatien_crit_loadout(session, player) -> list[str]:
    """Generate a full Thiên R9 crit-focused loadout and equip every slot.

    Picks the first base per slot from the registry (deterministic — the
    same player gets the same base every time so re-running the command
    is idempotent in spirit). Existing equipped items are displaced to
    bag by ``EquipmentRepository.equip``.
    """
    from src.db.repositories.equipment_repo import EquipmentRepository
    from src.game.engine.equipment import SLOT_ORDER

    erepo = EquipmentRepository(session)
    summary: list[str] = []
    for slot in SLOT_ORDER:
        bases = registry.bases_for_slot(slot)
        # Skip 2H weapon variants on the weapon slot — picking the first
        # 1H base keeps the off_hand slot usable.
        bases_1h = [b for b in bases if not b.get("two_handed", False)]
        if not bases_1h:
            bases_1h = bases
        if not bases_1h:
            continue
        base = bases_1h[0]
        item_data = _gen_crit_thien_r9_item(slot, base["key"])
        if item_data is None:
            continue
        inst = await erepo.add_to_bag(player.id, item_data)
        await erepo.equip(player.id, inst.id)
        summary.append(f"`{slot}` → **{item_data['display_name']}**")
    return summary


async def _apply_testbuild(session, player, cfg: dict) -> list[str]:
    """Apply the preset patch to a player and return a list of human-readable
    change descriptions for the admin.
    """
    lines: list[str] = []

    # ── Realms / xp / dao_ti ────────────────────────────────────────────────
    realm_keys = (
        "body_realm", "body_level", "body_xp",
        "qi_realm", "qi_level", "qi_xp",
        "formation_realm", "formation_level", "formation_xp",
    )
    for k in realm_keys:
        if k in cfg:
            setattr(player, k, int(cfg[k]))
    if "dao_ti_unlocked" in cfg:
        player.dao_ti_unlocked = bool(cfg["dao_ti_unlocked"])
    if any(k in cfg for k in realm_keys) or "dao_ti_unlocked" in cfg:
        lines.append(
            f"🧭 Realms: body {player.body_realm}.{player.body_level} · "
            f"qi {player.qi_realm}.{player.qi_level} · "
            f"form {player.formation_realm}.{player.formation_level}"
            + (f" · Đạo Thể ✅" if player.dao_ti_unlocked else "")
        )

    # ── Active axis (drives Thể/Khí/Trận Tu archetype + slot caps) ──────────
    if "active_axis" in cfg:
        axis = str(cfg["active_axis"])
        if axis in ("body", "qi", "formation"):
            player.active_axis = axis
            lines.append(f"🧭 Active axis: **{axis}**")

    # ── Currencies ──────────────────────────────────────────────────────────
    for k in ("merit", "karma_accum", "karma_usable", "primordial_stones"):
        if k in cfg:
            setattr(player, k, int(cfg[k]))
    if any(k in cfg for k in ("merit", "karma_accum", "karma_usable", "primordial_stones")):
        lines.append(
            f"{emojis.for_currency('merit')} Công Đức: {player.merit:,} · "
            f"{emojis.for_currency('karma_usable')} Nghiệp (usable): {player.karma_usable:,} · "
            f"{emojis.for_currency('primordial_stones')} Hỗn Nguyên: {player.primordial_stones:,}"
        )

    # ── Linh Căn ────────────────────────────────────────────────────────────
    # Two preset shapes are accepted:
    #   "linh_can": ["kim", "hoa"]                — list, each at level 1
    #   "linh_can_levels": {"kim": 9, "hoa": 9}   — explicit per-element level
    # Levels are clamped to the player's qi_realm cap so a fresh starter
    # preset can't accidentally hand out endgame Linh Căn power.
    if "linh_can_levels" in cfg or "linh_can" in cfg:
        cap = max_linh_can_level(int(player.qi_realm))
        if "linh_can_levels" in cfg:
            raw_map = {
                lc: int(lvl) for lc, lvl in cfg["linh_can_levels"].items()
                if lc in ALL_LINH_CAN
            }
        else:
            raw_map = {lc: 1 for lc in cfg["linh_can"] if lc in ALL_LINH_CAN}
        clamped = {lc: max(1, min(cap, lvl)) for lc, lvl in raw_map.items()}
        player.linh_can = format_linh_can_levels(clamped)
        lines.append(f"🌿 Linh Căn (cap Lv{cap}): {player.linh_can or '(trống)'}")

    # ── Constitutions ───────────────────────────────────────────────────────
    if "constitutions" in cfg:
        # Filter to keys that exist in the registry so a stale preset can't
        # write garbage into constitution_type.
        keys = [k for k in cfg["constitutions"] if registry.get_constitution(k)]
        if not keys:
            keys = ["ConstitutionPhamThe"]
        player.constitution_type = set_constitutions(keys)
        names = ", ".join(registry.get_constitution(k)["vi"] for k in keys)
        lines.append(f"🧬 Thể Chất ({len(keys)}): {names}")

    # ── Skills ──────────────────────────────────────────────────────────────
    if "skills" in cfg:
        valid_skills = [s for s in cfg["skills"] if registry.get_skill(s)][:MAX_SKILL_SLOTS]
        # Delete existing skills and clear the relationship list so SQLAlchemy
        # doesn't re-encounter the deleted objects during any subsequent
        # cascade (e.g. an explicit session.add on the player). Must flush
        # the DELETE before new rows are added — otherwise the UniqueConstraint
        # on (player_id, slot_index) fires against the about-to-be-deleted rows.
        for existing in list(player.skills or []):
            await session.delete(existing)
        if player.skills is not None:
            player.skills.clear()
        await session.flush()
        for i, skill_key in enumerate(valid_skills):
            new_skill = CharacterSkill(
                player_id=player.id, skill_key=skill_key, slot_index=i,
            )
            session.add(new_skill)
            if player.skills is not None:
                player.skills.append(new_skill)
        lines.append(f"🎯 Skills ({len(valid_skills)}): {', '.join(valid_skills) or '(trống)'}")

    # ── Active formation(s) — accepts either a single key or a list of keys
    # (multi-slot Trận Tu). Stored as comma-separated string.
    # Presets unlock formations directly (CharacterFormation row) since they
    # bypass the normal "learn the skill" path that does this for players.
    if "active_formation" in cfg:
        from src.game.systems.cultivation import (
            get_active_formations, set_active_formations,
        )
        from src.db.repositories.formation_repo import FormationRepository
        raw = cfg["active_formation"]
        if raw is None:
            player.active_formation = None
        elif isinstance(raw, str):
            player.active_formation = raw
        else:
            player.active_formation = set_active_formations(list(raw))
        active_keys = get_active_formations(player.active_formation)
        if active_keys:
            preset_frepo = FormationRepository(session)
            for fk in active_keys:
                if registry.get_formation(fk):
                    await preset_frepo.get_or_create(player.id, fk)
        lines.append(f"🔯 Active formation(s): {player.active_formation or '(none)'}")

    # ── Formation gems — inlay a full gem loadout per formation ─────────────
    if "formation_gems" in cfg and cfg["formation_gems"]:
        from src.db.repositories.formation_repo import FormationRepository
        frepo = FormationRepository(session)
        gem_summary: list[str] = []
        for formation_key, slot_map in cfg["formation_gems"].items():
            if not registry.get_formation(formation_key):
                continue
            formation = await frepo.get_or_create(player.id, formation_key)
            valid_slots: dict = {}
            for slot_idx, gem_key in slot_map.items():
                if registry.get_item(gem_key):
                    valid_slots[str(slot_idx)] = gem_key
            formation.gem_slots = valid_slots
            gem_summary.append(f"{formation_key}: {len(valid_slots)} gem")
        if gem_summary:
            lines.append(f"💠 Formation gems — {' · '.join(gem_summary)}")

    # ── Restore HP/MP to full so the test char doesn't start wounded ───────
    from src.game.systems.character_stats import (
        active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
    )
    from src.db.repositories.player_repo import _player_to_model
    char = _player_to_model(player)
    gem_keys = active_formation_gem_keys(player)
    gem_map = active_formation_gem_map(player)
    cs = compute_combat_stats(
        char, gem_count=len(gem_keys), gem_keys=gem_keys,
        gem_keys_by_formation=gem_map,
    )
    player.hp_current = cs.hp_max
    player.mp_current = cs.mp_max
    player.shield_current = cs.shield_max
    lines.append(
        f"❤️ HP/MP/Shield restored: {cs.hp_max:,} / {cs.mp_max:,} / {cs.shield_max:,}"
    )

    return lines


# ── Cog ───────────────────────────────────────────────────────────────────────

class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Convert ``_owner_only`` rejections into a clean ephemeral message.

        Unrelated errors are re-raised so the global handler still sees them.
        """
        if isinstance(error, app_commands.CheckFailure):
            msg = "🔒 Lệnh này chỉ dành cho **chủ bot**."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        embed=error_embed(msg), ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        embed=error_embed(msg), ephemeral=True,
                    )
            except discord.HTTPException:
                pass  # interaction expired or already replied — best effort only
            return
        raise error

    @app_commands.command(name="sync", description="[Admin] Đồng bộ các lệnh slash commands")
    @app_commands.default_permissions(administrator=True)
    @_owner_only()
    async def sync(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        guild_count = 0
        if interaction.guild:
            await self.bot.tree.sync(guild=interaction.guild)
            guild_count = 1
        await self.bot.tree.sync()
        await interaction.followup.send(
            embed=success_embed(
                f"Đã đồng bộ slash commands.\n"
                f"• Guild (tức thì): {'✅' if guild_count else '—'}\n"
                f"• Global (tối đa 1 giờ): ✅"
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="admin_testbuild",
        description="[Admin] Áp preset build để test combat / world boss / constitutions",
    )
    @app_commands.default_permissions(administrator=True)
    @_owner_only()
    @app_commands.describe(
        preset="Loại test build",
        target="Người chơi muốn áp (mặc định: chính bạn)",
    )
    @app_commands.choices(preset=_PRESET_CHOICES)
    async def admin_testbuild(
        self,
        interaction: discord.Interaction,
        preset: Choice[str],
        target: discord.User | None = None,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        target_user = target or interaction.user
        cfg = _preset_config(preset.value)
        if not cfg:
            await interaction.followup.send(
                embed=error_embed(f"Preset không hợp lệ: `{preset.value}`"),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(target_user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed(
                        f"{target_user.mention} chưa có nhân vật — gọi `/register` trước."
                    ),
                    ephemeral=True,
                )
                return

            change_lines = await _apply_testbuild(session, player, cfg)
            # ``player`` is already session-managed (loaded via get_by_discord_id),
            # so dirty attribute changes are tracked automatically. Skip the
            # explicit repo.save(player) — calling session.add on a managed
            # instance cascades through relationships and trips on rows that
            # were session.delete'd earlier in this flow (skills overwrite).
            await session.commit()

        summary = (
            f"✅ Áp preset **{preset.name}** cho {target_user.mention}.\n\n"
            + "\n".join(change_lines)
        )
        await interaction.followup.send(embed=success_embed(summary), ephemeral=True)

    @app_commands.command(
        name="admin_reset",
        description="[Admin] Xóa nhân vật của người chơi (phải /register lại)",
    )
    @app_commands.default_permissions(administrator=True)
    @_owner_only()
    @app_commands.describe(
        target="Người chơi cần reset (mặc định: chính bạn)",
        confirm="Gõ XOA để xác nhận xóa nhân vật",
    )
    async def admin_reset(
        self,
        interaction: discord.Interaction,
        confirm: str,
        target: discord.User | None = None,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        if confirm.strip().upper() != "XOA":
            await interaction.followup.send(
                embed=error_embed(
                    "Hủy reset — phải gõ chính xác `XOA` vào ô confirm để xác nhận."
                ),
                ephemeral=True,
            )
            return

        target_user = target or interaction.user
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(target_user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed(
                        f"{target_user.mention} chưa có nhân vật để reset."
                    ),
                    ephemeral=True,
                )
                return

            old_name = player.name
            await session.delete(player)
            await session.commit()

        await interaction.followup.send(
            embed=success_embed(
                f"✅ Đã xóa nhân vật **{old_name}** của {target_user.mention}.\n"
                f"Người chơi cần dùng `/register` để tạo nhân vật mới."
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="admin_reset_all",
        description="[Admin] Reset tiến trình của TẤT CẢ người chơi (giữ Thể Chất + Linh Căn)",
    )
    @app_commands.default_permissions(administrator=True)
    @_owner_only()
    @app_commands.describe(
        confirm="Gõ XOA TAT CA để xác nhận reset toàn bộ người chơi",
    )
    async def admin_reset_all(
        self,
        interaction: discord.Interaction,
        confirm: str,
    ) -> None:
        from datetime import datetime, timezone

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from src.db.models.player import Player
        from src.db.models.turn_tracker import TurnTracker
        from src.game.constants.currencies import BONUS_TURNS

        if not await safe_defer(interaction, ephemeral=True):
            return
        if confirm.strip().upper() != "XOA TAT CA":
            await interaction.followup.send(
                embed=error_embed(
                    "Hủy reset toàn bộ — phải gõ chính xác `XOA TAT CA` "
                    "vào ô confirm để xác nhận."
                ),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            # Eager-load every cascading relationship in one shot.
            # Async sessions forbid lazy-loading; touching ``player.inventory``
            # without selectinload triggers ``MissingGreenlet`` since SQLAlchemy
            # would have to fire a sync SELECT inside the iteration.
            result = await session.execute(
                select(Player).options(
                    selectinload(Player.turn_tracker),
                    selectinload(Player.inventory),
                    selectinload(Player.skills),
                    selectinload(Player.artifacts),
                    selectinload(Player.formations),
                    selectinload(Player.market_listings),
                    selectinload(Player.item_instances),
                )
            )
            players = list(result.scalars().all())

            if not players:
                await interaction.followup.send(
                    embed=error_embed("Không có người chơi nào để reset."),
                    ephemeral=True,
                )
                return

            reset_count = 0
            for player in players:
                # Wipe every cascading relationship so the player ends up
                # with zero items, no skills, no formations, no listings.
                # constitution_type and linh_can stay untouched on purpose.
                for rel_name in (
                    "inventory", "skills", "artifacts",
                    "formations", "market_listings", "item_instances",
                ):
                    rel = getattr(player, rel_name, None) or []
                    for row in list(rel):
                        await session.delete(row)
                    if rel is not None:
                        rel.clear()

                # Reset all progression scalars. ``constitution_type``,
                # ``linh_can``, ``id``, ``discord_id``, ``name`` are
                # intentionally omitted so the player's "born identity"
                # survives.
                player.body_realm = 0
                player.body_level = 1
                player.qi_realm = 0
                player.qi_level = 1
                player.formation_realm = 0
                player.formation_level = 1
                player.body_xp = 0
                player.qi_xp = 0
                player.formation_xp = 0
                player.dao_ti_unlocked = False
                player.merit = 0
                player.karma_accum = 0
                player.karma_usable = 0
                player.primordial_stones = 0
                player.active_axis = "qi"
                player.active_formation = None
                player.dan_doc = 0
                player.pill_buff_counts = "{}"
                player.preferred_furnace_key = None
                player.main_title = None
                player.sub_title = None
                player.evil_title = None

                # Refresh turn tracker — delete the old row, create a fresh
                # one with the registration-time bonus pool restored.
                if player.turn_tracker is not None:
                    await session.delete(player.turn_tracker)
                    player.turn_tracker = None
                await session.flush()
                session.add(TurnTracker(
                    player_id=player.id,
                    turns_today=0,
                    bonus_turns_remaining=BONUS_TURNS,
                    last_tick_at=datetime.now(timezone.utc),
                ))

                # Restore HP/MP to fresh-realm-0 totals so the player isn't
                # left with the old combat-state numbers from before reset.
                from src.game.systems.character_stats import (
                    active_formation_gem_keys, compute_combat_stats,
                )
                from src.db.repositories.player_repo import _player_to_model
                char = _player_to_model(player)
                gem_keys = active_formation_gem_keys(player)
                cs = compute_combat_stats(
                    char, gem_count=len(gem_keys), gem_keys=gem_keys,
                )
                player.hp_current = cs.hp_max
                player.mp_current = cs.mp_max
                player.shield_current = cs.shield_max

                reset_count += 1

            await session.commit()

        await interaction.followup.send(
            embed=success_embed(
                f"✅ Đã reset **{reset_count}** người chơi.\n"
                f"• Giữ lại: 🧬 Thể Chất, 🌿 Linh Căn, tên đạo hữu\n"
                f"• Reset: cảnh giới, EXP, công đức, túi đồ, trang bị, "
                f"kỹ năng, trận pháp, danh hiệu, đan độc, pill buffs"
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="admin_grant_item",
        description="[Admin] Cấp vật phẩm cho người chơi để test",
    )
    @app_commands.default_permissions(administrator=True)
    @_owner_only()
    @app_commands.describe(
        item_key="Key của vật phẩm (vd: MatDaoCotTinh, ChestWorldBossR9)",
        qty="Số lượng (mặc định 1)",
        target="Người chơi muốn cấp (mặc định: chính bạn)",
    )
    async def admin_grant_item(
        self,
        interaction: discord.Interaction,
        item_key: str,
        qty: int = 1,
        target: discord.User | None = None,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        target_user = target or interaction.user
        qty = max(1, int(qty))

        item = registry.get_item(item_key)
        if not item:
            await interaction.followup.send(
                embed=error_embed(f"Không tìm thấy vật phẩm `{item_key}`."),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(target_user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed(
                        f"{target_user.mention} chưa có nhân vật."
                    ),
                    ephemeral=True,
                )
                return
            irepo = InventoryRepository(session)
            grade = Grade(int(item.get("grade", 1)))
            await irepo.add_item(player.id, item_key, grade, qty)
            await session.commit()

        await interaction.followup.send(
            embed=success_embed(
                f"✅ Cấp **{item['vi']}** ×{qty} (grade {grade.name}) cho "
                f"{target_user.mention}."
            ),
            ephemeral=True,
        )


    @app_commands.command(
        name="admin_unstuck_dungeon",
        description="[Admin] Gỡ khoá phiên bí cảnh bị kẹt trong bộ nhớ",
    )
    @app_commands.default_permissions(administrator=True)
    @_owner_only()
    @app_commands.describe(
        target="Người chơi đang bị kẹt (mặc định: chính bạn)",
    )
    async def admin_unstuck_dungeon(
        self,
        interaction: discord.Interaction,
        target: discord.User | None = None,
    ) -> None:
        from src.bot.cogs.dungeon import force_release_dungeon_session

        if not await safe_defer(interaction, ephemeral=True):
            return
        target_user = target or interaction.user
        cleared = force_release_dungeon_session(target_user.id)
        if cleared:
            await interaction.followup.send(
                embed=success_embed(
                    f"✅ Đã gỡ khoá phiên bí cảnh cho {target_user.mention} "
                    f"(`{target_user.id}`). Họ có thể vào bí cảnh ngay."
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed(
                    f"{target_user.mention} (`{target_user.id}`) hiện không có "
                    "khoá phiên bí cảnh nào — vấn đề khác đang chặn họ."
                ),
                ephemeral=True,
            )


    @app_commands.command(
        name="admin_phatien_build",
        description="[Admin] Tạo build Phá Thiên Thần Thể + trang bị Thiên cấp 9 với crit",
    )
    @app_commands.default_permissions(administrator=True)
    @_owner_only()
    @app_commands.describe(
        target="Người chơi muốn áp build (mặc định: chính bạn)",
    )
    async def admin_phatien_build(
        self,
        interaction: discord.Interaction,
        target: discord.User | None = None,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        target_user = target or interaction.user

        # Khí Tu chassis tuned for Phá Thiên: max qi realm so the new
        # ``crit_dmg_rating_to_dmg_pct`` floor multiplies against R9 affix
        # values, every Linh Căn at Lv9 so element-passives all fire,
        # 10M Công Đức + 1M Hỗn Nguyên for follow-up crafting tests.
        cfg = {
            "body_realm": 1, "body_level": 1,
            "qi_realm": 8,   "qi_level": 9,
            "formation_realm": 4, "formation_level": 5,
            "dao_ti_unlocked": False,
            "merit": 10_000_000,
            "primordial_stones": 1_000_000,
            "linh_can_levels": {elem: 9 for elem in ALL_LINH_CAN},
            "constitutions": ["ConstitutionPhaTien"],
            "skills": [
                "SkillAtkKim_R8",
                "SkillAtkHoa_R8",
                "SkillAtkLoi_R8",
                "SkillAtkPhong_R8",
                "SkillAtkQuang_R8",
                "SkillAtkAm_R8",
            ],
        }

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(target_user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed(
                        f"{target_user.mention} chưa có nhân vật — gọi `/register` trước."
                    ),
                    ephemeral=True,
                )
                return

            change_lines = await _apply_testbuild(session, player, cfg)
            gear_lines = await _apply_phatien_crit_loadout(session, player)
            # HP/MP recompute so the freshly-equipped Thiên affixes land
            # before the test character starts taking hits.
            from src.game.systems.character_stats import (
                active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
            )
            from src.game.engine.equipment import compute_equipment_stats
            from src.db.repositories.player_repo import _player_to_model
            char = _player_to_model(player)
            equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
            cs = compute_combat_stats(
                char,
                gem_count=len(active_formation_gem_keys(player)),
                equip_stats=compute_equipment_stats(equipped),
                gem_keys=active_formation_gem_keys(player),
                gem_keys_by_formation=active_formation_gem_map(player),
            )
            player.hp_current = cs.hp_max
            player.mp_current = cs.mp_max
            player.shield_current = cs.shield_max

            await session.commit()

        gear_summary = (
            "\n".join(f"  • {l}" for l in gear_lines)
            if gear_lines else "  *(không sinh được trang bị nào)*"
        )
        summary = (
            f"✅ Áp build **Phá Thiên Thần Thể** cho {target_user.mention}.\n\n"
            + "\n".join(change_lines)
            + f"\n\n🗡️ **Trang bị Thiên Cấp 9 (max-roll, ưu tiên crit):**\n{gear_summary}"
            + f"\n\n❤️ HP/MP sau khi mặc: {cs.hp_max:,} / {cs.mp_max:,}"
        )
        await interaction.followup.send(embed=success_embed(summary), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))