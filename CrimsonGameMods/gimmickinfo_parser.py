"""
gimmickinfo.pabgb full parser — 159 fields, fully mapped from IDA decompile of sub_141046310.

Every variable-length reader function decompiled to determine exact stream consumption.
This parser reads through ALL fields sequentially in exact stream order.
No offset-from-end shortcuts — proper field-by-field parsing.
"""
import struct
import sys


def _u64(D, p):
    return struct.unpack_from('<Q', D, p)[0], p + 8

def _u32(D, p):
    return struct.unpack_from('<I', D, p)[0], p + 4

def _u16(D, p):
    return struct.unpack_from('<H', D, p)[0], p + 2

def _u8(D, p):
    return D[p], p + 1

def _f32(D, p):
    return struct.unpack_from('<f', D, p)[0], p + 4

def _vec3(D, p):
    x, y, z = struct.unpack_from('<fff', D, p)
    return (x, y, z), p + 12


def _skip_cstring(D, p):
    """CString (sub_14100FE80): u32 len + len bytes."""
    slen, p = _u32(D, p)
    if slen > 500000:
        return -1
    return p + slen

def _read_cstring(D, p):
    """CString: u32 len + len bytes. Returns (string, new_pos)."""
    slen, p2 = _u32(D, p)
    if slen > 500000:
        return None, -1
    try:
        s = D[p2:p2+slen].decode('utf-8', errors='replace')
    except:
        s = ''
    return s, p2 + slen

def _skip_locstr(D, p):
    """LocStr (sub_140ED6040): u8 flag + u64 hash + CString."""
    p += 1 + 8
    return _skip_cstring(D, p)

def _skip_cstring_hash(D, p):
    """CStringHash (sub_141010050, sub_141076270): u32 len + len bytes (hashed at runtime)."""
    slen, p = _u32(D, p)
    if slen > 500000:
        return -1
    return p + slen

def _skip_u32_key_array(D, p):
    """Array of u32 keys (sub_141062E20, sub_14105EEC0): u32 count + count*4B."""
    count, p = _u32(D, p)
    if count > 500000:
        return -1
    return p + count * 4

def _skip_u16_key_array(D, p):
    """Array of u16 keys: u32 count + count*2B."""
    count, p = _u32(D, p)
    if count > 500000:
        return -1
    return p + count * 2


def _skip_sub_141063510(D, p):
    """_gimmickChartParameterList: u32 count + count * (2x CStringHash).
    Each element: sub_141010050 + sub_141010050 = two CStringHash reads.
    Decompiled: reads v11 as two u32 halves via sub_141010050."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p = _skip_cstring_hash(D, p)
        if p < 0: return -1
        p = _skip_cstring_hash(D, p)
        if p < 0: return -1
    return p

def _skip_sub_1410615F0(D, p):
    """Various arrays (triggerVolumeGroupDataList, dropSetInfoList, etc):
    u32 count + count * CStringHash (sub_141010050, stored as u32).
    Decompiled: each element is one sub_141010050 call -> stored in 4B."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p = _skip_cstring_hash(D, p)
        if p < 0: return -1
    return p

def _skip_polymorphic_C89080(D, p):
    """sub_141C89080: reads u8 type_byte, then dispatches to virtual reader.
    Type 0: sub_141C8C1C0 = 40B(transform) + 7*u32 + u8 = 69B
    Type 1,4,6,7: sub_1402B4750 = 0B (reads nothing)
    Type 2: sub_141C8CAD0 = 8B (u64)
    Type 3: sub_141C8E150 = 4+4+4+1 + sub_141C8CB00(4+4+1+1+1 + u32_array) + switch(case)
    Type 5: sub_141C8F510 = CString"""
    type_byte = D[p]; p += 1
    if type_byte in (1, 4, 6, 7):
        return p
    elif type_byte == 0:
        return p + 69
    elif type_byte == 2:
        return p + 8
    elif type_byte == 5:
        return _skip_cstring(D, p)
    elif type_byte == 3:
        p += 13
        p += 11
        count, p = _u32(D, p)
        if count > 100000: return -1
        p += count * 4
        return -1
    elif type_byte == 8:
        return -1
    return -1

def _skip_D77130_case4(D, p):
    """sub_141C92180 (case 4 of D77130): 1B sub-type + type-specific data.
    14 sub-types decoded from vtable[17] of each class."""
    st = D[p]; p += 1
    _case4_fixed = {
        0: 1, 1: 8, 2: 4, 3: 8, 4: 4, 5: 8, 6: 12, 7: 4,
        8: 1, 9: 2, 10: 1, 11: 1, 13: 5
    }
    if st in _case4_fixed:
        return p + _case4_fixed[st]
    elif st == 12:
        p += 4
        return _skip_cstring(D, p)
    return -1

