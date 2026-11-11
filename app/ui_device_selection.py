"""A keyboard-accessible, independently saved device checklist for each surface."""
import tkinter as tk
from tkinter import ttk
import ui_theme as ui
from device_visibility import device_options, selected_keys
from tray_pixels import transport_for_key


def open_device_selection(parent, surface, readings, cfg, on_save):
    label = '任务栏' if surface == 'taskbar' else '桌面挂件'
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(f'{label}显示设备')
    dialog.configure(bg=ui.BG)
    dialog.transient(parent)
    ui.center_dialog(dialog, parent, 640 if surface == 'taskbar' else 560, 520 if surface == 'taskbar' else 480)
    dialog.minsize(round((560 if surface == 'taskbar' else 420) * ui.scale(parent)), round(400 * ui.scale(parent)))
    top = ttk.Frame(dialog, padding=(24, 20, 24, 12))
    top.pack(fill='x')
    heading = ttk.Frame(top)
    heading.pack(fill='x')
    merged = tk.BooleanVar(dialog, value=bool(cfg.get('taskbar_merge', False)))
    primary_key = cfg.get('taskbar_primary', '')
    primary_choices = []
    if surface == 'taskbar':
        merge_toggle = ttk.Frame(heading)
        merge_toggle.pack(side='right', padx=(12, 0))
        ttk.Label(merge_toggle, text='合并显示').pack(side='left', padx=(0, 8))
        ui.Switch(merge_toggle, merged, lambda: update_mode(), bg=ui.BG).pack(side='left')
    ttk.Label(heading, text=f'选择{label}设备', style='H1.TLabel').pack(side='left')
    ttk.Label(top, text='仅影响这里显示的设备，首页和电量监测保持完整。',
              style='Muted.TLabel', wraplength=440).pack(anchor='w', pady=(6, 14))
    selected = selected_keys(cfg, surface)
    automatic = tk.BooleanVar(dialog, value=selected is None)
    hidden = set(cfg.get('hidden_devices') or ())
    options = [item for item in device_options(readings, cfg) if item[0] not in hidden]
    variables, checks, primary_tags = {}, [], {}
    primary_box = ttk.Frame(top, padding=12, style='Card.TFrame')
    if surface == 'taskbar':
        ttk.Label(primary_box, text='任务栏常驻设备', style='CardTitle.TLabel').pack(anchor='w')
        primary_combo = ttk.Combobox(primary_box, state='readonly')
        primary_combo.pack(fill='x', pady=(8, 6))
        ttk.Label(primary_box, text='任务栏显示所选设备电量，悬停查看全部已选设备',
                  style='CardSub.TLabel', wraplength=480).pack(anchor='w')
        def choose_primary(_event=None):
            nonlocal primary_key
            index = primary_combo.current()
            primary_key = primary_choices[index][0] if 0 <= index < len(primary_choices) else ''
            update_mode()
        primary_combo.bind('<<ComboboxSelected>>', choose_primary)
    bottom = ttk.Frame(dialog, padding=(24, 12, 24, 20))
    bottom.pack(side='bottom', fill='x')
    summary = ttk.Label(bottom, style='Muted.TLabel')
    summary.pack(anchor='w', pady=(0, 12))
    actions = ttk.Frame(bottom)
    actions.pack(fill='x')
    scroll = ui.ScrollFrame(dialog)
    scroll.pack(fill='both', expand=True, padx=24)

    def update_mode():
        nonlocal primary_key, primary_choices
        for check in checks:
            check.configure(state='disabled' if automatic.get() else 'normal')
        count = sum(v.get() for v in variables.values())
        summary.configure(text='新连接的设备也会自动显示。' if automatic.get() else
                          f'已选 {count} 台设备，断连重连后保留选择。' if count else
                          '未选择设备：这里将不显示设备电量。')
        if surface == 'taskbar':
            primary_choices = [item for item in options if automatic.get() or variables[item[0]].get()]
            if primary_key not in {item[0] for item in primary_choices}:
                primary_key = primary_choices[0][0] if primary_choices else ''
            # Selection is keyed by hardware identity, even if two names match.
            from collections import Counter
            counts = Counter(title for _, title, _ in primary_choices)
            labels = [title + (f'（设备 {index + 1}）' if counts[title] > 1 else '')
                      + ('（未连接）' if not online else '')
                      for index, (_, title, online) in enumerate(primary_choices)]
            primary_combo.configure(values=labels, state='readonly' if labels else 'disabled')
            if primary_choices:
                primary_combo.current(next(i for i, item in enumerate(primary_choices) if item[0] == primary_key))
            else:
                primary_combo.set('请先选择需要显示的设备')
            if merged.get():
                primary_box.pack(fill='x', pady=(0, 14), before=automatic_check)
            else:
                primary_box.pack_forget()
            for key, tag in primary_tags.items():
                tag.configure(text='常驻' if merged.get() and key == primary_key else '')

    automatic_check = ttk.Checkbutton(top, text='自动显示全部设备（含以后连接的设备）',
                                     variable=automatic, command=update_mode)
    automatic_check.pack(anchor='w')
    if not options:
        ttk.Label(scroll.body, text='暂无可选择的设备。连接设备并刷新首页后再选择。',
                  style='Muted.TLabel', wraplength=430).pack(anchor='w', pady=20)
    for key, title, online in options:
        variables[key] = tk.BooleanVar(dialog, value=selected is None or key in selected)
        transport = '蓝牙' if transport_for_key(key) == 'bluetooth' else 'USB / 2.4G'
        row = ttk.Frame(scroll.body, padding=(0, 7))
        row.pack(fill='x')
        check = ttk.Checkbutton(row, variable=variables[key], command=update_mode)
        check.pack(side='left', padx=(0, 8))
        if surface == 'taskbar':
            from ui_connection import reading_color
            reading = next((r for r in readings if r.key == key), None)
            level = getattr(reading, 'level', None)
            value = ttk.Label(row, text=f'{level}%' if level is not None else '--',
                              font=('Segoe UI', 12), foreground=reading_color(level, key), anchor='e', width=5)
            value.pack(side='right', padx=(10, 0))
            tag = ttk.Label(row, text='', style='Group.TLabel')
            tag.pack(side='right', padx=(8, 0))
            primary_tags[key] = tag
        text = ttk.Frame(row)
        text.pack(side='left', fill='x', expand=True)
        name = ttk.Label(text, text=title.lstrip('🖱️⌨️🎧 '), wraplength=380, justify='left')
        name.pack(anchor='w')
        ttk.Label(text, text=f'{transport} · {"已连接" if online else "未连接 · 已保留选择"}',
                  style='Muted.TLabel').pack(anchor='w', pady=(3, 0))
        def toggle(_event=None, control=check):
            control.invoke()
        for target in (row, text, name):
            target.bind('<Button-1>', toggle)
        text.bind('<Configure>', lambda e, target=name: target.configure(wraplength=max(100, e.width)))
        checks.append(check)
        ttk.Separator(scroll.body).pack(fill='x')

    def close():
        dialog.grab_release()
        dialog.destroy()
        parent.focus_set()

    def save():
        value = None if automatic.get() else [key for key, v in variables.items() if v.get()]
        names = {key: title for key, title, _ in options}
        if surface == 'taskbar':
            on_save(value, names, {'taskbar_merge': merged.get(), 'taskbar_primary': primary_key})
        else:
            on_save(value, names)
        close()

    def select_all(value):
        automatic.set(False)
        for variable in variables.values():
            variable.set(value)
        update_mode()

    ttk.Button(actions, text='全选当前', command=lambda: select_all(True)).pack(side='left')
    ttk.Button(actions, text='清空', command=lambda: select_all(False)).pack(side='left', padx=6)
    ttk.Button(actions, text='保存', style='Accent.TButton', command=save).pack(side='right')
    ttk.Button(actions, text='取消', command=close).pack(side='right', padx=6)
    dialog.protocol('WM_DELETE_WINDOW', close)
    dialog.bind('<Escape>', lambda e: close())
    update_mode()
    dialog.deiconify()
    dialog.grab_set()
    dialog.focus_set()
    return dialog
