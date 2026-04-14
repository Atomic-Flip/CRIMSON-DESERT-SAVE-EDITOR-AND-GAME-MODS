"""
factionnode.pabgb parser — finds _needOperatorMaxCount for ALL schedules.

Field layout determined by:
1. IDA decompile of sub_14103DFA0 (FactionScheduleInfo reader) for field ORDER
2. Runtime hook confirmation that maxOp=4 for HernandCastle at struct offset +84
3. Empirical binary scan showing maxOp at TypeArray_end + 12 (3 empty u32 counts)

NOTE: IDA decompile (possibly from older build) shows PlayList has trailing fields
(enum2B + enum2B + u8), but current game binary does NOT include these when count=0.
The empirical approach uses data-driven field sizes confirmed across 700+ entries.

Schedule field sequence (32 fields from IDA, regrouped empirically):
  Group 1 - Parsed fields:
    1.  u8          _scheduleType
    2.  TypeArray   u32 count + per elem: 22B + u32 inner_count + inner_count*6B
    3.  u32         count_A (PlayList byte count, usually 0)
    4.  u32         count_B (CArray count, usually 0)
    5.  u32         _periodTimeInSecond
    6.  u32         _needOperatorMaxCount       <-- TARGET
    7.  u32         _maxCombatPower
    8.  u8          field
    9.  u8          field

  Group 2 - Variable-length tail (parse to find next schedule):
    10. u32         key (lookup)
    11. CString     u32 len + bytes
    12. LocStr      u8 + u64 + CString(u32 len + bytes)
    13. Array       u32 count + count*24B
    14-17. 4x enum2B (8B total)
    18. u32         key (lookup)
    19. CString     u32 len + bytes
    20. u32         _needOperatorMinCount
    21-23. 3x u32   (12B)
    24. Blob        u32 len + bytes
    25-26. 2x enum2B (4B)
    27. u64         (8B)
    28. 12B         position (XYZ)
    29. Array5B     u32 count + count*5B
    30. Array10B    u32 count + count*10B
    31. u8          field
"""
import struct
import sys
import json


def _read_u32(D, p):
    return struct.unpack_from('<I', D, p)[0], p + 4

def _read_cstring_skip(D, p):
    """Skip CString: u32 len + bytes. Returns new position."""
    slen = struct.unpack_from('<I', D, p)[0]
    return p + 4 + slen

def _read_locstr_skip(D, p):
    """Skip LocalizableString: u8 + u64 + CString."""
    p += 1
    p += 8
    return _read_cstring_skip(D, p)


def parse_operator_counts(pabgb_path, pabgh_path):
    with open(pabgb_path, 'rb') as f:
        D = f.read()
    with open(pabgh_path, 'rb') as f:
        G = f.read()

    c16 = struct.unpack_from('<H', G, 0)[0]
    if 2 + c16 * 8 == len(G):
        idx_start, count = 2, c16
    else:
        count = struct.unpack_from('<I', G, 0)[0]
        idx_start = 4

    idx = {}
    for i in range(count):
        pos = idx_start + i * 8
        if pos + 8 > len(G):
            break
        idx[struct.unpack_from('<I', G, pos)[0]] = struct.unpack_from('<I', G, pos + 4)[0]

    sorted_offs = sorted(set(idx.values()))
    results = []
    failures = 0

    for key, eoff in idx.items():
        bi = sorted_offs.index(eoff)
        end = sorted_offs[bi + 1] if bi + 1 < len(sorted_offs) else len(D)
        nlen = struct.unpack_from('<I', D, eoff + 4)[0]
        if nlen > 200:
            failures += 1
            continue
        name = D[eoff + 8:eoff + 8 + nlen].decode('utf-8', errors='replace')

        try:
            scheds, worker_count, worker_offset = _parse_all_schedules(D, eoff, end)
            results.append({
                'key': key,
                'name': name,
                'entry_offset': eoff,
                'schedules': scheds,
                'worker_count': worker_count,
                'worker_count_offset': worker_offset,
            })
        except Exception:
            failures += 1

    return results, failures


def _parse_entry_header(D, eoff, end):
    """Parse FactionNodeInfo header. Returns (sched_start, sched_count) or (-1, 0)."""
    p = eoff
    p += 4
    slen = struct.unpack_from('<I', D, p)[0]
    p += 4 + slen + 1

    p += 16

    slen2 = struct.unpack_from('<I', D, p)[0]
    p += 4 + slen2

    for _ in range(2):
        cnt = struct.unpack_from('<I', D, p)[0]
        p += 4 + cnt * 4

    p += 16

    for _ in range(4):
        cnt = struct.unpack_from('<I', D, p)[0]
        p += 4
        if cnt > 0:
            return -1, 0

    sched_count = struct.unpack_from('<I', D, p)[0]
    p += 4
    if sched_count <= 0 or sched_count > 100:
        return -1, 0
    return p, sched_count


