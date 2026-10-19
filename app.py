#!/usr/bin/env python3
"""
Reel – lokales Webinterface zum Herunterladen und Sortieren von
Instagram-Videos (Reels/Posts) mit Tags.

Alle Videos liegen in einem Ordner; Tags, Notiz und Herkunft stehen
in einer .json-Datei neben jedem Video.

Als Mac-App: Reel.app öffnen. Als Skript: python3 app.py
Oberfläche: http://localhost:5055 (öffnet sich automatisch)
"""

import datetime
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

DEFAULT_LIBRARY = Path.home() / "Sportuebungen"   # Standard-Ablage, in der App änderbar
PORT = 5055
URL = f"http://localhost:{PORT}"
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}

# Einstellungen und Log liegen unabhängig vom gewählten Ablage-Ordner
if platform.system() == "Darwin":
    APP_DIR = Path.home() / "Library" / "Application Support" / "Reel"
else:
    APP_DIR = Path.home() / ".reel"
APP_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = APP_DIR / "einstellungen.json"

# Als App ohne Konsole gibt es kein stdout/stderr – Ausgaben in eine Logdatei
if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
    _log = open(APP_DIR / "reel.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = _log


def already_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except OSError:
        return False


# Läuft die App schon? Dann nur die Seite öffnen und beenden.
if __name__ == "__main__" and already_running():
    webbrowser.open(URL)
    sys.exit(0)

from flask import Flask, abort, jsonify, request, send_file  # noqa: E402
import yt_dlp  # noqa: E402

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = shutil.which("ffmpeg")

app = Flask(__name__)
jobs = {}
jobs_lock = threading.Lock()
lib_lock = threading.Lock()


# ------------------------------------------------------------ Einstellungen
def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), "utf-8")


