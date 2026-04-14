# Credits

## Author & Maintainer
- **Rice (RicePaddySoftware / NattKh)** — editor development, `gui_i18n.py` internationalization framework, save-format work, releases.

## Core Contributors
- **gek** — original Qt desktop save editor (`Includes/desktopeditor/`). The first save editor was built on this foundation.
- **potter4208467** — `crimson_rs` Rust parser toolkit: PAZ packing, iteminfo parser, `PackGroupBuilder`, integrity algorithms.
- **LukeFZ** — `pycrimson` Python utilities: PABGB index reader, archive helpers.
- **fire** — 3.2.0 modular GUI refactor, socket fill/clear engine, ongoing co-development.

## Code Incorporated
- **`Includes/source/paz_*.py`** — community PAZ archive tooling (pre-Rust port).
- **`Includes/Laoder/src/cdumm/`** — community CMOD/ASI loader.
- **Vendor-patch JSON format** — compatible with **Pldada**'s Korean community tooling.

## External Libraries
- **PySide6** (Qt for Python) — LGPL-3.0 / Qt Company
- **python-lz4** — BSD-3-Clause
- **cryptography** — Apache-2.0 / BSD
- **Pillow** *(dev-time only, splash image generation)* — MIT-CMU
- **PyInstaller** *(dev-time only, build tool)* — GPL with bootloader exception

## Other
- **Dameon** — standalone Crimson Desert Teleporter (separate project, linked from the stubbed Teleport tab; no code incorporated).
- **Claude Code (Anthropic)** — AI coding assistant used during development.

## Game
- **Pearl Abyss** — Crimson Desert (© Pearl Abyss). This tool is an unofficial, non-commercial modding utility for save-file and PAZ archive editing. No game assets, binaries, or proprietary data are redistributed — extraction happens locally from the user's own installed copy.
