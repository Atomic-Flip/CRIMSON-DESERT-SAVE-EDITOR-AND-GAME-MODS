import struct
import json
import os


def _u32(D, p):
    return struct.unpack_from('<I', D, p)[0], p + 4


def _skip_cstring(D, p):
    slen, p = _u32(D, p)
    if slen > 50000: return -1
    return p + slen


def _skip_locstr(D, p):
    p += 1 + 8
    return _skip_cstring(D, p)


def _read_array_4B(D, p):
    count, p = _u32(D, p)
    if count > 100000: return None, -1
    values = []
    for _ in range(count):
        v, p = _u32(D, p)
        values.append(v)
    return values, p


def parse_pabgh(G):
    c16 = struct.unpack_from('<H', G, 0)[0]
    if 2 + c16 * 8 == len(G):
        idx_start, count = 2, c16
    else:
        count = struct.unpack_from('<I', G, 0)[0]
        idx_start = 4
    idx = {}
    for i in range(count):
        pos = idx_start + i * 8
        if pos + 8 > len(G): break
        idx[struct.unpack_from('<I', G, pos)[0]] = struct.unpack_from('<I', G, pos + 4)[0]
    return idx


def parse_quest_entry(D, eoff, end):
    p = eoff
    try:
        key, p = _u32(D, p)

        slen, _ = _u32(D, p)
        if slen > 500: return None
        name = D[p+4:p+4+slen].decode('utf-8', errors='replace')
        p = p + 4 + slen

        is_blocked = D[p]; p += 1
        p += 1
        p += 1

        p = _skip_locstr(D, p)
        if p < 0: return None

        p = _skip_locstr(D, p)
        if p < 0: return None

        p += 2

        p += 4

        pl_count, p = _u32(D, p)
        if pl_count > 10000: return None
        p += pl_count
        p += 4
        p += 4
        p += 1

        p += 4 + 4 + 1 + 1 + 4 + 4

        missions, p = _read_array_4B(D, p)
        if p < 0: return None

        f13_count, p = _u32(D, p)
        if f13_count > 10000: return None
        p += f13_count * 18

        f14_count, p = _u32(D, p)
        if f14_count > 10000: return None
        p += f14_count * 4

        stages, p = _read_array_4B(D, p)
        if p < 0: return None

        return {
            'key': key,
            'name': name,
            'is_blocked': is_blocked,
            'missions': missions,
            'stages': stages,
        }

    except (struct.error, IndexError):
        return None


def parse_all(pabgb_path, pabgh_path):
    with open(pabgb_path, 'rb') as f: D = f.read()
    with open(pabgh_path, 'rb') as f: G = f.read()

    idx = parse_pabgh(G)
    sorted_offs = sorted(set(idx.values()))
    entries = []
    failures = 0

    for key, eoff in idx.items():
        bi = sorted_offs.index(eoff)
        end = sorted_offs[bi + 1] if bi + 1 < len(sorted_offs) else len(D)
        entry = parse_quest_entry(D, eoff, end)
        if entry:
            entries.append(entry)
        else:
            failures += 1

    return entries, failures


def build_quest_stage_map(entries):
    result = {}
    for e in entries:
        if e['stages']:
            result[e['key']] = e['stages']
    return result


def build_quest_mission_map(entries):
    result = {}
    for e in entries:
        if e['missions']:
            result[e['key']] = e['missions']
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    try:
        import crimson_rs
        game_path = 'C:/Program Files (x86)/Steam/steamapps/common/Crimson Desert'
        dp = 'gamedata/binary__/client/bin'
        body = crimson_rs.extract_file(game_path, '0008', dp, 'questinfo.pabgb')
        gh = crimson_rs.extract_file(game_path, '0008', dp, 'questinfo.pabgh')
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pabgb', delete=False) as f:
            f.write(body); pb = f.name
        with tempfile.NamedTemporaryFile(suffix='.pabgh', delete=False) as f:
            f.write(gh); pg = f.name
    except:
        EXT = 'C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full'
        pb = f'{EXT}/questinfo.pabgb'
        pg = f'{EXT}/questinfo.pabgh'

    entries, failures = parse_all(pb, pg)
    print(f"Parsed: {len(entries)} entries, {failures} failures")

    quest_stages = build_quest_stage_map(entries)
    quest_missions = build_quest_mission_map(entries)

    with_stages = sum(1 for e in entries if e['stages'])
    with_missions = sum(1 for e in entries if e['missions'])
    total_stages = sum(len(e['stages']) for e in entries)
    total_missions = sum(len(e['missions']) for e in entries)

    print(f"Quests with stages: {with_stages} ({total_stages} stage links)")
    print(f"Quests with missions: {with_missions} ({total_missions} mission links)")

    out = {'quest_stages': quest_stages, 'quest_missions': quest_missions}
    with open('quest_stage_map.json', 'w') as f:
        json.dump(out, f, indent=2)
    print("Saved to quest_stage_map.json")

    for e in entries[:5]:
        if e['stages']:
            print(f"  {e['name']}: {len(e['stages'])} stages, {len(e['missions'])} missions")
            print(f"    stages: {e['stages'][:10]}")