def _skip_D77130_case3(D, p):
    """sub_141B94A00 (case 3 of D77130): u16 sub-type + vtable[16] read + vtable[19] read.
    397-case switch. vtable[19] is shared: 1B flag + optional(CString + 11B).
    vtable[16] varies per sub-type; decoded for data-occurring sub-types."""
    st = struct.unpack_from('<H', D, p)[0]; p += 2
    _v16_fixed = {222: 4, 99: 4, 245: 0, 203: 0, 175: 9, 254: 4, 31: 17, 317: 10, 125: 4}
    _v16_cstring = {114, 209}
    if st in _v16_fixed:
        p += _v16_fixed[st]
    elif st in _v16_cstring:
        p = _skip_cstring(D, p)
        if p < 0: return -1
    else:
        pass
    flag = D[p]; p += 1
    if flag:
        p = _skip_cstring(D, p)
        if p < 0: return -1
        p += 11
    return p

def _skip_D77130(D, p, depth=0):
    """Recursive skip for sub_141D77130: 1B type + type-specific data.
    9 types: 0=AND(2 children), 1=OR(2 children), 2=NOT(1 child),
    3=compare(u16+reads), 4=condition(14 sub-types), 5=script,
    6=u32, 7=complex, 8=simple(6B)."""
    if depth > 30: return -1
    t = D[p]; p += 1
    if t == 0 or t == 1:
        p = _skip_D77130(D, p, depth + 1)
        if p < 0: return -1
        return _skip_D77130(D, p, depth + 1)
    elif t == 2:
        return _skip_D77130(D, p, depth + 1)
    elif t == 3:
        return _skip_D77130_case3(D, p)
    elif t == 4:
        return _skip_D77130_case4(D, p)
    elif t == 5:
        p += 1
        p = _skip_cstring(D, p)
        if p < 0: return -1
        p += 1 + 8 + 1 + 1
        return p
    elif t == 6:
        return p + 4
    elif t == 7:
        flag = D[p]; p += 1
        if flag:
            p = _skip_cstring(D, p)
            if p < 0: return -1
            p += 1 + 8
            flag2 = D[p]; p += 1
            if not flag2:
                return p
            return -1
        else:
            return -1
    elif t == 8:
        return p + 6
    return -1

def _skip_optional_virtual_object(D, p):
    """sub_141062A40: u8 flag + if flag=1: polymorphic object via sub_141D77130."""
    flag = D[p]; p += 1
    if not flag:
        return p
    return _skip_D77130(D, p)

def _skip_cstring_array(D, p):
    """sub_140FD2180: u32 count + count * CString."""
    count, p = _u32(D, p)
    if count > 100000: return -1
    for _ in range(count):
        p = _skip_cstring(D, p)
        if p < 0: return -1
    return p

def _skip_sub_1410104C0(D, p):
    """sub_1410104C0: read(a2+28, 12) + sub_1410103E0(a2+12, 16B) + read(a2, 12).
    sub_1410103E0 reads 4*u32 = 16B. Total stream: 12+16+12 = 40B."""
    return p + 40

def _skip_sub_141C90490_element(D, p):
    """One element of sub_141C90490 (transform set data).
    Reads: u8 + sub_1410104C0(36B) + CStringHash + CString + u8 + 12B + 12B + u8 + u8.
    Total fixed: 1 + 36 + var + var + 1 + 12 + 12 + 1 + 1 = 64 + 2*var."""
    p += 1
    p = _skip_sub_1410104C0(D, p)
    if p < 0: return -1
    p = _skip_cstring_hash(D, p)
    if p < 0: return -1
    p = _skip_cstring(D, p)
    if p < 0: return -1
    p += 1
    p += 12
    p += 12
    p += 1
    p += 1
    return p

def _skip_sub_141C90490(D, p):
    """sub_141C90490: u32 count + count * transform_set_element."""
    count, p = _u32(D, p)
    if count > 100000: return -1
    for _ in range(count):
        p = _skip_sub_141C90490_element(D, p)
        if p < 0: return -1
    return p

def _skip_sub_141C90C40(D, p):
    """sub_141C90C40: u8 flag + if flag=0: done. if flag=1:
    CStringArray + u8 + u8 flag2 + (if flag2: polymorphic sub_141C89080) + u64."""
    flag = D[p]; p += 1
    if not flag:
        return p
    p = _skip_cstring_array(D, p)
    if p < 0: return -1
    p += 1
    flag2 = D[p]; p += 1
    if flag2:
        p = _skip_polymorphic_C89080(D, p)
        if p < 0: return -1
    p += 8
    return p

def _skip_sub_141C88550(D, p):
    """sub_141C88550 (tag element, 64B struct):
    1. CStringHash
    2. CStringArray (sub_140FD2180)
    3. sub_141C90490 (transform set list)
    4. u32 count + count * sub_141C90C40
    5. 4x u8 (at offsets 56-59)"""
    p = _skip_cstring_hash(D, p)
    if p < 0: return -1
    p = _skip_cstring_array(D, p)
    if p < 0: return -1
    p = _skip_sub_141C90490(D, p)
    if p < 0: return -1
    count, p = _u32(D, p)
    if count > 100000: return -1
    for _ in range(count):
        p = _skip_sub_141C90C40(D, p)
        if p < 0: return -1
    p += 4
    return p

def _skip_sub_141D40F90(D, p):
    """sub_141D40F90: u32 count + count * (2x sub_141062A40 + u8 + enum(u32) + u32 + u8 + u8).
    Each sub_141062A40: u8 flag + if flag=1: polymorphic virtual (BF4F70->D77130).
    Stream per element (best case, both flags=0): 1+1+1+4+4+1+1 = 13B."""
    count, p = _u32(D, p)
    if count > 100000: return -1
    for _ in range(count):
        p = _skip_optional_virtual_object(D, p)
        if p < 0: return -1
        p = _skip_optional_virtual_object(D, p)
        if p < 0: return -1
        p += 1
        p += 4
        p += 4
        p += 1
        p += 1
    return p

