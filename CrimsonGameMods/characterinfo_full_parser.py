
import struct
import json
import os
import sys
import logging

log = logging.getLogger(__name__)


def parse_pabgh_index(pabgh_data):
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
    slen = struct.unpack_from('<I', data, p)[0]
    p += 4
    if slen > 100000:
        return None, p
    s = data[p:p + slen].decode('utf-8', errors='replace')
    return s, p + slen


def _read_locstr(data, p):
    p += 1
    p += 8
    slen = struct.unpack_from('<I', data, p)[0]
    p += 4 + slen
    return p


def _read_locstr_with_hash(data, p):
    p += 1
    hv = struct.unpack_from('<Q', data, p)[0]
    p += 8
    slen = struct.unpack_from('<I', data, p)[0]
    p += 4 + slen
    return hv, p


def parse_entry(data, offset, end):
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

        result['_factionInfo_offset'] = p - 4
        result['_factionInfo_key'] = struct.unpack_from('<I', data, p - 4)[0]
        result['_upperActionChartPackageGroupName_offset'] = p
        result['_upperActionChartPackageGroupName_key'] = struct.unpack_from('<I', data, p)[0]
        result['_lowerActionChartPackageGroupName_offset'] = p + 4
        result['_lowerActionChartPackageGroupName_key'] = struct.unpack_from('<I', data, p + 4)[0]
        result['_characterGamePlayDataName_offset'] = p + 8
        result['_characterGamePlayDataName_key'] = struct.unpack_from('<I', data, p + 8)[0]
        result['_appearanceName_stream_offset'] = p + 12
        result['_appearanceName_offset'] = p + 12
        result['_appearanceName_key'] = struct.unpack_from('<I', data, p + 12)[0]
        result['_characterPrefabPath_stream_offset'] = p + 16
        result['_characterPrefabPath_offset'] = p + 16
        result['_characterPrefabPath_key'] = struct.unpack_from('<I', data, p + 16)[0]
        result['_skeletonName_offset'] = p + 20
        result['_skeletonName_key'] = struct.unpack_from('<I', data, p + 20)[0]
        result['_skeletonVariationName_offset'] = p + 24
        result['_skeletonVariationName_key'] = struct.unpack_from('<I', data, p + 24)[0]
        p += 28

        try:
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
            log.debug("Partial parse for %s (post-name fields skipped): %s", result.get('name', '?'), e)
            result['_partial_parse'] = True
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
