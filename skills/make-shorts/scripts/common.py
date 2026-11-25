"""Shared helpers for the stream-shorts scripts (Windows / macOS / Linux)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("STREAM_SHORTS_HOME", Path.home() / ".stream-shorts"))
SETTINGS_PATH = CONFIG_DIR / "settings.json"
LAYOUTS_PATH = CONFIG_DIR / "layouts.json"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LAYOUTS_PATH = SCRIPT_DIR.parent / "references" / "layouts-default.json"

DEFAULT_SETTINGS = {
    "whisper_model": "auto",      # auto | large-v3-turbo | large-v3 | medium | small
    "whisper_device": "auto",     # auto | cuda | cpu
    "whisper_compute_type": "auto",
    "language": "ja",
    "font": "Noto Sans JP",       # shorts-setup writes the best installed font here
    "encoder": "libx264",         # libx264 | h264_nvenc | h264_qsv | h264_amf
    "crf": 20,
    "preset": "veryfast",
    "fps": 30,
    "loudness_lufs": -14,
}

# Force UTF-8 console output on Windows so Japanese text prints cleanly.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(load_json(SETTINGS_PATH, {}) or {})
    return s


def load_layouts() -> dict:
    layouts = dict(load_json(DEFAULT_LAYOUTS_PATH, {}) or {})
    layouts.update(load_json(LAYOUTS_PATH, {}) or {})
    layouts.pop("_comment", None)
    return layouts


def add_nvidia_dll_dirs() -> None:
    """Windows: make pip-installed cuBLAS / cuDNN DLLs visible to CTranslate2."""
    if os.name != "nt":
        return
    import importlib.util
    for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
        try:
            spec = importlib.util.find_spec(pkg)
        except Exception:
            spec = None
        for loc in (spec.submodule_search_locations or []) if spec else []:
            b = Path(loc) / "bin"
            if b.is_dir():
                os.add_dll_directory(str(b))
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def ffmpeg_bin(name: str = "ffmpeg") -> str:
    found = shutil.which(name)
    if not found:
        sys.exit(f"[error] {name} が見つかりません。shorts-setup スキルで導入してください。")
    return found


def run(cmd: list[str], cwd: str | Path | None = None, capture: bool = False) -> subprocess.CompletedProcess:
    res = subprocess.run(
        cmd, cwd=cwd, capture_output=capture, text=True, encoding="utf-8", errors="replace"
    )
    if res.returncode != 0:
        tail = (res.stderr or "")[-3000:] if capture else ""
        sys.exit(f"[error] command failed ({res.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return res


def probe(path: str | Path) -> dict:
    out = run(
        [ffmpeg_bin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture=True,
    ).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    dur = float(info["format"].get("duration", 0))
    w = int(v["width"]) if v else 0
    h = int(v["height"]) if v else 0
    return {"duration": dur, "width": w, "height": h}


_TS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def parse_ts(value) -> float:
    """'1:23:45.6', '23:45', '5025.3' -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    v = str(value).strip()
    m = _TS.match(v)
    if m:
        h = int(m.group(1) or 0)
        return h * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return float(v)


def fmt_ts(sec: float, ms: bool = False) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:04.1f}" if ms else f"{h:02d}:{m:02d}:{int(s):02d}"
