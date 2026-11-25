---
name: shorts-setup
description: >
  This skill should be used when the user wants to set up or check their PC for making short videos
  from stream recordings, or when the make-shorts skill reports missing ffmpeg, faster-whisper,
  GPU/CUDA problems or a missing subtitle font. Trigger phrases include 「ショート作成の環境を整えて」
  「ffmpegを入れて」「Whisperが動かない」「GPUで文字起こししたい」「字幕のフォントを変えたい」
  「環境チェック」 "set up stream-shorts", "check my environment".
metadata:
  version: "0.1.0"
---

# ショート作成の環境チェック・セットアップ

Check and fix the local tools the make-shorts skill needs: Python 3.10+, ffmpeg with libass,
faster-whisper (GPU if available), and a bold Japanese font. Save the best settings to
`~/.stream-shorts/settings.json`.

The check script is `scripts/check_env.py` under this skill's base directory.

## Steps

1. Run the check and read the JSON report and the summary line:
   ```
   python "<base>/scripts/check_env.py"
   ```
   If `python` is not found on Windows, try `py`. If neither exists, Python must be installed first
   (step 2).

2. For each problem, explain it in one plain sentence and propose the fix. Installing software changes
   the user's PC, so get a yes before running any install command. Windows commands:

   | Problem | Fix |
   |---|---|
   | Python missing | `winget install -e --id Python.Python.3.12` (then open a new terminal) |
   | ffmpeg missing, or `libass: false` | `winget install -e --id Gyan.FFmpeg` (full build, includes libass and NVENC). Open a new terminal afterwards so PATH updates. |
   | numpy / faster-whisper missing | `python -m pip install -U faster-whisper numpy` |
   | NVIDIA GPU present but `cuda_devices: 0` or the CUDA test fails | `python -m pip install -U nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` — the scripts add these DLL folders automatically. Also make sure the NVIDIA driver is recent. |
   | No Japanese bold font | BIZ UDPGothic ships with Windows 10/11 and works. For a bolder look, install Noto Sans JP from Google Fonts (fonts.google.com → "Noto Sans JP" → Get font → Download, then right-click the .ttf files → Install for all users). |

   macOS: `brew install ffmpeg` and `python3 -m pip install -U faster-whisper numpy`. Apple GPUs are not
   used by faster-whisper; the CPU path with `medium` or `small` is used instead.

3. Re-run the check with the transcription test (downloads the small "tiny" model once, ~75 MB):
   ```
   python "<base>/scripts/check_env.py" --test-whisper
   ```
   `cuda_test: ok` means GPU transcription works.

4. Install the telop fonts used by the official-style renderer (free SIL OFL fonts from Google
   Fonts, downloaded into `~/.stream-shorts/fonts`, no system install needed):
   ```
   python "<base>/scripts/check_env.py" --install-fonts
   ```
   M PLUS Rounded 1c ExtraBold (captions), M PLUS 1p Black (titles), Yuji Syuku (chapter labels).

5. Save the recommended settings:
   ```
   python "<base>/scripts/check_env.py" --write
   ```
   This picks: Whisper `large-v3-turbo` on GPU or `medium` on CPU, a hardware video encoder when one
   actually works (NVENC / Quick Sync / AMF) otherwise libx264, and the best installed font.

   For a bolder, rounder look common in VTuber clip channels, download a free OFL font from
   Google Fonts into `~/.stream-shorts/fonts` (e.g. `MPLUSRounded1c-ExtraBold.ttf` from
   `https://github.com/google/fonts/raw/main/ofl/mplusrounded1c/`), then set
   `"font": "M PLUS Rounded 1c ExtraBold", "font_bold": false`. Compare candidates first with
   `render_short.py --preview-frame ... --font "<name>" --no-bold --preview-text "<line>"`.

6. Tell the user in two or three lines what is ready, which model/encoder/font was chosen, and that
   the first real transcription downloads the Whisper model (about 1.5 GB) into the Hugging Face
   cache.

## Manual settings
`~/.stream-shorts/settings.json` can be edited directly when the user asks. Keys:

| Key | Default | Meaning |
|---|---|---|
| `whisper_model` | auto | `large-v3-turbo`, `large-v3`, `medium`, `small` |
| `whisper_device` | auto | `cuda` / `cpu` |
| `whisper_compute_type` | auto | `float16` (GPU), `int8` (CPU) |
| `language` | ja | Whisper language |
| `font` | Noto Sans JP | Subtitle and hook font family name |
| `font_bold` | true | Synthesize bold. Set `false` for fonts that are already heavy (ExtraBold/Black) |
| `fonts_dir` | `~/.stream-shorts/fonts` | Private font folder passed to the subtitle renderer; fonts here need no system install |
| `encoder` | libx264 | `h264_nvenc`, `h264_qsv`, `h264_amf`, `libx264` |
| `crf` | 20 | Quality (lower = better, larger) |
| `preset` | veryfast | libx264 preset for normal quality (`--quality high` uses slow) |
| `fps` | 30 | Output frame rate |
| `loudness_lufs` | -14 | Loudness target |
| `default_layout` | (none) | Layout used when a clip does not name one |
| `official_layout` | (none) | Base layout for the official-style renderer (`edit_short.py`) |

Set `STREAM_SHORTS_HOME` to keep settings and layouts somewhere other than `~/.stream-shorts`.