def lib() -> Path:
    """Aktueller Ablage-Ordner (aus den Einstellungen, sonst Standard)."""
    p = Path(load_settings().get("library") or DEFAULT_LIBRARY).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def choose_folder():
    """Öffnet den normalen Mac-Dialog «Ordner wählen». Gibt Pfad oder None zurück."""
    if platform.system() != "Darwin":
        return None
    script = ('POSIX path of (choose folder with prompt "Ordner für deine Videos wählen" '
              f'default location (POSIX file "{lib()}"))')
    # «activate» holt den Dialog nach vorne, ohne zusätzliche Berechtigung
    r = subprocess.run(["osascript", "-e", "activate", "-e", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None          # abgebrochen
    return r.stdout.strip().rstrip("/") or None


# ------------------------------------------------------------ Dateien & Tags
def resolve(name: str) -> Path:
    """Dateiname aus der Oberfläche -> Pfad, nur innerhalb der Ablage."""
    root = lib().resolve()
    p = (root / name).resolve()
    if p.parent != root:
        abort(400)
    return p


def sidecar(video: Path) -> Path:
    return video.with_suffix(".json")


def read_meta(video: Path) -> dict:
    try:
        return json.loads(sidecar(video).read_text("utf-8"))
    except Exception:
        return {}


def write_meta(video: Path, meta: dict):
    sidecar(video).write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")


def clean_tags(tags) -> list:
    """Leerzeichen bereinigen, Gross-/Kleinschreibung vereinheitlichen, Doppelte entfernen."""
    out, seen = [], set()
    for t in tags or []:
        t = " ".join(str(t).replace(",", " ").split())[:40]
        if not t:
            continue
        t = t[0].upper() + t[1:]
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def unique_dest(folder: Path, name: str) -> Path:
    dest, stem, suf, n = folder / name, Path(name).stem, Path(name).suffix, 1
    while dest.exists():
        dest = folder / f"{stem}_{n}{suf}"
        n += 1
    return dest


def migrate_folders(root: Path):
    """Frühere Kategorie-Ordner auflösen: Videos von Reel (mit .json daneben)
    wandern in die Ablage, der Ordnername wird zum ersten Tag."""
    for d in [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]:
        for f in list(d.iterdir()):
            if f.suffix.lower() not in VIDEO_EXT or not sidecar(f).exists():
                continue
            meta = read_meta(f)
            meta["tags"] = clean_tags(meta.get("tags") or [d.name])
            dest = unique_dest(root, f.name)
            shutil.move(str(f), dest)
            sidecar(f).unlink(missing_ok=True)
            write_meta(dest, meta)
        rest = [p for p in d.iterdir() if p.name != ".DS_Store"]
        if not rest:
            (d / ".DS_Store").unlink(missing_ok=True)
            d.rmdir()


def title_of(meta: dict, uploader: str) -> str:
    """Erste Zeile der Caption als Titel, sonst «Video von @account»."""
    for line in (meta.get("caption") or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:90] + ("…" if len(line) > 90 else "")
    return f"Video von @{uploader}" if uploader else "Video"


# ------------------------------------------------------------------- Download
def run_job(job_id: str, url: str, tags: list, note: str):
    def update(**kw):
        with jobs_lock:
            jobs[job_id].update(kw)

    target = lib()
    opts = {
        "outtmpl": str(target / "%(upload_date)s_%(uploader_id)s_%(id)s.%(ext)s"),
        # Instagram liefert meist fertige MP4 mit Ton ("b"); sonst zusammenführen
        "format": "b/bv*+ba" if FFMPEG else "b",
        "merge_output_format": "mp4",
        "restrictfilenames": True,
        "quiet": True,
        "noprogress": True,
        "noplaylist": False,
    }
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    browser = load_settings().get("cookies_browser")
    if browser:
        opts["cookiesfrombrowser"] = (browser,)

    update(status="läuft")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        entries = info.get("entries") or [info]
        saved = 0
        for e in entries:
            if not e:
                continue
            for d in e.get("requested_downloads") or []:
                fp = Path(d.get("filepath") or d.get("_filename") or "")
                if not fp.exists():
                    continue
                write_meta(fp, {
                    "url": url,
                    "uploader": e.get("uploader_id") or e.get("uploader") or info.get("uploader_id") or "",
                    "caption": e.get("description") or info.get("description") or "",
                    "duration": e.get("duration"),
                    "tags": tags,
                    "note": note,
                    "added": datetime.date.today().isoformat(),
                })
                saved += 1
        if saved == 0:
            raise RuntimeError("Kein Video im Beitrag gefunden (nur Bilder?).")
        label = ", ".join(tags) if tags else "ohne Tags"
        update(status="fertig", detail=f"{saved} Video{'s' if saved > 1 else ''} gespeichert – {label}")
    except Exception as ex:
        msg = str(ex).splitlines()[0][:220].replace("ERROR: ", "")
        if "login" in msg.lower() or "cookie" in msg.lower():
            msg += " – Tipp: unten bei «Login über Browser» deinen Browser wählen."
        update(status="fehler", detail=msg)


# ---------------------------------------------------------------------- API
@app.get("/api/library")
def api_library():
    root = lib()
    with lib_lock:
        migrate_folders(root)
    videos, counts = [], {}
    for f in root.iterdir():
        if f.suffix.lower() not in VIDEO_EXT or f.name.startswith("."):
            continue
        m = read_meta(f)
        uploader = m.get("uploader") or (f.stem.split("_")[1] if f.stem.count("_") >= 2 else "")
        tags = clean_tags(m.get("tags"))
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
        videos.append({
            "name": f.name,
            "title": title_of(m, uploader),
            "uploader": uploader,
            "url": m.get("url", ""),
            "caption": m.get("caption", ""),
            "note": m.get("note", ""),
            "added": m.get("added", ""),
            "duration": m.get("duration"),
            "tags": tags,
            "mtime": f.stat().st_mtime,
        })
    videos.sort(key=lambda v: v["mtime"], reverse=True)
    tags = sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))
    return jsonify(videos=videos, tags=tags)


@app.post("/api/download")
def api_download():
    data = request.get_json(force=True)
    urls = [u.strip() for u in data.get("urls", []) if u.strip().startswith("http")]
    tags, note = clean_tags(data.get("tags")), data.get("note", "")
    ids = []
    for url in urls:
        jid = uuid.uuid4().hex[:8]
        with jobs_lock:
            jobs[jid] = {"id": jid, "url": url, "status": "wartet", "detail": ""}
        threading.Thread(target=run_job, args=(jid, url, tags, note), daemon=True).start()
        ids.append(jid)
    return jsonify(ids=ids)


