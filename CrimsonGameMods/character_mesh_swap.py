"""Character mesh-swap byte-patcher for characterinfo.pabgb overlays.

Transmog analogue for characters: overwrites the target character's
`_appearanceName` u32 key (4 bytes) with the source's, so the game renders
the target with the source's mesh/visual. Same overlay slot as Field Edit
(0039) — uses our existing pack_mod pipeline.

Location of `_appearanceName` stream offset:
  * struct runtime offset = +146 (u16 lookup result)
  * stream offset = byte 12-16 of the 7× 4B lookup block that starts right
    after `_factionInfo` (struct +138). Verified via IDA decompile of
    sub_141045620 + error-label decode (2026-04-12).

Entry parser (`characterinfo_full_parser.parse_all_entries`) records
`_appearanceName_stream_offset` and `_appearanceName_key` on every entry,
so this module just indexes by entry_key and writes 4 bytes.
"""
from __future__ import annotations

import logging
import struct
from typing import Iterable

log = logging.getLogger(__name__)


def apply_mesh_swaps(pabgb_data: bytes | bytearray,
                     pabgh_data: bytes | bytearray,
                     swaps: Iterable[dict]) -> tuple[bytearray, int, list[dict]]:
    """Apply a list of {src, tgt} character-key swaps to a characterinfo.pabgb buffer.

    Args:
      pabgb_data: raw characterinfo.pabgb bytes.
      pabgh_data: raw characterinfo.pabgh index bytes (needed to walk entries).
      swaps: iterable of {'src': src_char_key, 'tgt': tgt_char_key}. The
             target character's `_appearanceName` u32 is overwritten with
             the source's.

    Returns:
      (modified_pabgb, applied_count, report)
      report: list of {'tgt','src','tgt_offset','old_key','new_key'} dicts.
    """
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
