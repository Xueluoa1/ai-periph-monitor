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
    ui.center_dialog(dialog, parent, 560, 480)
    dialog.minsize(round(420 * ui.scale(parent)), round(340 * ui.scale(parent)))
    top = ttk.Frame(dialog, padding=(24, 20, 24, 12))
    top.pack(fill='x')
    ttk.Label(top, text=f'选择{label}设备', style='H1.TLabel').pack(anchor='w')
    ttk.Label(top, text='仅影响这里显示的设备，首页和电量监测保持完整。',
              style='Muted.TLabel', wraplength=440).pack(anchor='w', pady=(6, 14))
    selected = selected_keys(cfg, surface)
    automatic = tk.BooleanVar(dialog, value=selected is None)
    hidden = set(cfg.get('hidden_devices') or ())
    options = [item for item in device_options(readings, cfg) if item[0] not in hidden]
    variables, checks = {}, []
    bottom = ttk.Frame(dialog, padding=(24, 12, 24, 20))
    bottom.pack(side='bottom', fill='x')
    summary = ttk.Label(bottom, style='Muted.TLabel')
    summary.pack(anchor='w', pady=(0, 12))
    actions = ttk.Frame(bottom)
    actions.pack(fill='x')
    scroll = ui.ScrollFrame(dialog)
    scroll.pack(fill='both', expand=True, padx=24)

    def update_mode():
        for check in checks:
            check.configure(state='disabled' if automatic.get() else 'normal')
        count = sum(v.get() for v in variables.values())
        summary.configure(text='新连接的设备也会自动显示。' if automatic.get() else
                          f'已选 {count} 台设备，断连重连后保留选择。' if count else
                          '未选择设备：这里将不显示设备电量。')

    ttk.Checkbutton(top, text='自动显示全部设备（含以后连接的设备）',
                    variable=automatic, command=update_mode).pack(anchor='w')
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
        on_save(value, {key: title for key, title, _ in options})
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
