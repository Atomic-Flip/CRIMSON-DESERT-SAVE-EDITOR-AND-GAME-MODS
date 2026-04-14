# Crimson Desert — Save Editor & Game Mods

Two companion desktop tools for **Crimson Desert**. They share a codebase and auto-updater plumbing, but each one does a narrow job well.

## The two builds

| Tool | What it does | Download |
|---|---|---|
| **Save Editor — Standalone** | Edits your local `.save` file: inventory, equipment, sockets (fill/clear up to 5), quests, knowledge, abyss gates, dye. | [Releases → `standalone-v1.0.0`](../../releases/tag/standalone-v1.0.0) |
| **Game Mods** | Edits the game's `.pabgb` data via PAZ overlays: ItemBuffs, Stores, DropSets, SpawnEdit, FieldEdit (mount-everywhere, killable NPCs, etc.). | [Releases → `gamemods-v1.0.0`](../../releases/tag/gamemods-v1.0.0) |

**Install both** if you want the full experience — they run independently, don't clobber each other's config/backups, and auto-update separately via their own version manifests.

Neither tool requires the game to be closed, and neither modifies the game client in memory. Save Editor writes to your encrypted `save.save` file; Game Mods writes to PAZ overlay directories.

---

## Save Editor (Standalone) — highlights

- **Inventory / Equipment** — stack counts, enchants, endurance, sharpness, duplicate gear, swap items via 2,000+ real game templates.
- **Sockets** — swap gems, **fill empty sockets**, **clear gems** (v3.2.0 port — up to 5 sockets on unsocketed items).
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
├── editor_version_standalone.json   ← Save Editor update manifest
├── editor_version_gamemods.json     ← Game Mods update manifest
├── CrimsonGameMods/                 ← Game Mods source (public, MPL-2.0)
│   ├── LICENSE, CREDITS.md, README.md
│   ├── main.py + 35 parser/helper modules
│   ├── gui/                          (PySide6 package, 6 tab modules)
│   ├── data/, locale/, knowledge_packs/, dropset_packs/
│   └── CrimsonGameMods.spec
└── (release assets, icons, localization)
```

Save Editor Standalone source lives in a private repo. Both builds are licensed under **MPL-2.0**; see `CrimsonGameMods/LICENSE` and `CrimsonGameMods/CREDITS.md`.

## Build from source (Game Mods)

```bash
cd CrimsonGameMods
pip install PySide6 lz4 cryptography Pillow pyinstaller crimson-rs
python -m PyInstaller CrimsonGameMods.spec --noconfirm
# → dist/CrimsonGameMods.exe
```

## Credits

Big thanks to **gek** (original Qt desktop editor base), **potter4208467** (Rust `crimson_rs` toolkit), **LukeFZ** (`pycrimson` utilities), and **fire** (3.2.0 modular refactor, socket fill/clear). Full list in [`CrimsonGameMods/CREDITS.md`](./CrimsonGameMods/CREDITS.md).

## Disclaimer

Unofficial, non-commercial modding utilities for **Crimson Desert** (© [Pearl Abyss](https://www.pearlabyss.com/)). No game assets, binaries, or proprietary data are redistributed — all extraction happens locally from your own installed copy. Always back up your saves and game files. Use at your own risk.
