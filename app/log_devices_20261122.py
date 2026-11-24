"""Battery-only protocols matched against the November 21 diagnostic interfaces.

Protocol facts and hardware limitations: 适配说明-20261122.md.
"""
import time

RAPOO_NAME = '雷柏 Rapoo VT3S V2 鼠标'
HECATE_NAME = '漫步者 HECATE G5 Max 无线耳机'
HECATE_G4_NAME = '漫步者 HECATE G4 MAX 底座版耳机'
SONIX_NAME = 'SONiX 2.4G 无线键盘（共用接收器）'


def matches(d, protocol):
    shape = (d.usage_page, d.usage, d.input_len, d.output_len)
    if protocol == 'rapoo_v2':
        return (d.vid,d.pid) == (0x24AE,0x1464) and shape == (0xFF00,2,19,19)
    if protocol in ('hecate_g5','hecate_g4'):
        # RID 02, D0 and ED have identical sizes/usages but separate collections.
        pid,col = (0xA00B,'mi_03&col03') if protocol=='hecate_g5' else (0xA22D,'mi_03&col02')
        return ((d.vid,d.pid) == (0x35BB,pid) and shape == (0xFF02,2,64,64)
                and col in d.path.lower())
    if protocol == 'sonix_keyboard':
        return ((d.vid,d.pid) == (0x05AC,0x024F) and shape == (0xFF60,0x61,33,33)
                and 'mi_03' in d.path.lower())
    return False


def decode(protocol, raw):
    r=bytes(raw or b'')
    if protocol == 'rapoo_v2':
        if (len(r)!=19 or r[0]!=7 or r[1]&15 not in (0,1,2) or r[7] not in (0,1,2)
                or r[8]>100 or int.from_bytes(r[13:15],'little') not in (0x1464,0x4664)):
            return None
        # Firmware sources disagree on flag 0 semantics. Preserve a valid level,
        # but do not invent charging/discharging until a local capture confirms it.
        return r[8], 'unknown', RAPOO_NAME
    if protocol in ('hecate_g5','hecate_g4'):
        if (len(r)!=64 or r[:3]!=b'\xd0\x35\xbb' or not 7<=r[3]<=60
                or r[4]!=0 or r[5]!=1 or r[6]>100 or r[7] not in (0,1)):
            return None
        return r[6], 'charging' if r[7] else 'discharging', HECATE_NAME if protocol=='hecate_g5' else HECATE_G4_NAME
    if protocol == 'sonix_keyboard':
        if len(r)!=33 or r[:4]!=b'\0\x20\x01\0' or not 1<=r[4]<=100:
            return None  # This protocol reports 0 when the wireless keyboard is off.
        return r[4], 'unknown', SONIX_NAME
    return None


def read(dev,d,protocol):
    if not matches(d,protocol):
        raise ValueError('接口不符合此型号的电量协议')
    if protocol in ('hecate_g5','hecate_g4'):
        import hid_win as hw
        from gravastar_k1 import report_ids
        if any(0xD0 not in report_ids(dev,t) for t in (hw.HidP_Input,hw.HidP_Output)):
            raise ValueError('HECATE 接口未声明 D0 报告')
        request=b'\xd0\x35\xbb\x01\0'.ljust(64,b'\0')
    elif protocol == 'sonix_keyboard':
        request=b'\0\x20\x01'+bytes(29)+b'\x21'
    else:
        request=None
    if request:
        dev.drain()
        dev.write_output(request,use_hidd=False)
    deadline=time.monotonic()+(10 if protocol=='rapoo_v2' else 1.2)
    while time.monotonic()<deadline:
        raw=dev.read_input(timeout_ms=100,length=d.input_len)
        parsed=decode(protocol,raw)
        if parsed:return parsed,bytes(raw)
    return None,b''