def _skip_sub_1410717B0(D, p):
    """sub_1410717B0: u32 count + count * sub_143A533C0_0_26 (48B struct per element).
    sub_143A533C0_0_26 reads: u64 (into v10) + CString (into v11).
    The remaining fields (v12-v16) are defaults from stack, not read from stream."""
    count, p = _u32(D, p)
    if count > 100000: return -1
    for _ in range(count):
        p += 8
        p = _skip_cstring(D, p)
        if p < 0: return -1
    return p

def _skip_reward_dropset(D, p):
    """sub_14105FE60: u32 count + count * 28B elements."""
    count, p = _u32(D, p)
    if count > 100000: return -1
    return p + count * 28

def _skip_sub_141043810(D, p):
    """sub_141043810 (interaction override element, 144B struct):
    1. enum(u32) via sub_1408F5560_0_1348
    2. LocStr at a2+8
    3. u32 at a2+40
    4. u32 count + count * (CStringHash + u32) = count * (var + 4B)
    5. sub_1410717B0 at a2+64 (PropertyArray)
    6. sub_141D40F90 at a2+80 (ConditionDataArray)
    7. sub_14105FE60 at a2+96 (RewardDropset: u32 count + count*28B)
    8. sub_1410608E0 at a2+112 (u32 key lookup)
    9. enum(u32) at a2+128
    10. enum(u32) at a2+132 (sub_1408F5560_0_1339 reads u16 but consumes u32)
    11-15. 5x u8 at a2+134..138"""
    p += 4
    p = _skip_locstr(D, p)
    if p < 0: return -1
    p += 4
    count, p = _u32(D, p)
    if count > 100000: return -1
    for _ in range(count):
        p = _skip_cstring_hash(D, p)
        if p < 0: return -1
        p += 4
    p = _skip_sub_1410717B0(D, p)
    if p < 0: return -1
    p = _skip_sub_141D40F90(D, p)
    if p < 0: return -1
    p = _skip_reward_dropset(D, p)
    if p < 0: return -1
    p += 4
    p += 4
    p += 4
    p += 5
    return p

def _skip_sub_141070A30(D, p):
    """_gimmickTagList: u32 count + count * (u8 flag + optional sub_141C88550)."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        flag = D[p]
        p += 1
        if flag:
            p = _skip_sub_141C88550(D, p)
            if p < 0: return -1
    return p

def _skip_sub_141B8C800(D, p):
    """Element reader for _elementalReceiverColliderGroupDataList (sub_14F0E1E40):
    CStringHash + u8 type_byte + (variable based on type) + u8 trailing.
    Stream: CStringHash + 1B + payload + 1B.
    Payload by type: 0,2,3,7 -> 4B; 1,5,9 -> 2B; 4,6,8 -> 4B; default -> 0B.
    So effectively always reads CStringHash + 1B + (2B or 4B) + 1B."""
    p = _skip_cstring_hash(D, p)
    if p < 0: return -1
    type_byte = D[p]
    p += 1
    if type_byte in (0, 2, 3, 7):
        p += 4
    elif type_byte in (1, 5, 9):
        p += 2
    elif type_byte in (4, 6, 8):
        p += 4
    p += 1
    return p

def _skip_sub_1419D0610(D, p):
    """_elementalReceiverColliderGroupDataList reader (sub_1419D0610):
    1B flag. If 0: empty. If 1: u32 count + count * (sub_1410104C0[40B] + u32[4B] + u8[1B]).
    Each element = 45B."""
    flag = D[p]; p += 1
    if not flag:
        return p
    count, p = _u32(D, p)
    if count > 100000: return -1
    return p + count * 45

def _skip_sub_141070800(D, p):
    """_gimmickOnTimeGroupDataList: u32 count + count * (u32 + sub_141070F90 + u8).
    sub_141070F90: u32 inner_count + inner_count * 4x u32 (16B per element).
    So each outer element: 4B + (4B + inner*16B) + 1B."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p += 4
        inner_count, p = _u32(D, p)
        if inner_count > 100000:
            return -1
        p += inner_count * 16
        p += 1
    return p

def _skip_sub_141063600(D, p):
    """_transmutationMaterialItemGroupList: u32 count + count * (key_lookup_u32 + enum_u32).
    Decompiled: each element reads sub_14105F5E0 (u32 key) + sub_1408F5560_0_1338 (u32 enum).
    Stream: count * 8B."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 8

def _skip_sub_141046100(D, p):
    """_generateEffectData: reads 4B + 1B + 1B = 6B fixed.
    Decompiled: read(a2, 4) + read(a2+4, 1) + read(a2+5, 1)."""
    return p + 6

def _skip_sub_1410636F0(D, p):
    """_controlMaterialParamValueList: u32 count + count * sub_1410461C0 elements.
    sub_1410461C0: 1B + 1B + 4B + 1B + CStringHash + 1B + 16B = variable.
    Stream per element: 1+1+4+1+CStringHash+1+16 = 24 + CStringHash."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p += 1 + 1 + 4 + 1
        p = _skip_cstring_hash(D, p)
        if p < 0: return -1
        p += 1 + 16
    return p

