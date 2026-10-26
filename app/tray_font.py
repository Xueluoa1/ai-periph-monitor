"""Antialiased regular-weight Windows numerals; no third-party runtime needed."""
import ctypes as c
from ctypes import wintypes as w
from functools import lru_cache
from pathlib import Path

gdi = c.WinDLL('gdi32', use_last_error=True)
user = c.WinDLL('user32', use_last_error=True)

class BitmapHeader(c.Structure):
    _fields_ = [('size', w.DWORD), ('width', w.LONG), ('height', w.LONG),
                ('planes', w.WORD), ('bits', w.WORD), ('compression', w.DWORD),
                ('image_size', w.DWORD), ('xppm', w.LONG), ('yppm', w.LONG),
                ('used', w.DWORD), ('important', w.DWORD)]

gdi.CreateCompatibleDC.argtypes = [w.HDC]
gdi.CreateCompatibleDC.restype = w.HDC
gdi.CreateDIBSection.argtypes = [w.HDC, c.POINTER(BitmapHeader), w.UINT,
                               c.POINTER(c.c_void_p), w.HANDLE, w.DWORD]
gdi.CreateDIBSection.restype = w.HBITMAP
gdi.SelectObject.argtypes = [w.HDC, w.HANDLE]
gdi.SelectObject.restype = w.HANDLE
gdi.DeleteObject.argtypes = [w.HANDLE]
gdi.DeleteDC.argtypes = [w.HDC]
gdi.CreateFontW.argtypes = [c.c_int]*5 + [w.DWORD]*8 + [w.LPCWSTR]
gdi.CreateFontW.restype = w.HFONT
gdi.SetTextColor.argtypes = [w.HDC, w.DWORD]
gdi.SetBkColor.argtypes = [w.HDC, w.DWORD]
user.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, c.c_int, c.POINTER(w.RECT), w.UINT]
gdi.AddFontResourceExW.argtypes = [w.LPCWSTR, w.DWORD, c.c_void_p]
gdi.AddFontResourceExW.restype = c.c_int
gdi.GetTextFaceW.argtypes = [w.HDC, c.c_int, w.LPWSTR]
gdi.GetTextFaceW.restype = c.c_int
FONT_FAMILY = 'ZCOOL QingKe HuangYou'
FONT_PATH = Path(__file__).resolve().parent / 'fonts' / 'ZCOOLQingKeHuangYou-Regular.ttf'


@lru_cache(maxsize=1)
def load_bundled_font():
    # Private to this process: no system font installation or registry changes.
    if not gdi.AddFontResourceExW(str(FONT_PATH), 0x10, None):
        raise OSError('无法加载内置站酷庆科黄油体')
    return FONT_FAMILY


@lru_cache(maxsize=768)
def digit_mask(text, size):
    """Fit real glyph ink within a square frame with a transparent inner margin."""
    zoom = 4
    face = load_bundled_font()
    side = size * zoom
    dc = gdi.CreateCompatibleDC(None)
    if not dc:
        raise c.WinError(c.get_last_error())
    bits = c.c_void_p()
    header = BitmapHeader(c.sizeof(BitmapHeader), side, -side, 1, 32)
    bitmap = gdi.CreateDIBSection(dc, c.byref(header), 0, c.byref(bits), None, 0)
    if not bitmap:
        gdi.DeleteDC(dc)
        raise c.WinError(c.get_last_error())
    previous_bitmap = gdi.SelectObject(dc, bitmap)
    try:
        # Keep a fully transparent gap on all four sides, including the
        # antialiased fringe. Scale the entire glyph; never clip its edges.
        inset = max(1, round(size/32))
        stroke = max(2, round(size*.08))
        gap = max(1, round(size/16))
        margin = (inset + stroke + gap) * zoom
        available = side - margin * 2
        # Measure actual ink, not font advance: 100 must fit the same square.
        for height in range(round(side * .9), zoom, -1):
            font = gdi.CreateFontW(-height, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 3, 0, face)
            if not font:
                raise c.WinError(c.get_last_error())
            previous_font = gdi.SelectObject(dc, font)
            try:
                actual_face = c.create_unicode_buffer(64)
                gdi.GetTextFaceW(dc, 64, actual_face)
                if actual_face.value not in (face, '站酷庆科黄油体'):
                    raise OSError(f'内置字体未生效：{actual_face.value}')
                c.memset(bits, 0, side * side * 4)
                gdi.SetTextColor(dc, 0xffffff)
                gdi.SetBkColor(dc, 0)
                rect = w.RECT(0, 0, side, side)
                user.DrawTextW(dc, text, -1, c.byref(rect), 0x1 | 0x4 | 0x20)
                raw = c.string_at(bits, side * side * 4)
                points = [(i % side, i // side) for i in range(side*side) if raw[i*4] > 0]
                if not points:
                    continue
                left = min(p[0] for p in points); right = max(p[0] for p in points)
                top = min(p[1] for p in points); bottom = max(p[1] for p in points)
                # Reserve one extra pixel when measuring to avoid clipped ink.
                if (left > 0 and right < side-1 and top > 0 and bottom < side-1
                        and right-left+1 <= available and bottom-top+1 <= available):
                    break
            finally:
                gdi.SelectObject(dc, previous_font)
                gdi.DeleteObject(font)
        ox = (side-(right-left+1))//2-left
        oy = (side-(bottom-top+1))//2-top
        mask = bytearray(size*size)
        for y in range(size):
            for x in range(size):
                total = 0
                for dy in range(zoom):
                    yy = y*zoom+dy-oy
                    for dx in range(zoom):
                        xx = x*zoom+dx-ox
                        if 0 <= xx < side and 0 <= yy < side:
                            total += raw[(yy*side+xx)*4]
                mask[y*size+x] = round(total/(zoom*zoom))
        return bytes(mask)
    finally:
        gdi.SelectObject(dc, previous_bitmap)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(dc)
