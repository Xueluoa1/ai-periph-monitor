"""Taskbar style geometry; shared by the native icon and settings preview."""
from functools import lru_cache
import math

STYLES = {
    'classic': '原有方框',
    'bar': '底部电量条',
    'ring': '圆环进度',
    'battery': '电池轮廓',
    'chamfer': '切角铭牌',
    'segments': '分段电量条',
}


def normalize_style(style):
    return style if isinstance(style, str) and style in STYLES else 'classic'


def rounded(x, y, left, top, right, bottom, radius):
    dx = max(left+radius-x, 0, x-(right-radius))
    dy = max(top+radius-y, 0, y-(bottom-radius))
    return dx*dx+dy*dy <= radius*radius


def polygon(x, y, points):
    """Convex clockwise polygon, in screen coordinates."""
    return all((bx-ax)*(y-ay)-(by-ay)*(x-ax) >= 0
               for (ax, ay), (bx, by) in zip(points, points[1:]+points[:1]))


def geometry(style, x, y, ratio):
    """Return 0 for transparent, 1 for track, 2 for foreground."""
    if style == 'bar':
        if rounded(x, y, 5, 81, 91, 91, 4):
            return 2 if ratio > 0 and x < 5+max(4, 86*ratio) else 1
    elif style == 'segments':
        for i in range(5):
            if rounded(x, y, 5+i*18, 79, 18+i*18, 91, 2):
                return 2 if ratio > i/5 else 1
    elif style == 'ring':
        distance = math.hypot(x-48, y-48)
        if 40 <= distance <= 46:
            progress = ((math.atan2(y-48, x-48)+math.pi/2) % math.tau)/math.tau
            return 2 if progress < ratio else 1
    elif style == 'battery':
        if (rounded(x, y, 4, 22, 83, 76, 8) and
                not rounded(x, y, 10, 28, 77, 70, 3)):
            return 2
        if rounded(x, y, 86, 39, 94, 59, 2):
            return 2
    elif style == 'chamfer':
        outer = ((19, 12), (93, 12), (93, 69), (77, 85), (3, 85), (3, 28))
        inner = ((22, 18), (87, 18), (87, 66), (74, 79), (9, 79), (9, 31))
        if polygon(x, y, outer) and not polygon(x, y, inner):
            return 2
    return 0


@lru_cache(maxsize=512)
def render_style(text, size, style, light, transport, state):
    from tray_font import digit_mask
    from tray_pixels import number_color
    value = max(0, min(100, int(text))) if text.isdigit() else None
    text = '--' if value is None else str(value)
    color = number_color(value, transport, light)[:3]
    track = (204, 212, 220) if light else (72, 82, 94)
    ratio = 0 if value is None else value/100
    boxes = {'bar': (5, 7, 91, 72), 'segments': (5, 5, 91, 71),
             'ring': (20, 25, 76, 71), 'battery': (14, 32, 73, 66),
             'chamfer': (16, 26, 80, 70)}
    mask = digit_mask(text, size, tuple(v*size/96 for v in boxes[style]))
    pixels = bytearray(size*size*4)
    # Four subpixels per axis smooth curves and diagonal edges at 16–64 px.
    for y in range(size):
        for x in range(size):
            counts = [0, 0, 0]
            for sy in range(4):
                for sx in range(4):
                    xx, yy = (x+(sx+.5)/4)*96/size, (y+(sy+.5)/4)*96/size
                    part = geometry(style, xx, yy, ratio)
                    if state == 'charging' and xx >= 78 and yy >= 70:
                        # Corner badge sits outside the number's fitted bounds.
                        part = 2 if polygon(xx, yy, ((86, 70), (86, 82), (78, 82))) else 0
                        if polygon(xx, yy, ((86, 78), (94, 78), (83, 94))):
                            part = 2
                    counts[part] += 1
            a_shape = (counts[1]+counts[2])/16
            a_text = mask[y*size+x]/255
            alpha = a_text+a_shape*(1-a_text)
            if alpha:
                rgb = tuple(round((color[c]*a_text +
                    (track[c]*counts[1]+color[c]*counts[2])/16*(1-a_text))/alpha)
                    for c in range(3))
                offset = (y*size+x)*4
                pixels[offset:offset+4] = bytes((*rgb, round(alpha*255)))
    return bytes(pixels)
