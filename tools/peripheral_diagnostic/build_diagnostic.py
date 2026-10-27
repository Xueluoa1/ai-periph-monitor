"""Build a portable collector without changing the battery application's install."""
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def main():
    source = Path(__file__).resolve().parent
    root = source.parents[1]
    stage = root / 'output' / 'peripheral-diagnostic-v1.5-build'
    stage.mkdir(parents=True, exist_ok=True)
    release = root / 'release'
    release.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--console',
           '--name', 'PeripheralDiagnostic', '--distpath', str(stage / 'dist'),
           '--workpath', str(stage / 'build'), '--specpath', str(stage),
           '--paths', str(source.parent / 'am_diagnostic'), '--paths', str(root/'app'), str(source / 'peripheral_diagnostic.py')]
    with (stage / 'build.log').open('w', encoding='utf-8') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    target = release / '多品牌外设诊断工具-v1.5.exe'
    shutil.copy2(stage / 'dist' / 'PeripheralDiagnostic.exe', target)
    # Stable user-facing entry; versioned builds remain for audit/reproduction.
    shutil.copy2(target, release / '多品牌外设诊断工具.exe')
    with zipfile.ZipFile(release / '多品牌外设诊断工具-v1.5-便携版.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(target, target.name)
        z.write(source / '使用说明与适配指南.md', '使用说明.md')
        z.write(root / 'docs' / '适配核对-用户型号清单.md', '适配核对-用户型号清单.md')
        z.write(root / 'docs' / '适配说明-无线电量扩展.md', '适配说明-无线电量扩展.md')
    print(target)


if __name__ == '__main__':
    main()
