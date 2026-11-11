"""Display-only models for a merged tray icon and its hover list."""
from dataclasses import dataclass
from device_visibility import device_options, visible_readings


@dataclass(frozen=True)
class TrayRow:
    key: str
    title: str
    level: int | None
    info: str = ''
    headset: bool = False


def taskbar_rows(readings, cfg):
    if not cfg.get('taskbar_show', True):
        return []
    visible = {r.key: r for r in visible_readings(readings, cfg, 'taskbar')}
    selected = cfg.get('taskbar_devices')
    selected = set(selected) if isinstance(selected, list) else None
    hidden = set(cfg.get('hidden_devices') or ())
    rows = []
    for key, name, _ in device_options(readings, cfg):
        if key in hidden or (selected is not None and key not in selected):
            continue
        # Automatic mode retains only the pinned device when it disconnects.
        if key not in visible and selected is None and key != cfg.get('taskbar_primary'):
            continue
        r = visible.get(key)
        if r is None:
            rows.append(TrayRow(key, name, None, '未连接'))
        else:
            rows.append(TrayRow(key, r.title, r.level if r.ok else None,
                                r.status if r.ok else r.error or '电量未知', r.is_headset))
    return rows


def primary_row(rows, cfg):
    return next((r for r in rows if r.key == cfg.get('taskbar_primary')), rows[0] if rows else None)


def popup_position(anchor, work, width, height, gap=6):
    """Clamp to the icon's monitor, including monitors with negative coordinates."""
    left, top, right, bottom = work
    centered_x = (anchor[0] + anchor[2] - width) // 2
    x = min(max(left, centered_x), max(left, right - width))
    y = anchor[1] - height - gap
    if y < top:
        y = anchor[3] + gap
    y = min(max(top, y), max(top, bottom - height))
    return x, y