@app.get("/api/jobs")
def api_jobs():
    with jobs_lock:
        return jsonify(list(jobs.values())[-50:])


@app.post("/api/update")
def api_update():
    data = request.get_json(force=True)
    video = resolve(data["name"])
    if not video.is_file():
        abort(404)
    meta = read_meta(video)
    if "tags" in data:
        meta["tags"] = clean_tags(data["tags"])
    if "note" in data:
        meta["note"] = str(data["note"])
    write_meta(video, meta)
    return jsonify(ok=True, tags=meta.get("tags", []))


def trash(path: Path):
    """In den Mac-Papierkorb legen; geht das nicht, in .papierkorb in der Ablage."""
    for b in [Path.home() / ".Trash", lib() / ".papierkorb"]:
        try:
            b.mkdir(exist_ok=True)
            shutil.move(str(path), unique_dest(b, path.name))
            return
        except OSError:
            continue
    raise OSError("Konnte nicht löschen")


@app.post("/api/delete")
def api_delete():
    video = resolve(request.get_json(force=True)["name"])
    if video.suffix.lower() not in VIDEO_EXT or not video.is_file():
        abort(400)
    side = sidecar(video)
    trash(video)
    if side.exists():
        trash(side)
    return jsonify(ok=True)


@app.post("/api/reveal")
def api_reveal():
    video = resolve(request.get_json(force=True)["name"])
    if platform.system() == "Darwin":
        subprocess.run(["open", "-R", str(video)])
    elif platform.system() == "Windows":
        subprocess.run(["explorer", "/select,", str(video)])
    else:
        subprocess.run(["xdg-open", str(video.parent)])
    return jsonify(ok=True)


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        s = load_settings()
        data = request.get_json(force=True)
        if "cookies_browser" in data:
            b = data["cookies_browser"]
            s["cookies_browser"] = b if b in ("", "safari", "chrome", "firefox", "edge", "brave") else ""
        save_settings(s)
    s = load_settings()
    return jsonify(cookies_browser=s.get("cookies_browser", ""), library=str(lib()),
                   can_choose=platform.system() == "Darwin")


@app.post("/api/choose-folder")
def api_choose_folder():
    path = choose_folder()
    if path:
        s = load_settings()
        s["library"] = path
        save_settings(s)
    return jsonify(library=str(lib()), changed=bool(path))


@app.post("/api/quit")
def api_quit():
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify(ok=True)


@app.get("/media/<name>")
def media(name):
    return send_file(resolve(name), conditional=True)


@app.get("/")
def index():
    return PAGE