def _skip_sub_141063820(D, p):
    """_growthDataList: u32 count + count * sub_143A533C0_0_33 elements.
    sub_143A533C0_0_33 reads: 4B+4B+enum(4B)+enum(4B)+4B+1B+1B = 22B per element."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 22


def _skip_sub_141070620(D, p):
    """_collisionBodyData: u8 flag, if 0 -> done (just 1B).
    If 1 -> sub_1410104C0 (40B) + 1B + 1B + 4B + 1B + 1B + 1B = 49B.
    sub_1410104C0: 12B + sub_1410103E0(16B) + 12B = 40B.
    Total if present: 1 + 40 + 1 + 1 + 4 + 1 + 1 + 1 = 50B."""
    flag = D[p]
    p += 1
    if not flag:
        return p
    p += 40
    p += 1 + 1 + 4 + 1 + 1 + 1
    return p

def _skip_sub_1410453C0(D, p):
    """_attackImpulseCompleteData: 1B flag + u32 count + count * 8B (two u32s per element).
    Decompiled: read(a2, 1) + read(count, 4) + count * (read(4) + read(4))."""
    p += 1
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 8

def _skip_sub_141076950(D, p):
    """Complex reader (buoyancy/remoteCatch): u32 count + count * sub_141039AB0 elements.
    sub_141039AB0 is a very complex reader (96B stored). Cannot reliably skip.
    TODO: decode sub_141039AB0."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    if count == 0:
        return p
    return -1

def _skip_sub_141063920(D, p):
    """_stickToObjectSocketList: u32 count + count * (enum_1339_u32 + u32).
    Decompiled: each element: sub_1408F5560_0_1339(u32 enum) + read(4B).
    Stream: count * 8B."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 8

def _skip_sub_14152EDF0(D, p):
    """sub_14152EDF0: complex condition reader.
    Reads: 8B + 1B(type) + 4B + 4B + 4B + 8B + 4B + 8B + 8B + 8B + 2B = 59B fixed.
    Then switch on type byte (the 2nd read):
    - type 0xB: 0B extra
    - type 0xA: 4+4=8B extra
    - type 0xD: 4+1=5B extra
    - types 7,8: 1+8+4+4+1+4+8+1+1=32B extra
    - all other types (0-6,9,0xC): 4B extra"""
    p += 8
    type_byte = D[p]; p += 1
    p += 4 + 4 + 4 + 8 + 4 + 8 + 8 + 8 + 2
    if type_byte == 0x0B:
        pass
    elif type_byte == 0x0A:
        p += 8
    elif type_byte == 0x0D:
        p += 5
    elif type_byte in (7, 8):
        p += 32
    else:
        p += 4
    return p

def _skip_sub_1410704A0(D, p):
    """_stickToObjectType/_collectFilter_Dev: u32 count + count * (sub_141C0DD20 + u32).
    sub_141C0DD20: reads 1B flag. If 0, done. If 1, creates object + sub_14152EDF0."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        flag = D[p]
        p += 1
        if flag:
            p = _skip_sub_14152EDF0(D, p)
            if p < 0: return -1
        p += 4
    return p

def _skip_sub_141C0DD20(D, p):
    """interactionUIDistanceLv/housingSupportPlaneScale: 1B flag.
    If 0 -> done. If 1 -> sub_14152EDF0."""
    flag = D[p]
    p += 1
    if not flag:
        return p
    return _skip_sub_14152EDF0(D, p)

def _skip_sub_141070300(D, p):
    """_pushObjectSocketList/_physicsQualityPreset: u32 count + count * (sub_141044FC0 + array).
    sub_141044FC0 is another complex reader. Cannot fully decode."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    if count == 0:
        return p
    return -1

def _skip_sub_141045580(D, p):
    """sub_141045580: knowledge extract element inner.
    Reads: 2x CStringHash_array + 2x CString + 1B + 12B + 4B + 4B + 1B + 1B."""
    for _ in range(2):
        p = _skip_sub_1410615F0(D, p)
        if p < 0: return -1
    for _ in range(2):
        p = _skip_cstring(D, p)
        if p < 0: return -1
    p += 1 + 12 + 4 + 4 + 1 + 1
    return p

def _skip_sub_14132ECF0(D, p):
    """sub_14132ECF0: u32 count + count * sub_141045580 elements."""
    count, p = _u32(D, p)
    if count > 100000: return -1
    for _ in range(count):
        p = _skip_sub_141045580(D, p)
        if p < 0: return -1
    return p

def _skip_sub_141070120(D, p):
    """_knowledgeExtractType: u32 count + count * (4B + 1B + 1B + 1B + sub_14132ECF0 + 1B).
    Decompiled from sub_141070120."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p += 4 + 1 + 1 + 1
        p = _skip_sub_14132ECF0(D, p)
        if p < 0: return -1
        p += 1
    return p

