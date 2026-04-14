"""
Item name database for Crimson Desert save editor.
Loads from crimson_data.db (SQLite). Falls back to a GitHub download when the
database has no items (e.g., first run before build_db.py).
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List
from urllib.request import urlopen, Request
from urllib.error import URLError

from data_db import get_connection, get_db_path, open_writable, reset_connection
from models import ItemInfo

log = logging.getLogger(__name__)

GITHUB_URL = (
    "https://raw.githubusercontent.com/"
    "NattKh/CrimsonDesertCommunityItemMapping/main/item_names.json"
)


class ItemNameDB:
    """Item name and category database backed by crimson_data.db."""

    def __init__(self) -> None:
        self.items: Dict[int, ItemInfo] = {}
        self.loaded_path: str = ""
        self.version: int = 0
        self.load_auto()

    def load_auto(self) -> str:
        """Load from SQLite. If empty, attempt a GitHub download."""
        self.load()
        if self.items:
            return self.loaded_path

        try:
            ok, msg = self.sync_from_github()
            if ok and self.items:
                log.info("Bootstrap sync: %s", msg)
                return self.loaded_path
        except Exception as exc:
            log.warning("Bootstrap GitHub sync failed: %s", exc)
        return ""

    def load(self) -> None:
        """Load item names from crimson_data.db."""
        self.items.clear()
        self.version = 0
        try:
            db = get_connection()
            rows = db.execute(
                "SELECT item_key, name, internal_name, category, max_stack FROM items"
            ).fetchall()
            for row in rows:
                key = row["item_key"]
                self.items[key] = ItemInfo(
                    item_key=key,
                    name=row["name"],
                    internal_name=row["internal_name"],
                    category=row["category"],
                    max_stack=row["max_stack"],
                )
            self.loaded_path = get_db_path()
            log.info("Loaded %d items from SQLite", len(self.items))
        except Exception as exc:
            log.warning("SQLite item load failed: %s", exc)

    def save(self) -> None:
        """Persist current items dict to crimson_data.db via INSERT OR REPLACE."""
        if not self.items:
            return
        rows = [
            (
                key,
                info.name,
                info.internal_name,
                info.category,
                info.max_stack,
            )
            for key, info in self.items.items()
        ]
        try:
            conn = open_writable()
            conn.executemany(
                "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)", rows
            )
            conn.commit()
            conn.close()
            reset_connection()
            log.info("Saved %d items to SQLite", len(rows))
        except Exception as exc:
            log.warning("SQLite item save failed: %s", exc)

    def apply_localization(self) -> int:
        """Override item names with localized versions from the active language pack.
        Returns the number of items translated."""
        try:
            from localization import get_language, _names_data
            if get_language() == "en" or not _names_data:
                return 0
            items_map = _names_data.get("items", {})
            if not items_map:
                return 0
            count = 0
            for key, info in self.items.items():
                localized = items_map.get(str(key), "")
                if localized:
                    info.name = localized
                    count += 1
            return count
        except Exception:
            return 0

    def get_name(self, key: int) -> str:
        """Get display name for an item key."""
        info = self.items.get(key)
        if info and info.name:
            return info.name
        return f"Unknown ({key})"

    def get_category(self, key: int) -> str:
        """Get category for an item key."""
        info = self.items.get(key)
        return info.category if info else "Misc"

    def rename_item(self, key: int, new_name: str) -> None:
        """Rename an item in the database."""
        if key in self.items:
            self.items[key].name = new_name
        else:
            self.items[key] = ItemInfo(
                item_key=key,
                name=new_name,
                category="Misc",
            )

    def get_all_sorted(self) -> List[ItemInfo]:
        """Get all items sorted by key."""
        return [self.items[k] for k in sorted(self.items.keys())]

    def get_internal_name(self, key: int) -> str:
        """Get internal name for an item key."""
        info = self.items.get(key)
        return info.internal_name if info else ""

    def search(self, query: str) -> List[ItemInfo]:
        """Search items by name, internal name, or key (partial match)."""
        query_lower = query.lower().strip()
        if not query_lower:
            return self.get_all_sorted()

        results = []
        for info in self.items.values():
            if (
                query_lower in info.name.lower()
                or query_lower in info.internal_name.lower()
                or query_lower in str(info.item_key)
            ):
                results.append(info)
        results.sort(key=lambda x: x.item_key)
        return results

    def sync_from_github(self) -> tuple[bool, str]:
        """Download latest item_names.json from GitHub and merge into items + SQLite.

        Returns (success, message).
        """
        try:
            req = Request(GITHUB_URL, headers={"User-Agent": "CrimsonSaveEditor/1.0"})
            with urlopen(req, timeout=10) as resp:
                raw = resp.read()
                data = json.loads(raw.decode("utf-8"))
        except (URLError, json.JSONDecodeError, OSError) as exc:
            return False, f"Download failed: {exc}"

        remote_version = data.get("version", 0)
        remote_items = data.get("items", [])

        if remote_version <= self.version and self.items:
            return True, f"Already up to date (v{self.version})."

        added = 0
        updated = 0
        for entry in remote_items:
            key = entry.get("itemKey", 0)
            if key <= 0:
                continue
            name = entry.get("name", "")
            category = entry.get("category", "Misc")
            internal = entry.get("internalName", "")
            max_stack = entry.get("maxStack", 0)

            if key not in self.items:
                self.items[key] = ItemInfo(
                    item_key=key,
                    name=name,
                    internal_name=internal,
                    category=category,
                    max_stack=max_stack,
                )
                added += 1
            else:
                existing = self.items[key]
                if name and (not existing.name or existing.name.startswith("Unknown")):
                    existing.name = name
                    updated += 1
                if internal and not existing.internal_name:
                    existing.internal_name = internal
                if category != "Misc" and existing.category == "Misc":
                    existing.category = category
                if max_stack and not existing.max_stack:
                    existing.max_stack = max_stack

        if remote_version > self.version:
            self.version = remote_version

        self.save()
        return True, f"Synced v{remote_version}: {added} new, {updated} updated."