# --------------------------------------------------------------------- Seite
PAGE = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reel</title>
<style>
:root{
  --bg:#ffffff; --fg:#1f1f1e; --muted:#8a8a86; --line:#e4e4e1; --soft:#f5f5f3;
  --tag:#3d6b4f; --danger:#b3261e; --tile:#efefec;
  --sans:Arial,"Helvetica Neue",Helvetica,sans-serif;
  --mono:ui-monospace,Menlo,monospace;
  --s:13px; --m:15px; --l:20px;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#141414; --fg:#e8e8e5; --muted:#8c8c88; --line:#2a2a29; --soft:#1c1c1b;
  --tag:#8fc4a1; --danger:#f08a80; --tile:#222221; color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:var(--m)/1.45 var(--sans);padding:0 max(16px,env(safe-area-inset-left)) 64px}
button,input,textarea,select{font:inherit;color:inherit}
button{cursor:pointer}
:focus-visible{outline:2px solid var(--tag);outline-offset:2px}
a{color:inherit}
.wrap{max-width:1360px;margin:0 auto}

header{display:flex;flex-wrap:wrap;gap:12px 24px;align-items:center;padding:20px 0 16px;border-bottom:1px solid var(--line)}
.crumb{white-space:nowrap}
.crumb span{color:var(--muted)}
form.add{flex:1;display:flex;gap:8px;min-width:0;flex-wrap:wrap}
form.add .url{flex:2 1 260px;min-width:0}
.field{border:1px solid var(--line);background:var(--bg);border-radius:0;padding:7px 10px;font-size:var(--s)}
.field:focus{border-color:var(--fg);outline:none}
.btn{border:1px solid var(--fg);background:var(--fg);color:var(--bg);padding:7px 14px;font-size:var(--s);border-radius:0}
.btn.ghost{background:none;color:var(--fg);border-color:var(--line)}
.btn.ghost:hover{border-color:var(--fg)}
.btn.danger{background:none;color:var(--danger);border-color:var(--danger)}
.btn.danger.go{background:var(--danger);color:var(--bg)}

.tagin{flex:1 1 220px;display:flex;flex-wrap:wrap;gap:4px;align-items:center;border:1px solid var(--line);padding:4px 6px;min-height:34px;position:relative;min-width:0;background:var(--bg)}
.tagin:focus-within{border-color:var(--fg)}
.tagin input{border:0;outline:0;background:none;flex:1;min-width:80px;font-size:var(--s);padding:3px 2px}
.chip{display:inline-flex;align-items:center;gap:4px;font-size:var(--s);color:var(--tag);border:1px solid currentColor;padding:1px 4px 1px 7px;line-height:1.5}
.chip button{border:0;background:none;color:inherit;padding:0 2px;font-size:14px;line-height:1}
.sugg{position:absolute;left:-1px;right:-1px;top:100%;background:var(--bg);border:1px solid var(--fg);z-index:5;list-style:none;margin:0;padding:4px 0;font-size:var(--s)}
.sugg li{padding:4px 10px;cursor:pointer}
.sugg li[aria-selected="true"],.sugg li:hover{background:var(--soft)}
.sugg li em{font-style:normal;color:var(--muted)}

nav.tags{display:flex;flex-wrap:wrap;gap:4px 18px;align-items:baseline;padding:14px 0 18px;font-size:var(--s)}
nav.tags .lbl{color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-size:11px}
nav.tags button{border:0;background:none;padding:2px 0;color:var(--fg);border-bottom:1px solid transparent}
nav.tags button sup{color:var(--muted);font-size:10px;margin-left:2px}
nav.tags button.on{color:var(--tag);border-bottom-color:currentColor}
nav.tags button:hover{border-bottom-color:var(--line)}
nav.tags .search{margin-left:auto;width:200px;max-width:100%}
.hint{font-size:var(--s);color:var(--muted);padding-bottom:12px;min-height:1.4em}

.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:40px 28px}
.block{display:flex;flex-direction:column;gap:8px;border:0;background:none;padding:0;text-align:left;min-width:0}
.thumb{aspect-ratio:1;width:100%;background:var(--tile);position:relative;overflow:hidden;border:1px solid transparent;transition:border-color .15s}
.thumb video{width:100%;height:100%;object-fit:cover;display:block;pointer-events:none}
.block:hover .thumb,.block:focus-visible .thumb{border-color:var(--fg)}
.len{position:absolute;right:6px;bottom:6px;font:11px/1 var(--mono);background:var(--bg);color:var(--fg);padding:3px 5px}
.meta{font-size:var(--s);line-height:1.35;min-width:0;display:flex;flex-direction:column}
.meta .t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.meta .tg{color:var(--tag)}
.meta .a{color:var(--muted)}
.empty{color:var(--muted);font-size:var(--s);grid-column:1/-1;padding:40px 0}

