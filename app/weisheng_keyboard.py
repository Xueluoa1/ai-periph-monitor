"""Battery-only query for the logged 320F:5088 keyboard receiver.

Protocol facts: BatteryMonitor VgnHidProvider::queryWeisheng.
This shared USB identity does not establish a retail keyboard model.
"""
import time

MODEL = 'VGN V98（用户确认的共享接收器）'
QUERY = bytes.fromhex('0400001a06000000').ljust(64,b'\0')


def matches(d):
    return ((d.vid,d.pid)==(0x320F,0x5088)
            and (d.usage_page,d.usage,d.input_len,d.output_len)==(0xFF1C,0x92,64,64))


def decode(raw):
    r=bytes(raw or b'')
    if len(r)!=64 or r==QUERY or r[0]!=4 or r[3]!=0x1A:return None
    # Unlike G3M's protocol, the keyboard percentage follows the 7-byte body header.
    if r[8]>100 or r[9] not in (0,1):return None
    return r[8], 'charging' if r[9] else 'discharging', ''


def read(dev,d,emit=lambda *a,**k:None):
    if not matches(d):raise ValueError('键盘电量接口不匹配')
    dev.drain()
    emit('tx',brand='weisheng_keyboard',method='output_query',hex=QUERY.hex())
    dev.write_output(QUERY,use_hidd=False)
    until=time.monotonic()+1.2
    while time.monotonic()<until:
        raw=dev.read_input(timeout_ms=150,length=64)
        if raw:
            emit('rx',brand='weisheng_keyboard',method='input',hex=raw.hex())
            parsed=decode(raw)
            if parsed is not None:return parsed,raw
    return None,b''
