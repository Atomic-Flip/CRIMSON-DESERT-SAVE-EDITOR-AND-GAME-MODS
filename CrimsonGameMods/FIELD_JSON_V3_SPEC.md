# Field JSON v3 — Technical Specification for Mod Managers

**Version**: 3.0
**Author**: NattKh / RicePaddySoftware (CrimsonGameMods)
**Date**: 2026-04-24
**Status**: Stable — exported by CrimsonGameMods ItemBuffs and Stacker Tool
**License**: MPL 2.0 (see LICENSE)
**Successor**: [FIELD_JSON_V3_1_SPEC.md](FIELD_JSON_V3_1_SPEC.md) — v3.1 multi-target field patching (122 typed tables)

> **Note**: v3.1 (released 2026-05-01) extends this spec with multi-target intent grouping
> and 122-table coverage. v3.1 is **backward-compatible** with v3.0 — every v3.0 doc applies
> unchanged in v3.1 loaders. New mods that touch tables beyond `iteminfo.pabgb` should
> emit v3.1. See FIELD_JSON_V3_1_SPEC.md.

---

## Why Field JSON v3 Exists

Legacy mod formats (Format 1 / Format 2) use **byte offsets** into `iteminfo.pabgb`:

```json
{ "offset": 847291, "original": "e8030000", "patched": "a0860100" }
```

Every game update shifts the file layout. Every mod breaks. Every modder re-exports.

Field JSON v3 uses **field names** resolved at apply-time against the current game data:

```json
{ "entry": "Oath_Of_Darkness", "key": 391518535, "field": "cooltime", "op": "set", "new": 1 }
```

The mod loader parses the **current** `iteminfo.pabgb`, looks up the entry by name, navigates to the field, and writes the value. Offsets are never stored. Mods survive game updates automatically.

---

## File Structure

```json
{
  "modinfo": {
    "title": "My Mod Name",
    "version": "1.0",
    "author": "AuthorName",
    "description": "42 field-level intent(s)",
    "note": "Format 3 — uses field names, survives game updates"
  },
  "format": 3,
  "target": "iteminfo.pabgb",
  "intents": [ ... ]
}
```

### Detection

A file is Field JSON v3 if and only if:
```
doc["format"] == 3  AND  doc["intents"] is a non-empty list
```

The `"target"` field identifies which pabgb table the intents apply to. Currently always `"iteminfo.pabgb"`.

---

## Intent Schema

Each intent is a single atomic edit operation.

### `set` — Change a field value

```json
{
  "entry": "Oath_Of_Darkness",
  "key": 391518535,
  "field": "cooltime",
  "op": "set",
  "new": 1
}
```

| Field   | Type        | Description |
|---------|-------------|-------------|
| `entry` | `string`    | The item's `string_key` (entry name in the pabgb table). **Primary lookup key.** |
| `key`   | `int`       | The item's numeric key. Used as fallback if `entry` doesn't match (keys are stable across updates). |
| `field` | `string`    | Dot-separated path to the field within the parsed item dict. |
| `op`    | `"set"`     | Replace the value at `field` with `new`. |
| `new`   | `any`       | The new value. Type must match what `crimson_rs.parse_iteminfo_from_bytes()` returns for that field. |

### `add_entry` — Add an entirely new item (rare)

```json
{
  "entry": "Custom_Item_Name",
  "key": 9999999,
  "op": "add_entry",
  "data": { ... }
}
```

This is reserved for future use. Mod managers may skip `add_entry` intents if they don't support item creation.

---

## Field Path Syntax

Fields use **dot notation** to navigate nested dicts and **bracket notation** for list indices:

| Path | Meaning |
|------|---------|
| `cooltime` | Top-level field `item["cooltime"]` |
| `max_stack_count` | Top-level field `item["max_stack_count"]` |
| `drop_default_data.use_socket` | Nested: `item["drop_default_data"]["use_socket"]` |
| `drop_default_data.add_socket_material_item_list` | Nested list: replaces entire list |
| `enchant_data_list` | Replaces the entire enchant data list |
| `equip_passive_skill_list` | Replaces the entire passive skill list |
| `docking_child_data.gimmick_info_key` | Nested: `item["docking_child_data"]["gimmick_info_key"]` |

### Path resolution algorithm

```python
import re

def apply_field_set(target: dict, field_path: str, value):
    parts = re.split(r'\.(?![^\[]*\])', field_path)
    obj = target
    for part in parts[:-1]:
        m = re.match(r'^(.+?)\[(\d+)\]$', part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            obj = obj[key][idx]
        else:
            obj = obj[part]
    last = parts[-1]
    m = re.match(r'^(.+?)\[(\d+)\]$', last)
    if m:
        key, idx = m.group(1), int(m.group(2))
        obj[key][idx] = value
    else:
        obj[last] = value
```