def _parse_type_array(D, p):
    """Parse TypeArray: u32 count + elements(22B + u32 inner_count + inner*6B).
    Returns new position or -1."""
    tc, p = _read_u32(D, p)
    if tc > 100:
        return -1
    for _ in range(tc):
        p += 22
        ic, p = _read_u32(D, p)
        if ic > 1000:
            return -1
        p += ic * 6
    return p


def _parse_schedule_head(D, p, end):
    """Parse the CONFIRMED head fields of a FactionScheduleInfo.
    Returns dict with target fields + head_end position, or None."""
    if p + 20 > end:
        return None

    try:
        sched_type = D[p]
        if sched_type < 1 or sched_type > 5:
            return None
        p += 1

        p = _parse_type_array(D, p)
        if p < 0:
            return None

        count_a, p = _read_u32(D, p)
        if count_a > 0:
            p += count_a
        count_b, p = _read_u32(D, p)
        if count_b > 0:
            p += count_b * 4

        if p + 14 > end:
            return None

        period, p = _read_u32(D, p)

        maxop_off = p
        maxop, p = _read_u32(D, p)

        combat, p = _read_u32(D, p)

        p += 2

        if maxop > 200:
            return None

        return {
            'sched_type': sched_type,
            'max_operator_count': maxop,
            'max_operator_offset': maxop_off,
            'period_time': period,
            'max_combat_power': combat,
            'head_end': p,
        }

    except (struct.error, IndexError):
        return None


def _parse_schedule_tail(D, p, end):
    """Parse the tail of a FactionScheduleInfo to find the next schedule start.

    Tail field layout (determined empirically 2026-04-07):
      F10. u32 key (4B)
      F11. CString (u32 len + bytes, always empty len=0)
      F12. LocStr: u8(1) + u64(8) + CString(u32 len + bytes)
      F13. u32 enum/hash (4B, constant 0xA0021021)
      F14. u8 flag (1B, constant 0x0F)
      F15. u32 key (4B, variable hash)
      F16. CString numeric ID (u32 len + bytes)
      F17. u32 sub_count (4B, usually 3)
      F18. u32 param (4B, usually 11)
      F19. sub_count × 22B elements (4×u32 + u16 + u32 each)
      F20. 5×u32 (20B, usually zeros)
      F21-23. 3×u32 (12B: field, field, _needOperatorMinCount)
      F24. u32 key (4B, key for combat CString)
      F25. CString (u32 len + bytes, usually "Combat")
      F26. 4×u32 (16B, usually zeros)
      F27. 12B position (3 floats)
      F28. Array9B: u32 count + count×9B (enum4B + u8 + enum4B)
      F29. Array12B: u32 count + count×12B (enum4B + u32 + u32)
      F30. u8 final (1B)

    Note: enum readers consume 4 bytes from stream (stored as 2B in struct).

    Returns (next_position, min_op_offset, min_op_value) or (-1, -1, -1) on failure.
    """
    try:
        _, p = _read_u32(D, p)

        p = _read_cstring_skip(D, p)

        p += 1 + 8
        p = _read_cstring_skip(D, p)

        _, p = _read_u32(D, p)

        p += 1

        _, p = _read_u32(D, p)

        p = _read_cstring_skip(D, p)

        sub_count, p = _read_u32(D, p)
        if sub_count > 20:
            return -1, -1, -1, None, None
        _, p = _read_u32(D, p)

        sub_elements = []
        elem_start = p
        for _si in range(sub_count):
            ep = elem_start + _si * 30
            s_time, _ = _read_u32(D, ep + 8)
            s_count = D[ep + 26]
            sub_elements.append({
                'time': s_time,
                'count': s_count,
                'time_offset': ep + 8,
                'count_offset': ep + 26,
            })
        p += sub_count * 30

        p += 20

        p += 8

        min_op_offset = p
        min_op_value, p = _read_u32(D, p)

        _, p = _read_u32(D, p)

        p = _read_cstring_skip(D, p)

        p += 16

        p += 12

        arr9_count, p = _read_u32(D, p)
        if arr9_count > 100:
            return -1, -1, -1, None
        p += arr9_count * 9

        arr12_count, p = _read_u32(D, p)
        if arr12_count > 100:
            return -1, -1, -1, None
        p += arr12_count * 12

        p += 1

        if p > end:
            return -1, -1, -1, None

        return p, min_op_offset, min_op_value, sub_elements

    except (struct.error, IndexError):
        return -1, -1, -1, None