.view{position:fixed;inset:0;background:var(--bg);z-index:20;display:grid;grid-template-columns:minmax(0,1fr) 360px}
.stage{display:grid;place-items:center;padding:48px 24px;min-width:0;position:relative;background:var(--soft)}
.stage video{max-height:82vh;max-width:100%;background:#000;display:block}
.close{position:absolute;top:16px;left:16px;border:0;background:none;font-size:var(--s);color:var(--muted)}
.close:hover{color:var(--fg)}
.side{border-left:1px solid var(--line);padding:56px 24px 24px;overflow:auto;display:flex;flex-direction:column;gap:22px;min-width:0}
.side h2{font-size:var(--l);font-weight:400;margin:0;text-wrap:balance}
.kv{display:grid;grid-template-columns:96px minmax(0,1fr);gap:6px 12px;font-size:var(--s);margin:0}
.kv dt{color:var(--muted)}
.kv dd{margin:0;overflow-wrap:anywhere}
.sec{display:flex;flex-direction:column;gap:6px}
.sec .lbl{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.cap{font-size:var(--s);color:var(--muted);margin:0;white-space:pre-line}
textarea.field{min-height:72px;resize:vertical;width:100%}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:auto;padding-top:12px;border-top:1px solid var(--line);font-size:var(--s);align-items:center}

footer{margin-top:64px;padding-top:14px;border-top:1px solid var(--line);font-size:var(--s);color:var(--muted);display:flex;flex-wrap:wrap;gap:8px 24px;align-items:center}
footer select{border:1px solid var(--line);background:var(--bg);padding:3px 6px;font-size:var(--s)}
footer b{color:var(--fg);font-weight:400}
.link{border:0;background:none;padding:0;text-decoration:underline;text-underline-offset:2px;color:var(--fg);font-size:var(--s)}

.toasts{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;z-index:30;max-width:calc(100% - 32px)}
.toast{background:var(--fg);color:var(--bg);font-size:var(--s);padding:9px 14px;max-width:420px;animation:in .2s ease-out;overflow-wrap:anywhere}
.toast.err{background:var(--danger)}
.toast.out{opacity:0;transition:opacity .4s}
@keyframes in{from{opacity:0;transform:translateY(6px)}}
.bye{max-width:40ch;margin:20vh auto;text-align:center;color:var(--muted)}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media (max-width:760px){
  .view{grid-template-columns:1fr;overflow:auto}
  .stage{padding:48px 16px 16px}
  .side{border-left:0;border-top:1px solid var(--line);overflow:visible;padding:20px 16px}
  nav.tags .search{margin-left:0;width:100%}
}
</style></head><body>
<div class="wrap">
  <header>
    <div class="crumb"><b>Reel</b> <span>/ <span id="crumbtag">Alle Videos</span></span></div>
    <form class="add" id="addform">
      <input class="field url" id="addurl" placeholder="Instagram-Link(s) einfügen" aria-label="Instagram-Link">
      <div class="tagin" id="addtags" data-placeholder="Tags, z. B. Kraft"></div>
      <button class="btn" type="submit">Hinzufügen</button>
    </form>
    <button class="btn ghost" type="button" id="quit">Beenden</button>
  </header>
  <nav class="tags" id="tagnav" aria-label="Nach Tags filtern"></nav>
  <div class="hint" id="hint"></div>
  <main class="grid" id="grid"></main>
  <footer>
    <label>Login über Browser
      <select id="cookies">
        <option value="">keiner</option><option value="safari">Safari</option><option value="chrome">Chrome</option>
        <option value="firefox">Firefox</option><option value="edge">Edge</option><option value="brave">Brave</option>
      </select></label>
    <span>Ablage: <b id="libpath">…</b> <button class="link" id="choose" type="button">Ordner wählen …</button></span>
  </footer>
</div>

<div class="view" id="view" hidden role="dialog" aria-modal="true" aria-labelledby="vtitle">
  <div class="stage">
    <button class="close" id="vclose" type="button">← Zurück zur Übersicht</button>
    <video id="vvideo" controls playsinline loop></video>
  </div>
  <aside class="side">
    <h2 id="vtitle"></h2>
    <dl class="kv" id="vkv"></dl>
    <div class="sec"><span class="lbl">Tags</span><div class="tagin" id="vtags" data-placeholder="Tag hinzufügen"></div></div>
    <div class="sec"><label class="lbl" for="vnote">Notiz</label><textarea class="field" id="vnote" placeholder="z. B. 3 × 12, langsam ablassen"></textarea></div>
    <div class="sec" id="vcapsec"><span class="lbl">Caption</span><p class="cap" id="vcap"></p></div>
    <div class="actions" id="vactions"></div>
  </aside>
</div>
<div class="toasts" id="toasts" aria-live="polite"></div>

<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const media=n=>'/media/'+encodeURIComponent(n);
const dur=s=>s?`${Math.floor(s/60)}:${String(Math.round(s%60)).padStart(2,'0')}`:'';
let videos=[],tagList=[],active=[],q='',openName=null,poll=null;const seen=new Set();

// ---------- Chip-Eingabe für Tags ----------
function tagInput(el,initial,onChange){
  let tags=[...initial],sel=-1;
  el.innerHTML=`<input aria-label="${el.dataset.placeholder}" placeholder="${el.dataset.placeholder}"><ul class="sugg" hidden role="listbox"></ul>`;
  const inp=el.querySelector('input'),ul=el.querySelector('ul');
  const changed=()=>onChange&&onChange([...tags]);
  const draw=()=>{
    el.querySelectorAll('.chip').forEach(c=>c.remove());
    tags.forEach((t,i)=>{const c=document.createElement('span');c.className='chip';
      c.innerHTML=`${esc(t)}<button type="button" aria-label="${esc(t)} entfernen">×</button>`;
      c.querySelector('button').onclick=()=>{tags.splice(i,1);draw();changed()};
      el.insertBefore(c,inp)});
    inp.placeholder=tags.length?'':el.dataset.placeholder;
  };
  const add=t=>{t=t.trim().replace(/,$/,'').trim();if(!t){inp.value='';return}
    const ex=tagList.map(x=>x[0]).find(x=>x.toLowerCase()===t.toLowerCase());t=ex||t[0].toUpperCase()+t.slice(1);
    if(!tags.some(x=>x.toLowerCase()===t.toLowerCase())){tags.push(t);draw();changed()}inp.value='';hide()};
  const hide=()=>{ul.hidden=true;sel=-1};
  const show=()=>{const v=inp.value.trim().toLowerCase();
    const opts=tagList.filter(([t])=>!tags.includes(t)&&(!v||t.toLowerCase().includes(v))).slice(0,6);
    const isNew=v&&!tagList.some(([t])=>t.toLowerCase()===v);
    ul.innerHTML=opts.map(([t,n])=>`<li role="option" data-t="${esc(t)}">${esc(t)} <em>${n}</em></li>`).join('')+(isNew?`<li role="option" data-t="${esc(inp.value.trim())}">Neuer Tag «${esc(inp.value.trim())}»</li>`:'');
    ul.hidden=!ul.children.length;sel=-1};
  inp.addEventListener('focus',show);inp.addEventListener('input',()=>{if(inp.value.endsWith(','))add(inp.value);else show()});
  inp.addEventListener('blur',()=>setTimeout(()=>{if(inp.value.trim())add(inp.value);hide()},150));
  inp.addEventListener('keydown',e=>{const li=[...ul.children];
    if(e.key==='ArrowDown'&&li.length){e.preventDefault();sel=(sel+1)%li.length}
    else if(e.key==='ArrowUp'&&li.length){e.preventDefault();sel=(sel-1+li.length)%li.length}
    else if(e.key==='Enter'){if(inp.value.trim()||sel>=0){e.preventDefault();add(sel>=0?li[sel].dataset.t:inp.value)}return}
    else if(e.key==='Backspace'&&!inp.value&&tags.length){tags.pop();draw();changed();return}
    else return;
    li.forEach((l,i)=>l.setAttribute('aria-selected',i===sel))});
  ul.addEventListener('mousedown',e=>{const l=e.target.closest('li');if(l){e.preventDefault();add(l.dataset.t)}});
  el.addEventListener('click',e=>{if(e.target===el)inp.focus()});
  draw();
  return {get:()=>{if(inp.value.trim())add(inp.value);return [...tags]},clear:()=>{tags=[];draw()}};
}

// ---------- Übersicht ----------
async function load(){
  const d=await (await fetch('/api/library')).json();
  videos=d.videos;tagList=d.tags;render();
}
function render(){
  active=active.filter(t=>tagList.some(x=>x[0]===t));
  $('#tagnav').innerHTML=`<span class="lbl">Tags</span>`+
    `<button type="button" class="${active.length?'':'on'}" data-t="">Alle<sup>${videos.length}</sup></button>`+
    tagList.map(([t,n])=>`<button type="button" class="${active.includes(t)?'on':''}" data-t="${esc(t)}" aria-pressed="${active.includes(t)}">${esc(t)}<sup>${n}</sup></button>`).join('')+
    `<input class="field search" id="search" placeholder="Suchen" aria-label="Suchen" value="${esc(q)}">`;
  $('#search').oninput=e=>{q=e.target.value.toLowerCase();drawGrid()};
  $('#crumbtag').textContent=active.length?active.join(' + '):'Alle Videos';
  drawGrid();
}
function drawGrid(){
  const list=videos.filter(v=>active.every(t=>v.tags.includes(t))&&(!q||[v.title,v.note,v.uploader,v.caption,v.tags.join(' ')].join(' ').toLowerCase().includes(q)));
  $('#hint').textContent=active.length>1?`Zeigt Videos mit allen gewählten Tags · ${list.length} Treffer`:'';
  $('#grid').innerHTML=list.map(v=>`<button class="block" type="button" data-n="${esc(v.name)}">
      <div class="thumb"><video src="${media(v.name)}#t=0.5" muted playsinline loop preload="metadata"></video>${v.duration?`<span class="len">${dur(v.duration)}</span>`:''}</div>
      <span class="meta"><span class="t">${esc(v.title)}</span><span class="t tg">${esc(v.tags.join(', ')||'ohne Tags')}</span><span class="t a">@${esc(v.uploader)}</span></span>
    </button>`).join('')||`<p class="empty">${videos.length?'Keine Videos mit diesen Tags.':'Noch keine Videos. Füg oben einen Instagram-Link ein.'}</p>`;
}
$('#tagnav').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;const t=b.dataset.t;
  if(!t)active=[];else active=active.includes(t)?active.filter(x=>x!==t):[...active,t];
  render();
});
$('#grid').addEventListener('click',e=>{const b=e.target.closest('.block');if(b)openView(b.dataset.n)});
// Vorschau beim Darüberfahren abspielen
$('#grid').addEventListener('mouseover',e=>{const v=e.target.closest('.block')?.querySelector('video');if(v)v.play().catch(()=>{})});
$('#grid').addEventListener('mouseout',e=>{const b=e.target.closest('.block');if(b&&!b.contains(e.relatedTarget))b.querySelector('video')?.pause()});

