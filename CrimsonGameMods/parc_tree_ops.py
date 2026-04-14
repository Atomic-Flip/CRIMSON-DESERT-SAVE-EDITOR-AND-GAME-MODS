"""
PARC Tree Operations — Modify the parsed save tree in memory.

All operations modify the GenericFieldValue tree. The serializer
(parc_tree_serializer.py) handles producing correct bytes.

Operations:
    set_field_value()       — Change a scalar field's value
    insert_list_element()   — Add a new element to an object list
    remove_list_element()   — Remove an element from an object list
    clone_element()         — Deep-copy a tree node (clearing stale offsets)
    expand_bitmask()        — Add a field that wasn't present before
    navigate()              — Find a field by dot-path in the tree
"""

from __future__ import annotations

import copy
import struct
import logging

log = logging.getLogger(__name__)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Includes', 'desktopeditor'))
from save_parser import GenericFieldValue, ObjectBlock, TypeDef


def navigate(block: ObjectBlock, path: str) -> GenericFieldValue | None:
    """Navigate to a field by dot-path.

    Examples:
        navigate(block, "_list")                → the _list field
        navigate(block, "_list[2]")             → 3rd element of _list
        navigate(block, "_list[2]._itemKey")    → _itemKey in 3rd element

    Returns the GenericFieldValue, or None if not found.
    """
    parts = _parse_path(path)
    current_fields = block.fields

    for part_name, part_index in parts:
        found = None
        for f in current_fields:
            if f.name == part_name:
                found = f
                break
        if found is None:
            return None

        if part_index is not None:
            if found.list_elements is None or part_index >= len(found.list_elements):
                return None
            elem = found.list_elements[part_index]
            current_fields = elem.child_fields or []
            found = elem
        else:
            if found.child_fields is not None:
                current_fields = found.child_fields

    return found


def _parse_path(path: str) -> list[tuple[str, int | None]]:
    """Parse a dot-path like '_list[2]._itemKey' into [(name, index), ...]."""
    import re
    parts = []
    for segment in path.split('.'):
        m = re.match(r'^(\w+)\[(\d+)\]$', segment)
        if m:
            parts.append((m.group(1), int(m.group(2))))
        else:
            parts.append((segment, None))
    return parts


def set_field_value(
    block: ObjectBlock,
    field_path: str,
    new_value,
    raw_blob: bytes | None = None,
) -> bool:
    """Set a scalar field's value in the tree.

    Args:
        block: The ObjectBlock containing the field.
        field_path: Dot-path to the field (e.g., "_list[2]._stackCount").
        new_value: The new value (int, float, bool).
        raw_blob: Original blob (needed to update raw bytes for round-trip).

    Returns True if the field was found and updated.
    """
    field = navigate(block, field_path)
    if field is None:
        log.warning("set_field_value: field %s not found", field_path)
        return False

    if field.meta_kind not in (0, 2):
        log.warning("set_field_value: field %s is not scalar (mk=%d)", field_path, field.meta_kind)
        return False

    if not field.editable or not field.edit_format:
        log.warning("set_field_value: field %s is not editable", field_path)
        return False

    field.value_repr = str(new_value)

    if raw_blob is not None and field.start_offset > 0:
        _write_scalar_to_blob(raw_blob, field, new_value)

    field.note = (field.note or "") + " [modified]"

    return True


def _write_scalar_to_blob(blob: bytes | bytearray, field: GenericFieldValue, value) -> None:
    """Write a scalar value into the blob at the field's position."""
    if not isinstance(blob, bytearray):
        return

    fmt = field.edit_format
    if fmt == "bool":
        blob[field.start_offset] = 1 if value else 0
    elif fmt:
        try:
            struct.pack_into(fmt, blob, field.start_offset, value)
        except struct.error as e:
            log.warning("set_field_value: pack failed for %s: %s", field.name, e)


def clone_element(
    element: GenericFieldValue,
    raw_blob: bytes | None = None,
) -> GenericFieldValue:
    """Deep-copy a GenericFieldValue tree node.

    If raw_blob is provided, also captures the raw bytes so the clone
    can be serialized independently. Clears all offset fields since
    the clone won't be at the same position.

    Returns a new GenericFieldValue that can be inserted into a list.
    """
    cloned = copy.deepcopy(element)

    if raw_blob is not None and element.start_offset > 0 and element.end_offset > element.start_offset:
        cloned._raw_bytes = raw_blob[element.start_offset:element.end_offset]
    else:
        cloned._raw_bytes = None

    _clear_offsets(cloned)

    return cloned


