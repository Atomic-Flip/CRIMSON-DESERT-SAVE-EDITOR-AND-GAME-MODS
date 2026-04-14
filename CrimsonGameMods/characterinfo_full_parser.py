#!/usr/bin/env python3
"""CharacterInfo PABGB parser — extracts mount, NPC, and combat fields from
characterinfo.pabgb using the IDA-decoded reader order (sub_141037900).

Parses the header + scalar fields + boolean block covering:
  - Mount fields: _vehicleInfo, _callMercenaryCoolTime, _callMercenarySpawnDuration
  - Combat fields: _isAttackable, _isAggroTargetable, _sendKillEventOnDead, _invincibility
  - NPC fields: _isEnableFriendly, and 40 other boolean flags
"""

import struct
import json
import os
import sys
import logging

log = logging.getLogger(__name__)


def parse_pabgh_index(pabgh_data):
    """Parse characterinfo.pabgh: u16 count, then count * (u32 key + u32 offset)."""
    count = struct.unpack_from('<H', pabgh_data, 0)[0]
    entries = {}
    pos = 2
    for _ in range(count):
        key = struct.unpack_from('<I', pabgh_data, pos)[0]
        offset = struct.unpack_from('<I', pabgh_data, pos + 4)[0]
        entries[key] = offset
        pos += 8
    return entries


def _read_cstring(data, p):
    """Read u32-length-prefixed string, return (string, new_pos)."""
    slen = struct.unpack_from('<I', data, p)[0]
    p += 4
    if slen > 100000:
        return None, p
    s = data[p:p + slen].decode('utf-8', errors='replace')
    return s, p + slen


def _read_locstr(data, p):
    """Read LocalizableString: u8 flag + u64 hash + CString. Return new_pos."""
    p += 1
    p += 8
    slen = struct.unpack_from('<I', data, p)[0]
    p += 4 + slen
    return p


def _read_locstr_with_hash(data, p):
    """Read LocalizableString and return (u64_hash, new_pos).

    The u64 hash is the key used by the paloc localization table.
    """
    p += 1
    hv = struct.unpack_from('<Q', data, p)[0]
    p += 8
    slen = struct.unpack_from('<I', data, p)[0]
    p += 4 + slen
    return hv, p


def parse_entry(data, offset, end):
    """Parse one CharacterInfo entry through the boolean block.

    Returns dict with all parsed fields + byte offsets for in-place editing,
    or None on parse failure.

    Stream read order from IDA decompile of sub_141037900:
      H0:  entry_key (u32, 4B)
      H1:  entry_name (CString)
      H2:  _isBlocked (u8, 1B)
      L0:  _stringKey1 (LocStr)
      L1:  _stringKey2 (LocStr)
      F00: enum4 hash -> a2+88 (4B stream)
      F01: enum4 hash -> a2+90 (4B stream)
      F02: CString -> a2+96
      F03: _spawnFixType (u8)
      F04: _isRemoteCatchable (u8)
      F05: key lookup (4B stream -> u16 at a2+106)
      F06: key lookup (4B stream -> u16 at a2+108)
      F07: _vehicleInfo (sub_14105F770, 2B stream -> u16)
      F08: _callMercenaryCoolTime (u64, 8B)
      F09: _callMercenarySpawnDuration (u64, 8B)
      F10: _mercenaryCoolTimeType (u8, 1B)
      LOOP i=0..1:
        sub_1408F5560_0_1336 (4B stream)
        sub_14105F3A0 (2B stream)
      F11: sub_1408F5560_0_1342 (4B stream)
      F12-F18: sub_1408F5560_0_1335 x7 (4B each = 28B)
      F19: u32 direct (4B)
      F20-F21: sub_1408F5560_0_1335 x2 (8B)
      F22: sub_1408F5560_0_1343 (4B)
      F23: u32 direct (4B)
      F24: sub_1408F5560_0_1335 (4B)
      F25: u32 direct (4B)
      F26: u32 direct (4B)
      F27: u8 direct (1B)
      F28: u8 direct (1B)
      F29: sub_14105F910 (1B stream -> enum)
      F30: LocStr (variable)
      F31: sub_1408F5560_0_1336 (4B stream)
      F32: u8 direct (1B)
      F33: u16 direct (2B)
      F34-F73: 40x u8 booleans (a2+230..a2+269)
        a2+233 = _isAttackable
        a2+234 = _isAggroTargetable
        a2+247 = _sendKillEventOnDead
        a2+250 = _invincibility
    """
    p = offset
    result = {}

    try:
        result['entry_key'] = struct.unpack_from('<I', data, p)[0]; p += 4
        name, p = _read_cstring(data, p)
        if name is None:
            return None
        result['name'] = name
        result['_isBlocked'] = data[p]; p += 1

        name_hash, p = _read_locstr_with_hash(data, p)
        desc_hash, p = _read_locstr_with_hash(data, p)
        result['_characterName_hash'] = name_hash
        result['_characterDesc_hash'] = desc_hash

        p += 4
        p += 4

        _, p = _read_cstring(data, p)

        p += 1
        p += 1
        p += 4
        p += 4

        result['_vehicleInfo_offset'] = p
        result['_vehicleInfo'] = struct.unpack_from('<H', data, p)[0]
        p += 2

        result['_callMercenaryCoolTime_offset'] = p
        result['_callMercenaryCoolTime'] = struct.unpack_from('<Q', data, p)[0]
        p += 8

        result['_callMercenarySpawnDuration_offset'] = p
        result['_callMercenarySpawnDuration'] = struct.unpack_from('<Q', data, p)[0]
        p += 8

        result['_mercenaryCoolTimeType'] = data[p]; p += 1

        p += 4 + 2
        p += 4 + 2

        p += 4
        result['_appearanceName_stream_offset'] = p + 12
        result['_appearanceName_key'] = struct.unpack_from('<I', data, p + 12)[0]
        result['_characterPrefabPath_stream_offset'] = p + 16
        result['_characterPrefabPath_key'] = struct.unpack_from('<I', data, p + 16)[0]
        p += 28
        p += 4
        p += 8
        p += 4
        p += 4
        p += 4
        p += 4
        p += 4
        p += 1
        p += 1

        p += 1

        p += 1

        p = _read_locstr(data, p)

        p += 4

        p += 1
        p += 2

        bool_start = p
        bool_fields = {}
        for bi in range(40):
            bool_fields[bi] = data[p + bi]

        result['_isAttackable_offset'] = bool_start + 3
        result['_isAttackable'] = data[bool_start + 3]

        result['_isAggroTargetable_offset'] = bool_start + 4
        result['_isAggroTargetable'] = data[bool_start + 4]

        result['_sendKillEventOnDead_offset'] = bool_start + 17
        result['_sendKillEventOnDead'] = data[bool_start + 17]

        result['_invincibility_offset'] = bool_start + 20
        result['_invincibility'] = data[bool_start + 20]

        result['_boolBlock'] = bool_fields

        p += 40

        result['_parsed_bytes'] = p - offset
        result['_entry_size'] = end - offset

    except (struct.error, IndexError) as e:
        log.debug("Parse error for %s at offset %d: %s", result.get('name', '?'), p, e)
        return None

    return result


