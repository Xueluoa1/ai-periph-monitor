"""Smart Tx identity and evidence only: no unverified USB battery commands."""
import hashlib
import re

MODEL = 'JBL Smart Tx 发射器'
LIMITATION = '已识别 Smart Tx；USB 耳机/发射器电量协议待验证。蓝牙直连电量在蓝牙设备项中单独显示。'


def claims(iface):
    return (iface.vid, iface.pid) == (0x0ECB, 0x214B)


def matches(iface):
    return (claims(iface) and iface.usage == 1 and iface.feature_len == 0
            and (iface.usage_page, iface.input_len, iface.output_len)
            in ((0xFF13, 62, 62), (0xFF14, 512, 512)))


def candidate(iface):
    # One visible transmitter, not a fictitious headset per HID collection.
    return matches(iface) and iface.usage_page == 0xFF13


def battery_read(iface, result_type):
    return result_type(ok=False, source='JBL Smart Tx 识别（电量待适配）',
                       detail=LIMITATION if candidate(iface) else '不是已核实的 Smart Tx 主接口',
                       extra={'model_name': MODEL, 'battery_protocol_verified': False})


def bluetooth_evidence(devices):
    """Separate Windows readings; never associate by model name with a USB unit."""
    result = []
    for dev in devices:
        name = re.sub(r'[^A-Z0-9]', '', dev.name.upper())
        if not (name.startswith('JBL') and ('TOURONEM3' in name or 'SMARTTX' in name)):
            continue
        valid = dev.has_property and type(dev.battery) is int and 0 <= dev.battery <= 100
        result.append(dict(name=dev.name, device_token=hashlib.sha256(
            (dev.container_id or dev.instance_id).encode()).hexdigest()[:16],
            battery_percent=dev.battery if valid else None,
            connected=dev.connected, cached=dev.cached, source='Windows Bluetooth battery property',
            associated_with_usb=False, reason=dev.reason))
    return result
