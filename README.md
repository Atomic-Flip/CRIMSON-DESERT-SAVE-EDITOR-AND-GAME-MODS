# Crimson Desert — Save Editor & Game Mods

> **About this fork.** This is **Atomic-Flip's** maintenance fork of [`NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS`](https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS), focused on keeping the **Save Editor (Standalone)** ahead of game updates and adding the safety / quality-of-life work that hasn't landed upstream yet. Recent additions on top of upstream: socket design-limit validation that blocks crash-on-load saves, a locked-slot unlock UX hint, and same-day support for the new save layout that shipped with Crimson Desert game v1.0.5. Releases on this fork use the `vX.Y.Z` tag scheme (latest: **v1.0.6**); upstream uses `standalone-vX.Y.Z` and `gamemods-vX.Y.Z`. Upstream changes are merged in periodically.

Two companion desktop tools for **Crimson Desert**. They share a codebase and auto-updater plumbing, but each one does a narrow job well.

## The two builds

| Tool | What it does | Download |
|---|---|---|
| **Save Editor — Standalone** | Edits your local `.save` file: inventory, equipment, sockets (per-item design-limit aware), quests, knowledge, abyss gates, dye. Compatible with game versions through v1.0.5. | [Releases → `v1.0.6`](../../releases/tag/v1.0.6) |
| **Game Mods** | Edits the game's `.pabgb` data via PAZ overlays: ItemBuffs, Stores, DropSets, SpawnEdit, FieldEdit (mount-everywhere, killable NPCs, etc.). | [Releases → `gamemods-v1.1.4`](https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS/releases/tag/gamemods-v1.1.4) (upstream) |

**Install both** if you want the full experience — they run independently, don't clobber each other's config/backups, and auto-update separately via their own version manifests. Game Mods releases come from upstream; this fork only ships the Save Editor.

Neither tool modifies the game client in memory. Save Editor writes to your encrypted `save.save` file; Game Mods writes to PAZ overlay directories.

## What this fork adds on the Save Editor

- **Per-item socket design limits** (v1.0.4). Bundles `item_limits.json` (647 items) so the editor knows the real max gear count for each piece of equipment — gloves=2, boots=2, headgear=1, shields=2, one-handed=3, two-handed=5, cloaks/accessories=0. The Sockets tab shows exactly that many slot rows, hides cloaks from the dropdown entirely, and the apply step rejects fills that would exceed the limit (game CTDs on load otherwise).
- **Working design-limit lookup** (v1.0.4). Fixes a silent `NameError` from upstream that left `_socket_design_limits` and `_max_enchant_map` empty on every startup, so v1.0.3's safety guard was a no-op in practice.
- **Locked-slot unlock UX** (v1.0.5). When an item has slots within its design limit that aren't yet unlocked at the Witch, the Sockets tab shows an explicit hint pointing at the *Unlock Socket Slots* panel — no more guessing why slot 4 is greyed out.
- **Auto-updater points at the right repo** (v1.0.5). `UPDATE_REPO` reads from this fork instead of upstream's last-touched-2024 manifest.
- **Game v1.0.5 save format support** (v1.0.6). The locator structure changed (1-byte discriminator + 4-byte bitmask, vs. the old 2-byte mbc + 3-byte bitmask). The fork detects both formats; round-trip clear-and-refill is bit-identical on each.

---

## Save Editor (Standalone) — highlights

- **Inventory / Equipment** — stack counts, enchants, endurance, sharpness, duplicate gear, swap items via 2,000+ real game templates.
- **Sockets** — swap gems, **fill empty sockets**, **clear gems**, capped at each item's actual design limit (so cloaks no longer accept gems that would crash the game).
- **Quest Editor** — advance/reset/complete quests, diagnose corruption, batch complete filtered.
- **Knowledge / Abyss Gates** — mark as discovered, unlock puzzle states.
- **Dye** — edit RGB, material, grime on any previously-dyed item.
- **Repurchase (Vendor Swap)** — the safest swap method: sell junk, edit, buy back.
- **Backup/Restore** — auto-backup before every write; pristine backup support.
- **Auto-find saves** — Steam, Epic, Game Pass, Linux Proton.

