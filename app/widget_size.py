"""Widget dimensions are physical window pixels, independent of content density."""
PRESETS = {
    '自动适应内容': None,
    '小 · 280 × 180': (280, 180),
    '中 · 340 × 240': (340, 240),
    '大 · 420 × 320': (420, 320),
    '加大 · 520 × 400': (520, 400),
}
CUSTOM = '自定义尺寸'


def parse_size(width, height):
    try:
        width, height = int(str(width).strip()), int(str(height).strip())
    except (ValueError, TypeError):
        raise ValueError('请输入整数像素值，例如 340 和 240。') from None
    if not 240 <= width <= 3840 or not 100 <= height <= 2160:
        raise ValueError('宽度需为 240–3840，高度需为 100–2160 像素。')
    return width, height


def fixed_size(cfg, screen_width, screen_height):
    if cfg.get('widget_size_mode') != 'fixed':
        return None
    try:
        width, height = parse_size(cfg.get('widget_width'), cfg.get('widget_height'))
    except ValueError:
        width, height = 340, 240
    return min(width, screen_width), min(height, max(100, screen_height - 60))
