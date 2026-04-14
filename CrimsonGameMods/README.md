# Crimson Game Mods

A PySide6 desktop tool for modifying **Crimson Desert** game data via PAZ archive overlays. Companion to the [Crimson Save Editor (Standalone)](../releases) — this build is for `.pabgb` / PAZ modding, not save editing.

## Features

| Tab | What it does |
|---|---|
| **ItemBuffs** | Inject custom stats / buffs / enchants into `iteminfo.pabgb`. 28 stat hashes, presets from dev rings, in-game inventory lookup. |
| **Stores** | Edit vendor prices, purchase limits, and stock on `storeinfo.pabgb` (254 stores). Inline editable Limit / Buy / Sell cells. |
| **DropSets** | Modify item drop rates / quantities / keys in `dropsetinfo.pabgb`. Inline-editable rate/qty columns. |
| **SpawnEdit** | Edit creature / NPC / faction spawn counts, cooldowns, and region assignments across 6+ pabgb spawn tables. |
| **FieldEdit** | Unified editor for vehicle / region / mount / gimmick info — enable mounts in towns, tweak ride duration, adjust zone flags. |
| **Items → Database** | Readonly item lookup by key/name (used as reference for mod tabs). |

## Install

1. Download the latest `CrimsonGameMods.exe` from the [Releases page](../releases).
2. Place it in a folder where you want its config / backups to live.
3. Run it. The first time, point the "Game Path" bar at your Crimson Desert install (the tool tries to auto-detect).

## Build from source

```bash
pip install PySide6 lz4 cryptography Pillow pyinstaller crimson-rs
python -m PyInstaller CrimsonGameMods.spec --noconfirm
# Output: dist/CrimsonGameMods.exe
```

## Save File Integration

The tool can optionally load your `.save` file to display the items you actually have in your inventory — useful for targeting the exact items you want to buff in ItemBuffs. Save loading is on-demand (click "My Inventory" in ItemBuffs), not automatic.

The Save Browser panel can be popped open as a floating window via the "Save Browser" button in the top-right of the main tab bar.

## How mods are applied

ItemBuffs / Stores / DropSets / SpawnEdit / FieldEdit write modified `.pabgb` files into PAZ overlay directories (e.g. `0036/`, `0039/`, `0058/`, `0060/`) plus regenerate PAPGT metadata. Mods are installed via PAZ front-insertion — the game loads overlays before the base archive, so changes take effect on next launch.

Each tab can also **export a CDUMM-compatible mod package** (for users of the [CDUMM Mod Manager](https://github.com/...)) via the "Export as CDUMM Mod" button.

## Restore / Backup

Every patch writes to a side PAZ directory. Restore = delete the overlay directory and restore the original PAPGT. The tool handles both automatically via its Restore UI.

## License

[Mozilla Public License 2.0](LICENSE). See [CREDITS.md](CREDITS.md) for contributor attribution.

## Disclaimer

This is an unofficial, non-commercial modding utility for **Crimson Desert** (© [Pearl Abyss](https://www.pearlabyss.com/)). No game assets or proprietary data are redistributed — all extraction happens locally from the user's own installed copy of the game. Use at your own risk; always back up your game files.
