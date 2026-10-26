"""Current-DPI control for the catalogued Pulsar report-8 receivers."""
from contextlib import contextmanager
from dataclasses import replace
import os
import time
from dpi_more import scyrox_query as query

OPTIONS = tuple(range(10, 10001, 10)) + tuple(range(10050, 30001, 50)) + tuple(range(30100, 32001, 100))


def matches(d):
    return d.vid == 0x3710 and d.pid in (0x5406, 0x3414) and (d.usage_page, d.usage, d.output_len) == (0xFF02, 2, 17) and d.input_len >= 17


def online(dev, d, enabled):
    for _ in range(5):
        r = query(dev, d, 3, data=bytes([int(enabled)]), length=0)
        if r[4] != 1:
            if enabled and r[0] != 1:raise ValueError('Pulsar 鼠标未在线，请移动鼠标后重试')
            return
        time.sleep(.02)
    raise TimeoutError('Pulsar 配置通道持续忙碌')


@contextmanager
def session(dev, d, expected=None):
    info = tuple(query(dev, d, 1, data=os.urandom(4)+bytes(4))[4:8])
    if not any(info[:2]) or info[2] not in range(6) or expected is not None and info != expected:
        raise ValueError('Pulsar 身份回复无效或设备已变化')
    online(dev, d, True)
    try:yield info
    finally:online(dev, d, False)


def read(dev, d, mouse):
    import mouse_settings as ms
    from dpi_adapters import Config, validate
    from peripheral_controls import State
    with session(dev, d) as info:
        profile = query(dev, d, 14)[0]
        header = query(dev, d, 8, length=6)[:6]
        index = header[4]
        if profile > 4 or index > 7 or sum(header[4:6]) & 255 != 0x55:
            raise ValueError('Pulsar 当前档位无效')
        row = query(dev, d, 8, 12+index*4, length=4)[:4]
        pair = ms.atk_decode(row, 'ultra')
        if pair[0] != pair[1] or pair[0] not in OPTIONS:
            raise ValueError('Pulsar 返回了未经核实的 DPI 编码')
        if query(dev, d, 14)[0] != profile or query(dev, d, 8, length=6)[:6] != header:
            raise ValueError('读取期间 Pulsar 配置已变化')
    name = 'Pulsar X2 CrazyLight' if info[:2] == (0x57, 4) else mouse.name
    base = State(mouse, name, None, (), profile+1, (*mouse.identity, *info))
    return validate(Config(base, 'pulsar', (pair,), 0, 10, 32000, 10, header+row, index,
                           note=f'当前第 {index+1} 档；只修改当前 DPI，档位切换使用鼠标按键后重新读取。',
                           options=OPTIONS, can_activate=False))


def write(dev, d, s, stage, value, activate):
    import mouse_settings as ms
    row = ms.atk_encode(value, value, 'ultra')
    with session(dev, d, s.base.identity[2:]):
        if query(dev, d, 14)[0]+1 != s.base.profile or query(dev, d, 8, length=6)[:6] != s.raw[:6]:
            raise ValueError('Pulsar 当前档位或配置已经变化')
        query(dev, d, 7, 12+s.selector*4, row)
    return replace(s, raw=s.raw[:6]+row, stages=((value, value),))