Full feature list in the release notes.

## Game Mods — highlights

- **ItemBuffs** — inject stats/buffs/enchants into `iteminfo.pabgb`. 28 stat hashes, presets from dev rings, optional in-game inventory lookup.
- **Stores** — edit vendor **prices, limits, stock** (in-table editable). 254 vendors.
- **DropSets** — modify drop rates, quantities, item keys on `dropsetinfo.pabgb`.
- **SpawnEdit** — tweak creature / NPC / faction spawn counts and cooldowns across 6+ spawn tables.
- **FieldEdit** — unified vehicle / region / mount / gimmick editor. Enable mounts in towns, extend ride duration, make NPCs killable, etc.
- **Items → Database** — readonly item reference.
- **Export as CDUMM Mod** — ItemBuffs + SpawnEdit can produce mod packages importable by the CDUMM Mod Manager.

## How to use

1. Download the tool you want from [Releases](../../releases).
2. Put the `.exe` in a folder of its own — it'll write config / backups next to itself.
3. Run it. Use the in-app **Guides** menu for per-tab walkthroughs.
4. For Game Mods, point the Game Path bar at your Crimson Desert install (auto-detect tries first).

---

## Source layout

```
CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS/
├── editor_version_standalone.json   ← Save Editor update manifest (this fork)
├── editor_version_gamemods.json     ← Game Mods update manifest (upstream)
├── CrimsonSaveEditor/               ← Save Editor source (this fork's focus)
│   ├── main.py, gui.py
│   ├── parc_inserter3.py, item_scanner.py, save_crypto.py, …
│   ├── item_limits.json (647 items, fork addition)
│   ├── max_enchant_map.json, item_names.json, knowledge_packs/, …
│   └── CrimsonSaveEditor.spec
├── CrimsonGameMods/                 ← Game Mods source (upstream, MPL-2.0)
│   ├── LICENSE, CREDITS.md, README.md
│   ├── main.py + 35 parser/helper modules
│   ├── gui/                          (PySide6 package, 6 tab modules)
│   ├── data/, locale/, knowledge_packs/, dropset_packs/
│   └── CrimsonGameMods.spec
└── (release assets, icons, localization)
```

Both builds are licensed under **MPL-2.0**; see `CrimsonGameMods/LICENSE` and `CrimsonGameMods/CREDITS.md`.

## Build from source

**Save Editor (Standalone):**

```bash
cd CrimsonSaveEditor
pip install PySide6 lz4 cryptography pyinstaller crimson_rs
python -m PyInstaller CrimsonSaveEditor.spec --noconfirm
# → dist/CrimsonSaveEditorStandalone.exe
```

**Game Mods:**

```bash
cd CrimsonGameMods
pip install PySide6 lz4 cryptography Pillow pyinstaller crimson-rs
python -m PyInstaller CrimsonGameMods.spec --noconfirm
# → dist/CrimsonGameMods.exe
```

## Credits

Big thanks to **NattKh** (the upstream this fork tracks), **gek** (original Qt desktop editor base), **potter4208467** (Rust `crimson_rs` toolkit), **LukeFZ** (`pycrimson` utilities), and **fire** (3.2.0 modular refactor, socket fill/clear). Full upstream list in [`CrimsonGameMods/CREDITS.md`](./CrimsonGameMods/CREDITS.md). Fork-side socket validation, format compatibility, and unlock UX work is in this repo's commit history (v1.0.4..v1.0.6).

## Disclaimer

Unofficial, non-commercial modding utilities for **Crimson Desert** (© [Pearl Abyss](https://www.pearlabyss.com/)). No game assets, binaries, or proprietary data are redistributed — all extraction happens locally from your own installed copy. Always back up your saves and game files. Use at your own risk.
