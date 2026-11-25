"""Edit-list (EDL) based short renderer — the "official clip" quality path.

A short is described by <workdir>/edits/<id>.json (see references/edit-schema.md):
  - several source segments (montage), each with its own layout and silence-cut mode
    (e.g. a 2-second full-screen face "cold open" followed by the main scene)
  - a colour-coded title shown in the layout's title band
  - captions with per-line telop styles (purple / red / yellow / shout ...), positions and
    pop animations, written against SOURCE times so they survive re-cuts
  - chapter labels (一戦目 / 二戦目 ...), note call-outs and chat-comment bubbles

Commands
  draft   build edits/<id>.json from candidates.json + transcript (auto captions to edit)
  show    print the edit list compactly (for reviewing / editing captions)
  render  render clips/<id>.mp4 from edits/<id>.json
  sheet   contact sheet PNG of a rendered clip (for visual checks)

  python edit_short.py draft  --workdir W --clip c01 [--layout mychannel-official] [--force]
  python edit_short.py show   --workdir W --clip c01
  python edit_short.py render --workdir W --clip c01 [--quality high]
  python edit_short.py sheet  --workdir W --clip c01 [--every 2]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

from common import (CONFIG_DIR, SCRIPT_DIR, ffmpeg_bin, fmt_ts, load_json, load_layouts,
                    load_settings, parse_ts, probe, run, save_json)
from sfx_gen import ensure_sfx
from render_short import (CANVAS_H, CANVAS_W, ass_filter, build_captions, encoder_args,
                          keep_from_gaps, layout_graph, make_mapper, select_expr,
                          silence_intervals, snap_words_to_speech, speech_gaps, speech_regions,
                          wrap)

STYLES_DEFAULT = SCRIPT_DIR.parent / "references" / "telop-styles.json"
CAPTION_LINE_CHARS = 9


# ============================================================== helpers
def load_styles() -> dict:
    base = load_json(STYLES_DEFAULT, {})
    user = load_json(CONFIG_DIR / "telop-styles.json", {}) or {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def fs(styles: dict, font: str, size: float) -> int:
    """Convert a visible glyph size to the libass font size (libass sizes by ascent+descent)."""
    em = styles.get("font_em", {}).get(font) or styles.get("font_em", {}).get("_default", 0.7)
    return int(round(size / em))


def ass_color(hexrgb: str) -> str:
    h = hexrgb.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_alpha(opacity: float) -> str:
    return f"&H{int(round((1 - opacity) * 255)):02X}&"


def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def esc(text: str) -> str:
    return text.replace("{", "｛").replace("}", "｝").replace("\\", "＼")


def text_width(text: str, size: float) -> float:
    return sum(size * (0.56 if ord(ch) < 0x2000 else 1.0) for ch in text)


def rounded_rect(w: float, h: float, r: float) -> str:
    r = min(r, w / 2, h / 2)
    k = r * 0.45
    return (f"m {r:.0f} 0 l {w - r:.0f} 0 b {w - k:.0f} 0 {w:.0f} {k:.0f} {w:.0f} {r:.0f} "
            f"l {w:.0f} {h - r:.0f} b {w:.0f} {h - k:.0f} {w - k:.0f} {h:.0f} {w - r:.0f} {h:.0f} "
            f"l {r:.0f} {h:.0f} b {k:.0f} {h:.0f} 0 {h - k:.0f} 0 {h - r:.0f} "
            f"l 0 {r:.0f} b 0 {k:.0f} {k:.0f} 0 {r:.0f} 0")


def clean_caption(text: str) -> str:
    text = text.replace("。", " ").replace("\n", " ")
    text = re.sub(r"(?<=[^\x00-\x7F]) +(?=[^\x00-\x7F])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def anim_tags(kind: str) -> str:
    if kind == "pop":
        return r"\fscx70\fscy70\t(0,90,\fscx110\fscy110)\t(90,170,\fscx100\fscy100)"
    if kind == "slam":
        return r"\fscx190\fscy190\alpha&H60&\t(0,110,\fscx94\fscy94\alpha&H00&)\t(110,190,\fscx100\fscy100)"
    return ""


# ============================================================== draft
def collect_words(transcript: dict, a: float, b: float) -> list[dict]:
    words = []
    for s in transcript.get("segments", []):
        if s["end"] < a or s["start"] > b:
            continue
        first = True
        for w in s.get("words", []):
            if w["start"] >= a - 0.05 and w["end"] <= b + 0.05:
                words.append({"start": w["start"] - a, "end": min(w["end"], b) - a, "word": w["word"],
                              "seg_start": first, "seg_end": min(s["end"], b) - a})
                first = False
    return words


def auto_style(text: str) -> str:
    if re.search(r"[!！]{1,}|[?？][!！]|やば|まじ|マジ|最悪|くそ|クソ", text):
        return "red"
    if re.search(r"[?？]$", text):
        return "cyan"
    return "purple"


def cmd_draft(a, st, layouts) -> None:
    W = Path(a.workdir).resolve()
    cands = load_json(W / "candidates.json", {})
    clip = next((c for c in cands.get("clips", []) if c["id"] == a.clip), None)
    if not clip:
        sys.exit(f"[error] candidates.json に {a.clip} がありません")
    out = W / "edits" / f"{a.clip}.json"
    if out.exists() and not a.force:
        sys.exit(f"[error] {out} は既にあります（上書きは --force）")
    transcript = load_json(W / "transcript.json", {})
    base = a.layout or st.get("official_layout") or cands.get("layout") or "talk-fit"
    lay = layouts.get(base)
    if not lay:
        sys.exit(f"[error] レイアウト {base} がありません")
    # segments: --segments "0:22:40.6~0:23:21:off, 0:23:38~0:23:57.4, 0:25:26~0:25:42:speech:mychannel-face"
    if a.segments:
        specs = []
        for item in a.segments.split(","):
            parts = item.strip().split("~")
            rest = parts[1].split(";") if ";" in parts[1] else [parts[1]]
            tail = rest[0].split(":")
            # end time itself contains ':' -> take up to 3 numeric fields as the time
            nums = []
            while tail and re.fullmatch(r"[\d.]+", tail[0]) and len(nums) < 3:
                nums.append(tail.pop(0))
            specs.append((parse_ts(parts[0]), parse_ts(":".join(nums)),
                          tail[0] if tail and tail[0] else None, tail[1] if len(tail) > 1 else None))
    else:
        specs = [(parse_ts(clip["start"]), parse_ts(clip["end"]), clip.get("cut"), None)]
    fixes = {**(cands.get("fixes") or {}), **(clip.get("fixes") or {})}
    segments, captions = [], []
    for si, (s0, e0, cut, slay) in enumerate(specs):
        L = layouts.get(slay or base, lay)
        seg = {"src": [fmt_ts(s0, True), fmt_ts(e0, True)], "cut": cut or L.get("cut_mode", "audio")}
        if slay:
            seg["layout"] = slay
        segments.append(seg)
        words = collect_words(transcript, s0, e0)
        snap_words_to_speech(words, speech_regions(W / "audio.wav", s0, e0))
        for c in build_captions(words, lambda t: t, fixes, max_chars=CAPTION_LINE_CHARS * 2):
            txt = clean_caption(c["text"].replace("\\N", ""))
            if txt:
                captions.append({"seg": si, "s": fmt_ts(s0 + c["start"], True), "e": fmt_ts(s0 + c["end"], True),
                                 "text": txt, "style": auto_style(txt)})
    hook = clip.get("hook") or clip.get("title") or ""
    edit = {
        "id": a.clip,
        "layout": base,
        "title": hook if "{" in hook else "{y}" + hook,
        "segments": segments,
        "captions": captions,
        "notes": [],
        "_help": "Times are SOURCE times (hh:mm:ss.s of the original stream). 'seg' = index into "
                 "segments. Styles: white purple red pink yellow orange cyan black shout shout_v emph "
                 "small. pos: main(default) | top | face | center | [x,y]. notes kind: note | comment. "
                 "Title colour tags: {y}{r}{m}{o}{b}{c}{p}{g}{k}{w}, \\n for line break.",
    }
    save_json(out, edit)
    print(f"draft: {out}  (segments {len(segments)}, captions {len(captions)}, layout {base})")


# ============================================================== show
def cmd_show(a, st, layouts) -> None:
    W = Path(a.workdir).resolve()
    e = load_json(W / "edits" / f"{a.clip}.json")
    if not e:
        sys.exit("[error] edit list がありません（draft を先に）")
    print(f"# {e['id']}  layout={e.get('layout')}  title={e.get('title')!r}")
    for i, s in enumerate(e["segments"]):
        print(f"seg{i}: {s['src'][0]}–{s['src'][1]}  layout={s.get('layout', '(base)')} cut={s.get('cut', 'audio')}"
              f"{'  chapter=' + s['chapter'] if s.get('chapter') else ''}")
    for j, c in enumerate(e.get("captions", [])):
        print(f"  c{j:02d} s{c.get('seg', 0)} {c['s']}–{c['e']} [{c.get('style', 'purple')}"
              f"{'/' + str(c['pos']) if c.get('pos') else ''}] {c['text']}")
    for n in e.get("notes", []):
        print(f"  note s{n.get('seg', 0)} {n['s']}–{n['e']} [{n.get('kind', 'note')}] {n['text']}")


# ============================================================== render
class Timeline:
    """Maps (segment index, source time) to output time."""

    def __init__(self):
        self.items = []  # (offset, src_start, mapper, out_dur)

    def add(self, offset, src_start, keep, out_dur):
        self.items.append((offset, src_start, make_mapper(keep), out_dur))

    def t(self, seg: int, src: float) -> float:
        off, s0, mp, dur = self.items[seg]
        return off + min(max(mp(src - s0), 0.0), dur)

    def seg_range(self, seg: int):
        off, _, _, dur = self.items[seg]
        return off, off + dur


def segment_keep(seg, src, words, regions, cut) -> list:
    a, b = parse_ts(seg["src"][0]), parse_ts(seg["src"][1])
    dur = b - a
    if cut == "speech" and words:
        return keep_from_gaps(speech_gaps(words, dur), dur)
    if cut == "audio":
        return keep_from_gaps(silence_intervals(src, a, dur), dur)
    return [[0.0, dur]]


def render_segment(src, info, seg, layout, keep, out_path, st) -> None:
    a, b = parse_ts(seg["src"][0]), parse_ts(seg["src"][1])
    dur = b - a
    fps = int(st.get("fps", 30))
    cut = len(keep) > 1 or keep[0] != [0.0, dur]
    g = []
    fd = 0.008  # 8 ms fades at every cut point: removes clicks from jump cuts
    if cut:
        g.append(f"[0:v]fps={fps},select='{select_expr(keep)}',setpts=N/({fps}*TB)[vc]")
        labels = []
        for k, (ka, kb) in enumerate(keep):
            g.append(f"[0:a]atrim=start={ka:.3f}:end={kb:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d={fd},afade=t=out:st={max(0.0, kb - ka - fd):.3f}:d={fd}[ak{k}]")
            labels.append(f"[ak{k}]")
        g.append("".join(labels) + f"concat=n={len(keep)}:v=0:a=1[ac]")
    else:
        g.append(f"[0:v]fps={fps}[vc]")
        g.append(f"[0:a]afade=t=in:st=0:d={fd},afade=t=out:st={max(0.0, dur - fd):.3f}:d={fd}[ac]")
    g.append(layout_graph(layout, info["width"], info["height"], "vc", "vout", fps))
    g.append("[ac]aresample=48000,aformat=channel_layouts=stereo[aout]")
    inter = dict(st, crf=max(10, int(st.get("crf", 20)) - 6))
    run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostats",
         "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-filter_complex", ";".join(g),
         "-map", "[vout]", "-map", "[aout]", *encoder_args(inter, "normal"), "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", str(out_path)])


def anchors(layout: dict) -> dict:
    d = {"main": [540, 1000], "top": [540, 520], "face": [540, 1480], "center": [540, 960],
         "title": [540, 172], "chapter": [1050, 372], "note": [40, 380]}
    d.update(layout.get("anchors", {}))
    return d


def caption_events(cap, t0, t1, styles, layout) -> list[str]:
    sty = styles["styles"].get(cap.get("style", "purple")) or styles["styles"]["purple"]
    font = styles["fonts"]["caption"]
    size = sty["size"] * float(cap.get("scale", 1.0))
    pos = cap.get("pos", "main")
    x, y = pos if isinstance(pos, list) else anchors(layout).get(pos, anchors(layout)["main"])
    text = clean_caption(cap["text"])
    inner_c, inner_w = sty["inner"] if sty.get("inner") else ("#000000", 0)
    rot = f"\\frz{sty['rot']}" if sty.get("rot") else ""
    anim = anim_tags(sty.get("anim", "none"))
    if sty.get("vertical"):
        chars = [ch for ch in text if ch != " "]
        step = size * 1.04
        items = [(x, y + (i - (len(chars) - 1) / 2) * step, esc(ch.replace("ー", "｜").replace("-", "｜")))
                 for i, ch in enumerate(chars)]
    else:
        body = esc(wrap(text, int(cap.get("line_chars", CAPTION_LINE_CHARS)))).replace("＼N", r"\N")
        items = [(x, y, body)]
    ev = []
    for px, py, body in items:
        common = f"\\an5\\pos({px:.0f},{py:.0f})\\fn{font}\\fs{fs(styles, font, size)}\\b0\\i0{rot}{anim}"
        if sty.get("outer"):
            oc, ow = sty["outer"]
            ev.append(f"Dialogue: 2,{ass_time(t0)},{ass_time(t1)},Base,,0,0,0,,"
                      f"{{{common}\\1c{ass_color(oc)}\\3c{ass_color(oc)}\\bord{inner_w + ow}\\shad3\\4c&H000000&\\4a&H90&}}{body}")
        ev.append(f"Dialogue: 3,{ass_time(t0)},{ass_time(t1)},Base,,0,0,0,,"
                  f"{{{common}\\1c{ass_color(sty['fill'])}\\3c{ass_color(inner_c)}\\bord{inner_w}\\shad0}}{body}")
    return ev


def title_events(title: str, t0, t1, styles, layout) -> list[str]:
    tcfg, colors = styles["title"], styles["title_colors"]
    font = styles["fonts"]["title"]
    x, y = anchors(layout)["title"]
    size = int(layout.get("title_size", tcfg["size"]))
    lines = title.replace("\\n", "\n").split("\n")
    # fit each line to the band width
    sx = tcfg.get("scale_x", 100) / 100
    plain_max = max(text_width(re.sub(r"\{[a-z/]\}", "", ln), size) for ln in lines) * sx
    limit = layout.get("title_max_width", 940)
    if plain_max > limit:
        size = int(size * limit / plain_max)
    inner_c, inner_w = tcfg["inner"]
    kin_c, kin_w = tcfg["inner_k"]
    outer_c, outer_w = tcfg["outer"]

    def runs(line, layer):
        out, cur = [], "y"
        for tok in re.split(r"(\{[a-z/]\})", line):
            m = re.fullmatch(r"\{([a-z/])\}", tok)
            if m:
                cur = "y" if m.group(1) == "/" else m.group(1)
                continue
            if not tok:
                continue
            fill = colors.get(cur, colors["y"])
            if layer == "inner":
                ic = kin_c if cur == "k" else inner_c
                out.append(f"{{\\1c{ass_color(fill)}\\3c{ass_color(ic)}}}{esc(tok)}")
            else:
                out.append(esc(tok))
        return "".join(out)

    n = len(lines)
    lh = size + tcfg.get("line_gap", 6)
    ev = []
    for i, ln in enumerate(lines):
        ly = y + (i - (n - 1) / 2) * lh
        common = f"\\an5\\pos({x},{ly:.0f})\\fn{font}\\fs{fs(styles, font, size)}\\fscx{tcfg.get('scale_x', 100)}\\b0"
        ev.append(f"Dialogue: 4,{ass_time(t0)},{ass_time(t1)},Base,,0,0,0,,"
                  f"{{{common}\\1c{ass_color(outer_c)}\\3c{ass_color(outer_c)}\\bord{inner_w + outer_w}"
                  f"\\shad{tcfg.get('shadow', 3)}\\4c&H000000&\\4a&H80&}}{runs(ln, 'outer')}")
        ev.append(f"Dialogue: 5,{ass_time(t0)},{ass_time(t1)},Base,,0,0,0,,"
                  f"{{{common}\\bord{inner_w}\\shad0}}{runs(ln, 'inner')}")
    return ev


def box_events(kind: str, text: str, t0, t1, styles, layout, pos=None) -> list[str]:
    cfg = styles[kind]
    font = styles["fonts"]["chapter" if kind == "chapter" else "note"]
    size = cfg["size"]
    lines = [text] if kind == "chapter" else wrap(text, cfg.get("max_chars", 14)).split("\\N")
    tw = max(text_width(l, size) for l in lines)
    px, py = cfg["pad"]
    icon = size * 1.1 if cfg.get("icon") else 0
    w = tw + px * 2 + icon
    h = len(lines) * size * 1.18 + py * 2
    if kind == "chapter":
        ax, ay = anchors(layout)["chapter"]
        x, y = ax - w, ay
    else:
        x, y = pos if isinstance(pos, list) else anchors(layout)["note"]
    box = (f"Dialogue: 6,{ass_time(t0)},{ass_time(t1)},Base,,0,0,0,,"
           f"{{\\an7\\pos({x:.0f},{y:.0f})\\p1\\bord0\\1c{ass_color(cfg['box'])}\\1a{ass_alpha(cfg.get('box_alpha', 1))}"
           f"\\shad{cfg.get('shadow', 0)}\\4c&H000000&\\4a&HA0&}}{rounded_rect(w, h, cfg['radius'])}{{\\p0}}")
    ev = [box]
    if icon:
        cy = y + h / 2
        r = size * 0.42
        ev.append(f"Dialogue: 7,{ass_time(t0)},{ass_time(t1)},Base,,0,0,0,,"
                  f"{{\\an7\\pos({x + px - 4:.0f},{cy - r:.0f})\\p1\\bord0\\shad0\\1c{ass_color(cfg['icon'])}}}"
                  f"{rounded_rect(2 * r, 2 * r, r)}{{\\p0}}")
    body = r"\N".join(esc(l) for l in lines)
    ev.append(f"Dialogue: 8,{ass_time(t0)},{ass_time(t1)},Base,,0,0,0,,"
              f"{{\\an4\\pos({x + px + icon:.0f},{y + h / 2:.0f})\\fn{font}\\fs{fs(styles, font, size)}"
              f"\\b{1 if cfg.get('bold') else 0}\\bord{cfg.get('text_bord', 0)}\\3c{ass_color(cfg['fill'])}\\shad0"
              f"\\1c{ass_color(cfg['fill'])}\\q2}}{body}")
    return ev


def write_ass(path: Path, events: list[str]) -> None:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,Arial,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(head + "\n".join(events) + "\n", encoding="utf-8-sig")


def cmd_render(a, st, layouts) -> None:
    W = Path(a.workdir).resolve()
    e = load_json(W / "edits" / f"{a.clip}.json")
    if not e:
        sys.exit("[error] edit list がありません（draft を先に）")
    meta = load_json(W / "source.json", {})
    src = Path(meta["video"])
    info = probe(src)
    transcript = load_json(W / "transcript.json", {})
    styles = load_styles()
    outdir = W / "clips"
    tmp = outdir / f"_{a.clip}_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    def lay(name):
        name = name or e.get("layout")
        if name not in layouts:
            sys.exit(f"[error] レイアウト {name} がありません。候補: {', '.join(layouts)}")
        return layouts[name]

    tl = Timeline()
    offset = 0.0
    seg_files = []
    for i, seg in enumerate(e["segments"]):
        sa, sb = parse_ts(seg["src"][0]), parse_ts(seg["src"][1])
        L = lay(seg.get("layout"))
        cut = seg.get("cut") or L.get("cut_mode", "audio")
        words = collect_words(transcript, sa, sb) if transcript else []
        keep = segment_keep(seg, src, words, None, cut)
        f = tmp / f"seg_{i:02d}.mkv"
        render_segment(src, info, seg, L, keep, f, st)
        d = probe(f)["duration"]
        tl.add(offset, sa, keep, d)
        print(f"  seg{i}: {seg['src'][0]}–{seg['src'][1]} → {d:5.1f}s  layout={seg.get('layout') or e.get('layout')} cut={cut}")
        offset += d
        seg_files.append(f)
    total = offset

    # ---------------- overlay text (title, chapters, captions, notes)
    ev = []
    title = e.get("title")
    for i, seg in enumerate(e["segments"]):
        L = lay(seg.get("layout"))
        t0, t1 = tl.seg_range(i)
        if title and L.get("title_band", False) and not seg.get("hide_title"):
            ev += title_events(title, t0, t1, styles, L)
        if seg.get("chapter"):
            ev += box_events("chapter", seg["chapter"], t0, t1, styles, L)
    caps = []
    for c in e.get("captions", []):
        si = int(c.get("seg", 0))
        t0, t1 = tl.t(si, parse_ts(c["s"])), tl.t(si, parse_ts(c["e"]))
        if t1 - t0 < 0.25:
            t1 = t0 + max(0.6, len(c["text"]) * 0.12)
        caps.append((t0, t1, c, si))
    caps.sort(key=lambda x: x[0])
    for k, (t0, t1, c, si) in enumerate(caps):
        # one "main" caption at a time: end at the next main caption
        if c.get("pos", "main") == "main" and not c.get("overlap"):
            nxt = next((x[0] for x in caps[k + 1:] if x[2].get("pos", "main") == "main"), None)
            if nxt is not None:
                t1 = min(t1, nxt)
        t1 = min(t1, tl.seg_range(si)[1])
        ev += caption_events(c, t0, t1, styles, lay(e["segments"][si].get("layout")))
    for n in e.get("notes", []):
        si = int(n.get("seg", 0))
        t0, t1 = tl.t(si, parse_ts(n["s"])), tl.t(si, parse_ts(n["e"]))
        ev += box_events(n.get("kind", "note"), n["text"], t0, max(t1, t0 + 1.0), styles,
                         lay(e["segments"][si].get("layout")), n.get("pos"))
    ass_name = f"{a.clip}.ass"
    write_ass(outdir / ass_name, ev)

    # ---------------- sound effects (auto from telops/notes/chapters, overridable per caption)
    sfx_events = []
    if e.get("sfx", st.get("sfx", True)):
        auto = {"shout": "impact", "shout_v": "impact", "emph": "pop"}
        for t0, t1, c, si in caps:
            name = c.get("sfx", auto.get(c.get("style", "")))
            if name and name != "none":
                sfx_events.append((t0, name))
        for n in e.get("notes", []):
            name = n.get("sfx", "kira")
            if name != "none":
                sfx_events.append((tl.t(int(n.get("seg", 0)), parse_ts(n["s"])), name))
        prev_ch = None
        for i, seg in enumerate(e["segments"]):
            ch = seg.get("chapter")
            name = seg.get("sfx", "whoosh" if (i > 0 and ch and ch != prev_ch) else None)
            if name and name != "none":
                sfx_events.append((max(0.0, tl.seg_range(i)[0] - 0.12), name))
            prev_ch = ch
        sfx_events.sort()
        dedup = []
        for t, nme in sfx_events:
            if not dedup or t - dedup[-1][0] > 0.25:
                dedup.append((t, nme))
        sfx_events = dedup

    # ---------------- concat + text + loudness → final
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8")
    out_name = f"{a.clip}.mp4"
    inputs = ["-f", "concat", "-safe", "0", "-i", str(lst)]
    g = [f"[0:v]{ass_filter(ass_name, outdir, st)},format=yuv420p[v]"]
    # voice-friendly dynamics: remove rumble, gently even out shouts vs. talk
    g.append("[0:a]highpass=f=70,acompressor=threshold=-24dB:ratio=2.5:attack=10:release=180:makeup=2dB[prog]")
    mix = ["[prog]"]
    bgm = e.get("bgm") or (st.get("bgm") if st.get("bgm_default") else None)
    if bgm and Path(bgm["file"]).exists():
        inputs += ["-stream_loop", "-1", "-i", str(bgm["file"])]
        bi = 1
        g[-1] = g[-1].replace("[prog]", "[progx]")
        g.append("[progx]asplit=2[prog][psc]")
        g.append(f"[{bi}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                 f"volume={bgm.get('volume_db', -24)}dB,afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[bgmr]")
        g.append("[bgmr][psc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[bgm]")
        mix.append("[bgm]")
    if sfx_events:
        files = ensure_sfx()
        used = sorted({n for _, n in sfx_events if n in files or Path(n).exists()})
        base_idx = len([x for x in inputs if x == "-i"])
        idx = {}
        for k, nme in enumerate(used):
            inputs += ["-i", str(files.get(nme, nme))]
            idx[nme] = base_idx + k
        vol = e.get("sfx_volume_db", st.get("sfx_volume_db", 0))
        for nme in used:
            times = [t for t, n2 in sfx_events if n2 == nme]
            outs = "".join(f"[{nme}_{j}]" for j in range(len(times)))
            g.append(f"[{idx[nme]}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB,"
                     f"asplit={len(times)}{outs}" if len(times) > 1 else
                     f"[{idx[nme]}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB{outs}")
            for j, t in enumerate(times):
                ms = int(t * 1000)
                g.append(f"[{nme}_{j}]adelay={ms}|{ms}[{nme}_d{j}]")
                mix.append(f"[{nme}_d{j}]")
    if len(mix) > 1:
        g.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first:dropout_transition=0[mixed]")
    else:
        g.append("[prog]anull[mixed]")
    g.append(f"[mixed]loudnorm=I={st.get('loudness_lufs', -14)}:TP=-1.5:LRA=11,aresample=48000[aout]")
    graph = ";".join(g)
    run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostats",
         *inputs, "-filter_complex", graph,
         "-map", "[v]", "-map", "[aout]", *encoder_args(st, a.quality), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_name], cwd=outdir)
    if not a.keep_tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    warn = []
    if total > 180:
        warn.append("3分超: YouTubeショートの上限超え")
    if total > 140:
        warn.append("2分20秒超: Xの無料アカウントでは投稿不可")
    print(f"[{a.clip}] {total:.1f}s  segments={len(seg_files)} captions={len(caps)} notes={len(e.get('notes', []))}"
          f" sfx={len(sfx_events)}{' bgm' if bgm else ''}"
          f" → {outdir / out_name}")
    for w in warn:
        print(f"  [warn] {w}")


# ============================================================== sheet
def cmd_sheet(a, st, layouts) -> None:
    W = Path(a.workdir).resolve()
    clip = W / "clips" / f"{a.clip}.mp4"
    d = probe(clip)["duration"]
    n = max(1, int(d // a.every) + 1)
    cols = 8
    rows = math.ceil(n / cols)
    out = W / "clips" / f"{a.clip}_sheet.png"
    run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-vf", f"fps=1/{a.every},scale=180:-2,tile={cols}x{rows}", "-frames:v", "1", "-update", "1", str(out)])
    print(f"sheet: {out}  ({n} frames, every {a.every}s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["draft", "show", "render", "sheet"])
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--clip", required=True)
    ap.add_argument("--layout")
    ap.add_argument("--segments", help='montage: "start~end[:cut[:layout]], ..." in source time')
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--quality", choices=["normal", "high"], default="normal")
    ap.add_argument("--keep-tmp", action="store_true")
    ap.add_argument("--every", type=float, default=2.0)
    a = ap.parse_args()
    st = load_settings()
    layouts = load_layouts()
    {"draft": cmd_draft, "show": cmd_show, "render": cmd_render, "sheet": cmd_sheet}[a.cmd](a, st, layouts)


if __name__ == "__main__":
    main()
