"""Prepare a long stream recording for highlight selection.

Steps (each is skipped when its output already exists, unless --force):
  1. audio.wav       16 kHz mono audio extracted with ffmpeg
  2. energy.json     per-second loudness and "excitement" peaks (sudden loudness rise)
  3. transcript.json faster-whisper transcription with word timestamps
  4. digest/*.txt    timestamped transcript chunks annotated with excitement markers,
                     plus peaks.txt, for Claude to read when picking candidates

The input may also be an HTTPS URL (YouTube / Twitch VOD); it is downloaded with yt-dlp first.

Usage:
  python -u prepare.py "https://www.youtube.com/watch?v=XXXX" [--download-dir DIR] [--max-height 1080]
  python -u prepare.py "D:/録画/2026-11-15 配信.mp4" [--workdir DIR] [--model large-v3-turbo]
                    [--hotwords "ゲーム名 キャラ名"]
                    [--device cuda|cpu] [--skip-transcribe] [--chunk-min 20] [--force]
"""
from __future__ import annotations

import argparse
import re
import time
import wave
from pathlib import Path

import numpy as np

from common import add_nvidia_dll_dirs, fmt_ts, ffmpeg_bin, load_json, load_settings, probe, run, save_json


# ---------------------------------------------------------------- 1. audio
def extract_audio(src: Path, wav: Path) -> None:
    print(f"[1/4] 音声を抽出中 → {wav.name}")
    run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])


