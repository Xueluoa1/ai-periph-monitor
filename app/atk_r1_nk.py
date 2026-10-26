"""VXE R1 NK receiver identity and PAW3395SE wire encoding.

Facts: ATK HUB catalog via OpenMouse; read fixtures from run 46d63078.
The shared NearLink receiver alone does not identify its paired mouse.
"""
MODEL = 'VXE 蜻蜓 R1 星闪版'
IDENTITY = (2, 67)
MIN_DPI, MAX_DPI = 200, 18000
# The sensor skips these wire values; a linear step50 decoder is incorrect.
RESERVED_CODES = frozenset((7,13,20,26,33,40,46,53,60,66,73,80,86,93,100,106,
                           113,120,126,133,140,146,153,160,166,173,180,186,193,200,
                           206,213,220,226,233))
DPI_CODES = tuple(code for code in range(1,236) if code not in RESERVED_CODES)


def receiver(d):
    return (d.vid,d.pid,d.usage_page,d.usage,d.input_len,d.output_len) == (
        0x373B,0x10C9,0xFF02,2,17,17)


def decode_identity(raw):
    if (len(raw) < 17 or raw[:3] != b'\x08\x10\x00'
            or sum(raw[:17]) & 255 != 0x55 or not 2 <= raw[5] <= 10):
        return None
    return tuple(raw[6:8])


def decode_axis(code, mode):
    if code not in DPI_CODES or mode not in (0,2):
        raise ValueError('PAW3395SE DPI 编码无效')
    base = (DPI_CODES.index(code)+1)*50
    value = base*2 if mode == 2 else base
    if (mode == 2 and base <= 5000) or not MIN_DPI <= value <= MAX_DPI:
        raise ValueError('PAW3395SE DPI 超出已核实范围')
    return value


def encode_axis(value):
    if type(value) is not int:
        raise ValueError('DPI 必须为整数')
    step = 50 if value <= 10000 else 100
    if type(value) is not int or not MIN_DPI <= value <= MAX_DPI or value % step:
        raise ValueError('该型号 DPI 为 200–10000（步进 50）或 10100–18000（步进 100）')
    base = value//2 if value > 10000 else value
    return DPI_CODES[base//50-1], 2 if value > 10000 else 0
