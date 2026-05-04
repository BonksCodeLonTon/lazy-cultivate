"""Game data registry — loads all static JSON files once at startup."""
from __future__ import annotations
import json
import logging
from pathlib import Path

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
        self._loaded = False

    @classmethod
    def get(cls) -> GameRegistry:
        if cls._instance is None:
            cls._instance = GameRegistry()
            cls._instance.load()
        return cls._instance

    # Danh mục file item nằm trong thư mục con src/data/items/
    _ITEM_FILES = (
        "chests", "elixirs", "gems", "unique_gems", "materials", "scrolls", "specials",
        "forge_materials", "super_materials",
        "herbs", "yeu_thu", "pills", "furnaces",
        "constitution_materials",
        "linh_can_material",
        "world_boss_chests",
    )
    # Equipment definition files in src/data/equipment/
    _EQUIP_FILES = ("bases", "affixes", "uniques")

    def load(self) -> None:
        self.items = self._load_items()
        self.skills = self._load_skills()
        self._synthesize_skill_scrolls()
        self.enemies = self._load_enemy_dir()
        self.tribulations = self._load_tribulation_dir()
        self.formations = self._load_keyed("formations.json")
        self.constitutions = self._load_constitution_dir()
        self.dungeons = self._load_keyed("dungeons.json")
        self.loot_tables = self._load_loot_table_dir()
        self.bases, self.affixes, self.uniques = self._load_equipment_defs()
        self.forge_recipes = self._load_forge_recipes()
        self.world_bosses = self._load_keyed("world_bosses.json")
        self.pill_recipes = self._load_pill_recipes()
        self._loaded = True

    # ── Per-skill scroll synthesis ───────────────────────────────────────────
    # One Scroll_<SkillKey> item per learnable player skill, generated at load
    # time so /shop, /inventory, and the learn flow can treat them like any
    # other registry item. Skips Enemy* and TheChat_* skills (NPC / constitution
    # only — never learnable by scroll). Existing entries in scrolls.json win
    # so designers can override pricing or copy on a per-skill basis.
    #
    # ``scroll_grade`` reflects skill *power tier*, not realm. A realm-1 skill
    # may be grade 4 (rare, drop-only) and a realm-9 skill may be grade 1
    # (basic, market-buyable). Grade gates *availability* (1-2 in shop, 3-4 in
    # loot drops) and price; realm gates *usability* (player must reach the
    # skill's realm to learn it).

    _SCROLL_PRICE_BY_GRADE: dict[int, int] = {1: 1000, 2: 3000}

    def _synthesize_skill_scrolls(self) -> None:
        for skill_key, skill in self.skills.items():
            if skill_key.startswith("Enemy") or skill_key.startswith("TheChat_"):
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
        """Load Luyện Đan recipes from src/data/recipes/pill_recipes.json."""
        path = DATA_DIR / "recipes" / "pill_recipes.json"
        if not path.exists():
            log.warning("GameRegistry: Missing recipes/pill_recipes.json")
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {entry["key"]: entry for entry in data}

    def _load_items(self) -> dict[str, dict]:
        """Merge all per-type item files from src/data/items/ into one dict."""
        return self._merge_subdir("items", self._ITEM_FILES)

    def _load_equipment_defs(self) -> tuple[dict, dict, dict]:
        """Load bases, affixes, and uniques from src/data/equipment/.

        Uniques are split by build element under ``equipment/uniques/`` —
        ``kim.json``, ``moc.json``, ``thuy.json``, ``hoa.json``, ``tho.json``,
        ``general.json`` — and all merged into a single dict keyed by ``key``.
        The legacy flat ``equipment/uniques.json`` is still picked up if
        present so callers can fall back to a single-file layout.
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

        # Prefer directory layout (equipment/uniques/*.json); fall back to flat file.
        uniques_dir = base_dir / "uniques"
        if uniques_dir.is_dir():
            for path in sorted(uniques_dir.glob("*.json")):
                for entry in json.loads(path.read_text(encoding="utf-8")):
                    uniques[entry["key"]] = entry
        legacy = base_dir / "uniques.json"
        if legacy.exists():
            for entry in json.loads(legacy.read_text(encoding="utf-8")):
                uniques[entry["key"]] = entry

        return bases, affixes, uniques

    def _load_skills(self) -> dict[str, dict]:
        """Merge all JSON files under src/data/skills/** into one dict, keyed by 'key'.

        The directory is split into subfolders (``player/``, ``enemy/``). Player
        skills are grouped by element (``player/kim.json``, ``player/moc.json``,
        …), with ``player/general.json`` for non-elemental attacks/defenses and
        ``player/formation.json`` for every formation skill across elements.
        Enemy skills are grouped by realm tier (``enemy/realm_01.json`` …
        ``enemy/realm_09.json``). Files load in sorted path order, and drop-in
        files require no registry changes.
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "skills"
        if not base.exists():
            log.error("GameRegistry: Missing skills/ directory")
            return {}
        for path in sorted(base.rglob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
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

    def _load_constitution_dir(self) -> dict[str, dict]:
        """Merge all JSON files under src/data/constitutions/ into one dict.

        Files are keyed by the entry's ``key`` field. Drop a new
        ``<element>.json`` to add constitutions without touching the registry.
        Falls back to the legacy single ``constitutions.json`` file if the
        directory doesn't exist (lets external forks migrate at their pace).
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "constitutions"
        if not base.exists():
            legacy = DATA_DIR / "constitutions.json"
            if legacy.exists():
                for entry in json.loads(legacy.read_text(encoding="utf-8")):
                    merged[entry["key"]] = entry
            else:
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
        return self.items.get(key)

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
        """
        return [
            c for c in self.constitutions.values()
            if int(c.get("roll_weight", 0)) > 0
            and not c.get("special_requirements")
        ]

    def get_dungeon(self, key: str) -> dict | None:
        return self.dungeons.get(key)

    def get_loot_table(self, key: str) -> list[dict]:
        """Return the loot table for ``key``, merging dynamic scroll drops.

        ``LootZone_<N>`` tables are the realm-N farming zones. Grade 3-4
        skill scrolls are injected dynamically here so adding a new skill or
        re-grading an existing one is reflected in drops without touching the
        zone JSON files. A scroll appears in zone N iff its underlying skill
        has ``realm ≤ N`` (player has reached that realm by then) and its
        grade is 3 or 4 (drop-only tier).
        """
        static = self.loot_tables.get(key, [])
        if not key.startswith("LootZone_"):
            return static
        try:
            zone_realm = int(key.removeprefix("LootZone_"))
        except ValueError:
            return static
        return list(static) + self._skill_scroll_drops_for_zone(zone_realm)

    _SCROLL_DROP_WEIGHT_BY_GRADE: dict[int, int] = {3: 200_000, 4: 60_000}

    def _skill_scroll_drops_for_zone(self, zone_realm: int) -> list[dict]:
        """Compute grade 3-4 scroll drops for a realm-N farming zone."""
        out: list[dict] = []
        for item in self.items.values():
            if item.get("type") != "scroll":
                continue
            skill_key = item.get("taught_skill")
            if not skill_key:
                continue
            grade = int(item.get("grade", 0))
            weight = self._SCROLL_DROP_WEIGHT_BY_GRADE.get(grade)
            if weight is None:
                continue
            skill = self.skills.get(skill_key)
            if skill is None:
                continue
            if skill.get("realm", 99) > zone_realm:
                continue
            out.append({
                "item_key": item["key"],
                "weight": weight,
                "qty_min": 1,
                "qty_max": 1,
            })
        return out

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
        """Return an herb/yeu_thu ingredient by key (any alchemy ingredient type)."""
        item = self.items.get(key)
        if item and item.get("type") in ("herb", "yeu_thu"):
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
        """Return all dungeons matching a dungeon_type (``normal`` or ``duoc_vien``).

        Entries without an explicit ``dungeon_type`` field are treated as
        ``normal`` for backward compatibility with legacy dungeon JSON.
        """
        return [d for d in self.dungeons.values()
                if d.get("dungeon_type", "normal") == dungeon_type]

    def pill_recipes_for_realm(self, qi_realm: int) -> list[dict]:
        """Return Luyện Đan recipes whose min_qi_realm is unlocked."""
        return [r for r in self.pill_recipes.values()
                if r.get("min_qi_realm", 0) <= qi_realm]

    def items_by_type(self, item_type: str) -> list[dict]:
        """Lọc vật phẩm theo loại (material, elixir, gem, ...)."""
        return [i for i in self.items.values() if i.get("type") == item_type]

    def enemies_by_rank(self, rank: str) -> list[dict]:
        """Lọc quái vật theo rank. thien_kiep trả về từ tribulations, các rank khác từ enemies."""
        if rank == "thien_kiep":
            return [t for t in self.tribulations.values() if t.get("rank") == rank]
        return [e for e in self.enemies.values() if e.get("rank") == rank]

registry = GameRegistry.get()