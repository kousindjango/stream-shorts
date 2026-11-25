"""Check (and optionally save) the environment for the stream-shorts plugin.

Usage:
  python check_env.py                 # report only
  python check_env.py --test-whisper  # also run a 1-second GPU/CPU transcription test (downloads 'tiny' model)
  python check_env.py --write         # save recommended settings to ~/.stream-shorts/settings.json

Prints a JSON report followed by a short human-readable summary.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONFIG_DIR = Path(os.environ.get("STREAM_SHORTS_HOME", Path.home() / ".stream-shorts"))
SETTINGS = CONFIG_DIR / "settings.json"

# (family name for libass, filename patterns)
FONT_CANDIDATES = [
    ("Noto Sans JP", ["NotoSansJP*"]),
    ("Noto Sans CJK JP", ["NotoSansCJK*", "NotoSansCJKjp*"]),
    ("M PLUS Rounded 1c", ["MPLUSRounded1c*"]),
    ("BIZ UDPGothic", ["BIZ-UDGothic*", "BIZ-UDPGothic*", "BIZUDPGothic*", "BIZUDGothic*"]),
    ("Yu Gothic", ["YuGoth*"]),
    ("Meiryo", ["meiryo*"]),
    ("Hiragino Sans", ["ヒラギノ角ゴシック*", "Hiragino*"]),
]


def sh(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def font_dirs() -> list[Path]:
    dirs = []
    if os.name == "nt":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        la = os.environ.get("LOCALAPPDATA")
        if la:
            dirs.append(Path(la) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local/share/fonts"]
    return [d for d in dirs if d.is_dir()]


def find_fonts() -> list[str]:
    found = []
    dirs = font_dirs()
    for family, pats in FONT_CANDIDATES:
        for d in dirs:
            if any(next(d.rglob(p), None) for p in pats):
                found.append(family)
                break
    return found


def check_ffmpeg() -> dict:
    ff = shutil.which("ffmpeg")
    res = {"ffmpeg": ff, "ffprobe": shutil.which("ffprobe"), "version": None, "libass": False,
           "encoders": []}
    if not ff:
        return res
    _, out = sh([ff, "-hide_banner", "-version"])
    res["version"] = out.splitlines()[0] if out else None
    _, flt = sh([ff, "-hide_banner", "-filters"])
    res["libass"] = " ass " in flt
    # Probe hardware encoders with a real 1-frame encode (listing alone is not proof).
    for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
        code, _ = sh([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                      "color=c=black:s=256x256:d=0.1", "-frames:v", "1", "-c:v", enc, "-f", "null", "-"], 30)
        if code == 0:
            res["encoders"].append(enc)
    return res


def add_nvidia_dll_dirs() -> None:
    if os.name != "nt":
        return
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


def check_whisper(test: bool) -> dict:
    res = {"faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
           "opencv": importlib.util.find_spec("cv2") is not None,
           "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
           "numpy": importlib.util.find_spec("numpy") is not None,
           "cuda_devices": 0, "gpu_name": None, "cuda_test": None}
    add_nvidia_dll_dirs()
    if res["faster_whisper"]:
        try:
            import ctranslate2
            res["cuda_devices"] = ctranslate2.get_cuda_device_count()
        except Exception as e:  # noqa: BLE001
            res["cuda_error"] = str(e)
    if shutil.which("nvidia-smi"):
        code, out = sh(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
        if code == 0:
            res["gpu_name"] = out.strip().splitlines()[0]
    if test and res["faster_whisper"]:
        import wave
        tmp = Path(tempfile.gettempdir()) / "stream_shorts_silence.wav"
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000)
        from faster_whisper import WhisperModel
        for dev, ct in (("cuda", "float16"), ("cpu", "int8")):
            if dev == "cuda" and not res["cuda_devices"]:
                continue
            try:
                m = WhisperModel("tiny", device=dev, compute_type=ct)
                import numpy as np
                list(m.transcribe(np.zeros(16000, dtype=np.float32), language="ja")[0])
                res["cuda_test" if dev == "cuda" else "cpu_test"] = "ok"
                if dev == "cuda":
                    break
            except Exception as e:  # noqa: BLE001
                res["cuda_test" if dev == "cuda" else "cpu_test"] = f"failed: {str(e)[:300]}"
    return res


def recommend(ff: dict, wh: dict, fonts: list[str]) -> dict:
    gpu_ok = wh["cuda_devices"] > 0 and wh.get("cuda_test") in (None, "ok")
    enc = next((e for e in ("h264_nvenc", "h264_qsv", "h264_amf") if e in ff["encoders"]), "libx264")
    return {
        "whisper_model": "large-v3-turbo" if gpu_ok else "medium",
        "whisper_device": "cuda" if gpu_ok else "cpu",
        "whisper_compute_type": "float16" if gpu_ok else "int8",
        "encoder": enc,
        "font": fonts[0] if fonts else "Meiryo",
        **({"font": "M PLUS Rounded 1c ExtraBold", "font_bold": False}
           if (CONFIG_DIR / "fonts" / "MPLUSRounded1c-ExtraBold.ttf").exists() else {}),
    }


FONT_URLS = {
    "MPLUSRounded1c-ExtraBold.ttf": "https://github.com/google/fonts/raw/main/ofl/mplusrounded1c/MPLUSRounded1c-ExtraBold.ttf",
    "MPLUS1p-Black.ttf": "https://github.com/google/fonts/raw/main/ofl/mplus1p/MPLUS1p-Black.ttf",
    "YujiSyuku-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/yujisyuku/YujiSyuku-Regular.ttf",
}


MODEL_URLS = {
    # Real-face detector for the face-following crop (OpenCV Zoo YuNet, MIT)
    "face_detection_yunet_2023mar.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    # Anime face detector for VTuber avatars (nagadomi/lbpcascade_animeface, MIT)
    "lbpcascade_animeface.xml": "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml",
}


def _urlretrieve(url: str, dst: Path) -> None:
    """Download using the OS certificate store when available (python.org builds on Windows
    often lack the CA chain GitHub needs)."""
    import urllib.request
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass
    urllib.request.urlretrieve(url, dst)


def install_models() -> list[str]:
    d = CONFIG_DIR / "models"
    d.mkdir(parents=True, exist_ok=True)
    done = []
    for name, url in MODEL_URLS.items():
        dst = d / name
        if dst.exists() and dst.stat().st_size > 10_000:
            done.append(f"{name} (exists)")
            continue
        try:
            _urlretrieve(url, dst)
            done.append(name)
        except Exception as e:  # noqa: BLE001
            done.append(f"{name} FAILED: {e}")
    return done


def install_fonts() -> list[str]:
    """Download the official-style telop fonts (SIL OFL, Google Fonts) into ~/.stream-shorts/fonts."""
    d = CONFIG_DIR / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    done = []
    for name, url in FONT_URLS.items():
        dst = d / name
        if dst.exists() and dst.stat().st_size > 100_000:
            done.append(f"{name} (exists)")
            continue
        try:
            _urlretrieve(url, dst)
            done.append(name)
        except Exception as e:  # noqa: BLE001
            done.append(f"{name} FAILED: {e}")
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install-fonts", action="store_true")
    ap.add_argument("--install-models", action="store_true", help="アニメ顔検出モデル（VTuber追従用）を取得")
    ap.add_argument("--test-whisper", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.install_fonts:
        for line in install_fonts():
            print("font:", line)
    if a.install_models:
        for line in install_models():
            print("model:", line)

    ff = check_ffmpeg()
    wh = check_whisper(a.test_whisper)
    fonts = find_fonts()
    usage = shutil.disk_usage(Path.home())
    report = {
        "os": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "python_exe": sys.executable,
        "ffmpeg": ff,
        "whisper": wh,
        "fonts_found": fonts,
        "disk_free_gb": round(usage.free / 1e9, 1),
        "settings_path": str(SETTINGS),
        "current_settings": json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else None,
    }
    rec = recommend(ff, wh, fonts)
    report["recommended_settings"] = rec
    print(json.dumps(report, ensure_ascii=False, indent=2))

    problems = []
    if not ff["ffmpeg"]:
        problems.append("ffmpeg が見つからない")
    elif not ff["libass"]:
        problems.append("ffmpeg に字幕描画(libass)が入っていない → full版のビルドに入れ替え")
    if not wh["numpy"]:
        problems.append("numpy が未導入")
    if not wh["faster_whisper"]:
        problems.append("faster-whisper が未導入")
    if not wh["opencv"]:
        problems.append("opencv-python が未導入（顔追従クロップに必要・任意）")
    if not wh["yt_dlp"]:
        problems.append("yt-dlp が未導入（URL から直接作るときに必要・任意）")
    if wh["gpu_name"] and not wh["cuda_devices"]:
        problems.append("NVIDIA GPU はあるが文字起こしで使えていない（cuBLAS/cuDNN 未導入の可能性）")
    if str(wh.get("cuda_test", "")).startswith("failed"):
        problems.append("GPU での文字起こしテストに失敗（cuDNN 9 / cuBLAS 12 を確認）")
    if not fonts:
        problems.append("字幕向けの日本語太字フォントが見つからない（Noto Sans JP 推奨）")
    print("\n== summary ==")
    print("OK" if not problems else "要対応: " + " / ".join(problems))

    if a.write:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cur = report["current_settings"] or {}
        cur.update(rec)
        SETTINGS.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"settings saved: {SETTINGS}")


if __name__ == "__main__":
    main()
