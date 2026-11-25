---
name: make-shorts
description: >
  This skill should be used when the user wants to turn a long stream recording (配信録画・アーカイブ・
  ゲーム実況・雑談配信) into vertical short videos for YouTube Shorts or X. Trigger phrases include
  「配信からショートを作って」「切り抜きを作って」「ショート動画にして」「見どころを探して」
  「YouTubeショート用に」「X用の動画を作って」「アーカイブから切り抜き」「縦動画にして」
  "make shorts from my stream", "clip highlights from this VOD". It transcribes the recording,
  finds highlight candidates from speech content and loudness spikes, lets the user pick, then renders
  9:16 clips in an official-clip style (title band, game + face close-up, emotion-coloured telops,
  cold open, chapter labels, call-outs, multi-segment montage) and drafts post copy.
metadata:
  version: "0.3.0"
---

# 配信録画 → ショート動画

Turn a long stream recording into 9:16 short clips with dead air removed, edited telops,
-14 LUFS audio, and ready-to-paste post copy for YouTube and X.

Two render paths:
- **Official style (default)** — `edit_short.py`: edit-list based montage with title band, game +
  face close-up, cold open, emotion-coloured telops that Claude edits, chapter labels and call-outs.
  Target: 60–120 s. Rules: `references/telop-guide.md`; format: `references/edit-schema.md`.
- **Quick** — `render_short.py`: one continuous range, auto subtitles, a simple hook. Use when the
  user asks for speed or for many clips at once.
Everything runs locally on the user's PC with ffmpeg + faster-whisper. Upload is manual.

Scripts live in `scripts/` under this skill's base directory (shown when the skill loads). Call
them with the full path, e.g. `python -u "<base>/scripts/prepare.py" ...`. On Windows use `python`
(or `py`); quote every path because recordings often have spaces and Japanese names.

## Workflow

### 0. Preflight
1. Confirm the recording path. If the user only names a folder, list it and pick the newest video
   (`.mp4 .mkv .flv .mov .ts`), confirming the choice.
2. If `~/.stream-shorts/settings.json` does not exist, or a script errors with ffmpeg /
   faster-whisper missing, run the **shorts-setup** skill first, then return here.
3. Ask (one AskUserQuestion, skip what the user already said): stream type (ゲーム実況 / 雑談 / mixed),
   how many shorts they want (default 3–5), and proper nouns to help transcription
   (game title, character names, streamer/guest names). Pass those as `--hotwords`.

### 1. Prepare (transcribe + detect excitement)
```
python -u "<base>/scripts/prepare.py" "<video>" --hotwords "<names>" > "<workdir>/prepare.log" 2>&1
```
- The work folder defaults to `<video name>_shorts/` next to the video; create it before redirecting
  the log, or pass `--workdir`.
- Transcription of a multi-hour stream takes a long time (GPU with large-v3-turbo: a few minutes
  per hour of audio; CPU with medium: can approach real time). Run it in the background and poll the log
  instead of blocking; report progress lines to the user occasionally, not every poll.
- Steps already done are skipped on re-run, so an interrupted run can simply be restarted.
- Outputs: `transcript.json`, `energy.json`, `digest/chunk_XX.txt` (20-min chunks of
  `[hh:mm:ss] text  ▲+NdB`), `peaks.txt`, `source.json`.

### 2. Find highlight candidates
Read `peaks.txt` first, then every `digest/chunk_*.txt` in order. Do not skip chunks: talk highlights
often have no loudness spike. Apply the rubric in `references/highlight-guide.md`.

- Propose 2–3× as many candidates as the user wants (typically 8–12).
- For the official style a candidate can be a whole episode (2–6 min of source: repeated attempts,
  a struggle and its payoff); it is condensed to 60–120 s by the montage in step 3.
- Target raw length 40–80 s: silence cutting usually removes 15–35 % for talk, less for games,
  so the result lands in 30–60 s. Hard limits after cutting: 180 s (YouTube Shorts) and 140 s
  (X free accounts).
- Snap boundaries to transcript segments: start ≈ 0.3 s before the first word of the setup line,
  end ≈ 0.5–1.0 s after the punchline/reaction. Check `transcript.json` around each boundary so no
  sentence is cut mid-way.
- Choose a layout per clip (see "Layouts"). Write a `hook` (≤ 24 chars, shown at the top the whole
  clip) and a separate `title`.
- Scan each candidate's transcript for obvious misrecognitions of names or terms and add them to
  `fixes` (per clip) or the top-level `fixes` (whole stream).

Write `<workdir>/candidates.json` (schema in `references/candidates-schema.md`), all with
`"selected": false`, then present a table to the user:

| # | 時間 | 長さ(目安) | フック案 | 見どころ | 種別 / レイアウト | 推し度 |

Below the table, note any risks (copyrighted BGM, spoilers, personal info, other people's faces or
voices). Ask the user which to render and whether to change any hook text, using AskUserQuestion
with multiSelect for the picks.

