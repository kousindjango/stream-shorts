---
name: shorts-layout
description: >
  This skill should be used when the user wants to register or adjust the vertical layout used for
  short clips made from their stream — e.g. where the facecam/avatar is, how the game screen is
  cropped, or where subtitles and the hook title sit. Trigger phrases include 「レイアウトを登録」
  「顔枠の位置を設定」「縦動画の配置を変えたい」「ゲーム画面と顔を上下に分けたい」「字幕の位置を上げて」
  「アバターをワイプにしたい」 "set up my facecam layout", "adjust the shorts layout".
metadata:
  version: "0.1.0"
---

# 縦型レイアウトの登録・調整

Register a named layout (e.g. `my-game-split`) once, so make-shorts can reuse it for every stream with
the same OBS scene. Layouts are stored in `~/.stream-shorts/layouts.json`.

Scripts:
- `scripts/layout_tool.py` under this skill's base directory (`grid`, `crop`, `save`, `list`).
- `render_short.py` from the sibling **make-shorts** skill (`<base>/../make-shorts/scripts/`) for
  full previews.

## Layout model
Canvas is 1080×1920. A layout is a background plus one or more panels drawn in order (later panels
on top, so a small facecam "wipe" over the game is just a second panel).

```json
{
  "description": "ゲーム実況: 上に顔枠、下にゲーム",
  "background": "blur",
  "panels": [
    {"src": [0.74, 0.64, 0.26, 0.36], "dst": [0, 0, 1080, 640], "fit": "cover"},
    {"src": [0.25, 0.0, 0.5, 1.0], "dst": [0, 640, 1080, 1280], "fit": "cover"}
  ],
  "subtitle_margin_v": 460,
  "hook_margin_v": 540,
  "cut_mode": "audio"
}
```
- `src` = region of the source frame `[x, y, w, h]` as 0–1 ratios. Pixel values are also accepted by
  `save` and converted using the video's resolution.
- `dst` = where it goes on the canvas, in pixels. `fit`: `cover` fills and crops, `contain` fits whole.
- `background`: `blur` (blurred, slightly darkened source), `black`, or `#RRGGBB`.
- `subtitle_margin_v`: px from the bottom edge to the subtitle block. Keep ≥ 380: the bottom ~300 px
  is covered by the Shorts/X UI (channel name, caption). Avoid the right ~120 px for important
  content (like/comment buttons).
- `hook_margin_v`: px from the top edge to the hook title. Keep ≥ 150 (the top bar).
- `cut_mode`: default silence-cut mode for clips using this layout (`audio` for games, `speech` for talk).

## Steps

1. Get a recording that uses the scene to register, and a timestamp where the facecam/avatar and game
   are both visible (ask, or try 5–10 min in).

2. Ask whether the user can read the facecam source's position and size from OBS (right-click the
   source → 変換 → 変換の編集: 位置 / サイズ, plus the canvas resolution). If yes, use those pixel
   values directly — they are exact. Otherwise locate the region visually:
   ```
   python "<base>/scripts/layout_tool.py" grid --video "<video>" --at 0:10:00 --out "<workdir>/layout_grid.png"
   ```
   View the image (thin lines every 5 %, labelled lines every 25 %) and estimate the facecam box.
   Verify each guess:
   ```
   python "<base>/scripts/layout_tool.py" crop --video "<video>" --at 0:10:00 --src 0.74 0.64 0.26 0.36 --out "<workdir>/face.png"
   ```
   Tighten until the crop shows the whole face/avatar and no game UI. Do the same for the game area
   (usually the full frame minus the facecam, or a centered crop of the action).

3. Choose the arrangement with the user (AskUserQuestion, show what each means in one line):
   - 上下分割: facecam top (≈ 1080×600–720), game bottom (rest). Strongest for reaction clips.
   - ワイプ: game fills a large panel, facecam small in a corner on top (e.g. top-left of the game panel, `dst [40, 560, 360, 360]`).
   - ゲーム中心: game only (`contain` or 4:3 `cover`), blurred background — like built-in `game-zoom`.
   Match the facecam panel's aspect ratio to its `src` aspect to avoid heavy cropping of the face.

4. Write the layout JSON to a file in the work folder and save it:
   ```
   python "<base>/scripts/layout_tool.py" save --name my-game-split --video "<video>" --json "<workdir>/layout.json" [--default]
   ```
   `--default` makes it the layout used when a clip does not name one. Choose a name the user will
   recognise; reusing a built-in name (e.g. `game-split`) overrides that built-in.

5. Render a full preview with sample subtitle and hook text, view it, then show it to the user:
   ```
   python "<base>/../make-shorts/scripts/render_short.py" --video "<video>" --preview-frame 0:10:00 --layout my-game-split --out "<workdir>/preview.png"
   ```
   Check: face fully visible and not stretched, game action not cut off, subtitles and hook not over
   the face or key HUD, nothing important in the UI-covered zones. Adjust and repeat steps 4–5 until
   the user is happy. Preview a second timestamp if the scene changes during streams.

6. Confirm in one or two lines: layout name, arrangement, whether it is the default.

## Official style (title band + game + face close-up)
The official-style renderer (`edit_short.py`) needs two layouts per scene:

1. `<name>-official` — white title band, the game panel, and a tight face close-up below:
   ```json
   {"description": "...", "background": "black",
    "panels": [
      {"src": [<game x>, 0, <game w>, <source h>], "dst": [0, 345, 1080, 640], "fit": "cover"},
      {"src": [<face x>, <face y>, <face w>, <face w / 1.155>], "dst": [0, 985, 1080, 935], "fit": "cover"}],
    "boxes": [{"dst": [0, 0, 1080, 345], "color": "#FFFFFF"}],
    "title_band": true,
    "anchors": {"title": [540, 172], "main": [540, 985], "top": [540, 520], "face": [540, 1480],
                "center": [540, 1250], "chapter": [1050, 372], "note": [36, 380]},
    "cut_mode": "audio"}
   ```
   Game panel: crop the source to about 1.69:1 so the in-frame facecam stays visible (crop from the
   side away from it). Face panel: a box around the face only (hat/hair top to chin), with the eyes
   at ~45 % height; it is upscaled a lot, so keep it no smaller than ~160 px wide.
2. `<name>-face` — full-screen face for the cold open and big reactions:
   `"panels": [{"src": [<x>, <y>, <w>, <w × 1.78>], "dst": [0, 0, 1080, 1920], "fit": "cover"}]`,
   `"title_band": false`, `"anchors": {"main": [540, 1520], "center": [540, 960]}`, `"cut_mode": "off"`.

Locate the face with `grid` / `crop` as above (the eyes and chin are the reference points), save both
with `layout_tool.py save`, then set `"official_layout": "<name>-official"` in settings.json.
Verify with a quick `edit_short.py draft` + `render` of a short range and look at the sheet.

`layout_tool.py list` shows saved layouts. To delete one, edit `~/.stream-shorts/layouts.json` after
the user confirms.
