# candidates.json schema

Written by Claude in step 2 at `<workdir>/candidates.json`; read by `render_short.py`.

```json
{
  "layout": "game-split",
  "fixes": {"誤認識": "正しい表記"},
  "clips": [
    {
      "id": "c01",
      "start": "01:23:40.2",
      "end": "01:24:32.0",
      "hook": "ラスボス残り1ミリで…",
      "title": "ラスボス戦、まさかの結末",
      "reason": "残り体力わずかで逆転負け。叫び声+12dBのピーク、前振りから落ちまで45秒で完結",
      "score": 5,
      "kind": "game",
      "layout": "game-split",
      "cut": "audio",
      "fixes": {"ラスボズ": "ラスボス"},
      "selected": false
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `layout` (top) | no | Default layout for all clips. Falls back to `talk-fit`. |
| `fixes` (top) | no | Text replacements applied to every clip's subtitles. |
| `id` | yes | Short unique id; becomes the output file name (`c01.mp4`). |
| `start`, `end` | yes | `hh:mm:ss(.s)`, `mm:ss` or seconds, relative to the source video. |
| `hook` | yes | Title text burned in at the top (≤ 24 chars; wrapped to 2 lines of 12). Falls back to `title`. |
| `title` | yes | Working title, used for post copy. |
| `reason`, `score`, `kind` | yes | Shown to the user in the candidate table. `kind`: `game` / `talk`. |
| `layout` | no | Per-clip override. |
| `cut` | no | `speech` / `audio` / `off`. Defaults to the layout's `cut_mode`. |
| `fixes` | no | Per-clip subtitle replacements (applied after the top-level ones). |
| `selected` | yes | `render_short.py --selected` renders only clips set to `true`. |

Keep the file valid UTF-8 JSON. When the user edits a hook or range, update this file rather than
passing ad-hoc arguments, so the record stays complete.
