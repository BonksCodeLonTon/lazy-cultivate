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
        self._loaded = False

    @classmethod
    def get(cls) -> GameRegistry:
        if cls._instance is None:
            cls._instance = GameRegistry()
            cls._instance.load()
        return cls._instance

    # Danh mục file item nằm trong thư mục con src/data/items/
    _ITEM_FILES = ("chests", "elixirs", "gems", "materials", "scrolls", "specials", "forge_materials")
    # Equipment definition files in src/data/equipment/
    _EQUIP_FILES = ("bases", "affixes", "uniques")

    def load(self) -> None:
        self.items = self._load_items()
        self.skills = self._load_skills()
        self.enemies = self._load_enemy_dir()
        self.tribulations = self._load_tribulation_dir()
        self.formations = self._load_keyed("formations.json")
        self.constitutions = self._load_keyed("constitutions.json")
        self.dungeons = self._load_keyed("dungeons.json")
        self.loot_tables = self._load_loot_table_dir()
        self.bases, self.affixes, self.uniques = self._load_equipment_defs()
        self.forge_recipes = self._load_forge_recipes()
        self.world_bosses = self._load_keyed("world_bosses.json")
        self._loaded = True

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
        """Merge all JSON files from src/data/enemies/ into one dict, keyed by 'key'.

        Files are loaded in sorted order (realm_01.json … realm_10.json) so that
        adding a new realm file requires no registry changes — just drop the file in.
        """
        merged: dict[str, dict] = {}
        base = DATA_DIR / "enemies"
        if not base.exists():
            log.error("GameRegistry: Missing enemies/ directory")
            return {}
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

    def get_dungeon(self, key: str) -> dict | None:
        return self.dungeons.get(key)

    def get_loot_table(self, key: str) -> list[dict]:
        return self.loot_tables.get(key, [])

    def get_base(self, key: str) -> dict | None:
        return self.bases.get(key)

    def get_affix(self, key: str) -> dict | None:
        return self.affixes.get(key)

    def get_unique(self, key: str) -> dict | None:
        return self.uniques.get(key)

    def get_world_boss(self, key: str) -> dict | None:
        return self.world_bosses.get(key)

    def world_bosses_for_realm(self, realm_level: int) -> list[dict]:
        """Return all world bosses whose ``realm`` (1-9) matches the requested realm."""
        return [b for b in self.world_bosses.values() if b.get("realm") == realm_level]

    def bases_for_slot(self, slot: str) -> list[dict]:
        return [b for b in self.bases.values() if b["slot"] == slot]

    # ── Helpers cho Logic Game ────────────────────────────────────────────────

    def dungeons_for_realm(self, qi_realm: int) -> list[dict]:
        """Trả về danh sách bí cảnh mà người chơi có thể vào."""
        return [d for d in self.dungeons.values() if d.get("required_qi_realm", 0) <= qi_realm]

    def items_by_type(self, item_type: str) -> list[dict]:
        """Lọc vật phẩm theo loại (material, elixir, gem, ...)."""
        return [i for i in self.items.values() if i.get("type") == item_type]

    def enemies_by_rank(self, rank: str) -> list[dict]:
        """Lọc quái vật theo rank. thien_kiep trả về từ tribulations, các rank khác từ enemies."""
        if rank == "thien_kiep":
            return [t for t in self.tribulations.values() if t.get("rank") == rank]
        return [e for e in self.enemies.values() if e.get("rank") == rank]

registry = GameRegistry.get()