---

## How to Apply (Implementation Guide)

### Step 1: Parse vanilla iteminfo

```python
import crimson_rs

# Extract from game PAZ archives
raw = crimson_rs.extract_file(game_dir, "0008",
    "gamedata/binary__/client/bin", "iteminfo.pabgb")

# Parse into list of dicts
items = crimson_rs.parse_iteminfo_from_bytes(raw)
```

Each item is a dict with `key` (int) and `string_key` (str) plus all fields.

### Step 2: Build lookup index

```python
items_by_name = {it["string_key"]: it for it in items}
items_by_key  = {it["key"]: it for it in items}
```

### Step 3: Apply intents

```python
import json

with open("my_mod.field.json", "r") as f:
    doc = json.load(f)

for intent in doc["intents"]:
    entry = intent["entry"]
    target = items_by_name.get(entry)
    if not target:
        # Fallback: try numeric key
        target = items_by_key.get(intent.get("key"))
    if not target:
        print(f"SKIP: entry '{entry}' not found in current game data")
        continue

    op = intent.get("op", "set")
    if op == "set":
        apply_field_set(target, intent["field"], intent["new"])
    elif op == "add_entry":
        pass  # Optional: handle new item creation
```

### Step 4: Serialize and pack

```python
# Serialize back to bytes
modified = crimson_rs.serialize_iteminfo(items)

# Write to mod overlay
import crimson_rs.pack_mod
crimson_rs.pack_mod.pack_mod(
    game_dir=game_dir,
    mod_folder=mod_folder,     # must contain gamedata/binary__/client/bin/iteminfo.pabgb
    output_dir=output_dir,
    group_name="0058",         # or your overlay group
)
```

**Critical**: The output path inside `mod_folder` **must** be `gamedata/binary__/client/bin/iteminfo.pabgb`. The game's PAMT maps this exact path. A shorter path like `gamedata/iteminfo.pabgb` silently fails.

---

## Common Field Names (iteminfo.pabgb)

These are the fields returned by `crimson_rs.parse_iteminfo_from_bytes()`:

### Scalar Fields (top-level)

| Field | Type | Description |
|-------|------|-------------|
| `key` | `int` | Item numeric key (unique identifier) |
| `string_key` | `str` | Item name string (e.g. `"Oath_Of_Darkness"`) |
| `item_type` | `int` | Item type enum |
| `category_info` | `int` | Item category |
| `max_stack_count` | `int` | Maximum stack size |
| `max_endurance` | `int` | Maximum durability (65535 = indestructible) |
| `is_destroy_when_broken` | `int` | 0 = survives breaking, 1 = destroyed |
| `cooltime` | `int` | Cooldown in seconds (1 = minimum, 0 = crashes) |
| `unk_post_cooltime_a` | `int` | Must match `cooltime` |
| `unk_post_cooltime_b` | `int` | Must match `cooltime` |
| `item_charge_type` | `int` | 0 = activated, 2 = passive |
| `max_charged_useable_count` | `int` | Max charges |
| `unk_post_max_charged_a` | `int` | Must match `max_charged_useable_count` |
| `unk_post_max_charged_b` | `int` | Must match `max_charged_useable_count` |
| `respawn_time_seconds` | `int` | Respawn timer |
| `gimmick_info` | `int` | Gimmick key (visual effect / activated ability) |
| `is_dyeable` | `int` | 1 = can be dyed |
| `is_editable_grime` | `int` | 1 = grime editing enabled |
| `equip_type_info` | `int` | Equipment type (0 = not equipment) |

### List Fields

| Field | Type | Description |
|-------|------|-------------|
| `equip_passive_skill_list` | `list[{skill: int, level: int}]` | Passive skills on equip |
| `enchant_data_list` | `list[dict]` | Per-enchant-level data (stats, buffs) |
| `prefab_data_list` | `list[dict]` | Visual/tribe data per prefab variant |

### Nested: `enchant_data_list[N]`

| Field | Type | Description |
|-------|------|-------------|
| `.level` | `int` | Enchant level |
| `.equip_buffs` | `list[{buff: int, level: int}]` | Equipment buffs at this level |
| `.enchant_stat_data.stat_list_static` | `list[{stat: int, change_mb: int}]` | Flat stat bonuses |
| `.enchant_stat_data.stat_list_static_level` | `list[{stat: int, change_mb: int}]` | Level-scaled stats (rate 0-15) |
| `.enchant_stat_data.regen_stat_list` | `list[{stat: int, change_mb: int}]` | Regen stats |

