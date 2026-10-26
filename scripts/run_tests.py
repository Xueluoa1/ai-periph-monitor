"""Run independent unittest suites on Windows, without invoking manual hardware scripts."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    suites = [('app', 'verify_*.py'), ('tools/peripheral_diagnostic', 'test_*.py'), ('tools/am_diagnostic', 'test_*.py')]
    codes = []
    for folder, pattern in suites:
        print(f'Running {folder}', flush=True)
        codes.append(subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', '.', '-p', pattern], cwd=ROOT / folder).returncode)
    return int(any(codes))

if __name__ == '__main__':
    raise SystemExit(main())