def _clear_offsets(field: GenericFieldValue) -> None:
    """Recursively clear all offset fields in a cloned tree."""
    field.start_offset = 0
    field.end_offset = 0
    field.child_payload_offset = 0

    if field.child_fields:
        for cf in field.child_fields:
            _clear_offsets(cf)

    if field.list_elements:
        for elem in field.list_elements:
            _clear_offsets(elem)
            if elem.child_fields:
                for cf in elem.child_fields:
                    _clear_offsets(cf)


def insert_list_element(
    block: ObjectBlock,
    list_path: str,
    template: GenericFieldValue,
    field_values: dict | None = None,
) -> GenericFieldValue | None:
    """Insert a new element at the end of an object list.

    Args:
        block: The ObjectBlock containing the list.
        list_path: Dot-path to the list field (e.g., "_list").
        template: A GenericFieldValue to clone as the new element.
                  Should come from clone_element() of an existing element.
        field_values: Optional dict of {field_name: value} to patch in the new element.

    Returns the inserted element, or None on failure.
    """
    list_field = navigate(block, list_path)
    if list_field is None:
        log.warning("insert_list_element: list %s not found", list_path)
        return None

    if list_field.meta_kind not in (6, 7):
        log.warning("insert_list_element: %s is not a list (mk=%d)", list_path, list_field.meta_kind)
        return None

    if list_field.list_elements is None:
        list_field.list_elements = []

    new_elem = copy.deepcopy(template)

    new_elem.field_index = len(list_field.list_elements)
    new_elem.name = f"[{new_elem.field_index}]"

    if field_values and new_elem.child_fields:
        for fname, fvalue in field_values.items():
            for cf in new_elem.child_fields:
                if cf.name == fname and cf.present:
                    cf.value_repr = str(fvalue)
                    cf.note = (cf.note or "") + " [modified]"
                    break

    list_field.list_elements.append(new_elem)
    list_field.list_count = len(list_field.list_elements)

    list_field.note = (list_field.note or "") + " [modified]"

    log.info("insert_list_element: inserted element [%d] into %s",
             new_elem.field_index, list_path)

    return new_elem


def remove_list_element(
    block: ObjectBlock,
    list_path: str,
    index: int,
) -> GenericFieldValue | None:
    """Remove an element from an object list by index.

    Returns the removed element (for undo support), or None on failure.
    """
    list_field = navigate(block, list_path)
    if list_field is None:
        log.warning("remove_list_element: list %s not found", list_path)
        return None

    if list_field.list_elements is None or index >= len(list_field.list_elements):
        log.warning("remove_list_element: index %d out of range", index)
        return None

    removed = list_field.list_elements.pop(index)
    list_field.list_count = len(list_field.list_elements)

    for i, elem in enumerate(list_field.list_elements):
        elem.field_index = i
        elem.name = f"[{i}]"

    list_field.note = (list_field.note or "") + " [modified]"

    return removed


_ITEM_FIELD_DEFS = [
    ("_saveVersion",             0, 4, "<I", 1),
    ("_itemNo",                  0, 8, "<Q", 0),
    ("_itemKey",                 0, 4, "<I", 0),
    ("_slotNo",                  0, 2, "<H", 0),
    ("_stackCount",              0, 8, "<Q", 1),
    ("_enchantLevel",            0, 2, "<H", 0),
    ("_useableCtc",              0, 8, "<Q", 0),
    ("_endurance",               0, 2, "<H", 65535),
    ("_sharpness",               0, 2, "<H", 0),
    ("_batteryStat",             0, 8, "<Q", 0),
    ("_maxBatteryStat",          0, 8, "<Q", 0),
    ("_maxSocketCount",          0, 1, "<B", 0),
    ("_validSocketCount",        0, 1, "<B", 0),
    ("_socketSaveDataList",      6, 0, "",   None),
    ("_itemDyeDataList",         6, 0, "",   None),
    ("_dropResultSubSaveItemList", 6, 0, "", None),
    ("_transferredItemKey",      0, 4, "<I", 0),
    ("_currentGimmickState",     0, 4, "<I", 0),
    ("_chargedUseableCount",     0, 8, "<Q", 0),
    ("_timeWhenPushItem",        0, 8, "<Q", 0),
    ("_characterConversionData", 5, 8, "",   None),
    ("_isNewMark",               0, 1, "bool", True),
]

_ITEM_MASK_GENERAL   = {0, 1, 2, 3, 4, 7, 11, 13, 16, 18, 19, 21}
_ITEM_MASK_EQUIPMENT = {0, 1, 2, 3, 4, 7, 11, 13, 16, 18, 19, 21}


