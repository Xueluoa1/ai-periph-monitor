"""Read-only MCHOSE headset battery protocol; see 适配说明-20261120.md."""
import time

MODELS = {
    (0x291D, 0x385D): '迈从 MCHOSE 无线耳机（共享接收器）',
    (0x41E4, 0x3201): '迈从 MCHOSE V9 耳机',
    (0x41E4, 0x3202): '迈从 MCHOSE V9 Pro 耳机',
    (0x41E4, 0x3402): '迈从 MCHOSE S9 Pro 耳机',
    (0x41E4, 0x3502): '迈从 MCHOSE G9 Pro 耳机',
    (0x3837, 0x6045): '迈从 MCHOSE X9 耳机',
    (0x3837, 0x600D): '迈从 MCHOSE V9 Turbo 耳机',
    (0x3837, 0x600A): '迈从 MCHOSE V9 Turbo+ 耳机',
}


def matches(d):
    return ((d.vid, d.pid) in MODELS and (d.usage_page, d.usage) == (0xFF90, 1)
            and d.input_len == d.output_len == 64)


def model_name(d):
    if (d.vid, d.pid) == (0x291D, 0x385D):
        product = ' '.join((getattr(d, 'product', '') or '').upper().split())
        if product in ('MCHOSE V9', 'MCHOSE V9 PRO', 'MCHOSE S9 PRO', 'MCHOSE G9 PRO'):
            return '迈从 ' + product + ' 耳机'
    return MODELS[d.vid, d.pid]


def decode(raw):
    r = bytes(raw)
    if len(r) != 64 or r[:2] != b'\x55\x65' or r[2] > 100:
        return None
    # A request echo has status 0; sleep has status 0x1A. Neither is 0%.
    status = {2: 'discharging', 3: 'charging', 4: 'full'}.get(r[3])
    return (r[2], status) if status else None


def read(dev, d):
    if not matches(d):
        raise ValueError('迈从耳机接口不匹配')
    packet = b'\x55\x65\x01'.ljust(64, b'\0')
    dev.drain()
    dev.write_output(packet, use_hidd=False)
    deadline = time.monotonic()+1.2
    while time.monotonic() < deadline:
        raw = dev.read_input(timeout_ms=100, length=64)
        parsed = decode(raw or b'')
        if parsed:
            return (*parsed, model_name(d)), bytes(raw)
    return None, b''
