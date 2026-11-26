"""Run independent unittest suites on Windows, without invoking manual hardware scripts."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    suites = [('app', 'verify_*.py'), ('tools/peripheral_diagnostic', 'test_*.py'), ('tools/am_diagnostic', 'test_*.py')]
    codes = []
    with tempfile.TemporaryDirectory(prefix='peripheral-test-config-') as isolated:
        env = dict(os.environ, LOCALAPPDATA=isolated, APPDATA=isolated)
        for folder, pattern in suites:
            print(f'Running {folder}', flush=True)
            codes.append(subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', '.', '-p', pattern], cwd=ROOT / folder, env=env).returncode)
    return int(any(codes))

if __name__ == '__main__':
    raise SystemExit(main())