def build_item_element(
    item_key: int,
    item_no: int,
    stack_count: int = 1,
    slot_no: int = 0,
    is_equipment: bool = False,
    type_index: int = 17,
    timestamp: int = 0,
) -> GenericFieldValue:
    """Build an ItemSaveData element from scratch — no cloning needed.

    Args:
        item_key: The item key from iteminfo.pabgb.
        item_no: Unique item number (must be unique across all inventory items).
        stack_count: How many of this item.
        slot_no: Inventory slot number.
        is_equipment: True for weapons/armor (enables enchant/sharpness/sockets).
        type_index: The PARC type index for ItemSaveData in this save's schema.
        timestamp: Acquisition timestamp (_timeWhenPushItem). 0 = use current.

    Returns a GenericFieldValue tree ready to insert into an _itemList.
    """
    if timestamp == 0:
        import time
        timestamp = int(time.time() * 10_000_000)

    present_fields = _ITEM_MASK_EQUIPMENT if is_equipment else _ITEM_MASK_GENERAL

    mbc = 3
    mask = bytearray(mbc)
    for idx in present_fields:
        mask[idx // 8] |= (1 << (idx % 8))

    child_fields = []
    raw_bytes_parts = []

    for idx, (fname, mk, msize, fmt, default) in enumerate(_ITEM_FIELD_DEFS):
        present = idx in present_fields

        if fname == "_itemKey":
            value = item_key
        elif fname == "_itemNo":
            value = item_no
        elif fname == "_stackCount":
            value = stack_count
        elif fname == "_slotNo":
            value = slot_no
        elif fname == "_transferredItemKey":
            value = item_key
        elif fname == "_timeWhenPushItem":
            value = timestamp
        elif fname == "_isNewMark":
            value = True
        else:
            value = default

        field = GenericFieldValue(
            field_index=idx,
            name=fname,
            type_name="",
            meta_kind=mk,
            meta_size=msize,
            meta_aux=0,
            present=present,
            decode_kind="built" if present else "absent",
            start_offset=0,
            end_offset=0,
            value_repr=str(value) if value is not None else "",
            edit_format=fmt,
            editable=present and mk in (0, 2),
            note="[modified]" if present else "",
        )

        if mk == 6 and present:
            field.list_elements = []
            field.list_count = 0
            field.list_prefix_u8 = 1
            field.list_header_size = 21
            field._raw_bytes = b'\x01\x01\x01\x00' + b'\x00' * 17
            field.note = "[modified]"

        child_fields.append(field)

    element = GenericFieldValue(
        field_index=0,
        name="[0]",
        type_name="ItemSaveData",
        meta_kind=6,
        meta_size=0,
        meta_aux=0,
        present=True,
        decode_kind="list_element",
        start_offset=0,
        end_offset=0,
        value_repr=f"type=ItemSaveData",
        child_mask_byte_count=mbc,
        child_mask_bytes=bytes(mask),
        child_type_index=type_index,
        child_type_name="ItemSaveData",
        child_reserved_u8=0,
        child_sentinel1_u32=0xFFFFFFFF,
        child_sentinel2_u32=0xFFFFFFFF,
        child_payload_offset=0,
        child_reserved_u32=0,
        child_size_u32=0,
        child_fields=child_fields,
        child_undecoded_ranges=[],
        note="[modified]",
    )


    return element


def expand_bitmask(
    block: ObjectBlock,
    field_index: int,
    default_value = 0,
    schema_types: dict[int, TypeDef] | None = None,
) -> bool:
    """Enable a field that was previously absent (bit not set in bitmask).

    Args:
        block: The ObjectBlock to modify.
        field_index: Index of the field in the type's field list.
        default_value: Default value for the new field.
        schema_types: Type definitions for looking up field metadata.

    Returns True if the field was successfully enabled.
    """
    if field_index >= len(block.fields):
        return False

    target = block.fields[field_index]
    if target.present:
        return True

    mask = bytearray(block.header_mask_bytes)
    byte_idx = field_index // 8
    bit_idx = field_index % 8

    if byte_idx >= len(mask):
        mask += b'\x00' * (byte_idx + 1 - len(mask))
        block.mask_byte_count = len(mask)

    mask[byte_idx] |= (1 << bit_idx)
    block.header_mask_bytes = bytes(mask)

    target.present = True
    target.decode_kind = "expanded"
    target.value_repr = str(default_value)
    target.note = "[expanded]"

    return True