MOUNT_VEHICLE_TYPES = {
    16960: 'Horse',
    16966: 'Wolf',
    16978: 'Camel',
    16984: 'Dragon',
    16988: 'WarMachine/ATAG',
    16994: 'Domestic',
    16998: 'MachineBear',
    16999: 'MachineBird',
    17003: 'Wagon',
}


def parse_all_entries(pabgb_data, pabgh_data):
    """Parse all CharacterInfo entries, return list of dicts."""
    idx = parse_pabgh_index(pabgh_data)
    sorted_entries = sorted(idx.items(), key=lambda x: x[1])

    results = []
    for i, (key, eoff) in enumerate(sorted_entries):
        if i + 1 < len(sorted_entries):
            end = sorted_entries[i + 1][1]
        else:
            end = len(pabgb_data)
        r = parse_entry(pabgb_data, eoff, end)
        if r:
            results.append(r)

    return results


def parse_mounts_only(pabgb_data, pabgh_data):
    """Parse all entries and return only those with a vehicle type (mounts)."""
    all_entries = parse_all_entries(pabgb_data, pabgh_data)
    mounts = []
    for r in all_entries:
        vtype = r.get('_vehicleInfo', 0)
        if vtype in MOUNT_VEHICLE_TYPES:
            r['_vehicleTypeName'] = MOUNT_VEHICLE_TYPES[vtype]
            mounts.append(r)
        elif vtype != 0 and r.get('name', '').startswith('Riding_'):
            r['_vehicleTypeName'] = f'Unknown({vtype})'
            mounts.append(r)
    return mounts


def parse_npcs_only(pabgb_data, pabgh_data):
    """Parse all entries and return NPCs (non-mount, non-player characters)."""
    all_entries = parse_all_entries(pabgb_data, pabgh_data)
    npcs = []
    for r in all_entries:
        vtype = r.get('_vehicleInfo', 0)
        name = r.get('name', '')
        if vtype == 0 and not name.startswith('Riding_'):
            npcs.append(r)
    return npcs


def main():
    base = os.environ.get('EXTRACTED_PAZ', 'C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full')
    with open(os.path.join(base, 'characterinfo.pabgb'), 'rb') as f:
        pabgb = f.read()
    with open(os.path.join(base, 'characterinfo.pabgh'), 'rb') as f:
        pabgh = f.read()

    total = struct.unpack_from('<H', pabgh, 0)[0]
    all_entries = parse_all_entries(pabgb, pabgh)
    print(f"Parsed {len(all_entries)} / {total} entries")

    mounts = [e for e in all_entries if e.get('_vehicleInfo', 0) != 0]
    timed = [m for m in mounts if 0 < m.get('_callMercenarySpawnDuration', 0) < 100000]
    print(f"\nMounts: {len(mounts)} total, {len(timed)} timed")
    for m in sorted(timed, key=lambda x: x['name']):
        vtype = MOUNT_VEHICLE_TYPES.get(m['_vehicleInfo'], str(m['_vehicleInfo']))
        print(f"  {m['name']:<45} {vtype:<15} dur={m['_callMercenarySpawnDuration']}s cool={m['_callMercenaryCoolTime']}s")

    invincible = [e for e in all_entries if e.get('_invincibility', 0)]
    not_attackable = [e for e in all_entries if not e.get('_isAttackable', 1)]
    print(f"\n_invincibility=1: {len(invincible)} entries")
    for e in invincible[:20]:
        print(f"  {e['name']}")
    if len(invincible) > 20:
        print(f"  ... and {len(invincible) - 20} more")

    print(f"\n_isAttackable=0: {len(not_attackable)} entries")
    for e in not_attackable[:20]:
        print(f"  {e['name']}")
    if len(not_attackable) > 20:
        print(f"  ... and {len(not_attackable) - 20} more")

    not_aggro = [e for e in all_entries if not e.get('_isAggroTargetable', 1)]
    print(f"\n_isAggroTargetable=0: {len(not_aggro)} entries")


if __name__ == '__main__':
    main()
