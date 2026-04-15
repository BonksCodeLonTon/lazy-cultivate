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

    # Danh mục file item nằm trong thư mục con src/data/items/
    _ITEM_FILES = ("chests", "elixirs", "gems", "materials", "scrolls", "specials")
    # Skill sub-files loaded from src/data/skills/
    _SKILL_FILES = ("thien", "dia", "nhan", "tran_phap", "enemy")

    def load(self) -> None:
        self.items = self._load_items()
        self.skills = self._load_skills()
        self.enemies = self._load_enemy_dir()
        self.formations = self._load_keyed("formations.json")
        self.constitutions = self._load_keyed("constitutions.json")
        self.dungeons = self._load_keyed("dungeons.json")
        self.loot_tables = self._load_loot_table_dir()
        self._loaded = True

    def _load_items(self) -> dict[str, dict]:
        """Merge all per-type item files from src/data/items/ into one dict."""
        return self._merge_subdir("items", self._ITEM_FILES)

    def _load_skills(self) -> dict[str, dict]:
        """Merge all per-type skill files from src/data/skills/ into one dict."""
        return self._merge_subdir("skills", self._SKILL_FILES)

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
        """
        Lấy dữ liệu quái vật. 
        Nếu không tìm thấy key Thiên Kiếp cụ thể, hệ thống sẽ trả về quái vật mặc định.
        """
        enemy = self.enemies.get(key)
        if not enemy and key.startswith("trib_"):
             # Fallback cho thiên kiếp nếu chưa định nghĩa từng cảnh giới
            return self.enemies.get("default_heavenly_trib")
        return enemy

    def get_formation(self, key: str) -> dict | None:
        return self.formations.get(key)

    def get_constitution(self, key: str) -> dict | None:
        return self.constitutions.get(key)

    def get_dungeon(self, key: str) -> dict | None:
        return self.dungeons.get(key)

    def get_loot_table(self, key: str) -> list[dict]:
        return self.loot_tables.get(key, [])

    # ── Helpers cho Logic Game ────────────────────────────────────────────────

    def dungeons_for_realm(self, qi_realm: int) -> list[dict]:
        """Trả về danh sách bí cảnh mà người chơi có thể vào."""
        return [d for d in self.dungeons.values() if d.get("required_qi_realm", 0) <= qi_realm]

    def items_by_type(self, item_type: str) -> list[dict]:
        """Lọc vật phẩm theo loại (material, elixir, gem, ...)."""
        return [i for i in self.items.values() if i.get("type") == item_type]

    def enemies_by_rank(self, rank: str) -> list[dict]:
        """Lọc quái vật theo rank (pho_thong, tinh_anh, thien_kiep, ...)."""
        return [e for e in self.enemies.values() if e.get("rank") == rank]

registry = GameRegistry.get()