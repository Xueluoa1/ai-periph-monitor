"""Model-specific polling/lighting controls, with identity and readback guards.

Protocol references and hardware validation limits: 适配说明-v26.md / v27.md.
"""
from dataclasses import dataclass, replace
import time

import mouse_settings as ms
import am_controls as ac
import am_battery
import atk_r1_nk as r1nk
from device_io import device_session

RATES = (125, 250, 500, 1000)
ATK_CODES = {125: 8, 250: 4, 500: 2, 1000: 1}
LIGHT_MODES = {0: '常亮', 1: '呼吸', 2: '色彩循环', 4: '按键响应', 6: '电量指示', 0xF0: '关闭'}


@dataclass(frozen=True)
class Light:
    mode: int
    brightness: int
    rgb: tuple


@dataclass(frozen=True)
class Controls:
    dpi: object
    rate: int
    light: Light | None
    other_settings: bytes
    am_state: ac.State | None = None


def supported(mouse):
    iface = mouse.interface
    if mouse.protocol=='xiaomi2':return iface is not None and ms.xm.matches(iface)
    if mouse.protocol == 'am35':
        return iface is not None and am_battery.matches_interface(iface) and (iface.vid,iface.pid) == mouse.identity
    return (mouse.protocol == 'rog' and mouse.identity in ((0x1A94, 3), (0x1A94, 0), (0x1A92, 0), (0x1A92, 3))) or (
        mouse.protocol == 'atk' and mouse.identity in ms.ATK_MODELS and iface is not None
        and ((iface.vid, iface.pid) == (0x373B, 0x101A) and ms.atk_model(mouse.identity,iface) is not None
             or mouse.identity == r1nk.IDENTITY and r1nk.receiver(iface)))


def _interface(mouse):
    iface = next((d for d in ms.hw.enumerate_hid() if d.path == mouse.path), None)
    if iface is None:
        raise OSError('设备已断开')
    if mouse.protocol == 'am35':
        valid = am_battery.matches_interface(iface) and (iface.vid,iface.pid) == mouse.identity
    elif mouse.protocol == 'rog':
        valid = (iface.vid == 0x0B05 and (
            (iface.pid == 0x1ACE and iface.usage_page == 0xFF01 and iface.output_len == 64 and mouse.identity[1] == 3)
            or (iface.pid == mouse.identity[0] and iface.output_len == 65 and mouse.identity[1] == 0)))
    else:
        valid = (((iface.vid, iface.pid, iface.usage_page, iface.usage, iface.output_len)
                 == (0x373B, 0x101A, 0xFF02, 2, 17) and ms.atk_model(mouse.identity,iface) is not None)
                 or mouse.identity == r1nk.IDENTITY and r1nk.receiver(iface))
    if not valid:
        raise ValueError('配置接口已变化，请重新读取设备')
    return iface


def decode_rog(settings, lighting):
    if len(settings) < 20 or settings[13] not in range(4):
        raise ValueError('无法确认回报率，已暂停调整')
    if len(lighting) < 10:
        raise ValueError('灯光数据不完整')
    light = Light(lighting[5], lighting[6], tuple(lighting[7:10]))
    validate_light(light)
    # Exclude only polling; all other configuration bytes must survive a write.
    return RATES[settings[13]], light, bytes(settings[5:13] + settings[14:20])


def validate_light(light):
    if (not isinstance(light, Light) or light.mode not in LIGHT_MODES
            or type(light.brightness) is not int or not 0 <= light.brightness <= 100
            or len(light.rgb) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in light.rgb)):
        raise ValueError('灯效、亮度或 RGB 数值无效')


def read(mouse):
    with device_session():
        return _read(mouse)


def _read(mouse):
    if not supported(mouse):
        raise ValueError('此型号或接收器的回报率与灯光尚未适配')
    if mouse.protocol=='xiaomi2':
        dpi=ms.xm.read(mouse)
        return Controls(dpi,dpi.protocol_data.rate,None,b'')
    iface = _interface(mouse)
    dpi = ms._read(mouse)  # Revalidates paired model / CID+MID and current DPI.
    with ms.hw.HidDevice(mouse.path, write=True) as dev:
        if mouse.protocol == 'am35':
            state = ac.read(dev,iface)
            # Verify the profile and all DPI slots across the auxiliary reads.
            if ms.am_dpi.read(dev,iface) != dpi.protocol_data:
                raise ValueError('怒喵 DPI 或配置层已变化，请重新读取')
            return Controls(dpi,state.rate,None,b'',state)
        elif mouse.protocol == 'rog':
            rid = mouse.identity[1]
            settings = ms.rog_query(dev, iface, rid, (0x12, 4, 0))
            lighting = ms.rog_query(dev, iface, rid, (0x12, 3, 0))
            rate, light, other = decode_rog(settings, lighting)
        else:
            system = ms.atk_read(dev, iface, 0, 10)
            if len(system) != 10 or any((system[i]+system[i+1]) & 255 != 0x55 for i in range(0, 10, 2)):
                raise ValueError('ATK 配置校验失败')
            rates = {v: k for k, v in ATK_CODES.items()}
            if system[0] not in rates:
                raise ValueError('1K 接收器返回了未适配的回报率代码')
            rate, light, other = rates[system[0]], None, bytes(system[2:])
            if mouse.identity == r1nk.IDENTITY and ms.atk_profile(dev,iface)!=dpi.profile:
                raise ValueError('读取期间配置层已变化，请重新读取')
    return Controls(dpi, rate, light, other)


