# Building & Testing CrimsonGameMods with Field JSON v3.1 Support

**Audience**: Internal testers receiving a v1.1.5-rc1 build.
**Purpose**: Validate the Field JSON v3.1 multi-target field-level catchall before public release.
**Spec**: See `FIELD_JSON_V3_1_SPEC.md` for the format.

---

## What's new in this build

The Stacker tool can now produce **field-level intents** for 122 typed game-data tables
(`gimmick_info`, `condition_info`, `drop_set_info`, `character_info`, `buff_info`, etc.)
instead of the v1.1.4 blob-level fallback that replaced whole records.

This means:

- **Two mods touching different fields of the same gimmick coexist cleanly** — last-writer-wins
  no longer eats the other mod's edits.
- **Cross-table mods are first-class** — a weapon mod that buffs damage in `iteminfo` AND
  tweaks a visual in `gimmick_info` AND adds a passive in `skill_info` exports as three
  independently-mergeable target sections.
- **Mods survive game updates** — same as v3.0, field names resolve at apply-time.

---

## Quick test (precompiled build)

If you received `CrimsonDesktopSuite_v1.1.5-rc1.zip`:

1. Unzip anywhere.
2. Run `CrimsonGameMods.exe`.
3. Open the **Stacker** tab.
4. Drop two mods that both touch `gimmick_info.pabgb` (e.g. two visual-effect tweaks).
5. Click **Export Field JSON**. You should see log lines like:
   ```
   + 12 gimmick_info.pabgb intent(s) (field-level diff)
   ```
   The `(field-level diff)` suffix confirms v3.1 is active. If you see `(blob-level diff)`,
   `dmm_parser` wasn't bundled correctly — please report.
6. Open the exported `.field.json` and verify:
   - `format == 3`
   - `format_minor == 1` (or `targets[]` array present)
   - One `targets[]` entry per touched table
7. Apply the exported `.field.json` via DMM 1.3.4+ and verify the game data matches what the
   source mods would have produced.

---

## Building from source (developers / advanced testers)

### Prerequisites

```
- Python 3.13+
- Rust toolchain (stable) — only needed if building dmm-parser from source
- maturin (pip install maturin)
- The CrimsonSaveEditor/requirements.txt deps (PySide6==6.8.3, lz4, cryptography, pyinstaller, crimson_rs)
```

### Step 1: Build & install dmm-parser

```bash
git clone https://github.com/exodiaprivate-eng/dmm-parser
cd dmm-parser
maturin develop --release
```

Verify the install:
```bash
python -c "import dmm_parser; print(dmm_parser.parse_table.__doc__)"
```

You should see the `parse_table` docstring. If you see `ImportError`, the wheel didn't install
into the current Python env — try `pip install -e .` instead, or activate the same venv
CrimsonGameMods uses.

### Step 2: Install the v3.1 extras

```bash
cd /path/to/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS-clone/CrimsonGameMods
pip install -r requirements_v3_1.txt
```

(This is currently a no-op since `dmm-parser` was installed in Step 1, but ensures any
future v3.1 build deps are present.)

### Step 3: Build CrimsonGameMods

```bash
python -m PyInstaller CrimsonGameMods.spec --noconfirm
```

You should see this line in the build output:

```
[v3.1] Bundling dmm_parser from C:\...\Lib\site-packages\dmm_parser
```

If you see `[v3.1] dmm_parser not installed — Stacker will fall back...`, repeat Step 1
in the same Python environment.

The exe lands at `dist/CrimsonGameMods.exe` (~85-90 MB; v3.1 build adds ~5 MB for `dmm_parser.pyd`).

### Step 4: Build DMM (optional, only if testing v3.1 apply path)

DMM 1.3.4+ is required for non-iteminfo target apply. If your testers will only validate
the **export** side (Stacker → field.json), DMM 1.3.3 is fine — it'll apply the iteminfo
target and warn-skip the others.

```bash
cd CrimsonGameMods/DMMLoader
npm install
npx tauri build
# Output: src-tauri/target/release/definitive-mod-manager.exe
```

