"""Capture the real Windows Tk application; never connect to a head unit."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import tempfile
import time

root_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir / 'windows'))
ctypes.windll.user32.SetProcessDPIAware()
import tkinter as tk
from PIL import ImageGrab, ImageStat
from nbt_aad_pc.ui import App
from nbt_aad_pc import __version__
from nbt_aad_pc.releases import list_releases, package_identifier, select_assets

out = root_dir / 'docs/screenshots'
out.mkdir(parents=True, exist_ok=True)
get_ancestor = ctypes.windll.user32.GetAncestor
get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
get_ancestor.restype = wintypes.HWND

with tempfile.TemporaryDirectory(prefix='nbt-aad-screenshots-') as directory:
    root = tk.Tk()
    app = App(root, state=directory)
    # Keep the complete window inside the runner's actual desktop. PrintWindow
    # can return white/clipped regions when a window extends beyond that desktop.
    width = min(1120, root.winfo_screenwidth() - 24)
    height = min(860, root.winfo_screenheight() - 64)
    root.minsize(840, min(710, height))
    root.geometry(str(width) + 'x' + str(height) + '+0+0')
    root.update()
    # Obtain real release metadata and download verified packages. No fabricated
    # version list, status response, connection result or install log is shown.
    releases = list_releases()
    selected = [r for r in releases if '[UNSTABLE]' not in (r.get('name') or '')][:2]
    if len(selected) < 2:
        raise RuntimeError('Need two real releases for the offline-library example.')
    app.events.put(('releases', selected))
    packages = [app.get_package(False, (package_identifier(select_assets(release)), release))
                for release in reversed(selected)]
    root.after_cancel(app.poll_after)
    app.poll()
    root.update()
    root.lift()

    def capture(name):
        root.update_idletasks()
        root.update()
        time.sleep(0.7)
        root.update()
        hwnd = get_ancestor(root.winfo_id(), 2)
        screenshot = ImageGrab.grab(window=hwnd)
        if screenshot.width < 900 or screenshot.height < 650:
            raise RuntimeError('Window capture was clipped: ' + str(screenshot.size))
        if max(ImageStat.Stat(screenshot).stddev) < 10:
            raise RuntimeError('Window capture is blank.')
        screenshot.save(out / name)
        print('Captured real application window:', name, screenshot.size)

    capture('installer-main.png')
    app.preferred_release = packages[-1].path.name
    app.update_release_list()
    capture('installer-offline.png')
    (out / 'capture.json').write_text(json.dumps({
        'application': 'NBT-AAD PC', 'platform': 'Windows',
        'version': __version__,
        'method': 'Pillow ImageGrab single-window capture of the running Tk app',
        'head_unit_connected': False,
        'packages': [p.metadata['release'] for p in packages],
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    app.close()