# ---------------------------------------------------------------- 2. energy
def compute_energy(wav: Path, out: Path, top_n: int = 40, min_gap: float = 45.0,
                   min_excess: float = 5.0) -> dict:
    print("[2/4] 盛り上がり（音量の急上昇）を検出中")
    db = []
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        while True:
            frames = w.readframes(sr)
            if not frames:
                break
            x = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            db.append(20 * np.log10(max(rms, 1e-5)))
    db = np.array(db, dtype=np.float32)
    n = len(db)
    if n == 0:
        raise SystemExit("[error] 音声が空です")

    # Baseline: rolling median over 2 minutes; short-term: 3-second mean.
    # Silent seconds are excluded from the baseline so pauses don't make the next
    # utterance look like a spike.
    win = 121
    voiced = np.where(db < -50, np.nan, db)
    pad = np.pad(voiced, (win // 2, win // 2), constant_values=np.nan)
    with np.errstate(all="ignore"):
        import warnings
        warnings.simplefilter("ignore", RuntimeWarning)
        baseline = np.nanmedian(np.lib.stride_tricks.sliding_window_view(pad, win), axis=1)
    fallback = np.nanmedian(voiced) if np.isfinite(voiced).any() else -30.0
    baseline = np.where(np.isfinite(baseline), baseline, fallback)
    short = np.convolve(np.pad(db, (1, 1), mode="edge"), np.ones(3) / 3, mode="valid")
    excess = short - baseline
    excess[db < -50] = 0  # ignore near-silence

    order = np.argsort(-excess)
    peaks: list[dict] = []
    for i in order:
        if excess[i] < min_excess or len(peaks) >= top_n:
            break
        if all(abs(i - p["t"]) >= min_gap for p in peaks):
            peaks.append({"t": int(i), "ts": fmt_ts(i), "excess_db": round(float(excess[i]), 1)})
    peaks.sort(key=lambda p: p["t"])
    data = {
        "seconds": n,
        "db": [round(float(v), 1) for v in db],
        "excess": [round(float(v), 1) for v in excess],
        "peaks": peaks,
    }
    save_json(out, data)
    print(f"      ピーク {len(peaks)} 件")
    return data


# ---------------------------------------------------------------- 3. transcript
def pick_device(pref: str) -> str:
    if pref in ("cuda", "cpu"):
        return pref
    add_nvidia_dll_dirs()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def transcribe(wav: Path, out: Path, model_name: str, device: str, compute_type: str,
               language: str, hotwords: str | None = None) -> dict:
    add_nvidia_dll_dirs()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("[error] faster-whisper が未導入です。shorts-setup スキルで導入してください。")
    device = pick_device(device)
    if model_name == "auto":
        model_name = "large-v3-turbo" if device == "cuda" else "medium"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    print(f"[3/4] 文字起こし中 model={model_name} device={device} ({compute_type})")
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    # Pass samples directly (16 kHz mono float32) so faster-whisper never decodes the file
    # itself; this avoids PyAV version incompatibilities.
    with wave.open(str(wav), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segments, info = model.transcribe(
        audio, language=language, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5, condition_on_previous_text=False, hotwords=hotwords or None,
    )
    total = info.duration or 1
    t0 = time.time()
    next_report = 0.0
    segs = []
    for s in segments:
        text = s.text.strip()
        if not text or s.compression_ratio > 2.4:  # drop repetition hallucinations
            continue
        if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
            continue
        segs.append({
            "start": round(s.start, 2), "end": round(s.end, 2), "text": text,
            "words": [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word}
                      for w in (s.words or [])],
        })
        if s.end >= next_report:
            pct = s.end / total * 100
            print(f"      {fmt_ts(s.end)} / {fmt_ts(total)} ({pct:4.1f}%) 経過 {time.time() - t0:5.0f}s")
            next_report = s.end + 300
    data = {"model": model_name, "language": language, "duration": total, "segments": segs}
    save_json(out, data)
    print(f"      {len(segs)} セグメント")
    return data


# ---------------------------------------------------------------- 4. digest
def build_digest(transcript: dict | None, energy: dict, workdir: Path, chunk_min: int,
                 mark_db: float = 5.0) -> None:
    print("[4/4] ダイジェストを作成中")
    excess = energy["excess"]
    peaks = energy["peaks"]
    lines: list[tuple[float, str]] = []
    segs = (transcript or {}).get("segments", [])
    covered = set()
    for s in segs:
        a, b = int(s["start"]), min(int(s["end"]) + 1, len(excess))
        mx = max(excess[a:b] or [0])
        mark = f"  ▲+{mx:.0f}dB" if mx >= mark_db else ""
        covered.update(range(a, b))
        lines.append((s["start"], f"[{fmt_ts(s['start'])}] {s['text']}{mark}"))
    for p in peaks:
        if p["t"] not in covered:
            lines.append((p["t"], f"[{p['ts']}] （発話なし・音の盛り上がり ▲+{p['excess_db']:.0f}dB）"))
    lines.sort(key=lambda x: x[0])

    # Note long stretches without speech so Claude can see pacing.
    out_lines: list[tuple[float, str]] = []
    prev_end = 0.0
    for t, line in lines:
        if t - prev_end >= 90:
            out_lines.append((prev_end, f"[{fmt_ts(prev_end)}] （無発話 {int(t - prev_end)}秒）"))
        out_lines.append((t, line))
        prev_end = max(prev_end, t)
    ddir = workdir / "digest"
    ddir.mkdir(exist_ok=True)
    for old in ddir.glob("chunk_*.txt"):
        old.unlink()
    span = chunk_min * 60
    n_chunks = max(1, int(energy["seconds"] // span) + 1)
    overlap = 60  # each chunk repeats the last minute of the previous one, so a highlight that
    #               straddles a chunk boundary is still seen whole (idea from AI-Youtube-Shorts-Generator)
    for k in range(n_chunks):
        lo, hi = max(0, k * span - overlap), (k + 1) * span
        body = [l for t, l in out_lines if lo <= t < hi]
        if not body:
            continue
        ov = f"  先頭{overlap}秒は前チャンクと重複" if k else ""
        header = f"# {fmt_ts(lo)} – {fmt_ts(hi)}  (▲ = 直前2分の平均より声・音が大きい箇所){ov}\n"
        (ddir / f"chunk_{k + 1:02d}.txt").write_text(header + "\n".join(body) + "\n", encoding="utf-8")

    # peaks.txt: each peak with ±20 s of surrounding speech
    pl = ["# 盛り上がりピーク一覧（音量の急上昇。大きい順ではなく時系列）"]
    for p in peaks:
        ctx = [s["text"] for s in segs if s["end"] >= p["t"] - 20 and s["start"] <= p["t"] + 20]
        pl.append(f"[{p['ts']}] ▲+{p['excess_db']}dB  {' / '.join(ctx)[:300] or '（発話なし）'}")
    (workdir / "peaks.txt").write_text("\n".join(pl) + "\n", encoding="utf-8")
    print(f"      digest/ に {len(list(ddir.glob('chunk_*.txt')))} ファイル、peaks.txt を出力")


# ---------------------------------------------------------------- 0. URL input
ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be",
    "twitch.tv", "www.twitch.tv", "m.twitch.tv",
}


def validate_url(value: str) -> str:
    """Only HTTPS YouTube / Twitch URLs are passed to the downloader (adapted from
    AI-Youtube-Shorts-Generator's url_validation, MIT)."""
    from urllib.parse import urlsplit
    msg = "[error] URL は https の YouTube / Twitch のみ対応です（ローカルのファイルパスも可）"
    try:
        u = urlsplit(value.strip())
        host, port = u.hostname, u.port
    except ValueError:
        raise SystemExit(msg)
    if (u.scheme.lower() != "https" or not host or host.lower() not in ALLOWED_HOSTS
            or u.username or u.password or port not in (None, 443)):
        raise SystemExit(msg)
    return value.strip()


def download(url: str, dest: Path, max_height: int) -> tuple[Path, dict]:
    try:
        import yt_dlp
    except ImportError:
        raise SystemExit("[error] yt-dlp が未導入です: python -m pip install -U yt-dlp")
    dest.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
    }
    print(f"[0/4] ダウンロード中: {url}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    meta = {k: info.get(k) for k in ("id", "title", "channel", "uploader", "upload_date",
                                      "webpage_url", "duration", "was_live")}
    print(f"      {meta.get('title')} / {meta.get('channel') or meta.get('uploader')} → {path.name}")
    return path, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="録画ファイルのパス、または https の YouTube / Twitch URL")
    ap.add_argument("--download-dir", help="URL 入力時の保存先（既定: カレントフォルダ）")
    ap.add_argument("--max-height", type=int, default=1080)
    ap.add_argument("--workdir")
    ap.add_argument("--model")
    ap.add_argument("--device")
    ap.add_argument("--language")
    ap.add_argument("--hotwords", help="固有名詞のヒント（ゲーム名・キャラ名・配信者名などをスペース区切り）")
    ap.add_argument("--skip-transcribe", action="store_true")
    ap.add_argument("--chunk-min", type=int, default=20)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    st = load_settings()
    origin = {}
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", a.video):
        url = validate_url(a.video)
        src, origin = download(url, Path(a.download_dir or ".").resolve(), a.max_height)
    else:
        src = Path(a.video)
    src = src.resolve()
    if not src.exists():
        raise SystemExit(f"[error] ファイルがありません: {src}")
    workdir = Path(a.workdir).resolve() if a.workdir else src.with_name(src.stem + "_shorts")
    workdir.mkdir(parents=True, exist_ok=True)
    info = probe(src)
    print(f"入力: {src.name}  {fmt_ts(info['duration'])}  {info['width']}x{info['height']}")
    print(f"作業フォルダ: {workdir}")

    wav = workdir / "audio.wav"
    if a.force or not wav.exists():
        extract_audio(src, wav)
    energy_path = workdir / "energy.json"
    energy = compute_energy(wav, energy_path) if (a.force or not energy_path.exists()) else load_json(energy_path)

    tr_path = workdir / "transcript.json"
    transcript = None
    if not a.skip_transcribe:
        if a.force or not tr_path.exists():
            transcript = transcribe(wav, tr_path, a.model or st["whisper_model"], a.device or st["whisper_device"],
                                    st["whisper_compute_type"], a.language or st["language"], a.hotwords)
        else:
            transcript = load_json(tr_path)
    build_digest(transcript, energy, workdir, a.chunk_min)
    prev = load_json(workdir / "source.json", {}) or {}
    save_json(workdir / "source.json", {**prev, "video": str(src), **info,
                                        **({"origin": origin} if origin else {})})
    print("完了")


if __name__ == "__main__":
    main()
