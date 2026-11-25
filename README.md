# stream-shorts

配信録画（ゲーム実況・雑談）から、YouTube ショート / X 向けの縦型ショート動画を作るプラグインです。
処理はすべて自分の PC 上（ffmpeg + faster-whisper）で行い、録画をどこかにアップロードすることはありません。投稿は手動です。

> **English summary** — A Claude plugin (skills) that turns long stream recordings (game / talk VODs)
> into vertical 9:16 shorts for YouTube Shorts and X: local Whisper transcription, highlight
> detection, official-clip-style layout (title band, game + face close-up), emotion-coloured
> telops edited by Claude, cold open, chapter labels, call-outs, multi-segment montage,
> click-free cuts, auto sound effects and -14 LUFS audio. Runs locally with ffmpeg + faster-whisper.

## インストール

**Claude Code**
```
/plugin marketplace add kousindjango/stream-shorts
/plugin install stream-shorts@stream-shorts
```

**Claude デスクトップアプリ**：[Releases](https://github.com/kousindjango/stream-shorts/releases) から `stream-shorts.plugin` をダウンロードし、チャットに添付してインストールします。

**必要なもの**：Python 3.10 以上、ffmpeg（libass 入り。Windows は `winget install Gyan.FFmpeg`）、`pip install faster-whisper numpy`。任意：`yt-dlp`（URL から作る場合）、`opencv-python>=4.8,<5`（顔追従）。NVIDIA GPU があると文字起こしが大幅に速くなります。導入は「ショート作成の環境を整えて」と頼めば案内されます。

## できること（v0.4）

- 上に色分けした2行タイトル帯、真ん中にゲーム画面、下に顔のアップ、という公式切り抜き風の構成
- 冒頭に、決めゼリフを縦書きで大きく出す掴み（顔を画面いっぱいに映す）
- 複数の場面をつないで60〜120秒にまとめる（一戦目・二戦目／開始20分・3分後 などの章ラベル付き）
- 字幕はClaudeが台本として直してから入れる（短く整える、誤字の修正、感情ごとの色分け、叫びは特大表示、補足の吹き出し）
- 字幕の出だしを実際の声に合わせる（音声区間検出で自動補正）
- 音（v0.3）：カット部分のクリック音を除去、声の大小を均す軽いコンプレッサー、叫び・強調・補足・章の切り替えに効果音を自動で付与（効果音はプログラムで生成しているので権利の心配なし。自分の効果音への差し替えも可）、BGMは任意（声の間だけ自動で下がる）

- v0.4（[AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator)（MIT）の考え方を取り入れて改良）
  - YouTube / Twitch の URL を渡すだけで開始（yt-dlp で自動ダウンロード。https の YouTube / Twitch 以外は拒否）
  - 見どころを8つの指標（掴み・感情のピーク・強い意見・意外な事実・対立・名言・話のオチ・役立つ情報）で0〜100点採点。冒頭に置くべき一言と、伸びる理由も添える
  - 候補の自動チェック：文の途中で切れないよう境界を補正、50%以上重なる候補は点数の低い方を除外
  - 長い配信の文字起こしは区切りを60秒重ねて読み、境目の見どころを取りこぼさない
  - 顔を自動で追いかける縦切り抜き（カメラ配信の顔／VTuberのアニメ顔）
  - 複数本をまとめて書き出し、結果を `results.json` に出力
  - 文字起こしやチャットの中の「指示のような文」には従わない（プロンプトインジェクション対策）

## 基本機能

1. 録画を文字起こしし、声や音が急に大きくなった箇所（盛り上がり）を検出
2. 話の内容と盛り上がりから、見どころ候補を 8〜12 件、理由つきで一覧に
3. 選んだ候補を書き出し
   - 縦型 1080×1920（中央切り抜き / 全体＋ぼかし背景 / ゲーム拡大 / 顔枠とゲームの上下分割）
   - 間延びした無音をカット（雑談は発話の間、ゲームは完全な無音のみ）
   - 太字＋縁取りの字幕を焼き込み、上部にフック（タイトル）
   - 音量を -14 LUFS にそろえる
4. YouTube 用タイトル・説明・ハッシュタグと、X 用の投稿文を作成

## スキル

| スキル | 使うとき | 話しかけ方の例 |
|---|---|---|
| make-shorts | ショートを作る本体 | 「この配信からショートを作って」「アーカイブの見どころを探して切り抜いて」 |
| shorts-setup | 最初に一度／不具合時 | 「ショート作成の環境を整えて」「Whisper が GPU で動かない」 |
| shorts-layout | 顔枠の位置などを登録・調整 | 「ゲーム実況用のレイアウトを登録して」「字幕をもう少し上に」 |

## はじめかた（Windows）

1. 「ショート作成の環境を整えて」と頼む → ffmpeg・faster-whisper・フォントを確認し、足りないものの導入を提案します。
2. ゲーム実況で顔枠を使う場合は「レイアウトを登録して」と頼み、録画を1本指定 → 顔枠の位置を決めてプレビューで確認します（OBS の変換の値が分かると正確です）。
3. 「D:\録画\○○.mp4 からショートを作って」または「https://www.youtube.com/watch?v=… からショートを3本作って」と頼む。

## 出力

録画と同じフォルダに `<録画名>_shorts/` ができます。

- `clips/c01.mp4` … 書き出したショート
- `clips/posts.md` … 投稿文
- `candidates.json` … 候補一覧（時間・フック・選択状態）
- `transcript.json`, `digest/`, `peaks.txt` … 文字起こしと盛り上がり分析

## 設定の保存場所

`%USERPROFILE%\.stream-shorts\`（`settings.json` と `layouts.json`）。環境変数 `STREAM_SHORTS_HOME` で変更できます。

## 注意

- YouTube ショートは 3 分まで、X の無料アカウントは 2 分 20 秒までです（書き出し時に超えると警告します）。
- ゲーム BGM などの著作権のある音楽が入ると、YouTube で 1 分を超えるショートはブロックされることがあります。
- コラボ相手の声や顔が入る場合は、相手の了承を取ってから投稿してください。
- 必要なもの: Python 3.10 以上、ffmpeg（libass 入り。`winget install Gyan.FFmpeg` で入るもので OK）、`pip install faster-whisper numpy`。NVIDIA GPU があると文字起こしが大幅に速くなります。

## 権利について

- 切り抜く配信は、自分の配信か、切り抜きガイドラインで許可されているものだけにしてください。クレジット表記や収益化の条件は各ガイドラインに従ってください。
- 字幕フォント（M PLUS Rounded 1c / M PLUS 1p / Yuji Syuku）は SIL Open Font License のフォントで、同梱せずセットアップ時に Google Fonts からダウンロードします。
- 効果音はプログラムで生成しているため、第三者の権利は含みません。BGM を使う場合は、権利を持っている曲を指定してください。

## 謝辞・参考

- [AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator)
  （Copyright (c) 2026 Anil Chandra Naidu Matcha, MIT License）— 見どころの採点基準、重複除去、長尺のチャンク重ね、顔追従クロップ、URL 検証の考え方を参考にしました（コードは本プラグイン用に書き直しています）。
- [lbpcascade_animeface](https://github.com/nagadomi/lbpcascade_animeface)（nagadomi, MIT）— VTuber 追従用のアニメ顔検出モデル。
- [YuNet / OpenCV Zoo](https://github.com/opencv/opencv_zoo)（MIT）— 実写の顔検出モデル。
  どちらも同梱せず、セットアップ時（`check_env.py --install-models`）に取得します。

## ライセンス

[MIT](LICENSE)
