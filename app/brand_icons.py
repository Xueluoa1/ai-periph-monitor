"""Offline brand marks from official sites; display matching only, never HID routing."""
import re
import tkinter as tk
import ui_theme as ui


# Specific sub-brands precede umbrella/store names (e.g. ATK VXE MAD R).
BRAND_PATTERNS = (
    ('vxe', r'(?<![a-z])vxe(?![a-z])'),
    ('vgn', r'(?<![a-z])vgn(?![a-z])'),
    ('atk', r'(?<![a-z])atk(?![a-z])'),
    ('rog', r'(?<![a-z])rog(?![a-z])|republic\s+of\s+gamers|玩家国度'),
    ('angrymiao', r'angry[\s_-]*miao|怒喵|cyberboard'),
    ('razer', r'(?<![a-z])razer(?![a-z])|雷蛇'),
    ('mchose', r'(?<![a-z])mchose(?![a-z])|迈从'),
    ('lamzu', r'(?<![a-z])lamzu(?![a-z])|兰族'),
    ('wlmouse', r'(?<![a-z])wl[\s_-]*mouse(?![a-z])|万灵'),
    ('logitech', r'(?<![a-z])(?:logitech|logi)(?![a-z])|罗技'),
    ('dareu', r'(?<![a-z])dareu(?![a-z])|达尔优'),
    ('rapoo', r'(?<![a-z])rapoo(?![a-z])|雷柏'),
    ('ajazz', r'(?<![a-z])a[\s-]*jazz(?![a-z])|黑爵'),
    ('aula', r'(?<![a-z])aula(?![a-z])|狼蛛'),
    ('darmoshark', r'darmoshark|达摩鲨'),
    ('incott', r'(?<![a-z])(?:incott|ironcat)(?![a-z])|因科特'),
    ('delux', r'(?<![a-z])delux(?![a-z])|多彩'),
    ('inphic', r'(?<![a-z])inphic(?![a-z])|英菲克'),
    ('thunderobot', r'thunderobot|雷神'),
    ('machenike', r'machenike|机械师'),
    ('flesports', r'fl[\s_-]*esports|flesports|腹灵'),
    ('durgod', r'(?<![a-z])durgod(?![a-z])|杜伽'),
    ('kzzi', r'(?<![a-z])kzzi(?![a-z])|珂芝'),
    ('rk', r'(?<![a-z])rk(?![a-z])|royal[\s_-]*kludge'),
    ('akko', r'(?<![a-z])akko(?![a-z])|艾酷'),
    ('lofree', r'(?<![a-z])lofree(?![a-z])|洛斐'),
    ('varmilo', r'(?<![a-z])varmilo(?![a-z])|阿米洛'),
    ('melgeek', r'(?<![a-z])melgeek(?![a-z])'),
    ('iqunix', r'(?<![a-z])iqunix(?![a-z])'),
    ('hecate', r'(?<![a-z])hecate(?![a-z])|漫步者电竞|edifier[\s_-]*gaming'),
    ('edifier', r'(?<![a-z])edifier(?![a-z])|漫步者'),
)


def match_brand(title):
    text = (title or '').casefold()
    # Shared OEM receivers cannot establish which brand is paired.
    if 'aula / ajazz' in text or 'akko / monsgeek' in text:
        return None
    for brand, pattern in BRAND_PATTERNS:
        if re.search(pattern, text):
            return brand
    return None


class BrandIcon(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=ui.PANEL)
        self.factor = ui.scale(parent)
        self.logo = tk.Label(self, bg=ui.PANEL, borderwidth=0)
        self.fallback = ui.DeviceGlyph(self, size=36)
        self.photo = None
        self.brand = None
        self.show('')

    def show(self, title, kind='mouse'):
        self.title, self.kind = title, kind
        brand = match_brand(title)
        if brand and brand == self.brand and self.photo is not None:
            return
        self.brand = brand
        self.logo.pack_forget()
        self.fallback.pack_forget()
        self.photo = None
        if brand:
            # Pre-rendered sizes avoid Pillow as a runtime dependency.
            size = min((40, 50, 60, 80, 100, 120), key=lambda n: abs(n-40*self.factor))
            try:
                variant = '-light' if ui.CURRENT_THEME == 'light' else ''
                path = ui.asset_path(f'brand_assets/{brand}{variant}-{size}.png')
                if not path.is_file():
                    path = ui.asset_path(f'brand_assets/{brand}-{size}.png')
                self.photo = tk.PhotoImage(master=self, file=str(path))
            except (OSError, tk.TclError):
                pass
        if self.photo is not None:
            self.logo.configure(image=self.photo)
            self.logo.pack()
        else:
            self.fallback.draw(kind)
            self.fallback.pack()

    def refresh_theme(self):
        self.brand = None
        self.show(self.title, self.kind)
