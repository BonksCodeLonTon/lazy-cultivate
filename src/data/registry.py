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

    def load(self) -> None:
        """Thực hiện load toàn bộ dữ liệu từ các file JSON."""
        try:
            self.items = self._load_items()
            self.skills = self._load_keyed("skills.json")
            self.enemies = self._load_keyed("enemies.json")
            self.formations = self._load_keyed("formations.json")
            self.constitutions = self._load_keyed("constitutions.json")
            self.dungeons = self._load_keyed("dungeons.json")
            self.loot_tables = self._load_dict("loot_tables.json")
            self._loaded = True
            log.info("GameRegistry: Toàn bộ dữ liệu đã được tải thành công.")
        except Exception as e:
            log.error(f"GameRegistry: Lỗi khi tải dữ liệu: {e}")

    def _load_items(self) -> dict[str, dict]:
        """Gộp tất cả các file item nhỏ thành một dictionary duy nhất."""
        merged: dict[str, dict] = {}
        items_dir = DATA_DIR / "items"
        for name in self._ITEM_FILES:
            path = items_dir / f"{name}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data:
                    merged[item["key"]] = item
            else:
                log.warning(f"GameRegistry: Không tìm thấy file item: {path}")
        return merged

    def _load_keyed(self, filename: str) -> dict[str, dict]:
        """Load JSON dạng list và chuyển về dict với key là trường 'key'."""
        path = DATA_DIR / filename
        if not path.exists():
            log.error(f"GameRegistry: Thiếu file dữ liệu quan trọng: {path}")
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item["key"]: item for item in data}

    def _load_dict(self, filename: str) -> dict:
        """Load JSON trực tiếp thành dict (dành cho loot_tables)."""
        path = DATA_DIR / filename
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

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