"""Pixel-aligned device battery icons, independent of Windows handles."""
DIGITS = {
    '0': ('111','101','101','101','101','101','111'),
    '1': ('010','110','010','010','010','010','111'),
    '2': ('111','001','001','111','100','100','111'),
    '3': ('111','001','001','111','001','001','111'),
    '4': ('101','101','101','111','001','001','001'),
    '5': ('111','100','100','111','001','001','111'),
    '6': ('111','100','100','111','101','101','111'),
    '7': ('111','001','001','010','010','010','010'),
    '8': ('111','101','101','111','101','101','111'),
    '9': ('111','101','101','111','001','001','111'),
    '-': ('000','000','000','111','000','000','000'),
}
WIDE_DIGITS = {
    '0': ('01110','10001','10001','10001','10001','10001','01110'),
    '1': ('00100','01100','00100','00100','00100','00100','01110'),
    '2': ('01110','10001','00001','00010','00100','01000','11111'),
    '3': ('11110','00001','00001','01110','00001','00001','11110'),
    '4': ('00010','00110','01010','10010','11111','00010','00010'),
    '5': ('11111','10000','10000','11110','00001','00001','11110'),
    '6': ('01110','10000','10000','11110','10001','10001','01110'),
    '7': ('11111','00001','00010','00100','00100','00100','00100'),
    '8': ('01110','10001','10001','01110','10001','10001','01110'),
    '9': ('01110','10001','10001','01111','00001','00001','01110'),
    '-': ('00000','00000','00000','01110','00000','00000','00000'),
}
TRANSPORT_LABELS = {'bluetooth': '蓝牙', 'usb': 'USB / 2.4G', 'unknown': '连接方式待识别'}


def transport_for_key(key):
    """Use discovery identity, never a user-editable product/brand name."""
    if (key or '').upper().startswith('BT:'):
        return 'bluetooth'
    return 'usb' if key else 'unknown'


def kind_for_title(title, headset=False):
    title = title.lower()
    if headset or any(k in title for k in ('耳机','headset','buds','darkrim')):
        return 'headset'
    if any(k in title for k in ('键盘','keyboard','cyberboard','azoth','falchion','scope')):
        return 'keyboard'
    return 'unknown' if title == '无线外设' else 'mouse'


def number_color(value, transport='unknown', light=False):
    if value is None:
        return (112,121,133,255) if light else (177,185,197,255)
    if value <= 10:
        return (205,47,61,255) if light else (255,119,130,255)
    colors = {'bluetooth': (30,104,200,255), 'usb': (0,130,91,255)} if light else {
        'bluetooth': (109,181,255,255), 'usb': (53,211,158,255)}
    return colors.get(transport, (74,85,101,255) if light else (213,220,231,255))


def rim_progress(x, y, center):
    """Square perimeter: bottom middle -> left -> top -> right -> bottom.

    Normalizing each concentric square gives a clean diagonal end across the
    rim thickness, matching the reference, and equal progress along each side.
    """
    dx, dy = x-center, y-center
    radius = max(abs(dx), abs(dy))
    if radius == 0:
        return 0
    dx, dy = dx/radius, dy/radius
    if dy >= abs(dx):
        return -dx/8 if dx <= 0 else 1-dx/8
    if -dx >= abs(dy):
        return .125+(1-dy)/8
    if -dy >= abs(dx):
        return .375+(dx+1)/8
    return .625+(dy+1)/8


def render(text, size, kind='mouse', state='online', light=False, transport='unknown'):
    """HuangYou digits and a transport-colored perimeter battery indicator."""
    from tray_font import digit_mask
    pixels = bytearray(size*size*4)
    value = max(0,min(100,int(text))) if text.isdigit() else None
    text = '--' if value is None else str(value)
    color = number_color(value,transport,light)
    border = number_color(100,transport,light)[:3]
    mask = digit_mask(text, size)
    inset = max(1, round(size/32))
    stroke = max(2, round(size*.08))
    far = size-1-inset
    pale = tuple(round(v + (255-v)*.74) for v in border)
    for y in range(size):
        for x in range(size):
            framed = (inset <= x <= far and inset <= y <= far and
                      (x < inset+stroke or x > far-stroke or y < inset+stroke or y > far-stroke))
            alpha = 255 if framed else mask[y*size+x]
            if alpha:
                rgb = color[:3]
                if framed:
                    coverage = 0 if value is None else sum(
                        rim_progress(x+(sx+.5)/4, y+(sy+.5)/4, size/2) < value/100
                        for sy in range(4) for sx in range(4))/16
                    rgb = tuple(round(a*coverage+b*(1-coverage)) for a,b in zip(border,pale))
                pixels[(y*size+x)*4:(y*size+x)*4+4] = bytes((*rgb,alpha))
    return bytes(pixels)
