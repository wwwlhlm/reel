#!/usr/bin/env python3
"""
Reel – lokales Webinterface zum Herunterladen und Sortieren von
Instagram-Videos (Reels/Posts) in Kategorie-Ordnern.

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

LIBRARY = Path.home() / "Sportuebungen"   # Hauptordner der Ablage
PORT = 5055
URL = f"http://localhost:{PORT}"
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}
SETTINGS_FILE = LIBRARY / ".reel-einstellungen.json"
LIBRARY.mkdir(parents=True, exist_ok=True)

# Als App ohne Konsole gibt es kein stdout/stderr – Ausgaben in eine Logdatei
if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
    _log = open(LIBRARY / ".reel.log", "a", buffering=1, encoding="utf-8")
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


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text("utf-8"))
    except Exception:
        return {"cookies_browser": ""}


def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), "utf-8")


def safe_name(name: str) -> str:
    name = (name or "").strip().replace("/", "-").replace("\\", "-")
    return name.strip(". ") or "Unsortiert"


def resolve(rel: str) -> Path:
    p = (LIBRARY / rel).resolve()
    if LIBRARY.resolve() not in p.parents:
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


# ------------------------------------------------------------------- Download
def run_job(job_id: str, url: str, category: str, note: str):
    def update(**kw):
        with jobs_lock:
            jobs[job_id].update(kw)

    target = LIBRARY / safe_name(category)
    target.mkdir(parents=True, exist_ok=True)
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
                    "note": note,
                    "added": datetime.date.today().isoformat(),
                })
                saved += 1
        if saved == 0:
            raise RuntimeError("Kein Video im Beitrag gefunden (nur Bilder?).")
        update(status="fertig", detail=f"{saved} Video(s) in «{target.name}»")
    except Exception as ex:
        msg = str(ex).splitlines()[0][:220]
        if "login" in msg.lower() or "cookie" in msg.lower():
            msg += " – Tipp: unten bei «Login über Browser» deinen Browser wählen."
        update(status="fehler", detail=msg)


# ---------------------------------------------------------------------- API
@app.get("/api/library")
def api_library():
    LIBRARY.mkdir(parents=True, exist_ok=True)
    cats, videos = [], []
    for d in sorted(p for p in LIBRARY.iterdir() if p.is_dir() and not p.name.startswith(".")):
        cats.append(d.name)
        for f in d.iterdir():
            if f.suffix.lower() in VIDEO_EXT:
                m = read_meta(f)
                videos.append({
                    "path": f"{d.name}/{f.name}",
                    "category": d.name,
                    "uploader": m.get("uploader") or (f.stem.split("_")[1] if f.stem.count("_") >= 2 else ""),
                    "url": m.get("url", ""),
                    "caption": m.get("caption", ""),
                    "note": m.get("note", ""),
                    "added": m.get("added", ""),
                    "mtime": f.stat().st_mtime,
                })
    videos.sort(key=lambda v: v["mtime"], reverse=True)
    return jsonify(categories=cats, videos=videos)


@app.post("/api/download")
def api_download():
    data = request.get_json(force=True)
    urls = [u.strip() for u in data.get("urls", []) if u.strip().startswith("http")]
    category, note = data.get("category", ""), data.get("note", "")
    ids = []
    for url in urls:
        jid = uuid.uuid4().hex[:8]
        with jobs_lock:
            jobs[jid] = {"id": jid, "url": url, "status": "wartet", "detail": ""}
        threading.Thread(target=run_job, args=(jid, url, category, note), daemon=True).start()
        ids.append(jid)
    return jsonify(ids=ids)


@app.get("/api/jobs")
def api_jobs():
    with jobs_lock:
        return jsonify(list(jobs.values())[-30:])


@app.post("/api/move")
def api_move():
    data = request.get_json(force=True)
    src = resolve(data["path"])
    dest_dir = LIBRARY / safe_name(data["category"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dest_dir / src.name)
    if sidecar(src).exists():
        shutil.move(str(sidecar(src)), dest_dir / sidecar(src).name)
    return jsonify(ok=True)


@app.post("/api/note")
def api_note():
    data = request.get_json(force=True)
    video = resolve(data["path"])
    meta = read_meta(video)
    meta["note"] = data.get("note", "")
    write_meta(video, meta)
    return jsonify(ok=True)


@app.post("/api/reveal")
def api_reveal():
    video = resolve(request.get_json(force=True)["path"])
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
        b = request.get_json(force=True).get("cookies_browser", "")
        s["cookies_browser"] = b if b in ("", "safari", "chrome", "firefox", "edge", "brave") else ""
        save_settings(s)
    return jsonify(load_settings())


@app.post("/api/quit")
def api_quit():
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify(ok=True)


@app.get("/media/<path:rel>")
def media(rel):
    return send_file(resolve(rel), conditional=True)


@app.get("/")
def index():
    return PAGE


# --------------------------------------------------------------------- Seite
PAGE = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reel</title>
<style>
:root{--bg:#f4f3ef;--fg:#141414;--muted:#77756f;--line:#d9d6ce;--card:#fff;--accent:#141414;--ok:#2f7a3d;--err:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eceae4;--muted:#8d8a83;--line:#2c2b29;--card:#1c1c1b;--accent:#eceae4;--ok:#7fc58b;--err:#f08a80}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 ui-sans-serif,-apple-system,"Helvetica Neue",Arial,sans-serif}
main{max-width:1280px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:15px;font-weight:600;letter-spacing:.02em;margin:0 0 24px;text-transform:uppercase}
form{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:10px;align-items:start;margin-bottom:12px}
textarea,input,select,button{font:inherit;color:inherit}
textarea,input,select{width:100%;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:9px 11px}
textarea{min-height:42px;height:42px;resize:vertical}
button.go{background:var(--accent);color:var(--bg);border:0;border-radius:6px;padding:10px 18px;cursor:pointer;font-weight:600}
#jobs{list-style:none;margin:0 0 28px;padding:0;font-size:13px;color:var(--muted)}
#jobs li{padding:3px 0;display:flex;gap:10px}
#jobs .fertig b{color:var(--ok)} #jobs .fehler b{color:var(--err)}
#jobs span.u{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:40ch}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-top:1px solid var(--line);padding-top:18px;margin-bottom:18px}
.chip{background:none;border:1px solid var(--line);border-radius:99px;padding:5px 12px;cursor:pointer}
.chip span{color:var(--muted);margin-left:4px}
.chip.on{background:var(--accent);color:var(--bg);border-color:var(--accent)} .chip.on span{color:inherit;opacity:.7}
#q{max-width:220px;margin-left:auto;padding:6px 11px}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:18px}
article{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;display:flex;flex-direction:column}
video{width:100%;aspect-ratio:9/16;object-fit:cover;background:#000;display:block}
.body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:8px;font-size:13px}
.meta{display:flex;justify-content:space-between;color:var(--muted)}
.meta a{color:var(--fg);text-decoration:none;font-weight:600}
.cap{margin:0;color:var(--muted);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}
.cap.open{-webkit-line-clamp:unset}
.row{display:flex;gap:6px} .row select{flex:1;padding:6px 8px} .row button{background:none;border:1px solid var(--line);border-radius:6px;padding:0 10px;cursor:pointer}
.note{padding:6px 8px}
.empty{color:var(--muted)}
header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:24px}
header h1{margin:0}
.quit{background:none;border:1px solid var(--line);border-radius:6px;padding:5px 12px;cursor:pointer;font-size:13px}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);font-size:13px;color:var(--muted);display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center}
footer select{width:auto;padding:4px 8px}
.bye{max-width:40ch;margin:20vh auto;text-align:center;color:var(--muted)}
@media (max-width:720px){form{grid-template-columns:1fr}#q{margin-left:0;max-width:none}}
</style></head><body><main>
<header><h1>Reel</h1><button class="quit" id="quit">Beenden</button></header>
<form id="f">
  <textarea id="urls" placeholder="Instagram-Link(s) einfügen – ein Link pro Zeile" required></textarea>
  <input id="cat" list="cats" placeholder="Kategorie, z. B. Mobility" required>
  <input id="note" placeholder="Notiz (optional)">
  <button class="go">Laden</button>
  <datalist id="cats"></datalist>
</form>
<ul id="jobs"></ul>
<div class="bar"><div id="chips" style="display:contents"></div><input id="q" placeholder="Suchen …"></div>
<div id="grid"></div>
<footer>
  <label>Login über Browser
    <select id="cookies">
      <option value="">keiner</option><option value="safari">Safari</option><option value="chrome">Chrome</option>
      <option value="firefox">Firefox</option><option value="edge">Edge</option><option value="brave">Brave</option>
    </select></label>
  <span>Nur nötig, wenn Downloads mit Login-Fehler scheitern. Du musst in diesem Browser bei Instagram eingeloggt sein.</span>
  <span>Ablage: ~/Sportuebungen</span>
</footer>
</main>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc=p=>p.split('/').map(encodeURIComponent).join('/');
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
let lib={categories:[],videos:[]},filter='Alle',q='',poll=null;

async function load(){lib=await (await fetch('/api/library')).json();render()}
function render(){
  $('#cats').innerHTML=lib.categories.map(c=>`<option value="${esc(c)}">`).join('');
  const n={};lib.videos.forEach(v=>n[v.category]=(n[v.category]||0)+1);
  if(filter!=='Alle'&&!lib.categories.includes(filter))filter='Alle';
  $('#chips').innerHTML=['Alle',...lib.categories].map(c=>
    `<button class="chip ${c===filter?'on':''}" data-c="${esc(c)}">${esc(c)}<span>${c==='Alle'?lib.videos.length:(n[c]||0)}</span></button>`).join('');
  const list=lib.videos.filter(v=>(filter==='Alle'||v.category===filter)&&(!q||(v.caption+' '+v.note+' '+v.uploader).toLowerCase().includes(q)));
  $('#grid').innerHTML=list.map(v=>`<article data-p="${esc(v.path)}">
    <video src="/media/${enc(v.path)}" controls muted loop playsinline preload="metadata"></video>
    <div class="body">
      <div class="meta">${v.url?`<a href="${esc(v.url)}" target="_blank" rel="noopener">@${esc(v.uploader)}</a>`:`<b>@${esc(v.uploader)}</b>`}<span>${esc(v.added)}</span></div>
      ${v.caption?`<p class="cap">${esc(v.caption)}</p>`:''}
      <input class="note" placeholder="Notiz, z. B. 3 × 12, Fokus Hüfte" value="${esc(v.note)}">
      <div class="row"><select class="move">${lib.categories.map(c=>`<option ${c===v.category?'selected':''}>${esc(c)}</option>`).join('')}<option value="__new">+ Neue Kategorie …</option></select>
      <button class="reveal" title="Im Ordner zeigen">↗</button></div>
    </div></article>`).join('')||'<p class="empty">Noch keine Videos in dieser Ansicht.</p>';
}
$('#chips').onclick=e=>{const b=e.target.closest('.chip');if(b){filter=b.dataset.c;render()}};
$('#q').oninput=e=>{q=e.target.value.toLowerCase();render()};
$('#grid').addEventListener('click',e=>{
  if(e.target.classList.contains('cap'))e.target.classList.toggle('open');
  if(e.target.classList.contains('reveal'))post('/api/reveal',{path:e.target.closest('article').dataset.p});
});
$('#grid').addEventListener('change',async e=>{
  const p=e.target.closest('article')?.dataset.p;if(!p)return;
  if(e.target.classList.contains('note'))post('/api/note',{path:p,note:e.target.value});
  if(e.target.classList.contains('move')){
    let c=e.target.value;
    if(c==='__new'){c=prompt('Name der neuen Kategorie');if(!c){render();return}}
    await post('/api/move',{path:p,category:c});load();
  }
});
$('#f').onsubmit=async e=>{
  e.preventDefault();
  const urls=$('#urls').value.split(/\s+/).filter(Boolean);
  await post('/api/download',{urls,category:$('#cat').value,note:$('#note').value});
  $('#urls').value='';$('#note').value='';startPoll();
};
function startPoll(){if(poll)return;poll=setInterval(async()=>{
  const js=await (await fetch('/api/jobs')).json();
  $('#jobs').innerHTML=js.slice().reverse().map(j=>`<li class="${j.status}"><b>${j.status}</b><span class="u">${esc(j.url)}</span><span>${esc(j.detail)}</span></li>`).join('');
  if(!js.some(j=>j.status==='läuft'||j.status==='wartet')){clearInterval(poll);poll=null;load()}
},1200)}
fetch('/api/settings').then(r=>r.json()).then(s=>$('#cookies').value=s.cookies_browser||'');
$('#cookies').onchange=e=>post('/api/settings',{cookies_browser:e.target.value});
$('#quit').onclick=async()=>{await post('/api/quit',{});document.body.innerHTML='<p class="bye">Reel ist beendet. Du kannst diesen Tab schliessen.</p>'};
load();
</script></body></html>"""


if __name__ == "__main__":
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} Reel läuft: {URL} – Ordner: {LIBRARY}, ffmpeg: {FFMPEG}")
    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
