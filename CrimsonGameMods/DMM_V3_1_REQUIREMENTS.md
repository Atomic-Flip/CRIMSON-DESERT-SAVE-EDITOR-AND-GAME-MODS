# DMM 1.3.4 v3.1 Apply Requirements

**For**: NattKh, DMM (Definitive Mod Manager) maintainers.
**Spec**: [FIELD_JSON_V3_1_SPEC.md](FIELD_JSON_V3_1_SPEC.md)
**Stacker side**: PR #53 (already shipped — Stacker now exports v3.1 docs)

---

## What DMM needs to add for v3.1

The Stacker now produces `format: 3, format_minor: 1, targets: [...]` documents
covering 122 game-data tables (`gimmick_info`, `condition_info`, `drop_set_info`,
`character_info`, `buff_info`, etc.). DMM 1.3.3 only knows how to apply the
`iteminfo.pabgb` target — it warns and skips the rest. v1.3.4 needs to handle
the new target files.

### Required changes

**1. Detect v3.1**

```rust
// In apply path, after parsing the JSON doc:
let format_minor = doc["format_minor"].as_u64().unwrap_or(0);
let is_v3_1 = format_minor >= 1 || doc["targets"].is_array();
```

**2. Iterate `targets[]` instead of single-target**

```rust
let targets = if is_v3_1 {
    doc["targets"].as_array().unwrap()
} else {
    // v3.0 fallback: synthesize a single-target array
    vec![json!({
        "file": doc["target"].as_str().unwrap_or("iteminfo.pabgb"),
        "intents": doc["intents"].clone(),
    })]
};
for tgt in targets {
    apply_target(&tgt, game_dir, output_dir)?;
}
```

**3. Dispatch by table name in `apply_target`**

DMM has two options:

**Option A (recommended): use dmm-parser's parse_table/serialize_table.**

dmm-parser is already a Cargo dependency of DMM (vendored as `crimson-rs-main` patched
copy per `BUILD_UNIFIED.md`). The patched build can expose the `parse_table` family
directly to Rust callers (no Python in the loop):

```rust
use dmm_parser::tables;  // or whatever module the macro_dispatched parsers live in

fn apply_target(tgt: &Value, game_dir: &Path, out: &Path) -> Result<()> {
    let file = tgt["file"].as_str().unwrap();        // "gimmick_info.pabgb"
    let table_name = file.trim_end_matches(".pabgb"); // "gimmick_info"
    let pabgb = extract_file(game_dir, "0008", INTERNAL_DIR, file)?;
    let pabgh = extract_file(game_dir, "0008", INTERNAL_DIR,
                              &format!("{}.pabgh", table_name))?;

    // Parse to JSON
    let mut items = parse_table_dispatch(table_name, &pabgb, Some(&pabgh))?;

    // Index for lookup
    let by_name: HashMap<&str, usize> = items.iter().enumerate()
        .filter_map(|(i, it)| it["string_key"].as_str().map(|s| (s, i)))
        .collect();
    let by_key: HashMap<u64, usize> = items.iter().enumerate()
        .filter_map(|(i, it)| it["key"].as_u64().map(|k| (k, i)))
        .collect();

    // Apply intents
    for intent in tgt["intents"].as_array().unwrap() {
        let entry = intent["entry"].as_str().unwrap_or("");
        let key   = intent["key"].as_u64().unwrap_or(0);
        let target_idx = by_name.get(entry).copied()
            .or_else(|| by_key.get(&key).copied());
        if let Some(idx) = target_idx {
            apply_field_set(&mut items[idx],
                intent["field"].as_str().unwrap(),
                &intent["new"])?;
        } else {
            warn!("v3.1: entry '{entry}' (key {key}) not found in {file}");
        }
    }

    // Serialize back
    let modified = serialize_table_dispatch(table_name, &items)?;
    let out_path = out.join("gamedata/binary__/client/bin").join(file);
    create_dir_all(out_path.parent().unwrap())?;
    fs::write(out_path, modified)?;
    Ok(())
}
```

