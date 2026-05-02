# -*- mode: python ; coding: utf-8 -*-

# dmm_parser is optional — bundled if installed via `pip install dmm-parser`
# (or `maturin develop` from the dmm-parser repo). Provides Field JSON v3.1
# multi-target field-level catchall via dmm_parser.parse_table for 122
# typed game-data tables. See FIELD_JSON_V3_1_SPEC.md.
import importlib.util as _ilu
_dmm_spec = _ilu.find_spec('dmm_parser')
_v31_extra_datas = []
_v31_extra_hidden = []
if _dmm_spec is not None and _dmm_spec.submodule_search_locations:
    _dmm_pkg_dir = _dmm_spec.submodule_search_locations[0]
    _v31_extra_datas.append((_dmm_pkg_dir, 'dmm_parser'))
    _v31_extra_hidden.extend([
        'dmm_parser', 'dmm_parser.dmm_parser', 'dmm_parser.enums',
        'dmm_parser.pack_mod',
    ])
    print(f"  [v3.1] Bundling dmm_parser from {_dmm_pkg_dir}")
else:
    print("  [v3.1] dmm_parser not installed — Stacker will fall back to "
          "blob-level catchall (no field-level diff for non-iteminfo tables).")


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('crimson_data.db.gz', '.'), ('data', 'data'), ('vfx_equip_attachments.json', '.'), ('parc_parser.dll', '.'), ('locale', 'locale'), ('knowledge_packs', 'knowledge_packs'), ('dropset_packs', 'dropset_packs'), ('localizationstring_eng_items.tsv', '.'), ('pabgb_parser_local.py', '.'), ('crimson_rs', 'crimson_rs'), ('game_baselines', 'game_baselines'), ('stamina_presets', 'stamina_presets')] + _v31_extra_datas,
    hiddenimports=['lz4', 'lz4.block', 'iteminfo_parser', 'cryptography', 'cryptography.hazmat.primitives.ciphers', 'cryptography.hazmat.primitives.ciphers.algorithms', 'parc_inserter3', 'storeinfo_parser', 'gamedata_editor', 'pabgb_field_parsers', 'crimson_rs', 'crimson_rs.enums', 'crimson_rs.create_pack', 'crimson_rs.pack_mod', 'crimson_rs.validate_game_dir', 'universal_pabgb_parser', 'factionnode_operator_parser', 'fieldinfo_parser', 'vehicleinfo_parser', 'regioninfo_parser', 'armor_catalog', 'character_mesh_swap', 'gimmickinfo_parser', 'pipeline_report', 'characterinfo_full_parser', 'data_db', 'gui.tabs.buffs_v319', 'gui.tabs.field_edit', 'gui.tabs.skill_tree', 'gui.item_creator_dialog', 'gui.add_to_save_dialog', 'gui_i18n', 'lang_pack_downloader', 'gui.language_picker', 'item_creator', 'skilltreeinfo_parser', 'skillinfo_parser', 'mercenaryinfo_parser', 'dropset_editor', 'equipslotinfo_parser', 'gui.tabs.stacker', 'gui.tabs.mercpets', 'gui.tabs.world', 'gui.tabs.bagspace', 'gui.tabs.items'] + _v31_extra_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Read version from updater.py so splash always matches
import re as _re
with open('updater.py', 'r') as _f:
    _m = _re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)', _f.read())
_app_ver = _m.group(1) if _m else '?'

splash = Splash(
    'splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(24, 195),
    text_size=10,
    text_color='#F0F0F5',
    text_default=f'v{_app_ver} — Initializing...',
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='CrimsonGameMods',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon='app_icon.ico',
    codesign_identity=None,
    entitlements_file=None,
)
