"""Small cached, supersampled switch images without a new runtime dependency."""
from functools import lru_cache


@lru_cache(maxsize=64)
def render(factor, on, background, track):
    """Return an opaque PPM, composited against the widget's current background."""
    width, height = round(44 * factor), round(40 * factor)
    samples = 4
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            total = [0, 0, 0]
            for sy in range(samples):
                py = (y + (sy + .5) / samples) / factor
                for sx in range(samples):
                    px = (x + (sx + .5) / samples) / factor
                    center = 33 if on else 13
                    if (px - center) ** 2 + (py - 20) ** 2 <= 64:
                        color = (255, 255, 255)
                    elif (px - min(32, max(12, px))) ** 2 + (py - 20) ** 2 <= 121:
                        color = track
                    else:
                        color = background
                    for i in range(3):
                        total[i] += color[i]
            pixels.extend(round(c / (samples * samples)) for c in total)
    return f'P6\n{width} {height}\n255\n'.encode('ascii') + pixels