def _parse_post_schedule_fields(D, p, end):
    """Parse post-schedule fields to extract workerCount.

    Post-schedule layout (from IDA sub_14103E930):
      _factionType:          1B (u8)
      _subInnerTypeString:   Blob (4+len)
      _workerCount:          1B (u8)

    Returns (worker_count, worker_count_offset) or (-1, -1).
    """
    try:
        if p + 6 > end:
            return -1, -1
        p += 1
        blen, p = _read_u32(D, p)
        if blen > 100000:
            return -1, -1
        p += blen
        if p >= end:
            return -1, -1
        worker_count = D[p]
        worker_offset = p
        return worker_count, worker_offset
    except (struct.error, IndexError):
        return -1, -1


def _parse_all_schedules(D, eoff, end):
    """Parse all schedules from a factionnode entry.
    Parses head (target fields) + tail (to advance to next schedule).
    Also reads post-schedule workerCount."""
    sched_start, sched_count = _parse_entry_header(D, eoff, end)
    if sched_start < 0:
        return [], -1, -1

    schedules = []
    p = sched_start

    for si in range(sched_count):
        sched = _parse_schedule_head(D, p, end)
        if sched is None:
            break

        next_p, min_op_offset, min_op_value, sub_elems = _parse_schedule_tail(D, sched['head_end'], end)
        sched['min_operator_count'] = min_op_value if min_op_offset >= 0 else -1
        sched['min_operator_offset'] = min_op_offset
        sched['sub_elements'] = sub_elems or []

        schedules.append(sched)

        if next_p < 0:
            break
        p = next_p

    worker_count, worker_offset = _parse_post_schedule_fields(D, p, end)

    return schedules, worker_count, worker_offset


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')

    EXT = 'C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full'
    results, failures = parse_operator_counts(
        f'{EXT}/factionnode.pabgb',
        f'{EXT}/factionnode.pabgh',
    )

    total_scheds = sum(len(r['schedules']) for r in results)
    nodes_with = sum(1 for r in results if r['schedules'])
    print(f"Parsed: {len(results)} nodes, {failures} failed")
    print(f"Nodes with schedules: {nodes_with}, total schedules: {total_scheds}")

    from collections import Counter
    max_ops = Counter()
    periods = Counter()
    combats = Counter()
    for r in results:
        for s in r['schedules']:
            max_ops[s['max_operator_count']] += 1
            periods[s['period_time']] += 1
            combats[s['max_combat_power']] += 1

    print(f"\n_needOperatorMaxCount distribution:")
    for v, cnt in max_ops.most_common(30):
        print(f"  {v:>6d}: {cnt}x")

    print(f"\n_periodTimeInSecond distribution:")
    for v, cnt in periods.most_common(20):
        print(f"  {v:>6d}: {cnt}x")

    print(f"\n_maxCombatPower distribution:")
    for v, cnt in combats.most_common(20):
        print(f"  {v:>6d}: {cnt}x")

    print(f"\nSample nodes with schedules:")
    shown = 0
    for r in results:
        if r['schedules']:
            name = r['name'].replace('Node_', '').replace('_', ' ')[:50]
            ops = [(s['max_operator_count'], s['period_time'],
                    s['max_combat_power']) for s in r['schedules']]
            print(f"  {name:50s} scheds={len(r['schedules'])} data={ops}")
            shown += 1
            if shown >= 20:
                break

    edit_targets = []
    for r in results:
        for i, s in enumerate(r['schedules']):
            edit_targets.append({
                'node_key': r['key'],
                'node_name': r['name'],
                'schedule_index': i,
                'sched_type': s['sched_type'],
                'max_op_offset': s['max_operator_offset'],
                'max_op_value': s['max_operator_count'],
                'period': s['period_time'],
                'combat_power': s['max_combat_power'],
            })

    print(f"\nTotal editable schedule offsets: {len(edit_targets)}")
    with open('factionnode_operator_offsets.json', 'w', encoding='utf-8') as f:
        json.dump(edit_targets, f, indent=2, ensure_ascii=False)
    print("Saved to factionnode_operator_offsets.json")
