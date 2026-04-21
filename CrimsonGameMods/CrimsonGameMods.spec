# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('crimson_data.db.gz', '.'), ('data', 'data'), ('vfx_equip_attachments.json', '.'), ('parc_parser.dll', '.'), ('locale', 'locale'), ('knowledge_packs', 'knowledge_packs'), ('dropset_packs', 'dropset_packs'), ('localizationstring_eng_items.tsv', '.')],
    hiddenimports=['lz4', 'lz4.block', 'iteminfo_parser', 'cryptography', 'cryptography.hazmat.primitives.ciphers', 'cryptography.hazmat.primitives.ciphers.algorithms', 'parc_inserter3', 'storeinfo_parser', 'gamedata_editor', 'pabgb_field_parsers', 'crimson_rs', 'crimson_rs.enums', 'crimson_rs.create_pack', 'crimson_rs.pack_mod', 'crimson_rs.validate_game_dir', 'universal_pabgb_parser', 'factionnode_operator_parser', 'fieldinfo_parser', 'vehicleinfo_parser', 'regioninfo_parser', 'armor_catalog', 'character_mesh_swap', 'gimmickinfo_parser', 'pipeline_report', 'characterinfo_full_parser', 'data_db', 'gui.tabs.buffs_v319', 'gui.tabs.field_edit', 'gui.tabs.skill_tree', 'gui.item_creator_dialog', 'gui_i18n', 'lang_pack_downloader', 'gui.language_picker', 'item_creator', 'skilltreeinfo_parser'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

splash = Splash(
    'splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(24, 195),
    text_size=10,
    text_color='#F0F0F5',
    text_default='Initializing...',
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
