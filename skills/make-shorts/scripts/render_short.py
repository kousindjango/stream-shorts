"""Render vertical (9:16) short clips from a long stream recording.

For each clip: cut the range, remove dead air (silence cut), compose the layout
(crop / blur-fit / facecam split), burn in subtitles from the transcript, add the hook
title, normalise loudness to -14 LUFS, and encode to MP4 (1080x1920).

Usage:
  # render every clip marked "selected": true in <workdir>/candidates.json
  python render_short.py --workdir "D:/録画/配信_shorts" --selected
  # render specific clips
  python render_short.py --workdir DIR --clip c01 c04
  # ad-hoc range without candidates.json
  python render_short.py --workdir DIR --start 1:23:40 --end 1:24:25 --hook "まさかの展開" --layout talk-fit
  # layout preview PNG (used by the shorts-layout skill)
  python render_short.py --video "D:/録画/配信.mp4" --preview-frame 0:12:00 --layout game-split --out preview.png
  python render_short.py --list-layouts

Options: --layout NAME, --cut speech|audio|off, --no-subs, --hook-sec N, --quality high
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from common import (CONFIG_DIR, ffmpeg_bin, fmt_ts, load_json, load_layouts, load_settings, parse_ts,
                    probe, run, save_json)

CANVAS_W, CANVAS_H = 1080, 1920
SUB_FONT_SIZE = 96
SUB_LINE_CHARS = 12
HOOK_FONT_SIZE = 108
HOOK_LINE_CHARS = 12


# ---------------------------------------------------------------- keep segments
def silence_intervals(src: Path, start: float, dur: float, noise_db: float = -35, min_d: float = 0.7):
    res = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
               "-i", str(src), "-vn", "-af", f"silencedetect=n={noise_db}dB:d={min_d}", "-f", "null", "-"],
              capture=True)
    out, cur = [], None
    for line in res.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            cur = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            out.append((cur, float(m.group(1))))
            cur = None
    if cur is not None:
        out.append((cur, dur))
    return out


def speech_gaps(words: list[dict], dur: float, min_gap: float = 0.7):
    gaps, prev = [], 0.0
    for w in words:
        if w["start"] - prev >= min_gap:
            gaps.append((prev, w["start"]))
        prev = max(prev, w["end"])
    if dur - prev >= min_gap:
        gaps.append((prev, dur))
    return gaps


def keep_from_gaps(gaps, dur: float, pad: float = 0.15, min_keep: float = 0.25):
    keep, cur = [], 0.0
    for a, b in gaps:
        a2, b2 = a + pad, b - pad
        if b2 - a2 <= 0.05:
            continue
        if a2 > cur:
            keep.append([cur, a2])
        cur = b2
    if dur > cur:
        keep.append([cur, dur])
    keep = [k for k in keep if k[1] - k[0] >= min_keep] or [[0.0, dur]]
    merged = [keep[0]]
    for a, b in keep[1:]:
        if a - merged[-1][1] < 0.1:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return merged


def make_mapper(keep):
    def m(t: float):
        acc = 0.0
        for a, b in keep:
            if t < a:
                return acc  # inside a removed gap: snap to next kept point
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return acc
    return m


# ---------------------------------------------------------------- voice activity
def speech_regions(audio_wav: Path, start: float, end: float):
    """Silero VAD (bundled with faster-whisper) on the clip range. Returns [(s, e)] in clip time,
    or None when unavailable."""
    if not audio_wav or not Path(audio_wav).exists():
        return None
    try:
        import wave
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except Exception:
        return None
    with wave.open(str(audio_wav), "rb") as w:
        sr = w.getframerate()
        w.setpos(max(0, int(start * sr)))
        frames = w.readframes(int((end - start) * sr))
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    opts = VadOptions(threshold=0.5, min_speech_duration_ms=120, min_silence_duration_ms=250,
                      speech_pad_ms=60)
    return [(t["start"] / sr, t["end"] / sr) for t in get_speech_timestamps(audio, opts)]


def snap_words_to_speech(words, regions, max_shift: float = 6.0) -> int:
    """Whisper often places the first words of an utterance inside the preceding silence (or at
    the very tail of the previous utterance), so the subtitle appears before the voice. Move
    such words to the next speech onset found by VAD, staying within the same segment."""
    if not regions:
        return 0
    moved, prev = 0, 0.0
    for w in words:
        limit = min(w.get("seg_end", w["end"]) + 0.3, w["start"] + max_shift)
        reg = next(((a, b) for a, b in regions if a - 0.08 <= w["start"] <= b), None)
        target = None
        if reg is None:
            target = next((a for a, b in regions if w["start"] < a <= limit), None)
        elif reg[1] - w["start"] < 0.3 and w["end"] > reg[1] + 0.5:
            # starts at the very end of an earlier utterance and stretches over a pause
            target = next((a for a, b in regions if reg[1] < a <= max(limit, w["end"] + 0.6)), None)
        if target is not None:
            dur = min(w["end"] - w["start"], 0.6)
            w["start"] = target
            w["end"] = target + max(dur, 0.12)
            moved += 1
        w["start"] = max(w["start"], prev)
        w["end"] = max(w["end"], w["start"] + 0.05)
        prev = w["start"]
    return moved


# ---------------------------------------------------------------- subtitles
def clean_text(s: str) -> str:
    s = s.replace("。", " ").replace("、", " ").replace("\n", " ")
    s = s.replace("{", "｛").replace("}", "｝").replace("\\", "＼")
    return re.sub(r"\s+", " ", s).strip()


def _kind(ch: str) -> str:
    o = ord(ch)
    if 0x3040 <= o <= 0x309F:
        return "hira"
    if 0x30A0 <= o <= 0x30FF:
        return "kata"
    if 0x4E00 <= o <= 0x9FFF or ch in "々〆":
        return "kanji"
    if ch.isascii() and ch.isalnum():
        return "ascii"
    return "other"


def wrap(text: str, width: int) -> str:
    """Break into two lines at the most natural point (Japanese-aware)."""
    if len(text) <= width:
        return text
    mid = len(text) / 2
    best, best_score = None, 1e9
    for i in range(1, len(text)):
        left, right = text[:i].strip(), text[i:].strip()
        if not left or not right:
            continue
        c, n = text[i - 1], text[i]
        score = abs(i - mid) * 0.7
        over = max(0, len(left) - width) + max(0, len(right) - width)
        score += over * 4
        kc, kn = _kind(c), _kind(n)
        if c == " ":
            score -= 6
        elif c in "！？!?":
            score -= 3
        elif kc == "hira" and kn == "hira":
            # inside a hiragana run: only break right after a particle-like ending
            score += -2 if c in "はがをにでともへねよて" else 3
        if n in "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー）」』】、。！？!?":
            score += 10  # never start a line with small kana / closing marks
        if c in "（「『【":
            score += 10
        if kc == kn and kc in ("kanji", "kata", "ascii"):
            score += 6  # don't split a kanji compound, katakana word or latin word
        elif c in "はがをにでともへねよ" and kn != "hira":
            score -= 3
        if kc == "kanji" and kn == "hira" and n not in "はがをにでともへねよ":
            score += 4  # keep okurigana with its kanji
        if kc == "hira" and kn in ("kanji", "kata"):
            score -= 1.5
        if score < best_score:
            best, best_score = i, score
    if best is None:
        return text
    return text[:best].strip() + r"\N" + text[best:].strip()


_SENT_END = re.compile(r"[。！？!?]\s*$")
_BREAK_AFTER = "はがをにでともへねよてたやし、 "
_CJK_SPACE = re.compile(r"(?<=[^\x00-\x7F]) +(?=[^\x00-\x7F])")


def to_phrases(words, max_chars: int):
    """Group Whisper word tokens into phrases that are never split mid-word.

    Whisper's Japanese "words" are often sub-word tokens; a token that starts with a space,
    the first token of a segment, or a token after sentence-ending punctuation begins a new
    phrase. Phrases longer than max_chars are split after a particle where possible.
    """
    phrases = []
    for w in words:
        new = (not phrases or w.get("seg_start") or w["word"][:1].isspace()
               or bool(_SENT_END.search(phrases[-1]["text"])))
        if new:
            phrases.append({"start": w["start"], "end": w["end"], "toks": [w], "text": w["word"],
                            "seg_start": bool(w.get("seg_start"))})
        else:
            p = phrases[-1]
            p["end"] = max(p["end"], w["end"])
            p["toks"].append(w)
            p["text"] += w["word"]
    out = []
    for p in phrases:
        if len(p["text"].strip()) <= max_chars:
            out.append(p)
            continue
        toks, first = p["toks"], True
        while toks:
            n, best = 0, None
            for i, t in enumerate(toks):
                n += len(t["word"])
                if n > max_chars and i > 0:
                    break
                if t["word"].strip()[-1:] in _BREAK_AFTER and n >= max_chars * 0.4:
                    best = i
            else:
                best = len(toks) - 1
            cut = best if best is not None else max(0, i - 1)
            chunk, toks = toks[:cut + 1], toks[cut + 1:]
            out.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "toks": chunk,
                        "text": "".join(t["word"] for t in chunk), "seg_start": p["seg_start"] and first})
            first = False
    return out


def build_captions(words, mapper, fixes: dict | None = None, max_chars: int = SUB_LINE_CHARS * 2,
                   max_gap: float = 0.5, max_dur: float = 3.2):
    caps, cur = [], []
    fixes = fixes or {}

    def flush():
        if not cur:
            return
        text = "".join(p["text"] for p in cur)
        for wrong, right in fixes.items():
            text = text.replace(wrong, right)
        text = _CJK_SPACE.sub("", clean_text(text))
        if text:
            caps.append({"start": mapper(cur[0]["start"]), "end": mapper(cur[-1]["end"]), "text": text})
        cur.clear()

    for p in to_phrases(words, max_chars):
        if cur and (p["seg_start"]
                    or p["start"] - cur[-1]["end"] > max_gap
                    or p["end"] - cur[0]["start"] > max_dur
                    or len("".join(x["text"] for x in cur).strip()) + len(p["text"].strip()) > max_chars
                    or _SENT_END.search(cur[-1]["text"])):
            flush()
        cur.append(p)
    flush()
    for i, c in enumerate(caps):
        # Whisper word times inside very long segments drift; a caption that would stay up
        # far longer than it takes to say it is anchored to its end instead.
        limit = max(1.2, len(c["text"]) * 0.18 + 0.8)
        if c["end"] - c["start"] > max(max_dur, limit) * 1.5:
            c["start"] = c["end"] - max(max_dur, limit)
        nxt = caps[i + 1]["start"] if i + 1 < len(caps) else c["end"] + 1.0
        c["end"] = min(max(c["end"] + 0.2, c["start"] + 0.6), nxt)
        c["text"] = wrap(c["text"], SUB_LINE_CHARS)
    return [c for c in caps if c["end"] - c["start"] > 0.05]


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_ass(path: Path, captions, hook: str | None, hook_end: float, layout: dict, font: str,
              bold: bool = True) -> None:
    sub_mv = int(layout.get("subtitle_margin_v", 520))
    hook_mv = int(layout.get("hook_margin_v", 200))
    b = -1 if bold else 0
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{SUB_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,{b},0,0,0,100,100,0,0,1,7,2,2,60,60,{sub_mv},1
Style: Hook,{font},{HOOK_FONT_SIZE},&H0000E6FF,&H0000E6FF,&H00000000,&H80000000,{b},0,0,0,100,100,0,0,1,8,3,8,50,50,{hook_mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{wrap(clean_text(hook), HOOK_LINE_CHARS)}")
    for c in captions:
        ev.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},Sub,,0,0,0,,{c['text']}")
    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8-sig")


# ---------------------------------------------------------------- layout graph
def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def layout_graph(layout: dict, src_w: int, src_h: int, vin: str, vout: str, fps: int,
                 tracks: dict | None = None) -> str:
    panels = layout["panels"]
    bg = layout.get("background", "blur")
    n = len(panels) + 1
    parts = [f"[{vin}]split={n}" + "".join(f"[s{i}]" for i in range(n))]
    if bg == "blur":
        parts.append(f"[s0]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
                     f"boxblur=10:2,scale={CANVAS_W}:{CANVAS_H},eq=brightness=-0.08[bg]")
    else:
        color = "black" if bg in ("black", None) else bg.replace("#", "0x")
        parts.append(f"[s0]scale={CANVAS_W}:{CANVAS_H},setsar=1,drawbox=color={color}:t=fill[bg]")
    last = "bg"
    for i, p in enumerate(panels, start=1):
        rx, ry, rw, rh = p["src"]
        cw, ch = even(src_w * rw), even(src_h * rh)
        cx, cy = int(src_w * rx), int(src_h * ry)
        cx, cy = min(cx, src_w - cw), min(cy, src_h - ch)
        dx, dy, dw, dh = p["dst"]
        if p.get("fit") == "track":
            from track_face import window_size
            if tracks and i in tracks:
                name, x0, y0, (ww, wh) = tracks[i]
            else:
                name = None
                ww, wh = window_size(cw, ch, dw, dh)
                x0, y0 = cx + (cw - ww) // 2 // 2 * 2, cy + (ch - wh) // 2 // 2 * 2
            pre = f"sendcmd=f={name}," if name else ""
            f = f"[s{i}]{pre}crop@trk{i}=w={ww}:h={wh}:x={x0}:y={y0},scale={dw}:{dh},setsar=1[p{i}]"
            ox, oy = str(dx), str(dy)
        elif p.get("fit", "cover") == "cover":
            f = (f"[s{i}]crop={cw}:{ch}:{cx}:{cy},scale={dw}:{dh}:force_original_aspect_ratio=increase,"
                 f"crop={dw}:{dh},setsar=1[p{i}]")
            ox, oy = str(dx), str(dy)
        else:
            f = (f"[s{i}]crop={cw}:{ch}:{cx}:{cy},scale={dw}:{dh}:force_original_aspect_ratio=decrease:"
                 f"force_divisible_by=2,setsar=1[p{i}]")
            ox, oy = f"{dx}+({dw}-overlay_w)/2", f"{dy}+({dh}-overlay_h)/2"
        parts.append(f)
        nxt = f"o{i}"
        parts.append(f"[{last}][p{i}]overlay=x={ox}:y={oy}[{nxt}]")
        last = nxt
    # Solid boxes drawn on top of the panels (e.g. a title band). color: "#RRGGBB" or "#RRGGBB@0.8"
    boxes = "".join(
        f",drawbox=x={b['dst'][0]}:y={b['dst'][1]}:w={b['dst'][2]}:h={b['dst'][3]}"
        f":color={b.get('color', '#FFFFFF').replace('#', '0x')}:t=fill"
        for b in layout.get("boxes", []))
    parts.append(f"[{last}]format=yuv420p{boxes},fps={fps}[{vout}]")
    return ";".join(parts)


def ass_filter(ass_name: str, outdir: Path, st: dict) -> str:
    """ass filter with an optional private font folder (settings.fonts_dir, default
    ~/.stream-shorts/fonts). Uses a relative path because ffmpeg filter syntax treats the
    drive-letter colon of Windows paths as an option separator."""
    fonts = Path(st.get("fonts_dir") or (CONFIG_DIR / "fonts"))
    if not fonts.is_dir() or not any(fonts.iterdir()):
        return f"ass={ass_name}"
    try:
        rel = os.path.relpath(fonts, outdir)
    except ValueError:  # different drive on Windows: copy the fonts next to the output
        local = outdir / "_fonts"
        local.mkdir(exist_ok=True)
        for f in fonts.iterdir():
            if f.is_file() and not (local / f.name).exists():
                shutil.copy2(f, local / f.name)
        rel = "_fonts"
    return f"ass={ass_name}:fontsdir={rel.replace(os.sep, '/')}"


def encoder_args(st: dict, quality: str) -> list[str]:
    enc = st.get("encoder", "libx264")
    crf = int(st.get("crf", 20))
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p6" if quality == "high" else "p4",
                "-rc", "vbr", "-cq", str(crf + 1), "-b:v", "0"]
    if enc == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", str(crf + 1)]
    if enc == "h264_amf":
        return ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf + 2)]
    preset = "slow" if quality == "high" else st.get("preset", "veryfast")
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def select_expr(keep) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)


# ---------------------------------------------------------------- render one clip
def render_clip(src: Path, info: dict, transcript: dict | None, clip: dict, layout: dict,
                outdir: Path, st: dict, cut_mode: str, subs: bool, hook_sec: float | None,
                quality: str) -> dict:
    cid = clip.get("id", "clip")
    start, end = parse_ts(clip["start"]), parse_ts(clip["end"])
    if end <= start:
        raise SystemExit(f"[error] {cid}: end <= start")
    dur = end - start
    words = []
    if transcript:
        for s in transcript.get("segments", []):
            if s["end"] < start or s["start"] > end:
                continue
            first = True
            for w in s.get("words", []):
                if w["start"] >= start - 0.05 and w["end"] <= end + 0.05:
                    words.append({"start": w["start"] - start, "end": min(w["end"], end) - start,
                                  "word": w["word"], "seg_start": first,
                                  "seg_end": min(s["end"], end) - start})
                    first = False

    regions = speech_regions(st.get("_audio_wav"), start, end) if words else None
    snapped = snap_words_to_speech(words, regions)
    if cut_mode == "speech" and words:
        keep = keep_from_gaps(speech_gaps(words, dur), dur)
    elif cut_mode == "audio":
        keep = keep_from_gaps(silence_intervals(src, start, dur), dur)
    else:
        keep = [[0.0, dur]]
    mapper = make_mapper(keep)
    final_dur = sum(b - a for a, b in keep)

    font = clip.get("font") or st["font"]
    bold = bool(clip.get("font_bold", st.get("font_bold", True)))
    fixes = {**(clip.get("_global_fixes") or {}), **(clip.get("fixes") or {})}
    captions = build_captions(words, mapper, fixes) if subs else []
    hook = clip.get("hook") or clip.get("title")
    hook_end = min(hook_sec, final_dur) if hook_sec else final_dur
    ass_name = f"{cid}.ass"
    write_ass(outdir / ass_name, captions, hook, hook_end, layout, font, bold)

    fps = int(st.get("fps", 30))
    cut = len(keep) > 1 or keep[0] != [0.0, dur]
    g = []
    if cut:
        g.append(f"[0:v]select='{select_expr(keep)}',setpts=N/({fps}*TB)[vc]")
        g.append(f"[0:a]aselect='{select_expr(keep)}',asetpts=N/SR/TB[ac]")
    else:
        g.append("[0:v]null[vc]")
        g.append("[0:a]anull[ac]")
    if cut:
        g[0] = g[0].replace("[0:v]select", f"[0:v]fps={fps},select")
    from track_face import prepare_tracks
    tracks = prepare_tracks(layout, src, info, start, dur, keep, fps, outdir, cid)
    g.append(layout_graph(layout, info["width"], info["height"], "vc", "vl", fps, tracks))
    g.append(f"[vl]{ass_filter(ass_name, outdir, st)}[vout]")
    g.append(f"[ac]loudnorm=I={st.get('loudness_lufs', -14)}:TP=-1.5:LRA=11,aresample=48000[aout]")
    out_name = f"{cid}.mp4"
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostats",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
           "-filter_complex", ";".join(g), "-map", "[vout]", "-map", "[aout]",
           *encoder_args(st, quality), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_name]
    print(f"[{cid}] {fmt_ts(start)}–{fmt_ts(end)}  元 {dur:.1f}s → 無音カット後 {final_dur:.1f}s"
          f"  ({len(keep)} 区間, 字幕 {len(captions)} 行, layout={layout.get('_name')}, font={font}"
          f"{', VAD補正 ' + str(snapped) + '語' if regions is not None else ', VADなし'})")
    run(cmd, cwd=outdir)
    warn = []
    if final_dur > 180:
        warn.append("3分超: YouTubeショートの上限を超えています")
    if final_dur > 140:
        warn.append("2分20秒超: Xの無料アカウントでは投稿できません")
    for w in warn:
        print(f"  [warn] {w}")
    return {"id": cid, "file": str(outdir / out_name), "duration": round(final_dur, 1),
            "source_range": [fmt_ts(start, True), fmt_ts(end, True)], "segments": len(keep),
            "captions": len(captions), "warnings": warn}


# ---------------------------------------------------------------- preview
def preview(src: Path, info: dict, at: float, layout: dict, out: Path, st: dict) -> None:
    outdir = out.parent.resolve()
    ass_name = out.stem + "_preview.ass"
    write_ass(outdir / ass_name, [{"start": 0, "end": 5, "text": wrap(st.get("_preview_text") or "字幕はこの位置に出ます サンプル", SUB_LINE_CHARS)}],
              st.get("_preview_hook") or "タイトル（フック）の位置", 5, layout, st["font"], bool(st.get("font_bold", True)))
    from track_face import prepare_tracks
    tracks = prepare_tracks(layout, src, info, at, 0.4, [[0.0, 0.4]], 30, outdir, "preview")
    g = ";".join([layout_graph(layout, info["width"], info["height"], "0:v", "vl", 30, tracks),
                  f"[vl]{ass_filter(ass_name, outdir, st)}[vout]"])
    run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(src),
         "-filter_complex", g, "-map", "[vout]", "-frames:v", "1", "-update", "1", out.name], cwd=outdir)
    (outdir / ass_name).unlink(missing_ok=True)
    print(f"preview: {out.resolve()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    ap.add_argument("--video")
    ap.add_argument("--clip", nargs="*")
    ap.add_argument("--selected", action="store_true")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--hook")
    ap.add_argument("--id", default="manual")
    ap.add_argument("--layout")
    ap.add_argument("--cut", choices=["speech", "audio", "off"])
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--hook-sec", type=float)
    ap.add_argument("--quality", choices=["normal", "high"], default="normal")
    ap.add_argument("--preview-frame")
    ap.add_argument("--out")
    ap.add_argument("--list-layouts", action="store_true")
    ap.add_argument("--font", help="字幕・フックのフォント名（settings.json の font を上書き）")
    ap.add_argument("--no-bold", action="store_true", help="太字合成をしない（極太フォント向け）")
    ap.add_argument("--preview-text")
    a = ap.parse_args()

    st = load_settings()
    if a.font:
        st["font"] = a.font
    if a.no_bold:
        st["font_bold"] = False
    st["_preview_text"] = a.preview_text
    st["_preview_hook"] = a.hook
    layouts = load_layouts()
    if a.list_layouts:
        for k, v in layouts.items():
            print(f"{k:16s} {v.get('description', '')}")
        return

    workdir = Path(a.workdir).resolve() if a.workdir else None
    meta = load_json(workdir / "source.json", {}) if workdir else {}
    src = Path(a.video or meta.get("video", "")).resolve()
    if not src.is_file():
        raise SystemExit("[error] 元動画が見つかりません（--video か prepare 済みの --workdir を指定）")
    info = probe(src)
    cands = load_json(workdir / "candidates.json", {}) if workdir else {}

    def get_layout(name: str | None) -> dict:
        name = name or cands.get("layout") or st.get("default_layout") or "talk-fit"
        if name not in layouts:
            raise SystemExit(f"[error] レイアウト '{name}' がありません。候補: {', '.join(layouts)}")
        lay = dict(layouts[name])
        lay["_name"] = name
        return lay

    if a.preview_frame:
        out = Path(a.out or "layout_preview.png")
        preview(src, info, parse_ts(a.preview_frame), get_layout(a.layout), out, st)
        return
    if not workdir:
        raise SystemExit("[error] --workdir が必要です")

    transcript = load_json(workdir / "transcript.json")
    st["_audio_wav"] = workdir / "audio.wav"
    if a.start and a.end:
        clips = [{"id": a.id, "start": a.start, "end": a.end, "hook": a.hook}]
    else:
        allc = cands.get("clips", [])
        if a.clip:
            clips = [c for c in allc if c.get("id") in a.clip]
        elif a.selected:
            clips = [c for c in allc if c.get("selected")]
        else:
            raise SystemExit("[error] --clip ID… / --selected / --start と --end のいずれかを指定")
        if not clips:
            raise SystemExit("[error] 対象クリップがありません（candidates.json を確認）")

    outdir = workdir / "clips"
    outdir.mkdir(exist_ok=True)
    results = []
    for c in clips:
        c = {**c, "_global_fixes": cands.get("fixes")}
        lay = get_layout(c.get("layout") or a.layout)
        cut = a.cut or c.get("cut") or lay.get("cut_mode", "speech")
        if a.hook and not (a.start and a.end):
            c = {**c, "hook": a.hook}
        results.append(render_clip(src, info, transcript, c, lay, outdir, st, cut, not a.no_subs,
                                   a.hook_sec, a.quality))
    log = load_json(outdir / "render_log.json", []) or []
    save_json(outdir / "render_log.json", log + results)
    print("完了:", ", ".join(r["file"] for r in results))


if __name__ == "__main__":
    sys.exit(main())
