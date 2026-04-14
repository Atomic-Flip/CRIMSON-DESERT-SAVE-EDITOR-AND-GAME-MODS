# Data Pipeline

```
data/*.json  →  build_db.py  →  crimson_data.db + crimson_data.db.gz  →  PyInstaller EXE
```

Source files live in `data/` (tracked in git). `build_db.py` reads them and writes two outputs to the project root:

- `crimson_data.db` — uncompressed SQLite; gitignored; used by `--verify` and development
- `crimson_data.db.gz` — gzip level 6; committed; bundled by PyInstaller

`build_db.py` must be re-run whenever any file in `data/` changes.

```bash
# Convert data/*.json to SQLite (run after any data/ change)
python build_db.py

# Sanity-check: row counts vs. source JSON entry counts
python build_db.py --verify

# Custom output location (default: project root)
python build_db.py --output path/to/crimson_data.db

# Build the exe (crimson_data.db.gz is picked up automatically)
pyinstaller CrimsonSaveEditor.spec
```