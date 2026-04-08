"""Game data registry — loads all static JSON files once at startup."""
from __future__ import annotations
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent


class GameRegistry:
    """Singleton holding all static game data."""
    _instance: GameRegistry | None = None

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}               # key → item data
        self.skills: dict[str, dict] = {}              # key → skill data
        self.enemies: dict[str, dict] = {}             # key → enemy data
        self.formations: dict[str, dict] = {}          # key → formation data
        self.constitutions: dict[str, dict] = {}       # key → constitution data
        self.dungeons: dict[str, dict] = {}            # key → dungeon data
        self.loot_tables: dict[str, list[dict]] = {}   # key → list of drop entries
        self._loaded = False

    @classmethod
    def get(cls) -> GameRegistry:
        if cls._instance is None:
            cls._instance = GameRegistry()
            cls._instance.load()
        return cls._instance

    # Item sub-files loaded from src/data/items/
    _ITEM_FILES = ("chests", "elixirs", "gems", "materials", "scrolls", "specials")
    # Skill sub-files loaded from src/data/skills/
    _SKILL_FILES = ("thien", "dia", "nhan", "tran_phap")

    def load(self) -> None:
        self.items = self._load_items()
        self.skills = self._load_skills()
        self.enemies = self._load_keyed("enemies.json")
        self.formations = self._load_keyed("formations.json")
        self.constitutions = self._load_keyed("constitutions.json")
        self.dungeons = self._load_keyed("dungeons.json")
        self.loot_tables = self._load_dict("loot_tables.json")
        self._loaded = True

    def _load_items(self) -> dict[str, dict]:
        """Merge all per-type item files from src/data/items/ into one dict."""
        return self._merge_subdir("items", self._ITEM_FILES)

    def _load_skills(self) -> dict[str, dict]:
        """Merge all per-type skill files from src/data/skills/ into one dict."""
        return self._merge_subdir("skills", self._SKILL_FILES)

    def _merge_subdir(self, subdir: str, filenames: tuple[str, ...]) -> dict[str, dict]:
        """Load and merge JSON arrays from a data subdirectory, keyed by 'key' field."""
        merged: dict[str, dict] = {}
        base = DATA_DIR / subdir
        for name in filenames:
            data = json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
            for entry in data:
                merged[entry["key"]] = entry
        return merged

    def _load_keyed(self, filename: str) -> dict[str, dict]:
        """Load a JSON array of objects and index them by their 'key' field."""
        path = DATA_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item["key"]: item for item in data}

    def _load_dict(self, filename: str) -> dict:
        """Load a JSON object directly (already keyed at the top level)."""
        path = DATA_DIR / filename
        return json.loads(path.read_text(encoding="utf-8"))

    def get_item(self, key: str) -> dict | None:
        return self.items.get(key)

    def get_skill(self, key: str) -> dict | None:
        return self.skills.get(key)

    def get_enemy(self, key: str) -> dict | None:
        return self.enemies.get(key)

    def get_formation(self, key: str) -> dict | None:
        return self.formations.get(key)

    def get_constitution(self, key: str) -> dict | None:
        return self.constitutions.get(key)

    def get_dungeon(self, key: str) -> dict | None:
        return self.dungeons.get(key)

    def get_loot_table(self, key: str) -> list[dict]:
        """Return the drop entry list for a loot table key, or [] if not found."""
        return self.loot_tables.get(key, [])

    def dungeons_for_realm(self, qi_realm: int) -> list[dict]:
        """Return all dungeons the player can enter (required_qi_realm <= qi_realm)."""
        return [d for d in self.dungeons.values() if d.get("required_qi_realm", 0) <= qi_realm]

    def items_by_type(self, item_type: str) -> list[dict]:
        return [i for i in self.items.values() if i.get("type") == item_type]

    def enemies_by_rank(self, rank: str) -> list[dict]:
        return [e for e in self.enemies.values() if e.get("rank") == rank]


registry = GameRegistry.get()
