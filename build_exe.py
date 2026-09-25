#!/usr/bin/env python3
"""Build a standalone Nitro GFX executable - no Python needed on the
machine that runs it - and check that the built program really starts.

    python build_exe.py             # single file (default)
    python build_exe.py --onedir    # a folder instead: starts faster
    python build_exe.py --clean     # wipe build/ and dist/ first

PyInstaller does NOT cross-compile: the result runs on the system it was
built on. For Windows, macOS and Linux, run this script once on each.
  Windows  dist/NitroGFX.exe
  macOS    dist/NitroGFX.app (with --onedir) or dist/NitroGFX
  Linux    dist/NitroGFX        (needs Tk on the build machine:
                                 e.g. `sudo apt install python3-tk`)

Requirements: `pip install pyinstaller pillow numpy` (+ `miniaudio` for the
background music - optional, the app runs silent without it).

Bundled: the `nitrokit` package, the GUI assets (logo, icon, music) and
GUIA.md (read by the in-app Guide). Left out on purpose: tests, survey
scripts, research notes - the shipped app never opens them.

After building, the executable is started once with NITROGFX_SELFTEST=1:
it builds the whole interface, checks the bundled files, renders text with
the editor's font and exits. A build that fails this check is reported as
failed - a packaged app that only breaks on double-click is the usual
PyInstaller surprise (a missing data file or native module).
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / 'NitroGFX.pyw'
ASSETS = ROOT / 'nitrokit' / 'gui' / 'assets'
NAME = 'NitroGFX'


def icon():
    if sys.platform == 'win32':
        return ASSETS / 'icon.ico'
    if sys.platform == 'darwin':
        return ASSETS / 'logo.png'           # PyInstaller converts it (uses Pillow)
    return None                               # Linux executables carry no icon


def output(onedir):
    dist = ROOT / 'dist'
    if sys.platform == 'darwin' and onedir:
        return dist / f'{NAME}.app', dist / f'{NAME}.app' / 'Contents' / 'MacOS' / NAME
    exe = NAME + ('.exe' if sys.platform == 'win32' else '')
    path = dist / NAME / exe if onedir else dist / exe
    return path, path


def size_mb(path):
    if path.is_file():
        return path.stat().st_size / 1048576
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file()) / 1048576


def selftest(binary):
    env = dict(os.environ, NITROGFX_SELFTEST='1')
    try:
        run = subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, 'timed out (the window never closed)'
    detail = (run.stdout + run.stderr).strip()[-600:]
    return run.returncode == 0, detail or f'exit code {run.returncode}'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--onedir', action='store_true', help='build a folder instead of a single file')
    ap.add_argument('--clean', action='store_true', help='wipe build/ and dist/ before building')
    ap.add_argument('--no-check', action='store_true', help='skip the post-build self-test')
    args = ap.parse_args()

    if importlib.util.find_spec('PyInstaller') is None:
        sys.exit('PyInstaller is not installed. Run:  pip install pyinstaller')
    if importlib.util.find_spec('tkinter') is None:
        sys.exit('This Python has no Tk (tkinter). On Linux: sudo apt install python3-tk')

    if args.clean:
        for d in ('build', 'dist'):
            shutil.rmtree(ROOT / d, ignore_errors=True)

    cmd = [sys.executable, '-m', 'PyInstaller', '--name', NAME, '--windowed', '--noconfirm',
           '--onedir' if args.onedir else '--onefile', '--paths', str(ROOT),
           # both are imported lazily; naming them makes a missing one fail the
           # BUILD instead of the first time someone clicks Music / the text tool
           # miniaudio wraps a cffi "API mode" extension (_miniaudio.pyd); that
           # extension links against _cffi_backend at import time, but nothing
           # in miniaudio.py imports it directly, so PyInstaller's modulegraph
           # never sees the dependency and silently drops it - the frozen app
           # then fails "ModuleNotFoundError: _cffi_backend" deep inside
           # miniaudio's own try/except, so it looks like "no audio device"
           # instead of a missing module. Must be named explicitly.
           '--hidden-import', 'miniaudio', '--hidden-import', '_cffi_backend',
           '--hidden-import', 'PIL._imagingft']
    ico = icon()
    if ico and ico.exists():
        cmd += ['--icon', str(ico)]
    for src, dest in ((ASSETS, 'nitrokit/gui/assets'), (ROOT / 'GUIA.md', '.')):
        cmd += ['--add-data', f'{src}{os.pathsep}{dest}']
    cmd.append(str(ENTRY))

    print('running:', ' '.join(f'"{c}"' if ' ' in c else c for c in cmd))
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        sys.exit('PyInstaller failed - see the log above.')
    shutil.rmtree(ROOT / 'build', ignore_errors=True)
    (ROOT / f'{NAME}.spec').unlink(missing_ok=True)

    target, binary = output(args.onedir)
    if not binary.exists():
        sys.exit(f'Build finished but {binary} was not found - check the log above.')
    print(f'\nBuilt: {target}  (~{size_mb(target):.0f} MB)')
    if args.no_check:
        return
    ok, detail = selftest(binary)
    print(('Self-test OK: ' if ok else 'SELF-TEST FAILED: ') + detail)
    if not ok:
        sys.exit(1)
    print('Copy it anywhere (for --onedir, the whole folder) and run it - no Python needed.')


if __name__ == '__main__':
    main()
