"""AM Infinity .97 DPI via the captured Feature 0x14 / RACE channel.

Command layouts follow the official AM35 driver snapshot in output/am-analysis.
Only the .97 identities validated by the user's USB/receiver captures are enabled.
No bridge initialization, pairing, profile switching, RGB or firmware writes.
"""
from dataclasses import dataclass, replace
import time

import am_battery as am

GET_DPI = 0x30C5
GET_PROFILE = 0x30C9
SET_DPI = 0x30C4
SET_ACTIVE = 0x30C7
MINIMUM, MAXIMUM, STEP = 50, 30000, 50


@dataclass(frozen=True)
class Config:
    # Includes disabled slots, so writes can verify that they were preserved.
    stages: tuple
    count: int
    active: int
    profile: int
    colors: bytes = b''


def validate_channel(dev, iface):
    if not am.matches_interface(iface):
        raise ValueError('设备不是已适配的怒喵 AM Infinity .97 配置接口')
    if not 62 <= iface.feature_len <= 1024:
        raise ValueError('当前固件缺少 DPI 配置通道，请通过官方 AM Master 检查鼠标和接收器固件')
    if not dev.write_access:
        raise OSError('无法访问怒喵配置接口，请关闭 AM Master 和官方网页驱动后重试')
    if am.REPORT_ID not in am.feature_report_ids(dev):
        raise ValueError('当前固件未提供 Feature 0x14 DPI 通道，请通过官方 AM Master 检查固件')


def request(command, payload, target, length):
    sizes = {GET_DPI: 0, GET_PROFILE: 0, SET_DPI: 5, SET_ACTIVE: 1}
    if command not in sizes or len(payload) != sizes[command]:
        raise ValueError('不支持的怒喵 DPI 命令或长度')
    if target not in (0, 0x80) or not 62 <= length <= 1024:
        raise ValueError('怒喵 DPI 通道或目标无效')
    if command in (SET_DPI, SET_ACTIVE) and payload[0] >= 8:
        raise ValueError('怒喵 DPI 档位无效')
    if command == SET_DPI:
        for offset in (1, 3):
            value = int.from_bytes(payload[offset:offset+2], 'little')
            if not MINIMUM <= value <= MAXIMUM or value % STEP:
                raise ValueError('怒喵 DPI 数值或步进无效')
    frame = bytes((5, 0x5A)) + (len(payload)+2).to_bytes(2, 'little')
    frame += command.to_bytes(2, 'little') + payload
    return (bytes((am.REPORT_ID, len(frame), target)) + frame).ljust(length, b'\0')


class Response:
    def __init__(self, target, command):
        self.target, self.command = target, command
        self.pending = bytearray()
        self.previous = None

    def feed(self, raw):
        if not raw or raw[0] != am.REPORT_ID or raw == self.previous:
            return None
        self.previous = raw
        if len(raw) < 3:
            raise ValueError('怒喵 DPI 响应头不完整')
        size, target = raw[1:3]
        if not size or target != self.target:
            return None
        if size > 59 or size > len(raw)-3:
            raise ValueError('怒喵 DPI 响应分片长度无效')
        self.pending.extend(raw[3:3+size])
        while len(self.pending) >= 6:
            length = int.from_bytes(self.pending[2:4], 'little') + 4
            if self.pending[0] != 5 or not 6 <= length <= 1024:
                raise ValueError('怒喵 DPI 响应帧无效')
            if len(self.pending) < length:
                return None
            frame = bytes(self.pending[:length])
            del self.pending[:length]
            if frame[1] not in (0x5B, 0x5D) or int.from_bytes(frame[4:6], 'little') != self.command:
                continue
            payload = frame[6:]
            if not payload or payload[0] != 0:
                raise ValueError(f'怒喵拒绝 DPI 命令 {self.command:04X}（状态 {payload.hex() or "空"}）')
            return payload
        return None


def exchange(dev, iface, command, payload=b''):
    target = am.IDENTITIES[(iface.vid, iface.pid)]
    parser = Response(target, command)
    dev.set_feature_report(request(command, payload, target, iface.feature_len))
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        time.sleep(.05)
        reply = parser.feed(dev.get_feature_report(am.REPORT_ID, iface.feature_len))
        if reply is not None:
            return reply
    raise TimeoutError(f'怒喵 DPI 命令 {command:04X} 超时，请唤醒鼠标并关闭 AM Master 或官方网页驱动后重试')


def decode(payload, profile):
    if len(payload) < 3 or payload[0] != 0:
        raise ValueError('怒喵 DPI 响应不完整或状态无效')
    count, active = payload[1:3]
    if not 1 <= count <= 8 or active >= count:
        raise ValueError('怒喵 DPI 档位数或当前档位无效')
    slots = 8 if len(payload) >= 35 else count
    end = 3 + slots*4
    if len(payload) not in (end, end+slots*3):
        raise ValueError('怒喵 DPI 数组长度无效，已停止调整')
    stages = tuple((int.from_bytes(payload[3+i*2:5+i*2], 'little'),
                    int.from_bytes(payload[3+slots*2+i*2:5+slots*2+i*2], 'little'))
                   for i in range(slots))
    if any(not MINIMUM <= value <= MAXIMUM for row in stages[:count] for value in row):
        raise ValueError('怒喵返回的 DPI 超出型号范围，已停止调整')
    return Config(stages, count, active, profile, payload[end:])


def read(dev, iface):
    validate_channel(dev, iface)
    profile = exchange(dev, iface, GET_PROFILE)
    if len(profile) != 2:
        raise ValueError('怒喵配置层响应长度无效')
    config = decode(exchange(dev, iface, GET_DPI), profile[1])
    if exchange(dev, iface, GET_PROFILE) != profile:
        raise ValueError('怒喵配置层已变化，请重新读取')
    return config


def expected(config, stage, value, activate):
    if type(stage) is not int or not 0 <= stage < config.count:
        raise ValueError('怒喵 DPI 档位无效')
    stages = list(config.stages)
    if value is not None:
        if type(value) is not int or not MINIMUM <= value <= MAXIMUM or value % STEP:
            raise ValueError('怒喵 DPI 数值或步进无效')
        stages[stage] = (value, value)
    return replace(config, stages=tuple(stages), active=stage if activate else config.active)


def write(dev, iface, config, stage, value, activate):
    updated = expected(config, stage, value, activate)
    validate_channel(dev, iface)
    changed = updated.stages != config.stages
    if changed:
        encoded = value.to_bytes(2, 'little')
        # Omit optional RGB bytes: editing a number must not overwrite its color.
        exchange(dev, iface, SET_DPI, bytes((stage,)) + encoded + encoded)
    # The official AM35 driver reselects an edited current stage to apply it.
    if (activate and stage != config.active) or (changed and stage == config.active):
        exchange(dev, iface, SET_ACTIVE, bytes((stage,)))
