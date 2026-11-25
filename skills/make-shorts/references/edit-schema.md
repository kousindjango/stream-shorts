# Edit list schema (edits/<id>.json)

`edit_short.py draft` writes it, Claude edits it, `edit_short.py render` renders it.
All times are **source times** of the original recording (`h:mm:ss.s`), so captions stay
attached to the right words even when segments or silence cuts change.

```json
{
  "id": "c01",
  "layout": "mychannel-official",
  "title": "{y}メッセージを{r}信じた{y}結果…\\n{k}開始20分で{r}詰む!?",
  "segments": [
    {"src": ["0:23:53.5", "0:23:57.3"], "layout": "mychannel-face", "cut": "off"},
    {"src": ["0:22:40.6", "0:23:20.0"], "cut": "off",    "chapter": "開始20分"},
    {"src": ["0:23:40.5", "0:23:57.4"], "cut": "speech", "chapter": "開始20分"}
  ],
  "captions": [
    {"seg": 0, "s": "0:23:53.5", "e": "0:23:57.3", "text": "道が終わった!?", "style": "shout_v", "pos": "center"},
    {"seg": 1, "s": "0:22:58.5", "e": "0:22:59.6", "text": "ダメじゃん!!", "style": "shout", "pos": "center"},
    {"seg": 1, "s": "0:22:59.6", "e": "0:23:01.0", "text": "どういうこと!?", "style": "red"}
  ],
  "notes": [
    {"seg": 1, "s": "0:22:47.0", "e": "0:22:52.0", "text": "他プレイヤーが地面に残したメッセージ", "kind": "note"},
    {"seg": 2, "s": "0:23:45.0", "e": "0:23:49.0", "text": "コーラ一択", "kind": "comment"}
  ]
}
```

## Top level
| Field | Notes |
|---|---|
| `layout` | Base layout for segments that don't name one. Official-style layouts have `title_band: true`. |
| `title` | Title in the band. Colour tags `{y}` yellow (default) `{r}` red `{m}` magenta `{o}` orange `{b}` blue `{c}` cyan `{p}` purple `{g}` green `{k}` black (gets a white inner ring) `{w}` white, `{/}` back to yellow. `\n` = line break. 2 lines, ≤ 9 characters per line reads best; longer lines are shrunk to fit. |
| `segments` | Played in order (montage). |
| `captions` | Telops. One `main` caption is shown at a time (a new one ends the previous). |
| `notes` | Call-out boxes. |
| `sfx` | `false` turns off all sound effects for this clip (default on). |
| `sfx_volume_db` | Shift all effects up/down (default 0). |
| `bgm` | Optional: `{"file": "<path to a music file you have rights to>", "volume_db": -24}`. Loops, ducks under the voice automatically, fades out at the end. Off by default — the reference shorts use none. |

## Segment
| Field | Notes |
|---|---|
| `src` | `[start, end]` in source time. |
| `layout` | Override, e.g. the full-screen face layout for a cold open or a big reaction. |
| `cut` | `off` (keep everything — use for falls, cut-scenes, silent action), `audio` (cut only true silence), `speech` (cut pauses between words — talk). |
| `chapter` | Label at the top-right of the game panel for the whole segment (一戦目, 開始20分, 3分後 …). A change of chapter plays a `whoosh`. |
| `sfx` | Effect at the segment start (overrides the automatic chapter whoosh), or `none`. |
| `hide_title` | `true` to hide the title band text in this segment. |

## Caption
| Field | Notes |
|---|---|
| `seg` | Index into `segments`. Required when the same source time appears in two segments (cold open + main). |
| `s`, `e` | Source start / end. |
| `text` | Display text (edit freely: shorten, fix, add `!?` `…`). Wrapped to 2 lines of 9 characters. |
| `style` | `white` `purple` `red` `pink` `yellow` `orange` `cyan` `black` `shout` `shout_v` `emph` `small` — see `telop-guide.md`. |
| `pos` | `main` (panel boundary, default) `top` (upper game area) `face` (over the face) `center` or `[x, y]`. |
| `scale` | Size multiplier (e.g. `1.2`). |
| `line_chars` | Override the wrap width. |
| `overlap` | `true` to let a `main` caption stay up under the next one. |
| `sfx` | `pop` `impact` `whoosh` `kira` `boing`, a path to a WAV, or `none`. Defaults: `shout`/`shout_v` → `impact`, `emph` → `pop`, others → none. |

## Note
| Field | Notes |
|---|---|
| `kind` | `note` (white rounded box: explanations, game terms, context) or `comment` (box with a round user icon: a listener comment the streamer reacts to). |
| `pos` | `[x, y]` top-left; default is the layout's `note` anchor (upper-left of the game panel). |
| `sfx` | Default `kira`; `none` to mute. |
