"""Validate, tidy and rank <workdir>/candidates.json.

  python candidates.py check --workdir W          # report problems, print the ranked table
  python candidates.py check --workdir W --fix    # also write the cleaned file back

What it does (ideas adapted from AI-Youtube-Shorts-Generator, MIT):
  - sanitize: parse times, clamp to the video length, drop entries with end <= start
  - snap boundaries to whole sentences (transcript segments) so no clip starts or ends
    mid-sentence: start -> 0.3 s before its segment start, end -> 0.6 s after its segment end
  - dedupe: if two candidates overlap by more than 50 % of the shorter one, keep the higher score
  - rank by score (0-100) and warn about lengths outside 15-180 s
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import fmt_ts, load_json, parse_ts, save_json

PRE, POST = 0.3, 0.6


def seg_at(segs, t):
    for s in segs:
        if s["start"] - 0.05 <= t <= s["end"] + 0.05:
            return s
    return None


def snap(segs, start, end):
    notes = []
    s = seg_at(segs, start)
    if s and start - s["start"] > 0.4:
        notes.append(f"start was mid-sentence → {fmt_ts(s['start'] - PRE, True)}")
        start = s["start"] - PRE
    e = seg_at(segs, end)
    if e and e["end"] - end > 0.4:
        notes.append(f"end was mid-sentence → {fmt_ts(e['end'] + POST, True)}")
        end = e["end"] + POST
    return max(0.0, start), end, notes


def overlap(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return max(0.0, hi - lo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--min", type=float, default=15)
    ap.add_argument("--max", type=float, default=180)
    a = ap.parse_args()
    W = Path(a.workdir).resolve()
    cands = load_json(W / "candidates.json")
    if not cands:
        sys.exit("[error] candidates.json がありません")
    transcript = load_json(W / "transcript.json", {}) or {}
    segs = transcript.get("segments", [])
    duration = float((load_json(W / "source.json", {}) or {}).get("duration") or transcript.get("duration") or 1e12)

    clean, problems = [], []
    for c in cands.get("clips", []):
        cid = c.get("id", "?")
        try:
            st, en = parse_ts(c["start"]), parse_ts(c["end"])
        except Exception:  # noqa: BLE001
            problems.append(f"{cid}: 時刻が読めないので除外")
            continue
        st, en = max(0.0, min(st, duration)), max(0.0, min(en, duration))
        if en <= st:
            problems.append(f"{cid}: end <= start なので除外")
            continue
        if segs:
            st, en, notes = snap(segs, st, en)
            problems += [f"{cid}: {n}" for n in notes]
        try:
            score = max(0, min(100, int(float(c.get("score", 0)))))
        except (TypeError, ValueError):
            score = 0
        if 0 < score <= 5:  # old ★1-5 scale
            score *= 20
        d = en - st
        if d < a.min:
            problems.append(f"{cid}: {d:.0f}s は短すぎ（{a.min:.0f}s 未満）")
        if d > a.max:
            problems.append(f"{cid}: {d:.0f}s は長すぎ（{a.max:.0f}s 超）")
        clean.append({**c, "start": fmt_ts(st, True), "end": fmt_ts(en, True), "score": score,
                      "_s": st, "_e": en})

    kept = []
    for c in sorted(clean, key=lambda x: x["score"], reverse=True):
        dup = next((k for k in kept if overlap((c["_s"], c["_e"]), (k["_s"], k["_e"]))
                    > 0.5 * min(c["_e"] - c["_s"], k["_e"] - k["_s"])), None)
        if dup:
            problems.append(f"{c['id']}: {dup['id']} と50%以上重複（スコアの低い方を除外）")
            continue
        kept.append(c)

    print(f"{'id':5} {'start':>11} {'dur':>5} {'score':>5}  hook")
    for c in kept:
        print(f"{c['id']:5} {c['start']:>11} {c['_e'] - c['_s']:5.0f} {c['score']:5d}  {c.get('hook', '')}")
    if problems:
        print("\n指摘:")
        for p in problems:
            print("  -", p)
    else:
        print("\n指摘なし")
    if a.fix:
        for c in kept:
            c.pop("_s", None)
            c.pop("_e", None)
        cands["clips"] = kept
        save_json(W / "candidates.json", cands)
        print(f"\n書き戻しました: {W / 'candidates.json'}（{len(kept)} 件）")


if __name__ == "__main__":
    main()
