"""
Data classes for Crimson Desert Save Editor.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class QuestState(IntEnum):
    LOCKED           = 0x0D01
    AVAILABLE        = 0x0902
    AVAILABLE_PLUS   = 0x0903
    IN_PROGRESS      = 0x0905
    IN_PROGRESS_PLUS = 0x1102
    COMPLETED        = 0x1105
    SIDE_CONTENT     = 0x1502
    FULLY_COMPLETED  = 0x1905


@dataclass
class SaveItem:
    """Represents a single item found in the decompressed save blob."""
    offset: int = 0
    item_no: int = 0
    item_key: int = 0
    slot_no: int = 0
    stack_count: int = 0
    enchant_level: int = 0
    endurance: int = 0
    sharpness: int = 0

    @property
    def actual_endurance(self) -> int:
        """Low byte of endurance — the real durability value."""
        return self.endurance & 0xFF

    @property
    def socket_count_from_endurance(self) -> int:
        """High byte of endurance — number of unlocked socket slots."""
        return (self.endurance >> 8) & 0xFF
    has_enchant: bool = False
    is_equipment: bool = False
    source: str = "Inventory"
    bag: str = ""
    section: int = 0
    name: str = ""
    category: str = "Misc"
    block_size: int = 0
    field_offsets: dict = field(default_factory=dict)
    parc_parsed: bool = False


@dataclass
class ParseCache:
    """Cached result of parse_and_collect for the current decompressed blob.

    Maintained in sync with SaveData.decompressed_blob after each splice.
    Set to None to force a re-parse on next fill/clear.
    """
    offset_positions: list
    trailing_sizes: list
    schema_end: int
    toc_entries: list

    def apply_splice(
        self,
        old_start: int,
        old_end: int,
        delta: int,
        new_po_entries: list,
        new_ts_entries: list,
    ) -> None:
        """Update the cache to reflect a splice that replaced
        [old_start, old_end) with (old_end - old_start + delta) bytes.
        """
        updated_po = []
        for po_pos, po_val in self.offset_positions:
            if old_start <= po_pos < old_end:
                continue
            new_pos = po_pos + delta if po_pos >= old_end else po_pos
            new_val = po_val + delta if po_val >= old_end else po_val
            updated_po.append((new_pos, new_val))
        updated_po.extend(new_po_entries)
        self.offset_positions = updated_po

        updated_ts = []
        for size_pos, payload_start in self.trailing_sizes:
            if old_start <= size_pos < old_end:
                continue
            new_size_pos = (
                size_pos + delta if size_pos >= old_end else size_pos
            )
            new_payload_start = (
                payload_start + delta if payload_start >= old_end else payload_start
            )
            updated_ts.append((new_size_pos, new_payload_start))
        updated_ts.extend(new_ts_entries)
        self.trailing_sizes = updated_ts

        for e in self.toc_entries:
            if e.data_offset >= old_end:
                e.data_offset += delta


@dataclass
class SaveData:
    """Holds a loaded save file's state."""
    raw_header: bytes = b""
    decompressed_blob: bytearray = field(default_factory=bytearray)
    original_compressed_size: int = 0
    original_decompressed_size: int = 0
    file_path: str = ""
    is_raw_stream: bool = False
    parse_cache: Optional['ParseCache'] = None


@dataclass
class ItemInfo:
    """An entry from the item name database."""
    item_key: int = 0
    name: str = ""
    internal_name: str = ""
    category: str = "Misc"
    max_stack: int = 0


@dataclass
class UndoEntry:
    """Stores an undo-able edit."""
    description: str = ""
    offset: int = 0
    old_bytes: bytes = b""
    new_bytes: bytes = b""
    patches: list = field(default_factory=list)
