"""
Shared iteminfo.pabgb cache for gem and item property lookups.

ItemInfoCache is owned by MainWindow and shared with SocketsTab and
ItemBuffsTab. It loads lazily from the PAZ archives on first query, and
is refreshed when ItemBuffsTab performs a fresh extraction via
update_from_lookup().

Two load paths:
  1. crimson_rs (Potter's Rust parser) — fast, full field access
  2. Python fallback via iteminfo_parser — always available; extracts
     max_endurance from tail_raw[-6:-4] (verified: 100 for durability
     gems, 65535 for permanent gems across all tested item types)
"""
from __future__ import annotations

import logging
import struct
from typing import Dict, Optional

log = logging.getLogger(__name__)


class ItemInfoCache:
    """
    Lazy cache of iteminfo.pabgb item properties.

    Shared at MainWindow scope between ItemBuffsTab and SocketsTab.
    All access is from the main thread — no locking required.
    """

    def __init__(self) -> None:
        self._game_path: str = ""
        self._data: Dict[int, dict] = {}
        self._loaded: bool = False
        self._load_attempted: bool = False


    def set_game_path(self, path: str) -> None:
        """Invalidate the cache when the game install path changes."""
        if path != self._game_path:
            self._game_path = path
            self._invalidate()

    def update_from_lookup(self, rust_lookup: Dict[int, dict]) -> None:
        """Absorb a freshly-built rust_lookup dict from ItemBuffsTab.

        Called after every successful Rust extraction so the cache always
        reflects the most recently seen on-disk state of iteminfo.pabgb,
        including any mods that alter it.
        """
        if rust_lookup is self._data:
            return
        self._data = rust_lookup
        self._loaded = True
        log.debug("ItemInfoCache: updated from lookup (%d items)", len(self._data))

    def _invalidate(self) -> None:
        self._data = {}
        self._loaded = False
        self._load_attempted = False

    def _try_load(self) -> None:
        """Load from PAZ archives on demand. Tries crimson_rs first, then Python fallback."""
        if self._loaded or self._load_attempted:
            return
        if not self._game_path:
            return
        self._load_attempted = True
        raw: Optional[bytes] = None
        try:
            import crimson_rs
            from paz_patcher import ItemBuffPatcher
            patcher = ItemBuffPatcher(self._game_path)
            raw = bytes(patcher.extract_iteminfo())
            items = crimson_rs.parse_iteminfo_from_bytes(raw)
            self._data = {it['key']: it for it in items}
            self._loaded = True
            log.debug("ItemInfoCache: loaded %d items via crimson_rs", len(self._data))
        except ImportError:
            log.debug("ItemInfoCache: crimson_rs unavailable, using Python parser")
            self._try_load_python(raw)
        except Exception as e:
            log.debug("ItemInfoCache: crimson_rs failed (%s), using Python parser", e)
            self._try_load_python(raw)

    def _try_load_python(self, raw: Optional[bytes] = None) -> None:
        """Extract max_endurance from iteminfo.pabgb using the Python parser.

        max_endurance is a u16 at tail_raw[-6:-4] in each ItemRecord.
        Verified: durability gems = 100, permanent gems = 65535.
        """
        try:
            import iteminfo_parser as _ip
            if raw is None:
                from paz_patcher import ItemBuffPatcher
                patcher = ItemBuffPatcher(self._game_path)
                raw = bytes(patcher.extract_iteminfo())
            all_items = _ip.find_all_items(raw)
            data: Dict[int, dict] = {}
            for i, (off, key, _name) in enumerate(all_items):
                nxt = all_items[i + 1][0] if i + 1 < len(all_items) else len(raw)
                try:
                    rec = _ip.parse_item(raw, off, nxt)
                    if rec and len(rec.tail_raw) >= 6:
                        max_end = struct.unpack_from('<H', rec.tail_raw, len(rec.tail_raw) - 6)[0]
                        data[key] = {'key': key, 'max_endurance': max_end}
                except Exception:
                    pass
            self._data = data
            self._loaded = True
            log.debug("ItemInfoCache: loaded %d items via Python parser", len(data))
        except Exception as e:
            log.debug("ItemInfoCache: Python parser failed (%s)", e)


    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def get_item(self, item_key: int) -> dict:
        self._try_load()
        return self._data.get(item_key, {})

    def get_max_endurance(self, item_key: int) -> Optional[int]:
        """Return max_endurance for item_key, or None if not found."""
        v = self.get_item(item_key).get('max_endurance')
        return int(v) if v is not None else None

    def is_durability_gem(self, item_key: int) -> bool:
        """Return True if the gem has finite endurance (0 < max_endurance < 65535)."""
        v = self.get_max_endurance(item_key)
        return v is not None and 0 < v < 65535
