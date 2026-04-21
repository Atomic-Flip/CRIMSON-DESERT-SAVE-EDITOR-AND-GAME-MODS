from __future__ import annotations

import logging
import struct
from typing import Iterable

log = logging.getLogger(__name__)


def apply_mesh_swaps(pabgb_data: bytes | bytearray,
                     pabgh_data: bytes | bytearray,
                     swaps: Iterable[dict]) -> tuple[bytearray, int, list[dict]]:
    from characterinfo_full_parser import parse_all_entries

    entries = parse_all_entries(bytes(pabgb_data), bytes(pabgh_data))
    by_key = {int(e.get('entry_key', 0)): e for e in entries}

    out = bytearray(pabgb_data)
    applied = 0
    report: list[dict] = []
    missing_tgt: list[int] = []
    missing_src: list[int] = []

    for sw in swaps:
        try:
            tgt_key = int(sw['tgt'])
            src_key = int(sw['src'])
        except (KeyError, TypeError, ValueError):
            continue
        if tgt_key == src_key:
            continue

        tgt = by_key.get(tgt_key)
        src = by_key.get(src_key)
        if tgt is None:
            missing_tgt.append(tgt_key)
            continue
        if src is None:
            missing_src.append(src_key)
            continue

        tgt_off = tgt.get('_appearanceName_stream_offset')
        src_val = src.get('_appearanceName_key')
        if tgt_off is None or src_val is None:
            log.warning(
                "mesh swap %d->%d: entry parsed but no appearance offset/key",
                tgt_key, src_key)
            continue

        old_val = struct.unpack_from('<I', out, tgt_off)[0]
        struct.pack_into('<I', out, tgt_off, int(src_val))
        applied += 1
        report.append({
            'tgt': tgt_key,
            'src': src_key,
            'tgt_offset': tgt_off,
            'old_key': old_val,
            'new_key': int(src_val),
            'tgt_name': tgt.get('name', ''),
            'src_name': src.get('name', ''),
        })

    if missing_tgt:
        log.warning("mesh swap: target character(s) not found in pabgb: %s",
                    missing_tgt)
    if missing_src:
        log.warning("mesh swap: source character(s) not found in pabgb: %s",
                    missing_src)

    log.info("mesh swap: applied %d byte-patches for %d queued swap(s)",
             applied, sum(1 for _ in swaps) if hasattr(swaps, '__len__') else applied)
    return out, applied, report


def _load_appearance_paths() -> list[dict]:
    import json
    import os
    for base in [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'),
    ]:
        p = os.path.join(base, 'appearance_paths.json')
        if os.path.isfile(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f).get('entries') or []
            except Exception:
                continue
    return []


_SKIP_TOKENS = {
    'boss', 'mon', 'animal', 'nhm', 'nom', 'phm', 'pv', 'cd',
    'riding', 'rider', 'stage', 'drone', 'summon', 'upper', 'lower',
    'part', 'body', 'foot', 'hand', 'head', 'tail', 'wing', 'object',
}


def _guess_xml_path(source_entry: dict, catalog: list[dict]) -> str | None:
    import re
    internal = str(source_entry.get('internal_name') or '').lower()
    display = str(source_entry.get('display_name') or '').lower()
    combined = internal + ' ' + display
    tokens = [
        t for t in re.findall(r'[a-z]+', combined)
        if t not in _SKIP_TOKENS and len(t) >= 4
    ]
    if not tokens:
        return None
    best = None
    best_score = 0
    for e in catalog:
        score = 0
        stem = e.get('stem') or ''
        folder = e.get('folder') or ''
        for t in tokens:
            if t in stem:
                score += 2
            elif t in folder:
                score += 1
        if score > best_score:
            best_score = score
            best = e.get('path')
    return best if best_score >= 2 else None


def build_scale_override_mod(swaps, game_path: str, charinfo_data: bytes,
                             charinfo_header: bytes, out_dir: str) -> dict:
    """Generate a crimson_sharp_mod_v1 mod that overrides CharacterScale for
    each swap whose entry has a positive 'scale' value.

    swaps: iterable of {'src': int, 'tgt': int, 'scale': float, ...}
    Returns a summary dict: {written: [paths], skipped: [{src, reason}]}.
    """
    import json
    import os
    import re
    import shutil

    try:
        import crimson_rs
    except Exception as e:
        return {'written': [], 'skipped': [], 'error': 'crimson_rs not importable: ' + str(e)}

    from characterinfo_full_parser import parse_all_entries
    entries = parse_all_entries(bytes(charinfo_data), bytes(charinfo_header))
    by_key = {int(e.get('entry_key', 0)): e for e in entries}

    catalog = _load_appearance_paths()
    if not catalog:
        return {'written': [], 'skipped': [], 'error': 'appearance_paths.json not found'}

    scale_swaps = [s for s in swaps if float(s.get('scale') or 0) > 0]
    if not scale_swaps:
        return {'written': [], 'skipped': []}

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    manifest = {
        'format': 'crimson_sharp_mod_v1',
        'id': 'mesh-swap-scale-overrides',
        'title': 'Mesh Swap — Scale Overrides',
        'author': 'CrimsonGameMods',
        'version': '1.0.0',
        'description': 'Per-source CharacterScale overrides emitted alongside a mesh swap.',
        'enabled': True,
        'priority': 0,
        'files_dir': 'files',
        'patches_dir': 'patches',
    }
    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    paz_id = '0009'
    written: list[str] = []
    skipped: list[dict] = []

    for s in scale_swaps:
        sk = int(s.get('src', 0))
        scale = float(s.get('scale'))
        entry = by_key.get(sk)
        if not entry:
            skipped.append({'src': sk, 'reason': 'source not in characterinfo'})
            continue

        xml_path = _guess_xml_path(entry, catalog)
        if not xml_path:
            skipped.append({'src': sk, 'reason': 'no matching .app.xml path'})
            continue

        parts = xml_path.rsplit('/', 1)
        dirp, fn = parts[0], parts[1]
        try:
            raw = crimson_rs.extract_file(game_path, paz_id, dirp, fn)
        except Exception as ex:
            skipped.append({'src': sk, 'reason': 'paz extract failed: ' + str(ex)})
            continue
        if not raw:
            skipped.append({'src': sk, 'reason': 'paz extract empty'})
            continue

        text = bytes(raw).decode('utf-8', errors='replace')
        new_text, n = re.subn(
            r'CharacterScale="[\d.]+"',
            'CharacterScale="' + str(scale) + '"',
            text, count=1,
        )
        if n == 0:
            if '<Prefab' in new_text:
                new_text = new_text.replace(
                    '<Prefab',
                    '<Prefab CharacterScale="' + str(scale) + '"',
                    1,
                )
            else:
                skipped.append({'src': sk, 'reason': 'no <Prefab> tag to patch'})
                continue

        out_path = os.path.join(out_dir, 'files', paz_id, dirp.replace('/', os.sep), fn)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(new_text)
        written.append(out_path)
        log.info("scale override: src=%d scale=%.2f xml=%s", sk, scale, xml_path)

    return {'written': written, 'skipped': skipped}
