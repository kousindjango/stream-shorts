"""Helpers for registering vertical-short layouts.

  grid  : grab a frame and overlay a labelled 5 %/25 % grid to locate the facecam / game area
          python layout_tool.py grid --video V --at 0:10:00 --out grid.png
  crop  : save the region [x, y, w, h] (ratios 0-1, or pixels) as an image to verify it
          python layout_tool.py crop --video V --at 0:10:00 --src 0.74 0.64 0.26 0.36 --out face.png
  save  : validate and store a layout in ~/.stream-shorts/layouts.json
          python layout_tool.py save --name my-split --video V --json layout.json [--default]
          (src values > 1 are treated as pixels of the source video and converted to ratios)
  list  : show stored user layouts
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONFIG_DIR = Path(os.environ.get("STREAM_SHORTS_HOME", Path.home() / ".stream-shorts"))
LAYOUTS = CONFIG_DIR / "layouts.json"
SETTINGS = CONFIG_DIR / "settings.json"
CANVAS_W, CANVAS_H = 1080, 1920


def ff() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        sys.exit("[error] ffmpeg が見つかりません（shorts-setup を実行）")
    return p


def size_of(video: str) -> tuple[int, int]:
    out = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
                         capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        sys.exit(f"[error] ffmpeg failed:\n{r.stderr[-2000:]}")


def to_ratio(vals: list[float], w: int, h: int) -> list[float]:
    if any(v > 1 for v in vals):
        x, y, cw, ch = vals
        vals = [x / w, y / h, cw / w, ch / h]
    return [round(min(max(v, 0.0), 1.0), 4) for v in vals]


def cmd_grid(a) -> None:
    w, h = size_of(a.video)
    labels = []
    for i in range(1, 20):
        if i % 5 == 0:
            labels.append(f"drawtext=expansion=none:text='{i * 5}%':x=w*{i / 20}+4:y=4:fontsize=h/40:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=4")
            labels.append(f"drawtext=expansion=none:text='{i * 5}%':x=4:y=h*{i / 20}+4:fontsize=h/40:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=4")
    vf = ",".join(["drawgrid=w=iw/20:h=ih/20:t=1:c=white@0.35", "drawgrid=w=iw/4:h=ih/4:t=3:c=red@0.8", *labels])
    out = Path(a.out).resolve()
    run([ff(), "-y", "-hide_banner", "-loglevel", "error", "-ss", a.at, "-i", a.video, "-frames:v", "1", "-update", "1", "-vf", vf, str(out)])
    print(f"grid: {out}  (source {w}x{h}; thin lines every 5 %, red every 25 %)")


def cmd_crop(a) -> None:
    w, h = size_of(a.video)
    x, y, cw, ch = to_ratio(a.src, w, h)
    out = Path(a.out).resolve()
    vf = f"crop=iw*{cw}:ih*{ch}:iw*{x}:ih*{y}"
    run([ff(), "-y", "-hide_banner", "-loglevel", "error", "-ss", a.at, "-i", a.video, "-frames:v", "1", "-update", "1", "-vf", vf, str(out)])
    print(f"crop: {out}  src(ratio)={[x, y, cw, ch]}  ≈ {int(w * cw)}x{int(h * ch)}px at ({int(w * x)},{int(h * y)})")


def cmd_save(a) -> None:
    lay = json.loads(Path(a.json).read_text(encoding="utf-8")) if Path(a.json).exists() else json.loads(a.json)
    w, h = size_of(a.video) if a.video else (1920, 1080)
    errs = []
    if not lay.get("panels"):
        errs.append("panels が空です")
    for i, p in enumerate(lay.get("panels", [])):
        p["src"] = to_ratio([float(v) for v in p["src"]], w, h)
        sx, sy, sw, sh = p["src"]
        if sw <= 0 or sh <= 0 or sx + sw > 1.0001 or sy + sh > 1.0001:
            errs.append(f"panel {i}: src が画面外です {p['src']}")
        dx, dy, dw, dh = [int(v) for v in p["dst"]]
        p["dst"] = [dx, dy, dw - dw % 2, dh - dh % 2]
        if dx < 0 or dy < 0 or dx + dw > CANVAS_W or dy + dh > CANVAS_H:
            errs.append(f"panel {i}: dst がキャンバス(1080x1920)外です {p['dst']}")
        p.setdefault("fit", "cover")
    lay.setdefault("background", "blur")
    lay.setdefault("subtitle_margin_v", 460)
    lay.setdefault("hook_margin_v", 220)
    lay.setdefault("cut_mode", "speech")
    if lay["subtitle_margin_v"] < 340:
        print("[warn] subtitle_margin_v < 340: ショートの下部UI（チャンネル名・説明）に字幕が隠れる可能性")
    if errs:
        sys.exit("[error] " + " / ".join(errs))
    data = json.loads(LAYOUTS.read_text(encoding="utf-8")) if LAYOUTS.exists() else {}
    data[a.name] = lay
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LAYOUTS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved layout '{a.name}' → {LAYOUTS}")
    if a.default:
        st = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
        st["default_layout"] = a.name
        SETTINGS.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"default_layout = {a.name}")


def cmd_list(_a) -> None:
    data = json.loads(LAYOUTS.read_text(encoding="utf-8")) if LAYOUTS.exists() else {}
    print(json.dumps(data, ensure_ascii=False, indent=2) if data else "(ユーザー登録レイアウトなし)")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grid"); g.add_argument("--video", required=True); g.add_argument("--at", default="0:05:00"); g.add_argument("--out", default="layout_grid.png")
    c = sub.add_parser("crop"); c.add_argument("--video", required=True); c.add_argument("--at", default="0:05:00")
    c.add_argument("--src", nargs=4, type=float, required=True); c.add_argument("--out", default="layout_crop.png")
    s = sub.add_parser("save"); s.add_argument("--name", required=True); s.add_argument("--json", required=True)
    s.add_argument("--video"); s.add_argument("--default", action="store_true")
    sub.add_parser("list")
    a = ap.parse_args()
    {"grid": cmd_grid, "crop": cmd_crop, "save": cmd_save, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    main()
