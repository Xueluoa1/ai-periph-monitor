"""Anonymous, read-only Windows battery evidence shared by the collector."""
from dataclasses import asdict
import hashlib


def bluetooth_records(devices):
    result = []
    for dev in devices:
        valid = dev.has_property and type(dev.battery) is int and 0 <= dev.battery <= 100
        result.append(dict(name=dev.name[:256], device_token=hashlib.sha256(
            (dev.container_id or dev.instance_id).encode()).hexdigest()[:16],
            battery_percent=dev.battery if valid else None, connected=dev.connected,
            cached=dev.cached, source='Windows Bluetooth battery property',
            headset=dev.headset, reason=dev.reason, associated_with_usb=False))
    return result


def collect(emit):
    try:
        import bt_battery
        devices = bt_battery.group_devices(bt_battery.list_bluetooth_devices(), connected_only=False)
        emit('bluetooth_battery_evidence', devices=bluetooth_records(devices))
    except Exception as exc:
        emit('bluetooth_battery_error', error=str(exc))
    try:
        import xinput_battery
        emit('xinput_battery_evidence', devices=[asdict(d) for d in xinput_battery.collect()],
             available=xinput_battery.api() is not None,
             note='Slots only; categories are not percentages; no USB/BT association')
    except Exception as exc:
        emit('xinput_battery_error', error=str(exc))


