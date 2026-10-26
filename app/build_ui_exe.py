"""Build a separate UI edition; never deploy, stop processes or alter autostart."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def build():
    # Fail before building if the environment cannot initialize Tcl/Tk. Otherwise
    # PyInstaller silently excludes tkinter and produces an unusable application.
    import tkinter
    probe = tkinter.Tcl()
    probe.eval('info library')
    source = Path(__file__).resolve().parent
    output = source.parent / 'release'
    output.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='battery_ui_build_'))
    for file in source.iterdir():
        if file.suffix in ('.py', '.ico', '.png'):
            shutil.copy2(file, stage / file.name)
    shutil.copytree(source/'brand_assets', stage/'brand_assets')
    shutil.copytree(source/'fonts', stage/'fonts')
    shutil.copy2(source/'THIRD_PARTY_NOTICES.txt', stage/'THIRD_PARTY_NOTICES.txt')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onefile', '--windowed', '--name', 'DeviceBatteryViewerUI',
               '--icon', 'app_icon.ico', '--add-data', 'app_icon.ico;.',
               '--add-data', 'app_icon.png;.', '--add-data', 'brand_assets;brand_assets',
               '--add-data', 'fonts;fonts',
               '--add-data', 'THIRD_PARTY_NOTICES.txt;.', '--distpath', str(stage/'dist'),
               '--workpath', str(stage/'build'), '--specpath', str(stage), 'launcher_main.py']
    log_path = output/'build.log'
    with log_path.open('w', encoding='utf8') as log:
        log.write(f'Staging: {stage}\n');log.flush()
        result = subprocess.run(command, cwd=stage, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Build failed. See {log_path}')
    log_text = log_path.read_text(encoding='utf8', errors='replace')
    if 'tkinter installation is broken' in log_text or "'pyi_rth__tkinter.py'" not in log_text:
        raise RuntimeError(f'Tcl/Tk was not bundled. See {log_path}')
    target = output/'无线外设电量监视器-v1.0.2.exe'
    shutil.copy2(stage/'dist'/'DeviceBatteryViewerUI.exe', target)
    print(target)
    return target


if __name__ == '__main__':
    build()
