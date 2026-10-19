# PyInstaller-Bauplan für Reel.app (wird vom GitHub-Workflow verwendet)
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = collect_submodules("yt_dlp")
datas = collect_data_files("imageio_ffmpeg")  # enthält das ffmpeg-Programm

a = Analysis(
    ["app.py"],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Reel",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Reel")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Reel.app",
        bundle_identifier="io.github.wwwlhlm.reel",
        info_plist={
            "CFBundleShortVersionString": "1.0",
            "LSUIElement": True,  # kein Dock-Symbol, die Oberfläche läuft im Browser
        },
    )