### 3. Render (official style)
For each chosen clip:
1. **Plan the montage.** From the transcript around the candidate, pick: a cold-open line
   (1.5–4 s, the best reaction), then 2–6 segments that tell setup → payoff (attempts, time jumps).
   Skip menus, walking and loading by splitting segments. Mark silent-but-important action
   (falls, deaths, boss moves) `off`.
2. **Draft**:
   ```
   python "<base>/scripts/edit_short.py" draft --workdir "<workdir>" --clip c01 --force \
     --segments "0:23:53.5~0:23:57.3:off:<face-layout>, 0:22:40.6~0:23:20:off, 0:23:40.5~0:23:57.4:speech"
   ```
   Segment syntax: `start~end[:cut[:layout]]`. The base layout is `settings.official_layout`
   (or `--layout`). Captions are drafted from the transcript, aligned to the voice with VAD.
3. **Edit** `<workdir>/edits/c01.json` following `references/telop-guide.md`: title with colour
   tags, chapters, rewrite every caption (shorten, fix, choose style/pos), shout lines, notes.
   Review with `edit_short.py show`.
4. **Render**: `python -u "<base>/scripts/edit_short.py" render --workdir "<workdir>" --clip c01 --quality high`
   → `clips/c01.mp4`. Intermediate segments are rendered then concatenated; text is burned last.
   Audio: 8 ms fades at every cut (no clicks), high-pass + gentle compression, automatic sound
   effects (built-in, generated by `sfx_gen.py`, no third-party rights), optional ducked BGM,
   then -14 LUFS / -1.5 dBTP.
5. **Check**: `edit_short.py sheet --workdir ... --clip c01` → `clips/c01_sheet.png` (a frame every
   2 s). View it; also extract full-size frames at the cold open, a shout and a note. Fix and re-render.

Official-style layouts need a face close-up panel and a full-screen face layout for the user's
scene. If `settings.official_layout` is not set, run **shorts-layout** ("official" section) first.
Fonts (M PLUS Rounded 1c ExtraBold, M PLUS 1p Black, Yuji Syuku) are installed by
`shorts-setup` (`check_env.py --install-fonts`).

### 3b. Render (quick)
Set `"selected": true` on the chosen clips (and apply edits), then:
```
python -u "<base>/scripts/render_short.py" --workdir "<workdir>" --selected
```
- Other options: `--clip c01 c03`, `--layout NAME`, `--cut speech|audio|off`, `--no-subs`,
  `--hook-sec 3`, `--quality high`, ad-hoc `--start 1:02:10 --end 1:03:00 --hook "..."`.
- Subtitle onsets are aligned to the voice with Silero VAD (bundled with faster-whisper); clips where
  the action happens without sound should use `"cut": "off"`.

### 4. Check the result (quick path)
Extract 2–3 frames per clip (`ffmpeg -ss <t> -i clip.mp4 -frames:v 1 -vf scale=540:-2 f.png`) and
view them. Fix layouts with **shorts-layout**, subtitle typos via `fixes`, then re-render.

### 5. Post copy
Write `<workdir>/clips/posts.md` following `references/post-copy.md`: per clip, a YouTube title,
description and hashtags, and an X post. Use the actual content of the clip; never invent events.

### 6. Report
Give the user the clips folder path, a one-line list of clips with final durations, and point to
`posts.md`. Mention anything they should check by eye before posting (e.g. BGM rights).

## Layouts
Run `render_short.py --list-layouts` to see what is available. Built-ins:
- `talk-center` — center 9:16 crop, full screen (speaker centered in frame).
- `talk-fit` — whole 16:9 frame in the middle, blurred background, hook above, subs below (safe default).
- `game-zoom` — 4:3 center of the game zoomed in the middle, blurred background.
- `official-game` / `official-face` / `official-talk` — templates for the official style (title band,
  game panel, face close-up; full-screen face; talk). Register the user's own versions with
  **shorts-layout** (their facecam position differs) and set `settings.official_layout`.
- `game-split` — facecam on top, gameplay below. The built-in facecam position is a guess; register
  the user's real position with **shorts-layout** before the first use.

User-registered layouts (via shorts-layout) are stored in `~/.stream-shorts/layouts.json` and
override built-ins with the same name. Default silence-cut mode comes from the layout: `speech`
for talk (cuts gaps between words) and `audio` for games (cuts only true silence, so game action
without talking is kept).

## Troubleshooting
- `ass` filter error / subtitles missing → ffmpeg build lacks libass; run shorts-setup.
- Subtitles in a fallback font → the font in settings.json is not installed; run shorts-setup.
- GPU not used / cuDNN or cuBLAS DLL errors on Windows → shorts-setup covers the fix; meanwhile
  `--device cpu --model small` works.
- Whisper repeats a phrase over a long span (hallucination over BGM) → those segments are dropped
  automatically; if a candidate looks odd, check the transcript text around it before proposing it.
- Audio/video drift on VFR recordings (some OBS/phone captures) → re-render with `--cut off` to
  confirm; if it persists, convert the source to CFR first:
  `ffmpeg -i in.mkv -vf fps=30 -c:v libx264 -crf 18 -c:a copy in_cfr.mp4`.