### Nested: `drop_default_data`

| Field | Type | Description |
|-------|------|-------------|
| `.use_socket` | `int` | 1 = sockets enabled |
| `.socket_valid_count` | `int` | Number of socket slots |
| `.add_socket_material_item_list` | `list[{item: int, value: int}]` | Socket unlock costs |
| `.drop_enchant_level` | `int` | Default enchant level on drop |

### Nested: `docking_child_data`

| Field | Type | Description |
|-------|------|-------------|
| `.gimmick_info_key` | `int` | Gimmick key for the docked entity |
| `.attach_parent_socket_name` | `str` | Bone/socket name on parent |
| `.docking_equip_slot_no` | `int` | 65535 = any slot |
| ... | | See full schema from `crimson_rs` |

---

## Example: Complete Mod File

```json
{
  "modinfo": {
    "title": "QoL No Cooldown + Max Stacks",
    "version": "1.0",
    "author": "NattKh",
    "description": "2 field-level intent(s)"
  },
  "format": 3,
  "target": "iteminfo.pabgb",
  "intents": [
    {
      "entry": "Oath_Of_Darkness",
      "key": 391518535,
      "field": "cooltime",
      "op": "set",
      "new": 1
    },
    {
      "entry": "Oath_Of_Darkness",
      "key": 391518535,
      "field": "unk_post_cooltime_a",
      "op": "set",
      "new": 1
    },
    {
      "entry": "Oath_Of_Darkness",
      "key": 391518535,
      "field": "unk_post_cooltime_b",
      "op": "set",
      "new": 1
    },
    {
      "entry": "Oath_Of_Darkness",
      "key": 391518535,
      "field": "max_stack_count",
      "op": "set",
      "new": 999999
    },
    {
      "entry": "Oath_Of_Darkness",
      "key": 391518535,
      "field": "equip_passive_skill_list",
      "op": "set",
      "new": [
        {"skill": 70994, "level": 1},
        {"skill": 76009, "level": 1}
      ]
    },
    {
      "entry": "Oath_Of_Darkness",
      "key": 391518535,
      "field": "enchant_data_list",
      "op": "set",
      "new": [
        {
          "level": 0,
          "equip_buffs": [
            {"buff": 1000212, "level": 3},
            {"buff": 1000091, "level": 5}
          ],
          "enchant_stat_data": {
            "stat_list_static": [
              {"stat": 1000037, "change_mb": 100000000}
            ],
            "stat_list_static_level": [
              {"stat": 1000010, "change_mb": 15},
              {"stat": 1000011, "change_mb": 15}
            ],
            "regen_stat_list": [
              {"stat": 1000026, "change_mb": 100000}
            ]
          }
        }
      ]
    }
  ]
}
```

---

## Stacking Multiple Mods

Field JSON v3 is designed for **multi-mod stacking**. Because edits are per-field, two mods that edit different fields on the same item don't conflict:

- Mod A sets `cooltime = 1` on Oath_Of_Darkness
- Mod B sets `max_stack_count = 999999` on Oath_Of_Darkness
- Both apply cleanly — no overwrite

When two mods set the **same field** on the same item, the last-loaded mod wins (last-writer-wins). The CrimsonGameMods Stacker Tool detects and reports these conflicts.

---

## Dependencies

The reference implementation uses [crimson-rs](https://github.com/potterhd/crimson-rs) (Potter's Rust library) for:

- `crimson_rs.extract_file()` — extract pabgb from PAZ archives
- `crimson_rs.parse_iteminfo_from_bytes()` — parse binary to dicts
- `crimson_rs.serialize_iteminfo()` — serialize dicts back to binary
- `crimson_rs.pack_mod.pack_mod()` — pack into PAZ overlay

If your mod manager already has its own iteminfo parser/serializer, you only need to match the field names. The field names come from the Rust parser and are stable across game versions.

---

## Validation

After applying intents, validate with a round-trip:

```python
for item in items:
    try:
        rt = crimson_rs.serialize_iteminfo([item])
        crimson_rs.parse_iteminfo_from_bytes(rt)
    except Exception:
        print(f"BROKEN: {item['string_key']} — reverting to vanilla")
        # Replace with vanilla copy
```

This catches structural corruption (wrong list lengths, missing required fields) before the mod reaches the game.

---

## Questions / Support

- **Format spec**: NattKh (CrimsonGameMods Discord)
- **crimson-rs API**: Potter (crimson-rs GitHub)
- **Stacker Tool**: Ships with CrimsonGameMods — can import, merge, and re-export Field JSON v3 files
