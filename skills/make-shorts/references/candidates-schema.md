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
      "hook_sentence": "勝った、と思った瞬間に…",
      "virality_reason": "感情のピーク＋前振りと落ちが45秒で完結し、初見でも結末まで見たくなる",
      "score": 88,
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
| `reason`, `kind` | yes | Shown to the user in the candidate table. `kind`: `game` / `talk`. |
| `score` | yes | 0–100 viral potential (see `highlight-guide.md`). Old ★1–5 values are converted ×20 by the checker. |
| `hook_sentence` | yes | The transcript line that should open the clip / cold open. |
| `virality_reason` | yes | One sentence: which signals, why a stranger watches to the end. |
| `layout` | no | Per-clip override. |
| `cut` | no | `speech` / `audio` / `off`. Defaults to the layout's `cut_mode`. |
| `fixes` | no | Per-clip subtitle replacements (applied after the top-level ones). |
| `selected` | yes | `render_short.py --selected` renders only clips set to `true`. |

Keep the file valid UTF-8 JSON. When the user edits a hook or range, update this file rather than
passing ad-hoc arguments, so the record stays complete.

After writing the file, run `python "<base>/scripts/candidates.py" check --workdir "<workdir>" --fix`:
it snaps start/end to whole sentences, clamps times, drops overlapping duplicates (keeps the higher
score) and prints the ranked table to show the user.
