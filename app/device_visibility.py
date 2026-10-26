"""Global device edits plus independent display choices; raw discovery is retained."""
from copy import copy


def managed_readings(readings, cfg):
    hidden = set(cfg.get('hidden_devices') or ())
    names = cfg.get('custom_models') or {}
    result = []
    for reading in readings:
        if reading.key in hidden:
            continue
        name = names.get(reading.key)
        if name and name != reading.title:
            reading = copy(reading)
            reading.title = name
        result.append(reading)
    return result


def selected_keys(cfg, surface):
    value = cfg.get(surface + '_devices')
    return set(key for key in value if isinstance(key, str)) if isinstance(value, list) else None


def visible_readings(readings, cfg, surface):
    keys = selected_keys(cfg, surface)
    return [r for r in managed_readings(readings, cfg) if r.key and (keys is None or r.key in keys)]


def selection_caption(cfg, surface):
    keys = selected_keys(cfg, surface)
    return '全部设备' if keys is None else f'已选 {len(keys)} 台' if keys else '未选择设备'


def device_options(readings, cfg):
    """Keep saved selections editable when a device is temporarily disconnected."""
    names = dict(cfg.get('device_names') or {})
    names.update(cfg.get('custom_models') or {})
    online = {r.key: r for r in readings if r.key}
    saved = set(cfg.get('hidden_devices') or ())
    for surface in ('taskbar', 'widget'):
        saved.update(selected_keys(cfg, surface) or ())
    keys = list(online) + sorted(saved - online.keys())
    return [(key, online[key].title if key in online else names.get(key, key), key in online)
            for key in keys]