def _fingerprint(state):
    d = state.dpi
    return (d.stages, d.active, d.profile, d.colors, d.protocol_data,
            state.rate, state.light, state.other_settings,
            ac.fingerprint(state.am_state) if state.am_state else None)


def apply(snapshot, *, rate=None, light=None, am_light=None, am_dongle=None):
    if snapshot.dpi.mouse.protocol=='xiaomi2':
        if light is not None or am_light is not None or am_dongle is not None:raise ValueError('小米仅提供 DPI 档位指示色设置')
        dpi=ms.xm.apply(snapshot.dpi,rate=rate)
        return Controls(dpi,dpi.protocol_data.rate,None,b'')
    if snapshot.dpi.mouse.protocol == 'am35':
        if light is not None:raise ValueError('怒喵请使用专用灯光配置')
        return _apply_am(snapshot,rate,am_light,am_dongle)
    if am_light is not None or am_dongle is not None:
        raise ValueError('当前设备不是怒喵 .97')
    if rate is not None and (type(rate) is not int or rate not in RATES):
        raise ValueError('此接收器仅适配 125 / 250 / 500 / 1000 Hz')
    if light is not None:
        validate_light(light)
        if snapshot.light is None:
            raise ValueError('此型号灯光尚未适配')
    with device_session():
        current = _read(snapshot.dpi.mouse)
        if _fingerprint(current) != _fingerprint(snapshot):
            raise ValueError('设备配置已变化，请重新读取后再应用')
        expected = replace(current, rate=current.rate if rate is None else rate,
                           light=current.light if light is None else light)
        if _fingerprint(expected) == _fingerprint(current):
            return current
        mouse = current.dpi.mouse
        iface = _interface(mouse)
        try:
            with ms.hw.HidDevice(mouse.path, write=True) as dev:
                if mouse.protocol == 'rog':
                    rid = mouse.identity[1]
                    if expected.rate != current.rate:
                        ms.rog_query(dev, iface, rid, (0x51, 0x31, 4, 0, RATES.index(expected.rate)))
                    if expected.light != current.light:
                        value = expected.light
                        # Harpe: read zone index 0, write scrollwheel zone ID 1.
                        ms.rog_query(dev, iface, rid, (0x51, 0x28, 1, 0, value.mode,
                                     value.brightness, *value.rgb, 0, 0, 0))
                    ms.rog_query(dev, iface, rid, (0x50, 3))
                else:
                    code = ATK_CODES[expected.rate]
                    dev.write_output(ms.atk_frame(7, (0, 0, 0, 2, code, (0x55-code) & 255)), use_hidd=False)
                    time.sleep(.05)
            confirmed = _read(mouse)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'设置未完整确认：{exc}；部分改动可能已生效，请重新读取。未自动重试') from exc
        if _fingerprint(confirmed) != _fingerprint(expected):
            raise RuntimeError('读回结果与提交值或原有配置不一致；部分改动可能已生效，请重新读取')
        return confirmed


def _apply_am(snapshot,rate,light,dongle):
    if snapshot.am_state is None:raise ValueError('请先读取怒喵配置')
    ac.expected(snapshot.am_state,rate,light,dongle)  # Validate before HID access.
    with device_session():
        current=_read(snapshot.dpi.mouse)
        if _fingerprint(current) != _fingerprint(snapshot):
            raise ValueError('设备配置已变化，请重新读取后再应用')
        updated=ac.expected(current.am_state,rate,light,dongle)
        expected=replace(current,rate=updated.rate,am_state=updated)
        if _fingerprint(expected) == _fingerprint(current):return current
        iface=_interface(current.dpi.mouse)
        write_error=None
        try:
            with ms.hw.HidDevice(iface.path,write=True) as dev:
                ac.write(dev,iface,current.am_state,updated)
        except (OSError,ValueError) as exc:
            write_error=exc
        # Read back even after a write error, but never silently report success
        # or retry a possibly committed command.
        try:
            confirmed=_read(current.dpi.mouse)
        except (OSError,ValueError) as exc:
            raise RuntimeError(f'怒喵设置读回失败：{exc}；部分改动可能已生效，请重新读取') from exc
        if write_error is not None:
            raise RuntimeError(f'怒喵写入未完整确认：{write_error}；部分改动可能已生效，未自动重试，请重新读取') from write_error
        if _fingerprint(confirmed) != _fingerprint(expected):
            raise RuntimeError('怒喵读回结果与提交值或原有配置不一致；部分改动可能已生效，请重新读取')
        return confirmed
