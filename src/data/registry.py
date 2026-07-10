"""Game data registry — loads all static JSON files once at startup."""
from __future__ import annotations
import json
import logging
from pathlib import Path

from src.game.engine.loot import inject_global_drops, inject_scroll_drops

# Khởi tạo logger để theo dõi việc load dữ liệu
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent

class GameRegistry:
    """Singleton holding all static game data."""
    _instance: GameRegistry | None = None

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}               # key → item data
        self.skills: dict[str, dict] = {}              # key → skill data
        self.enemies: dict[str, dict] = {}             # key → enemy (farming) data
        self.tribulations: dict[str, dict] = {}        # key → thien_kiep breakthrough data
        self.formations: dict[str, dict] = {}          # key → formation data
        self.constitutions: dict[str, dict] = {}       # key → constitution data
        self.dungeons: dict[str, dict] = {}            # key → dungeon data
        self.loot_tables: dict[str, list[dict]] = {}   # key → list of drop entries
        self.bases: dict[str, dict] = {}               # key → equipment base definition
        self.affixes: dict[str, dict] = {}             # key → affix definition
        self.uniques: dict[str, dict] = {}             # key → unique item definition
        self.forge_recipes: list[dict] = []            # grade-ordered forge recipe list
        self.world_bosses: dict[str, dict] = {}        # key → world boss definition
        self.pill_recipes: dict[str, dict] = {}        # key → Luyện Đan recipe
        self.sect_levels: list[dict] = []              # Tông Môn level curve (sorted by level)
        self.sect_facilities: dict[str, dict] = {}     # key → Tông Môn facility definition
        self.sect_shop: dict[str, list[dict]] = {}     # {"fixed": [...], "rotating": [...]}
        self.sect_missions: list[dict] = []            # Tông Môn daily mission definitions
        self.sect_bosses: dict[str, dict] = {}         # key → Trấn Sơn Thú boss definition
        self.sect_mines: dict[str, dict] = {}          # key → Khoáng Mạch mine definition
        self._loaded = False

    @classmethod
    def get(cls) -> GameRegistry:
        if cls._instance is None:
            cls._instance = GameRegistry()
            cls._instance.load()
        return cls._instance

    # Item JSON files in src/data/items/. Gem items moved to
    # ``src/data/gems/``; alchemy items (herbs, pills, furnaces) moved to
    # ``src/data/pills/``; forge + super materials moved to
    # ``src/data/equipment/`` — see ``_load_items``.
    _ITEM_FILES = (
        "chests", "scrolls", "specials",
        "constitution_materials",
        "constitution_process_materials",
        "mastery_materials",
        "linh_can_material",
        "world_boss_chests",
        "vital_essences",
    )
    # All gem items (elemental + stat + unique) live in a single file under
    # ``src/data/gems/gems.json``. Each entry carries its own pre-scaled
    # ``bonus`` dict — no separate per-prefix base table.
    _GEM_ITEM_FILES = ("gems",)
    # Alchemy package — herbs + pill items + furnaces + pill recipes all
    # under ``src/data/pills/`` so a balance pass on the alchemy economy
    # touches one directory. ``pill_recipes`` loads via
    # ``_load_pill_recipes`` (kept as a separate registry dict); ``herbs``,
    # ``pills``, and ``furnaces`` fold into the generic items dict via
    # ``_load_items``.
    _PILL_ITEM_FILES = ("herbs", "pills", "furnaces")
    # Equipment-crafting items under ``src/data/equipment/`` that fold into
    # the generic items dict (forge materials + super materials are inventory
    # drops). ``bases``, ``affixes``, and ``uniques`` are NOT items —
    # they're equipment definitions consumed by ``_load_equipment_defs``.
    _EQUIP_ITEM_FILES = ("forge_materials", "super_materials")
    # Equipment definition files in src/data/equipment/
    _EQUIP_FILES = ("bases", "affixes", "uniques")

    def load(self) -> None:
        self.items = self._load_items()
        self.skills = self._load_skills()
        self.formations = self._load_formations()
        # Scroll synthesis runs after formations so the formation-embedded
        # skills also get a Scroll_<key> shop/loot entry.
        self._synthesize_skill_scrolls()
        self.enemies = self._load_enemy_dir()
        self.tribulations = self._load_tribulation_dir()
        self.constitutions = self._load_constitution_dir()
        self.dungeons = self._load_dungeon_dir()
        self.loot_tables = self._load_loot_table_dir()
        self.bases, self.affixes, self.uniques = self._load_equipment_defs()
        self.forge_recipes = self._load_forge_recipes()
        self.world_bosses = self._load_keyed("world_bosses.json")
        self.pill_recipes = self._load_pill_recipes()
        self.sect_levels = self._load_sect_levels()
        self.sect_facilities = self._load_keyed("sects/facilities.json")
        self.sect_shop = self._load_sect_shop()
        self.sect_missions = self._load_keyed_list("sects/sect_missions.json")
        self.sect_bosses = self._load_keyed("sects/sect_bosses.json")
        self.sect_mines = self._load_keyed("sects/mines.json")
        # Derived view: per-prefix grade-1 bonus, used by the handbook gem
        # table. Built after ``_load_items`` already merged gem entries into
        # ``self.items``.
        self.gem_bonus = self._derive_gem_bonus()
        self._loaded = True

    def _load_formations(self) -> dict[str, dict]:
        """Load formations from src/data/formations/ (per-element files) and
        split out embedded skill blocks.

        Each formation entry may carry a ``skill: {...}`` sub-object — that's
        the formation's primary channeled skill (the one that fires when the
        formation is active). The embedded skill is registered into
        ``self.skills`` here, and ``formation_skill_key`` /
        ``formation_key`` back-refs are injected so consuming code paths
        (combat builders, formation_key_for_skill) can navigate either
        direction (formation → skill or skill → formation).

        The directory layout mirrors ``skills/player/`` — one file per
        element (``am.json``, ``hoa.json``, …) plus ``general.json`` for
        non-elemental formations. Drop a new ``<element>.json`` to add
        formations without touching the registry.
        """
        formations: dict[str, dict] = {}
        base = DATA_DIR / "formations"
        if not base.is_dir():
            log.error("GameRegistry: Missing formations/ directory")
            return {}
        entries: list[dict] = []
        for path in sorted(base.glob("*.json")):
            entries.extend(json.loads(path.read_text(encoding="utf-8")))

        for entry in entries:
            skill_block = entry.pop("skill", None)
            if skill_block:
                skill_key = skill_block["key"]
                entry["formation_skill_key"] = skill_key
                skill_block = dict(skill_block)
                skill_block["formation_key"] = entry["key"]
                self.skills[skill_key] = skill_block
            formations[entry["key"]] = entry
        return formations

    # ── Per-skill scroll synthesis ───────────────────────────────────────────
    # One Scroll_<SkillKey> item per learnable player skill, generated at load
    # time so /shop, /inventory, and the learn flow can treat them like any
    # other registry item. NPC-only skills (loaded from ``skills/enemy/`` or
    # ``skills/boss/``, marked ``_npc_only`` by ``_load_skills``) and constitution
    # skills (``TheChat_*``) are skipped — never learnable by scroll. Existing
    # entries in scrolls.json win so designers can override pricing or copy on
    # a per-skill basis.
    #
    # ``scroll_grade`` is the only progression axis. Grade gates availability
    # (1-2 in shop, 3-4 in loot drops) and price; the Linh Căn root the player
    # carries gates which elemental scrolls they can actually study.

    _SCROLL_PRICE_BY_GRADE: dict[int, int] = {1: 1000, 2: 3000}

    def _synthesize_skill_scrolls(self) -> None:
        for skill_key, skill in self.skills.items():
            # ``no_scroll`` — skills that can only be gained through a game
            # system (e.g. vital-essence awakening), never studied from a
            # Ngọc Giản. No shop/loot scroll is synthesized for them.
            if (
                skill.get("_npc_only")
                or skill.get("no_scroll")
                or skill_key.startswith("TheChat_")
            ):
                continue
            scroll_key = f"Scroll_{skill_key}"
            if scroll_key in self.items:
                continue  # explicit override wins
            grade = self._scroll_grade_for_skill(skill)
            self.items[scroll_key] = {
                "key": scroll_key,
                "vi": f"Ngọc Giản: {skill.get('vi', skill_key)}",
                "en": f"Scroll: {skill.get('en', skill_key)}",
                "type": "scroll",
                "grade": grade,
                "taught_skill": skill_key,
                "shop_price_merit": self._SCROLL_PRICE_BY_GRADE.get(grade, 0),
                "description_vi": f"Ngọc giản ghi chép kỹ năng {skill.get('vi', skill_key)}.",
            }

    @staticmethod
    def _scroll_grade_for_skill(skill: dict) -> int:
        """Read explicit ``scroll_grade`` from skill JSON.

        Defaults to grade 1 when missing — grade is power-tier, not realm-
        derived, so there's no meaningful auto-mapping. Authors must set
        ``scroll_grade`` explicitly when adding a skill.
        """
        explicit = skill.get("scroll_grade")
        if explicit is not None:
            return int(explicit)
        log.warning(
            "Skill %s missing scroll_grade; defaulting to grade 1.",
            skill.get("key", "<unknown>"),
        )
        return 1

    def _load_pill_recipes(self) -> dict[str, dict]:
        """Load Luyện Đan recipes from src/data/pills/pill_recipes.json."""
        path = DATA_DIR / "pills" / "pill_recipes.json"
        if not path.exists():
            log.warning("GameRegistry: Missing pills/pill_recipes.json")
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {entry["key"]: entry for entry in data}

    def _derive_gem_bonus(self) -> dict[str, dict]:
        """Build the per-prefix grade-1 bonus view from loaded gem items.

        ``gems.json`` carries each item's bonus inline (already scaled for
        its own grade). The handbook still wants a "per-grade base" table
        for the gem-tier reference; we derive it by reading the grade-1
        entry of each prefix family. Unique gems are skipped — their bonus
        is item-specific, not prefix-templated.
        """
        out: dict[str, dict] = {}
        for key, item in self.items.items():
            if not key.startswith("Gem") or item.get("unique"):
                continue
            if item.get("grade") != 1:
                continue
            body = key[3:]
            if "_" not in body:
                continue
            prefix = body.split("_", 1)[0]
            bonus = item.get("bonus")
            if bonus:
                out[prefix] = bonus
        return out

    def _load_items(self) -> dict[str, dict]:
        """Merge all per-type item files into one dict.

        Pulls from four roots:
          • ``src/data/items/`` for general item categories
          • ``src/data/gems/gems.json`` for the unified gem catalogue
            (elemental + stat + unique, each entry carrying its own
            pre-scaled ``bonus`` dict)
          • ``src/data/pills/`` for alchemy items (herbs + pills + furnaces);
            ``pill_recipes`` from the same directory loads via
            ``_load_pill_recipes``
          • ``src/data/equipment/`` for forge materials. The ``bases``,
            ``affixes``, and ``uniques`` files in the same directory are
            equipment *definitions*, not items, and load through
            ``_load_equipment_defs`` separately.
        """
        merged = self._merge_subdir("items", self._ITEM_FILES)
        merged.update(self._merge_subdir("gems", self._GEM_ITEM_FILES))
        merged.update(self._merge_subdir("pills", self._PILL_ITEM_FILES))
        merged.update(self._merge_subdir("equipment", self._EQUIP_ITEM_FILES))
        return merged

    def _load_equipment_defs(self) -> tuple[dict, dict, dict]:
        """Load bases, affixes, and uniques from src/data/equipment/.

        Uniques are split by build element under ``equipment/uniques/`` —
        ``kim.json``, ``moc.json``, ``thuy.json``, ``hoa.json``, ``tho.json``,
        ``general.json`` — and all merged into a single dict keyed by ``key``.
        """
        base_dir = DATA_DIR / "equipment"
        if not base_dir.exists():
            log.error("GameRegistry: Missing equipment/ directory")
            return {}, {}, {}
        bases: dict[str, dict] = {}
        affixes: dict[str, dict] = {}
        uniques: dict[str, dict] = {}
        for entry in json.loads((base_dir / "bases.json").read_text(encoding="utf-8")):
            bases[entry["key"]] = entry
        for entry in json.loads((base_dir / "affixes.json").read_text(encoding="utf-8")):
            affixes[entry["key"]] = entry

        uniques_dir = base_dir / "uniques"
        if uniques_dir.is_dir():
            for path in sorted(uniques_dir.glob("*.json")):
                for entry in json.loads(path.read_text(encoding="utf-8")):
                    uniques[entry["key"]] = entry

        return bases, affixes, uniques

    # Skill subdirectories whose entries are NPC-only — never learnable by
    # players, never get a Scroll_<key> synthesized item. Anything outside
    # this set (e.g. ``player/``) is treated as player-facing.
    _NPC_SKILL_DIRS: frozenset[str] = frozenset({"enemy", "boss"})

    def _load_skills(self) -> dict[str, dict]:
        """Merge all JSON files under src/data/skills/** into one dict, keyed by 'key'.

        The directory is split into subfolders (``player/``, ``enemy/``,
        ``boss/``). Player skills are grouped by element
        (``player/kim.json``, ``player/moc.json``, …), with
        ``player/general.json`` for non-elemental attacks/defenses and
        ``player/formation.json`` for every formation skill across elements.
        Enemy skills are grouped by realm tier (``enemy/realm_01.json`` …
        ``enemy/realm_09.json``). Boss-specific skills live under
        ``boss/<bossname>.json``. Files load in sorted path order, and
        drop-in files require no registry changes.

        Entries loaded from NPC-only directories (``enemy/``, ``boss/``)
        are tagged at load time with ``_npc_only=True`` so scroll
        synthesis and any future player-facing surfaces can skip them
        without per-skill prefix bookkeeping.
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "skills"
        if not base.exists():
            log.error("GameRegistry: Missing skills/ directory")
            return {}
        for path in sorted(base.rglob("*.json")):
            try:
                top = path.relative_to(base).parts[0]
            except (ValueError, IndexError):
                top = ""
            npc_only = top in self._NPC_SKILL_DIRS
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                if npc_only:
                    # Annotate at load time — keeps the JSON files free of
                    # redundant flags while giving downstream code a single
                    # data-driven check instead of key-prefix heuristics.
                    entry["_npc_only"] = True
                merged[entry["key"]] = entry
        return merged

    def _load_enemy_dir(self) -> dict[str, dict]:
        """Merge all JSON files under src/data/enemies/** into one dict, keyed by 'key'.

        Subdirectories are supported so that enemy packages can be split by
        dungeon type (``normal/realm_01.json``, ``duoc_vien/r01.json``, …).
        Files are loaded in sorted path order; drop-in a new file or a new
        subfolder to add a realm without touching the registry.
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "enemies"
        if not base.exists():
            log.error("GameRegistry: Missing enemies/ directory")
            return {}
        for path in sorted(base.rglob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                merged[entry["key"]] = entry
        return merged

    def _load_dungeon_dir(self) -> dict[str, dict]:
        """Merge all JSON files under src/data/dungeons/ into one dict.

        Each file holds a single ``dungeon_type`` family (e.g. ``normal.json``,
        ``duoc_vien.json``, ``the_chat.json``, ``linh_can.json``,
        ``cam_dia.json``); files are loaded in sorted path order with
        last-writer-wins on key collision. Drop a new ``<type>.json`` to
        introduce a dungeon family — no registry change needed.
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "dungeons"
        if not base.is_dir():
            log.error("GameRegistry: Missing dungeons/ directory")
            return merged
        for path in sorted(base.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                merged[entry["key"]] = entry
        return merged

    def _load_constitution_dir(self) -> dict[str, dict]:
        """Merge all JSON files under src/data/constitutions/ into one dict.

        Files are keyed by the entry's ``key`` field. Drop a new
        ``<element>.json`` to add constitutions without touching the registry.
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "constitutions"
        if not base.exists():
            log.error("GameRegistry: Missing constitutions/ directory")
            return merged
        for path in sorted(base.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                merged[entry["key"]] = entry
        return merged

    def _load_tribulation_dir(self) -> dict[str, dict]:
        """Load thien_kiep breakthrough enemies from src/data/tribulations/.

        Keyed by 'key'. Drop a new trib_realm_XX.json to add a tribulation
        without touching the registry.
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "tribulations"
        if not base.exists():
            log.warning("GameRegistry: Missing tribulations/ directory")
            return {}
        for path in sorted(base.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                merged[entry["key"]] = entry
        return merged

    def _merge_subdir(self, subdir: str, filenames: tuple[str, ...]) -> dict[str, dict]:
        """Load and merge JSON arrays from a data subdirectory, keyed by 'key' field."""
        merged: dict[str, dict] = {}
        base = DATA_DIR / subdir
        for name in filenames:
            data = json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
            for entry in data:
                merged[entry["key"]] = entry
        return merged

    def _load_forge_recipes(self) -> list[dict]:
        """Load grade-ordered forge recipes from src/data/equipment/forge_recipes.json."""
        path = DATA_DIR / "equipment" / "forge_recipes.json"
        if not path.exists():
            log.error("GameRegistry: Missing forge_recipes.json")
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return sorted(data, key=lambda r: r["grade"])

    def _load_keyed(self, filename: str) -> dict[str, dict]:
        """Load JSON dạng list và chuyển về dict với key là trường 'key'."""
        path = DATA_DIR / filename
        if not path.exists():
            log.error(f"GameRegistry: Thiếu file dữ liệu quan trọng: {path}")
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item["key"]: item for item in data}

    def _load_sect_levels(self) -> list[dict]:
        """Load the Tông Môn level curve from ``src/data/sects/sect_levels.json``.

        Returns the rows sorted by ``level`` so ``sect.level_row`` can index
        directly. Consumed by ``src.game.systems.sect``.
        """
        path = DATA_DIR / "sects" / "sect_levels.json"
        if not path.exists():
            log.error(f"GameRegistry: Thiếu file dữ liệu quan trọng: {path}")
            return []
        rows = json.loads(path.read_text(encoding="utf-8"))
        return sorted(rows, key=lambda r: int(r["level"]))

    def _load_keyed_list(self, filename: str) -> list[dict]:
        """Load a JSON list file verbatim (order preserved)."""
        path = DATA_DIR / filename
        if not path.exists():
            log.error(f"GameRegistry: Thiếu file dữ liệu quan trọng: {path}")
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_sect_shop(self) -> dict[str, list[dict]]:
        """Load the Tông Môn shop catalog (``{"fixed": [...], "rotating": [...]}``)."""
        path = DATA_DIR / "sects" / "sect_shop.json"
        if not path.exists():
            log.error(f"GameRegistry: Thiếu file dữ liệu quan trọng: {path}")
            return {"fixed": [], "rotating": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"fixed": data.get("fixed", []), "rotating": data.get("rotating", [])}

    def _load_loot_table_dir(self) -> dict[str, list[dict]]:
        """Merge all JSON files from src/data/loot_tables/ into one loot-table dict.

        Each file is a JSON object mapping table_key → list of drop entries.
        Files are loaded in sorted order so naming (zone_01, zone_03, …, bosses, chests)
        determines precedence on key collision (last writer wins).
        Drop a new .json file into the directory to add a custom farm zone — no registry
        changes required.
        """
        merged: dict[str, list[dict]] = {}
        base = DATA_DIR / "loot_tables"
        if not base.exists():
            log.error("GameRegistry: Missing loot_tables/ directory")
            return {}
        for path in sorted(base.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            merged.update(data)
        return merged

    # ── Getters ──────────────────────────────────────────────────────────────

    def get_item(self, key: str) -> dict | None:
        item = self.items.get(key)
        if item is not None:
            return item
        # Pill recipes ship as inventory drops (chest loot, world-boss rewards),
        # so callers like the world-boss embed look them up via ``get_item``
        # to render a Vietnamese name. The recipe registry is keyed separately,
        # so without this fallback the embed shows the raw key
        # (e.g. ``DanPhuongThuyLuyenHuyetDan``) instead of "Đan Phương - Thủy
        # Luyện Huyết Đan". Inject ``type="pill_recipe"`` so the dict can be
        # filtered by category like any other inventory item.
        recipe = self.get_pill_recipe(key)
        if recipe is not None:
            return {**recipe, "type": "pill_recipe"}
        return None

    def get_skill(self, key: str) -> dict | None:
        return self.skills.get(key)

    def get_enemy(self, key: str) -> dict | None:
        return self.enemies.get(key)

    def get_tribulation(self, key: str) -> dict | None:
        """Lấy Thiên Kiếp theo key; fallback về default_heavenly_trib nếu chưa định nghĩa."""
        return self.tribulations.get(key) or self.tribulations.get("default_heavenly_trib")

    def get_formation(self, key: str) -> dict | None:
        return self.formations.get(key)

    def get_constitution(self, key: str) -> dict | None:
        return self.constitutions.get(key)

    def rollable_constitutions(self) -> list[dict]:
        """Return constitutions eligible for the /register random roll.

        A constitution is rollable iff its ``roll_weight`` is > 0 AND it has
        no ``special_requirements`` (late-game thần thể cannot appear here).
        Mid-chain progression entries (``progresses_from`` set) are also
        gated out — they must be reached by activating their predecessor.
        """
        return [
            c for c in self.constitutions.values()
            if int(c.get("roll_weight", 0)) > 0
            and not c.get("special_requirements")
            and not c.get("progresses_from")
        ]

    def get_dungeon(self, key: str) -> dict | None:
        return self.dungeons.get(key)

    def get_loot_table(self, key: str) -> list[dict]:
        """Return the loot table for ``key``, merging dynamic drops.

        Two injection lanes on top of the static JSON:
          * global world drops (``inject_global_drops``) — appended to EVERY
            non-empty table so ultrarare "drops anywhere" items (Thiên Mệnh
            Thạch) reach zones, dungeons, chests, and world bosses uniformly;
          * grade 3-4 skill scrolls (``inject_scroll_drops``) — ``LootZone_<N>``
            farming zones only, so adding or re-grading a skill flows through
            to drops without touching the zone JSON files.
        """
        static = self.loot_tables.get(key, [])
        if not static:
            return static
        extra = inject_global_drops(static)
        if key.startswith("LootZone_"):
            try:
                zone_realm = int(key.removeprefix("LootZone_"))
            except ValueError:
                zone_realm = None
            if zone_realm is not None:
                extra = inject_scroll_drops(
                    self.items, self.skills, zone_realm, static,
                ) + extra
        return list(static) + extra

    def get_base(self, key: str) -> dict | None:
        return self.bases.get(key)

    def get_affix(self, key: str) -> dict | None:
        return self.affixes.get(key)

    def get_unique(self, key: str) -> dict | None:
        return self.uniques.get(key)

    def get_super_material(self, key: str) -> dict | None:
        """Return a super-rare forge material definition, or None if not one.

        Super materials carry a ``granted_passive`` dict that is grafted onto
        the forged item at craft time. A forge operation may consume at most
        one — the forge entry point enforces this via a singular argument.
        """
        item = self.items.get(key)
        if item and item.get("type") == "super_material":
            return item
        return None

    def get_world_boss(self, key: str) -> dict | None:
        return self.world_bosses.get(key)

    def get_linh_can_material(self, key: str) -> dict | None:
        item = self.items.get(key)
        if item and item.get("type") == "linh_can_material":
            return item
        return None

    def linh_can_materials_for(
        self, element: str, role: str, level: int | None = None,
    ) -> list[dict]:
        """Lookup linh_can materials by element and role (unlock/upgrade/catalyst)."""
        out: list[dict] = []
        for item in self.items.values():
            if item.get("type") != "linh_can_material":
                continue
            if item.get("linh_can_role") != role:
                continue
            if role != "catalyst" and item.get("linh_can_element") != element:
                continue
            if role == "upgrade" and level is not None and item.get("linh_can_level") != level:
                continue
            out.append(item)
        return out

    def get_pill_recipe(self, key: str) -> dict | None:
        return self.pill_recipes.get(key)

    def get_herb(self, key: str) -> dict | None:
        """Return an alchemy ingredient by key (``type == "herb"``)."""
        item = self.items.get(key)
        if item and item.get("type") == "herb":
            return item
        return None

    def get_pill(self, key: str) -> dict | None:
        item = self.items.get(key)
        if item and item.get("type") == "pill":
            return item
        return None

    def get_furnace(self, key: str) -> dict | None:
        item = self.items.get(key)
        if item and item.get("type") == "furnace":
            return item
        return None

    def all_furnaces(self) -> list[dict]:
        return [i for i in self.items.values() if i.get("type") == "furnace"]

    def world_bosses_for_realm(self, realm_level: int) -> list[dict]:
        """Return all world bosses whose ``realm`` (1-9) matches the requested realm."""
        return [b for b in self.world_bosses.values() if b.get("realm") == realm_level]

    def bases_for_slot(self, slot: str) -> list[dict]:
        return [b for b in self.bases.values() if b["slot"] == slot]

    # ── Helpers cho Logic Game ────────────────────────────────────────────────

    def dungeons_for_realm(self, qi_realm: int) -> list[dict]:
        """Trả về danh sách bí cảnh mà người chơi có thể vào."""
        return [d for d in self.dungeons.values() if d.get("required_qi_realm", 0) <= qi_realm]

    def dungeons_of_type(self, dungeon_type: str) -> list[dict]:
        """Return all dungeons matching a dungeon_type (``normal``, ``duoc_vien``,
        ``the_chat``, ``linh_can``, ``cam_dia``, ``thap_van_dai_son``)."""
        return [d for d in self.dungeons.values()
                if d.get("dungeon_type") == dungeon_type]

    def pill_recipes_for_realm(self, qi_realm: int) -> list[dict]:
        """Return Luyện Đan recipes whose min_qi_realm is unlocked."""
        return [r for r in self.pill_recipes.values()
                if r.get("min_qi_realm", 0) <= qi_realm]

    def items_by_type(self, item_type: str) -> list[dict]:
        """Lọc vật phẩm theo loại (pill, gem, scroll, ...)."""
        return [i for i in self.items.values() if i.get("type") == item_type]

    def enemies_by_rank(self, rank: str) -> list[dict]:
        """Lọc quái vật theo rank. thien_kiep trả về từ tribulations, các rank khác từ enemies."""
        if rank == "thien_kiep":
            return [t for t in self.tribulations.values() if t.get("rank") == rank]
        return [e for e in self.enemies.values() if e.get("rank") == rank]

registry = GameRegistry.get()