// ---------- Blockansicht ----------
function openView(name){
  const v=videos.find(x=>x.name===name);if(!v)return;openName=name;
  $('#vtitle').textContent=v.title;
  const vid=$('#vvideo');vid.src=media(v.name);vid.play().catch(()=>{});
  $('#vkv').innerHTML=`<dt>Account</dt><dd>@${esc(v.uploader)}</dd>`+
    (v.url?`<dt>Original</dt><dd><a href="${esc(v.url)}" target="_blank" rel="noopener">Auf Instagram öffnen ↗</a></dd>`:'')+
    (v.added?`<dt>Hinzugefügt</dt><dd>${esc(v.added)}</dd>`:'')+
    `<dt>Datei</dt><dd style="font-family:var(--mono);font-size:12px">${esc(v.name)}</dd>`;
  tagInput($('#vtags'),v.tags,async t=>{v.tags=t;await post('/api/update',{name:v.name,tags:t});refreshTags()});
  $('#vnote').value=v.note;
  let timer;$('#vnote').oninput=e=>{v.note=e.target.value;clearTimeout(timer);timer=setTimeout(()=>post('/api/update',{name:v.name,note:v.note}),400)};
  $('#vcap').textContent=v.caption;$('#vcapsec').hidden=!v.caption;
  drawActions();
  $('#view').hidden=false;document.body.style.overflow='hidden';$('#vclose').focus();
}
async function refreshTags(){const d=await (await fetch('/api/library')).json();tagList=d.tags}
function drawActions(confirming){
  $('#vactions').innerHTML=confirming
    ?`In den Papierkorb legen? <button class="btn danger go" id="vyes" type="button">Löschen</button><button class="btn ghost" id="vno" type="button">Abbrechen</button>`
    :`<button class="btn ghost" type="button" id="vfinder">Im Finder zeigen</button><button class="btn danger" type="button" id="vdel">Löschen</button>`;
  if(confirming){
    $('#vyes').onclick=async()=>{const name=openName;const r=await post('/api/delete',{name});
      if(r.ok){closeView();toast('Video liegt im Papierkorb')}else toast('Löschen hat nicht geklappt. Ist die Datei noch im Ordner?',true)};
    $('#vno').onclick=()=>drawActions();
  }else{
    $('#vfinder').onclick=()=>post('/api/reveal',{name:openName});
    $('#vdel').onclick=()=>drawActions(true);
  }
}
function closeView(){const vid=$('#vvideo');vid.pause();vid.removeAttribute('src');vid.load();
  $('#view').hidden=true;document.body.style.overflow='';openName=null;load()}