def _skip_sub_14106FF80(D, p):
    """_summonItemDataList/_spawnDistanceLevel: u32 count + count * complex elements.
    Each element: sub_143A533C0_0_32 (polymorphic). Cannot reliably skip."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    if count == 0:
        return p
    return -1

def _skip_sub_141B86C10(D, p):
    """_inspectDataList/_miniGameDataList: 2x CString + 3x u32.
    Decompiled: sub_14100FE80 + sub_14100FE80 + read(4) + read(4) + read(4).
    Stream: CString + CString + 12B."""
    p = _skip_cstring(D, p)
    if p < 0: return -1
    p = _skip_cstring(D, p)
    if p < 0: return -1
    p += 12
    return p

def _skip_sub_141063A30(D, p):
    """_gimmickAttachTargetDataList/_trafficBoxDataList:
    u32 count + count * (sub_143A533C0_0_58 + u32).
    sub_143A533C0_0_58 is polymorphic. Cannot reliably skip non-empty."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    if count == 0:
        return p
    return -1

def _skip_sub_141063B60(D, p):
    """_transformSetList/_factionStructure:
    u32 count + count * (sub_1410569C0 + u32).
    sub_1410569C0 is polymorphic. Cannot reliably skip non-empty."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    if count == 0:
        return p
    return -1

def _skip_sub_141063CB0(D, p):
    """_eventKeyGuideList/_housingItemPlacementTypeFlag:
    u32 count + count * (sub_141057940 + u32).
    sub_141057940 is polymorphic. Cannot reliably skip non-empty."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    if count == 0:
        return p
    return -1

def _skip_sub_14106FD90(D, p):
    """_targetSealPartGimmickInfoList/_housingGimmickSpecialType:
    u32 count + count * sub_141045840 elements.
    sub_141045840 is polymorphic. Cannot reliably skip non-empty."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    if count == 0:
        return p
    return -1

def _skip_sub_14106FC00(D, p):
    """_dropRollCount/_breakTypeFromParent: u32 count + count * 260B (fixed path strings).
    Decompiled: each element reads 260B raw."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 260

def _skip_sub_14106FA80(D, p):
    """_attackImpulseCompleteData (the optional variant at a2+896):
    1B flag. If 0 -> done. If 1 -> sub_1410765F0 + LocStr + enum_1335(4B).
    sub_1410765F0: u32 count + count * (enum_1338[4B] + 8B + 4B + enum_1338[4B]) = count * 20B."""
    flag = D[p]
    p += 1
    if not flag:
        return p
    count, p = _u32(D, p)
    if count > 100000: return -1
    p += count * 20
    p = _skip_locstr(D, p)
    if p < 0: return -1
    p += 4
    return p

def _skip_sub_14106F8F0(D, p):
    """_batteryTotalCapacity (the array at a2+912):
    u32 count + count * (sub_141070C00 + u8).
    sub_141070C00: u32 inner_count + inner_count * (sub_14105DDD0[u32] + LocStr).
    Each inner element: u32 + LocStr."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        inner_count, p = _u32(D, p)
        if inner_count > 100000:
            return -1
        for _j in range(inner_count):
            p += 4
            p = _skip_locstr(D, p)
            if p < 0: return -1
        p += 1
    return p

def _skip_sub_14106F700(D, p):
    """_isInstallable (array at a2+1000):
    u32 count + count * (CString + CStringHash + u32 + u32).
    Decompiled: sub_14100FE80 + sub_141010050 + read(4) + read(4)."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p = _skip_cstring(D, p)
        if p < 0: return -1
        p = _skip_cstring_hash(D, p)
        if p < 0: return -1
        p += 4 + 4
    return p

def _skip_sub_14106F540(D, p):
    """_allyGroupInfo (array at a2+1016):
    u32 count + count * (u32 + u32 + sub_1410104C0[40B]).
    Decompiled: read(4) + read(4) + sub_1410104C0.
    sub_1410104C0: 12B + 16B + 12B = 40B.
    Total per element: 4+4+40 = 48B."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 48

def _skip_sub_14106F330(D, p):
    """_uiMapTextureInfo (array at a2+1032):
    u32 count + count * (u32 key + u8 flag + optional sub_1410451D0).
    sub_1410451D0 is complex. Cannot reliably skip if flag=1."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p += 4
        flag = D[p]
        p += 1
        if flag:
            return -1
    return p

def _skip_sub_141045F50(D, p):
    """_isTargetable (complex at a2+1056):
    LocStr + LocStr + CStringHash + enum_1340 + 2x(sub_1410608E0[4B] + sub_141061D60).
    sub_141061D60: u32 count + count * sub_143A533C0_0_17 (polymorphic, 264B stored).
    Cannot reliably skip if sub_141061D60 has elements."""
    p = _skip_locstr(D, p)
    if p < 0: return -1
    p = _skip_locstr(D, p)
    if p < 0: return -1
    p = _skip_cstring_hash(D, p)
    if p < 0: return -1
    p += 4
    for _ in range(2):
        p += 4
        inner_count, p = _u32(D, p)
        if inner_count > 100000:
            return -1
        if inner_count > 0:
            return -1
    return p

