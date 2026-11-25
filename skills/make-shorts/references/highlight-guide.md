# Highlight selection guide

Use this rubric when reading `peaks.txt` and `digest/chunk_*.txt`.

## Signals in the digest
- `▲+NdB` — the line is N dB louder than the median of the surrounding 2 minutes. +6 dB or more is a
  clear spike (shout, laugh, game explosion). Spikes are leads, not verdicts: read the lines before
  and after to see whether there is a story.
- `（発話なし・音の盛り上がり）` — loud moment with no speech. In game streams this is often a
  boss kill, jump scare or clutch play. Include it only if the lines just before/after give enough
  context (a reaction, a setup line).
- `（無発話 N秒）` — long quiet stretch; usually loading, menus or BGM. Avoid starting a clip here.
- Dense runs of short lines with `！` `？` `www` `笑` `やば` `うそ` `まじ` `えっ` — rapid reactions.

## What makes a good short (score each 1–5, report the total as 推し度 ★1–5)
1. **Hook in the first 3 s** — the clip opens on a surprising line, a question, or right before the
   payoff. If the best line is in the middle, start closer to it and let the hook text set context.
2. **Self-contained** — understandable without the rest of the stream. Inside jokes and references
   to "さっきの" need a hook text that explains them, otherwise drop the candidate.
3. **Payoff** — a punchline, a reveal, a fail/success, a strong opinion, or a useful tip.
4. **Emotion** — laughter, shock, frustration, excitement (loudness spikes help here).
5. **Fits 30–60 s after cutting** — one setup → one payoff. Two unrelated topics in one clip lose
   viewers; split them into two candidates instead.

## Game streams
- Good: clutch plays, fails with a big reaction, boss kills, jump scares, funny bugs, "まさか" moments,
  first-time reactions.
- Prefer `game-split` when the face/avatar reaction matters, `game-zoom` when the screen action
  carries the clip. Default cut mode `audio` keeps action without talking.
- Flag story spoilers (cutscenes, endings) and ask before including them.
- Game BGM or licensed music can trigger Content ID claims; on YouTube, a Short that matches
  copyrighted music is generally limited to 60 s or may be blocked. Prefer clips under 60 s when
  BGM is loud, and mention the risk.

## Talk streams
- Good: a story with a punchline, a hot take, a surprising fact, a clear explanation of one thing,
  a heartfelt moment, a reply to a chat question that stands alone.
- Use `talk-fit` by default; `talk-center` when the speaker sits in the center and the screen has
  nothing else worth showing. Cut mode `speech` removes pauses between sentences.

## Avoid
- Personal information (addresses, real names of non-public people, phone screens).
- Other people's voices or faces (guests, collab partners) unless the user confirms permission.
- Moments that only make sense with chat on screen, unless the streamer reads the comment aloud.
- Starting inside a sentence or ending before the reaction finishes.

## Boundary rules
- Start: 0.3 s before the first word of the setup sentence (`segments[].start - 0.3`).
- End: 0.5–1.0 s after the last word of the payoff/reaction, longer if laughter continues.
- Check `transcript.json` segments around both boundaries; adjust to whole sentences.