(See `BUILD_UNIFIED.md` for DMM build details.)

### Step 5: Bundle for distribution

```bash
cd CrimsonGameMods
python bundle_unified.py
# Output: dist/CrimsonDesktopSuite/
#         dist/CrimsonDesktopSuite_full.zip
```

Zip the result and ship to testers.

---

## Smoke tests for testers

### Test A — Single gimmick mod (export round-trip)

1. Source mod: edit `gimmick_info.pabgb` so one entry has its `cooltime` field changed.
2. Stack with that one mod, click **Export Field JSON**.
3. Verify the exported JSON has:
   ```json
   {
     "format": 3, "format_minor": 1,
     "targets": [
       {
         "file": "gimmick_info.pabgb",
         "intents": [
           {"entry": "...", "key": ..., "field": "cooltime", "op": "set", "new": ...}
         ]
       }
     ]
   }
   ```
4. **Pass**: exactly one intent emitted with the correct field path and new value.
5. **Fail**: `_blob_b64` field in the intent (= v3.1 catchall fell back to blob-level), or
   missing `format_minor`, or wrong `targets[]` shape.

### Test B — Two-mod no-conflict stack

1. Source mod A: edits `gimmick_info` entry X field `duration_ms`.
2. Source mod B: edits `gimmick_info` entry X field `intensity` (different field, same entry).
3. Stack both, export.
4. Verify the exported JSON has **both intents** in the `gimmick_info.pabgb` target:
   ```json
   {
     "intents": [
       {"entry": "X", "field": "duration_ms", "op": "set", "new": ...},
       {"entry": "X", "field": "intensity",   "op": "set", "new": ...}
     ]
   }
   ```
5. **Pass**: both intents present.
6. **Fail**: only one intent (= last-writer ate the other = v3.0 blob-level behavior).

### Test C — Cross-table stack

1. Source mod: edits `iteminfo` (cooldown) AND `gimmick_info` (visual) AND `skill_info` (passive).
2. Stack, export.
3. Verify exported JSON has **3 entries in `targets[]`**, one per file.
4. **Pass**: 3 target sections with correct file names and intents per table.
5. **Fail**: missing target sections, or all intents collapsed under `iteminfo.pabgb`.

### Test D — Backward compatibility

1. Source mod: edits `iteminfo` ONLY (no other tables).
2. Stack one mod, export.
3. Verify the exported JSON uses **legacy single-target shape**:
   ```json
   {
     "format": 3,
     "target": "iteminfo.pabgb",
     "intents": [...]
   }
   ```
   (NOT `targets[]` array — single iteminfo target falls back to v3.0 shape for
   backward compat with older DMM.)
4. **Pass**: legacy shape, applies cleanly in DMM 1.3.3.
5. **Fail**: `targets[]` shape used for single-target — older DMM will reject.

---

## Known limitations (will not pass these tests yet)

- **List append/remove**: only `set` op is implemented. If a mod adds a new element to
  `enchant_data_list`, the export will emit a whole-list `set` (replaces all levels).
  v3.2 will introduce `list_append`/`list_remove`.
- **Add new entry**: if a mod adds a brand-new `gimmick_info` entry not in vanilla, the
  export skips it (v3 spec doesn't yet support `add_entry`).
- **Cross-table references**: `"new": "@gimmick_info.X"` syntax is v3.2-only.
- **DMM 1.3.3 and older** apply iteminfo intents and warn-skip everything else. This is
  expected — non-iteminfo apply requires DMM 1.3.4+.

---

## Reporting issues

Please report with:

1. Build version (file → about → CrimsonGameMods version)
2. The source mods you stacked (zip them up)
3. The exported `.field.json` (paste or attach)
4. The Stacker log output (right-click log → copy all)
5. What you expected vs what happened

Filing channels:
- Discord: CrimsonGameMods #beta-testing
- GitHub: https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS/issues with `[v3.1-rc]` prefix
