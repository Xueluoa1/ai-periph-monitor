"""Battery-only M3/M3S receiver queries; see 适配说明-20261120.md."""
import time

MODELS = {0xFF30: (0xFF12, '达摩鲨 Darmoshark M3 鼠标'),
          0xFF31: (0xFF18, '达摩鲨 Darmoshark M3S 鼠标')}


def matches(d):
    return (d.vid == 0x248A and d.pid in MODELS and
            (d.usage_page, d.usage, d.input_len, d.output_len, d.feature_len)
            == (0x008C, 1, 33, 33, 1001))


def query(dev, opcode, deadline):
    if opcode not in (3, 6):
        raise ValueError('仅允许配对信息和电量查询')
    dev.drain()
    dev.set_feature_report(bytes((0x51, opcode)).ljust(1001, b'\0'))
    while time.monotonic() < deadline:
        ack = dev.read_input(timeout_ms=20, length=33) or b''
        if ack[:3] == b'\x54\xe4\x02':
            return None
        raw = dev.get_feature_report(0x51, 1001)
        if len(raw) >= 12 and raw[:2] == bytes((0x51, opcode)):
            return bytes(raw)
    return None


def read(dev, d):
    if not matches(d):
        raise ValueError('达摩鲨 M3/M3S 接收器接口不匹配')
    mouse, name = MODELS[d.pid]
    # A different opcode replaces a previous battery reply in the feature buffer.
    # Never accept a battery reply until the bond echo has actually been seen.
    bond = query(dev, 3, time.monotonic()+0.5)
    if (not bond or int.from_bytes(bond[3:5], 'little') != 0x248A or
            int.from_bytes(bond[5:7], 'little') != mouse or not bond[7]):
        return None, bond or b''
    raw = query(dev, 6, time.monotonic()+0.5)
    if (not raw or raw[2] != 1 or int.from_bytes(raw[4:6], 'little') != 0x248A or
            int.from_bytes(raw[6:8], 'little') != mouse or raw[11] > 100):
        return None, raw or b''
    return (raw[11], 'unknown', name), raw
