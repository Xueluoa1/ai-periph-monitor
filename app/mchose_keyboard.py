"""Shared Sinowealth wireless keyboard battery identities, never settings writes."""
import gravastar_k1 as transport
import time

RECEIVERS = {(0x3554, 0xFA09), (0x41E4, 0x2004)}
MODELS = {bytes.fromhex(signature): '迈从 MCHOSE '+name+' 键盘'
          for signature, name in (
    ('03000000021c', 'G87'), ('030000000077', 'K99'),
    ('0300000001df', 'K99 V2'), ('030000000132', 'G98'),
    ('0300000000d6', 'X75'), ('0a0000000003', 'G98 Pro'),
    ('0300000001c6', 'X75 V2'), ('030000000115', 'G75 Pro'),
    ('0a0000000018', 'G98 V2'), ('0a000000002b', 'UT98'),
    ('0a0000000001', 'Z75'), ('0a0000000009', 'Z75S'),
    ('0300000001c7', 'KX75'), ('030000000174', 'GX87'),
    ('0300000001ed', 'GX87 V2'), ('030000000135', 'K87'),
    ('030000000335', 'G75 V2'), ('0a0000000034', 'G87 V2'),
)}
GENERIC = '2.4G 无线键盘接收器（待读取配对型号）'
HOLA_SIGNATURE = bytes.fromhex('0300000001a701000000')
HOLA_NAME = '阿斯盾 AUSDOM Hola87 Pro 键盘'
HP_GT98_SIGNATURE = bytes.fromhex('03000000033c01000000')
HP_GT98_NAME = '惠普 HP GT98 键盘'


def read_signature(dev):
    """The logged Hola firmware returns 10 bytes; MCHOSE/K1 return 6."""
    dev.drain()
    dev.write_output(transport.frame(5),use_hidd=False)
    deadline=time.monotonic()+1.5
    while time.monotonic()<deadline:
        r=dev.read_input(timeout_ms=100)
        if not r or r[:2]!=b'\x13\x05':continue
        if (len(r)!=20 or r[2:4]!=b'\x01\0' or r[4] not in (6,10)
                or sum(r[:19])&255!=r[19]):
            raise ValueError('键盘型号回复长度、分包或校验错误')
        return bytes(r[5:5+r[4]])
    raise TimeoutError('键盘未返回型号信息')


def matches(d):
    return ((d.vid, d.pid) in RECEIVERS and
            (d.usage_page, d.usage, d.input_len, d.output_len) == (0xFF02, 2, 20, 20))


def identify(dev, d):
    if transport.query(dev, 7, 1) != b'\x01':
        raise OSError('无线键盘未在线，请唤醒后重试')
    signature = read_signature(dev)
    models = dict(MODELS)
    if (d.vid, d.pid) == transport.IDENTITY:
        models.update(transport.SIGNATURES)
        models[HOLA_SIGNATURE] = HOLA_NAME
        models[HP_GT98_SIGNATURE] = HP_GT98_NAME
    name = models.get(signature)
    if name is None:
        raise ValueError('未收录的键盘型号签名：'+signature.hex())
    return signature, name


def read(dev, d):
    if not matches(d):
        raise ValueError('共享无线键盘接口不匹配')
    for kind in (transport.hw.HidP_Input, transport.hw.HidP_Output):
        if 0x13 not in transport.report_ids(dev, kind):
            raise ValueError('键盘接口未声明报告 0x13')
    signature, name = identify(dev, d)
    raw = transport.query(dev, 0x4A, 2)
    if len(raw) != 2 or raw[0] > 100 or raw[1] not in (0, 1, 0x10, 0x11):
        raise ValueError('键盘未返回有效电量')
    if raw == b'\0\0':
        return None, raw  # Existing firmware uses an all-zero response when asleep.
    if identify(dev, d)[0] != signature:
        raise ValueError('查询期间键盘配对型号已变化')
    status = 'full' if raw[1]&1 else 'charging' if raw[1]&0x10 else 'discharging'
    return (raw[0], status, name), raw