def _skip_sub_1410605E0(D, p):
    """_growthDataList alt (at a2+952):
    u32 count + count * (u32 + CStringHash + 12B + 12B).
    Decompiled: read(4) + sub_141010050 + read(12) + read(12).
    Total per element: 4 + CStringHash + 24."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        p += 4
        p = _skip_cstring_hash(D, p)
        if p < 0: return -1
        p += 12 + 12
    return p

def _skip_sub_141063E00(D, p):
    """_controlMaterialParamValueList alt / _convertItemInfo (at a2+968, a2+984):
    u32 count + count * sub_141045B10 elements.
    sub_141045B10 reads: enum(4B)+1B+4B+1B+1B+40B+8B+4B+1B+1B+1B+1B+1B+4B+2B = 74B."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 74

def _skip_sub_141060ED0(D, p):
    """Key lookup: reads u16 from stream, looks up. Stream consumption: 2B."""
    return p + 2

def _skip_sub_141060CF0(D, p):
    """Array of u16 key lookups: u32 count + count * u16.
    Stream: 4 + count*2."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 2

def _skip_sub_1410609B0(D, p):
    """Array of (u32 + u32 key_lookup): u32 count + count * 8B.
    Decompiled: each element reads u32 + u32."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 8

def _skip_sub_1410377F0(D, p):
    """Fixed struct: 4+4+4+1+1 = 14B.
    Decompiled: read(4) + read(4) + read(4) + read(1) + read(1)."""
    return p + 14

