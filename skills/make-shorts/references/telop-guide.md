# Telop editing guide (official-clip style)

Derived from the official VTuber shorts this plugin targets (game + talk). Apply when editing
`edits/<id>.json` after `draft`.

## Structure of a short (72–120 s is normal for this style)
1. **Cold open (1.5–4 s)** — the best line of the clip, played first on the full-screen face layout,
   with the line as a giant vertical red telop (`shout_v`, `pos: center`, ≤ 8 characters).
   The same moment plays again later in context.
2. **Main body** — 2–6 segments cut from the stream: attempts, setup → payoff, before/after.
   Drop dead stretches (menus, walking, loading) by ending one segment and starting the next.
   Use `cut: off` where the action has no voice but matters (falls, deaths, boss moves).
3. **Ending** — finish on the payoff or reaction; no outro needed.

## Title (band at the top, whole video)
- 2 lines, ≤ 9 characters each, question/exclamation driven: 「ボスに{r}ブチギレ!?」「絶対に○す宣言」.
- Line 1 sets the situation, line 2 the hook. Colour 1–2 key words red `{r}` or magenta `{m}`;
  the rest yellow; occasional black `{k}` for contrast.
- Never promise something the clip doesn't show.

## Captions
Rewrite the transcript into telops — this is where most of the quality comes from.
- **Shorten**: 1 line ideally, 2 lines max (9 chars/line). Drop fillers (えーっと, なんか, あの).
  Keep the streamer's dialect and tone (〜やん, 〜ねん).
- **Fix** misrecognitions using context (names, game terms).
- **Timing**: keep `s`/`e` from the draft (aligned to the voice); merge or split lines as needed.
  Leave gaps when she is silent — do not caption game sounds.
- **Colour by emotion** (one style per line; vary it, but keep runs of the same mood consistent):
  | Style | Use for |
  |---|---|
  | `purple` | default speech, narration of what she is doing |
  | `red` | anger, shock, complaints, danger, failure (「痛った」「最悪」) |
  | `yellow` | excitement, hype, success (「今！」「よっしゃ」) |
  | `cyan` | questions, confusion, calm remarks (「どういうこと?」) |
  | `pink` | cute / playful lines, laughter |
  | `white` | reading in-game text, neutral asides |
  | `black` | dry punchline or deadpan statement (「魔術師が撃破」) |
  | `emph` + `pos: top` | short excited interjection over the game (「やばっ」「信じるよ?」) |
  | `shout` + `pos: center` | the 1–3 biggest reactions in the clip (「なに!?」「ダメじゃん!!」) ≤ 7 chars |
  | `shout_v` | cold-open line only |
  | `small` + `pos: top` | stage directions (～落下中～, ～3分後～) |
- Density: one caption per breath group; 20–35 captions per minute of speech is typical.

## Chapters and notes
- `chapter` on segments for attempt counters (一戦目, 二戦目 …) or time jumps (開始20分, 3分後).
- `note` for context a viewer needs: game terms, what just happened, rules
  (「金(ルーン)を拾い忘れています」「※メッセージには罠も多い」). Short, factual, 1–2 lines.
- `comment` only for real listener comments she reacts to (when chat is visible or she reads it).
  Never invent comments.

## Sound
Measured on the reference shorts: no background music; the stream audio is kept as is; a handful
of short effects (≈ 6 per minute) sit at speech level on the biggest moments. So:
- Leave the automatic effects on (shout → impact, emph → pop, note → kira, chapter change → whoosh).
- Add `"sfx": "boing"` on a comedic fail line, `"sfx": "pop"` on a punchline that isn't styled emph.
  Keep it sparse: more than ~8 effects per minute starts to feel cheap.
- Don't add BGM unless the user asks and supplies music they have rights to (`bgm` field).
- Users can replace the built-in effects by putting their own `pop.wav` / `impact.wav` /
  `whoosh.wav` / `kira.wav` / `boing.wav` in `~/.stream-shorts/sfx/`.

## Checks before rendering
- `edit_short.py show` → read every line once.
- After rendering, `edit_short.py sheet` and look at it: no telop over the face's eyes,
  shouts readable, title fits, chapter visible.