$('#vclose').onclick=closeView;
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#view').hidden&&document.activeElement.tagName!=='INPUT')closeView()});

// ---------- Hinzufügen ----------
const addTags=tagInput($('#addtags'),[]);
let loading=null;
$('#addform').addEventListener('submit',async e=>{
  e.preventDefault();
  const urls=$('#addurl').value.split(/\s+/).filter(u=>/^https?:\/\//.test(u)),tags=addTags.get();
  if(!urls.length){toast('Bitte einen Instagram-Link einfügen, z. B. instagram.com/reel/…',true);return}
  await post('/api/download',{urls,tags,note:''});
  $('#addurl').value='';addTags.clear();startPoll();
});
function startPoll(){if(poll)return;poll=setInterval(checkJobs,1200);checkJobs()}
async function checkJobs(){
  const js=await (await fetch('/api/jobs')).json();
  const busy=js.filter(j=>j.status==='läuft'||j.status==='wartet');
  if(busy.length){
    if(!loading)loading=toast('',false,true);
    loading.textContent=busy.length>1?`${busy.length} Videos werden geladen …`:'Video wird geladen …';
  }else if(loading){loading.remove();loading=null}
  let fresh=false;
  js.filter(j=>(j.status==='fertig'||j.status==='fehler')&&!seen.has(j.id)).forEach(j=>{
    seen.add(j.id);
    if(j.status==='fertig'){toast(j.detail);fresh=true}else toast(j.detail,true);
  });
  if(fresh)load();
  if(!busy.length){clearInterval(poll);poll=null}
}

// ---------- Meldungen, die von selbst verschwinden ----------
function toast(msg,err,sticky){
  const d=document.createElement('div');d.className='toast'+(err?' err':'');d.textContent=msg;$('#toasts').appendChild(d);
  if(!sticky)setTimeout(()=>{d.classList.add('out');setTimeout(()=>d.remove(),400)},err?10000:4000);
  return d;
}

// ---------- Einstellungen ----------
const short=p=>p.replace(/^\/Users\/[^/]+/,'~');
fetch('/api/settings').then(r=>r.json()).then(s=>{
  $('#cookies').value=s.cookies_browser||'';$('#libpath').textContent=short(s.library);
  if(!s.can_choose)$('#choose').hidden=true;
});
$('#cookies').onchange=e=>{post('/api/settings',{cookies_browser:e.target.value});toast('Gespeichert')};
$('#choose').onclick=async()=>{
  $('#choose').textContent='Dialog ist offen …';
  const r=await (await post('/api/choose-folder',{})).json();
  $('#choose').textContent='Ordner wählen …';
  $('#libpath').textContent=short(r.library);
  if(r.changed){active=[];load()}
};
$('#quit').onclick=async()=>{await post('/api/quit',{});document.body.innerHTML='<p class="bye">Reel ist beendet. Du kannst diesen Tab schliessen.</p>'};

// Alte Meldungen aus dieser Sitzung nicht erneut anzeigen
fetch('/api/jobs').then(r=>r.json()).then(js=>{js.forEach(j=>{if(j.status==='fertig'||j.status==='fehler')seen.add(j.id)});
  if(js.some(j=>j.status==='läuft'||j.status==='wartet'))startPoll()});
load();
</script></body></html>"""


if __name__ == "__main__":
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} Reel läuft: {URL} – Ordner: {lib()}, ffmpeg: {FFMPEG}")
    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