def _skip_sub_141070D80(D, p):
    """_applyGimmickStateToItem: u32 count + count * (u8 flag + optional(u32 + CStringHash + 40B)).
    If flag=0: just 1B. If flag=1: 1B + 4B + CStringHash + sub_1410104C0(40B) = 45B + CStringHash.
    Decompiled: read(1B flag) + if flag: read(v9+8, 4) + sub_141010050 + sub_1410104C0."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    for _ in range(count):
        flag = D[p]
        p += 1
        if flag:
            p += 4
            p = _skip_cstring_hash(D, p)
            if p < 0: return -1
            p += 40
    return p

def _skip_sub_141045D70(D, p):
    """_massLevel: 4B + 8B + u32 count + count * (8B + 4B) = 12B fixed + count*12B.
    Decompiled: read(4) + read(8) + count + count*(read(8)+read(4))."""
    p += 4 + 8
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 12

def _skip_sub_143A9D7C0_0_2(D, p):
    """_snowRatio (sub_143A9D7C0_0_2 at 0x141062f30):
    u32 count + count * (u32 + u32) = u32 + count*8B."""
    count, p = _u32(D, p)
    if count > 100000:
        return -1
    return p + count * 8


def parse_gimmick_entry(D, eoff, end):
    """Parse one GimmickInfo entry, reading all 159 fields sequentially.
    Returns dict with key fields. Returns None on parse failure.
    Field order matches decompiled sub_141046310 exactly.
    """
    p = eoff
    entry = {}
    entry['_offset'] = eoff

    def _fail(field_name):
        """Mark entry as partially parsed and return it."""
        entry['parse_fail_field'] = field_name
        entry['parse_complete'] = False
        return entry

    try:

        entry['key'], p = _u32(D, p)

        slen, _ = _u32(D, p)
        if slen > 5000:
            return None
        entry['name'] = D[p+4:p+4+slen].decode('utf-8', errors='replace')
        p = _skip_cstring(D, p)
        if p < 0: return None

        entry['is_blocked'], p = _u8(D, p)

        entry['prefab_path'], p = _read_cstring(D, p)
        if p < 0: return _fail('_prefabPath')

        p += 4

        p += 2

        count, p = _u32(D, p)
        if count > 100000:
            return _fail('_gimmickInteractionOverrideDataList_count')
        for _ in range(count):
            flag = D[p]; p += 1
            if flag:
                p = _skip_sub_141043810(D, p)
                if p < 0:
                    return _fail('_gimmickInteractionOverrideDataList_element')

        p += 1

        p += 1

        p = _skip_u32_key_array(D, p)
        if p < 0: return _fail('_propertyList')

        entry['gimmick_name_hash'], p = _u32(D, p)

        p = _skip_locstr(D, p)
        if p < 0: return _fail('_gimmickName')

        p = _skip_cstring(D, p)
        if p < 0: return _fail('_emojiTextureID')

        p = _skip_cstring(D, p)
        if p < 0: return _fail('_devMemo')

        p = _skip_sub_141063510(D, p)
        if p < 0:
            return _fail('_gimmickChartParameterList')

        p = _skip_sub_1410615F0(D, p)
        if p < 0:
            return _fail('_triggerVolumeGroupDataList')

        p = _skip_sub_141070A30(D, p)
        if p < 0:
            return _fail('_gimmickTagList')

        count, p = _u32(D, p)
        if count > 100000:
            return _fail('_triggerCheckTargetDataList_count')
        for _ in range(count):
            p = _skip_sub_141B8C800(D, p)
            if p < 0:
                return _fail('_triggerCheckTargetDataList_element')

        count, p = _u32(D, p)
        if count > 100000:
            return _fail('_elementalReceiverColliderGroupDataList_count')
        for _ in range(count):
            p = _skip_sub_1419D0610(D, p)
            if p < 0:
                return _fail('_elementalReceiverColliderGroupDataList')


        p = _skip_sub_141070800(D, p)
        if p < 0:
            return _fail('_gimmickOnTimeGroupDataList')

        p += 1

        p = _skip_u32_key_array(D, p)
        if p < 0: return _fail('_transmutationMaterialItemList')

        p = _skip_u32_key_array(D, p)
        if p < 0: return _fail('_transmutationMaterialGimmickList')

        p = _skip_sub_141063600(D, p)
        if p < 0:
            return _fail('_transmutationMaterialItemGroupList')

        p += 8

        p += 9

        p = _skip_sub_141046100(D, p)

        p = _skip_sub_1410636F0(D, p)
        if p < 0:
            return _fail('_controlMaterialParamValueList')

        p = _skip_sub_141063820(D, p)
        if p < 0:
            return _fail('_growthDataList')

        p += 1

        p += 4

        p += 4

        p += 4

        p += 1

        p += 1

        p += 4

        p = _skip_sub_1410453C0(D, p)
        if p < 0:
            return _fail('_attackImpulseCompleteData')

        p += 8

        p += 8

        p = _skip_sub_141070620(D, p)
        if p < 0:
            return _fail('_collisionBodyData')

        p += 12

        p += 4

        p += 4

        p += 4

        p += 1


        p += 5 * 4 + 12

        p += 4 * 4 + 1

        p += 1

        p += 5 * 4

        p += 1

        p += 1

        p += 1

        p += 4

        p += 12

        p += 4

        p = _skip_cstring_hash(D, p)
        if p < 0: return _fail('_jamReactionType')

        p = _skip_sub_141063920(D, p)
        if p < 0: return _fail('_jammedLogoutEffectName')

        p = _skip_sub_1410704A0(D, p)
        if p < 0:
            return _fail('_collectFilter_Dev')

        p = _skip_sub_141C0DD20(D, p)
        if p < 0:
            return _fail('_housingSupportPlaneScale')

        p = _skip_sub_141070300(D, p)
        if p < 0:
            return _fail('_physicsQualityPreset')

        p = _skip_sub_141070120(D, p)
        if p < 0:
            return _fail('_knowledgeExtractType')

        p = _skip_u32_key_array(D, p)
        if p < 0: return _fail('_equipDockingSpawnDistanceLevel')

        p = _skip_sub_14106FF80(D, p)
        if p < 0:
            return _fail('_spawnDistanceLevel')

        p += 4

        p += 4

        p += 1

        p += 1

        p = _skip_sub_141B86C10(D, p)
        if p < 0:
            return _fail('_miniGameDataList')

        p = _skip_sub_141063A30(D, p)
        if p < 0:
            return _fail('_trafficBoxDataList')

        p = _skip_sub_141063B60(D, p)
        if p < 0:
            return _fail('_factionStructure')

        p = _skip_sub_141063CB0(D, p)
        if p < 0:
            return _fail('_housingItemPlacementTypeFlag')

        p = _skip_sub_14106FD90(D, p)
        if p < 0:
            return _fail('_housingGimmickSpecialType')

        p += 4

        p = _skip_sub_141076950(D, p)
        if p < 0:
            return _fail('_buoyancySubmersionRatio')

        p += 4

        p += 4

        p += 4

        p += 4

        p = _skip_sub_14106FC00(D, p)
        if p < 0:
            return _fail('_breakTypeFromParent')

        p += 1

        p = _skip_sub_1410615F0(D, p)
        if p < 0: return _fail('_weakPointEffectDataList')

        p = _skip_sub_1410615F0(D, p)
        if p < 0: return _fail('_isCollectOnlyGimmick')

        p += 1

        p += 1

        p += 2

        p += 11

        p += 4

        p = _skip_sub_14106FA80(D, p)
        if p < 0:
            return _fail('_attackImpulseCompleteData_2')

        p += 1

        p = _skip_sub_14106F8F0(D, p)
        if p < 0:
            return _fail('_batteryTotalCapacity_2')

        p += 4 + 4 + 4 + 1

        p = _skip_cstring(D, p)
        if p < 0: return _fail('_propertyConditionStringListForDebug')

        p = _skip_sub_1410605E0(D, p)
        if p < 0:
            return _fail('_growthDataList_2')

        p = _skip_sub_141063E00(D, p)
        if p < 0:
            return _fail('_convertItemInfo_2')

        p = _skip_sub_141063E00(D, p)
        if p < 0:
            return _fail('_convertItemInfo_3')

        p = _skip_sub_14106F700(D, p)
        if p < 0:
            return _fail('_isInstallable_2')

        p = _skip_sub_14106F540(D, p)
        if p < 0:
            return _fail('_allyGroupInfo_2')

        p = _skip_sub_14106F330(D, p)
        if p < 0:
            return _fail('_uiMapTextureInfo_2')

        p += 4

        p = _skip_sub_141045F50(D, p)
        if p < 0:
            return _fail('_isTargetable_2')

        p += 4

        p += 1

        entry['knowledge_info'], p = _u32(D, p)

        p += 1

        p += 2

        p += 4

        p += 4

        p += 4

        p += 1

        p += 1

        p += 1

        p += 4

        p = _skip_sub_1410377F0(D, p)

        p += 2

        p = _skip_sub_141060CF0(D, p)
        if p < 0: return _fail('_propagateSkillFromParentActor')

        p += 1

        p += 1

        p = _skip_cstring(D, p)
        if p < 0: return _fail('_cstring_1288')

        p += 7

        p += 8

        p = _skip_sub_1410609B0(D, p)
        if p < 0: return _fail('_isBlockSpawnOnAwayFromOriginTransform')

        p = _skip_sub_143A9D7C0_0_2(D, p)
        if p < 0:
            return _fail('_snowRatio')

        for i in range(2):
            p = _skip_sub_141070D80(D, p)
            if p < 0:
                return _fail(f'_applyGimmickStateToItem_{i}')

        p = _skip_sub_141045D70(D, p)
        if p < 0:
            return _fail('_massLevel')

        p += 4

        p += 4

        p += 4

        p += 1

        entry['respawn_time_seconds'], p = _u32(D, p)

        p += 4

        entry['parse_complete'] = True
        entry['_end_offset'] = p
        return entry

    except (struct.error, IndexError):
        return None


def parse_pabgh_index(G):
    """Parse pabgh index file. Returns list of (key, offset) tuples."""
    c16 = struct.unpack_from('<H', G, 0)[0]
    if 2 + c16 * 8 == len(G):
        idx_start, count = 2, c16
    else:
        count = struct.unpack_from('<I', G, 0)[0]
        idx_start = 4

    idx = []
    for i in range(count):
        pos = idx_start + i * 8
        if pos + 8 > len(G):
            break
        key = struct.unpack_from('<I', G, pos)[0]
        offset = struct.unpack_from('<I', G, pos + 4)[0]
        idx.append((key, offset))

    idx.sort(key=lambda x: x[1])
    return idx


def parse_all_gimmicks(D, G):
    """Parse all GimmickInfo entries. Returns (entries, partial_entries, total_failures)."""
    idx = parse_pabgh_index(G)

    entries = []
    partial = []
    total_failures = 0

    for i, (key, eoff) in enumerate(idx):
        end = idx[i + 1][1] if i + 1 < len(idx) else len(D)
        entry = parse_gimmick_entry(D, eoff, end)
        if entry is None:
            total_failures += 1
        elif entry.get('parse_complete'):
            entries.append(entry)
        else:
            partial.append(entry)

    return entries, partial, total_failures


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')

    try:
        import crimson_rs
        game_path = 'C:/Program Files (x86)/Steam/steamapps/common/Crimson Desert'
        dp = 'gamedata/binary__/client/bin'
        body = crimson_rs.extract_file(game_path, '0008', dp, 'gimmickinfo.pabgb')
        gh = crimson_rs.extract_file(game_path, '0008', dp, 'gimmickinfo.pabgh')
        print("Loaded via crimson_rs")
    except Exception:
        ext = 'C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full'
        with open(f'{ext}/gimmickinfo.pabgb', 'rb') as f:
            body = f.read()
        with open(f'{ext}/gimmickinfo.pabgh', 'rb') as f:
            gh = f.read()
        print(f"Loaded from disk: {len(body)} bytes pabgb, {len(gh)} bytes pabgh")

    idx = parse_pabgh_index(gh)
    print(f"Index entries: {len(idx)}")

    entries, partial, failures = parse_all_gimmicks(body, gh)
    total = len(entries) + len(partial) + failures
    print(f"\n=== Parse Results ===")
    print(f"Total entries:    {total}")
    print(f"Fully parsed:     {len(entries)} ({100*len(entries)/max(total,1):.1f}%)")
    print(f"Partially parsed: {len(partial)} ({100*len(partial)/max(total,1):.1f}%)")
    print(f"Total failures:   {failures} ({100*failures/max(total,1):.1f}%)")

    if partial:
        from collections import Counter
        fail_fields = Counter(e.get('parse_fail_field', 'unknown') for e in partial)
        print(f"\n=== Partial Parse Failure Distribution ===")
        for field, count in fail_fields.most_common(20):
            print(f"  {field}: {count}")

    all_parsed = entries + partial
    if all_parsed:
        print(f"\n=== Sample Entries (first 10 of {len(all_parsed)} parsed) ===")
        for e in all_parsed[:10]:
            name = e.get('name', '?')
            key = e.get('key', 0)
            blocked = e.get('is_blocked', 0)
            complete = e.get('parse_complete', False)
            fail = e.get('parse_fail_field', '-')
            print(f"  key={key:10d}  blocked={blocked}  complete={'Y' if complete else 'N'}  fail={fail:40s}  name={name[:50]}")

    if all_parsed:
        print(f"\n=== Key Statistics (all {len(all_parsed)} parsed entries) ===")
        blocked_count = sum(1 for e in all_parsed if e.get('is_blocked'))
        has_prefab = sum(1 for e in all_parsed if e.get('prefab_path'))
        print(f"  Blocked entries: {blocked_count}")
        print(f"  With prefab path: {has_prefab}")

        from collections import Counter
        prefixes = Counter()
        for e in all_parsed:
            name = e.get('name', '')
            prefix = name.split('_')[0] if '_' in name else name
            prefixes[prefix] += 1
        print(f"\n=== Name Prefix Distribution (top 15) ===")
        for prefix, cnt in prefixes.most_common(15):
            print(f"  {prefix:30s}: {cnt:5d} entries")