The Rust-native `parse_table_dispatch` / `serialize_table_dispatch` are NOT yet
exposed in `dmm-parser`'s public API — only the Python bindings via `pyo3` are. To
avoid embedding Python in DMM, expose Rust-callable variants. Recommended PR to
`dmm-parser`:

```rust
// in src/lib.rs (or src/dispatch.rs):
pub fn parse_table_to_json(table_name: &str, pabgb: &[u8], pabgh: Option<&[u8]>)
    -> io::Result<Vec<serde_json::Value>>
{
    // Same body as src/python.rs::dispatch_parse() but returning io::Result
    // instead of PyResult, no PyO3 types.
}

pub fn serialize_table_from_json(table_name: &str, items: &[serde_json::Value])
    -> io::Result<Vec<u8>>
{
    // Same body as src/python.rs::dispatch_serialize_bytes()
}
```

**Option B: shell out to a Python helper.**

If embedding Python in DMM is acceptable (the bundled distribution already includes
Python via PyInstaller), DMM can call `dmm_parser.parse_table` from a small Python
subprocess that reads JSON intent + file path on stdin and writes modified pabgb
to a path. Slower, more fragile, but no Rust API additions needed.

**Recommended: Option A.** It's a ~50-line addition to dmm-parser and removes a
runtime dependency on Python from DMM's apply path.

### Field-path resolver

DMM needs to walk dot-separated/bracket-indexed paths in `serde_json::Value`:

```rust
fn apply_field_set(target: &mut Value, path: &str, new: &Value) -> Result<()> {
    let parts = split_path(path);  // "drop_default_data.use_socket" → ["drop_default_data", "use_socket"]
    let mut cur = target;
    for part in &parts[..parts.len() - 1] {
        cur = navigate_one_step(cur, part)?;  // handles foo[3] indexing
    }
    let last = parts.last().unwrap();
    set_one_step(cur, last, new)
}
```

Same algorithm as the Python reference in `FIELD_JSON_V3_1_SPEC.md` § "Path resolution algorithm".

### Backward compatibility

DMM 1.3.4 must continue to apply v3.0 (`format: 3, target: "iteminfo.pabgb"`, no `targets[]`)
documents unchanged. The detection logic above synthesizes a single-target array for v3.0
docs so the same `apply_target` loop handles both formats.

---

## Test fixtures

Once DMM 1.3.4 is built, run these against the Stacker output from
`BUILD_TEST_V3_1.md` Tests A-D:

| Test | Input | Expected DMM behavior |
|---|---|---|
| A | Single-table v3.1 doc (gimmick_info only) | Apply gimmick_info intents, success |
| B | Two-mod no-conflict stack | Both intents applied to same entry, different fields |
| C | Cross-table v3.1 doc | All 3 target sections applied |
| D | Legacy v3.0 doc (iteminfo only, no targets[]) | Apply iteminfo intents (legacy path) |

---

## Coordinating the release

1. **dmm-parser**: expose `parse_table_to_json` / `serialize_table_from_json` as Rust API.
   Tag a release (e.g. `v0.2.0`).
2. **DMM**: bump version to 1.3.4, depend on dmm-parser ≥ 0.2.0, implement `apply_target`
   per Option A above.
3. **CrimsonGameMods**: ship Stacker v1.1.5 (PR #53 merged) which already emits v3.1 docs.
4. **Coordinate distribution**: bundle DMM 1.3.4 with CrimsonGameMods v1.1.5 so testers
   get matched versions.

---

## Stretch (defer to v3.2)

Don't implement these in 1.3.4:

- `list_set` / `list_append` / `list_remove` / `list_merge` ops
- `add_entry` op (creating new records)
- Cross-table reference resolution (`@gimmick_info.X` syntax)
- Schema discovery API
