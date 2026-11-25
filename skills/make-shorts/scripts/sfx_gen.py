"""Generate the built-in sound effects (procedural, so free of third-party rights).

Writes 48 kHz mono WAVs to ~/.stream-shorts/sfx/ (existing files are kept, so a user can drop in
their own pop.wav / impact.wav / whoosh.wav / kira.wav / boing.wav to replace them).

  python sfx_gen.py [--force]
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

from common import CONFIG_DIR

SR = 48000
SFX_DIR = CONFIG_DIR / "sfx"
rng = np.random.default_rng(7)


def _env(n, attack=0.003, decay=0.08):
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(t - attack, 0) / decay)


def _sweep(f0, f1, dur, curve=3.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-curve * t / dur)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _bandnoise(n, lo, hi):
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(X, n)
    return y / (np.abs(y).max() + 1e-9)


def pop():  # short bubbly "pon" for emphasis telops
    d = 0.12
    s = _sweep(1300, 420, d, 4) * _env(int(d * SR), 0.002, 0.035)
    s += 0.25 * _bandnoise(int(d * SR), 2000, 8000) * _env(int(d * SR), 0.001, 0.008)
    return s


def impact():  # "don" for shouts / cold open
    d = 0.55
    n = int(d * SR)
    body = _sweep(140, 48, d, 5) * _env(n, 0.002, 0.16)
    crack = _bandnoise(n, 150, 3500) * _env(n, 0.001, 0.035)
    sub = np.sin(2 * np.pi * 55 * np.arange(n) / SR) * _env(n, 0.004, 0.22)
    return 0.9 * body + 0.55 * crack + 0.5 * sub


def whoosh():  # swish for scene / chapter changes
    d = 0.38
    n = int(d * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    noise = rng.standard_normal(n)
    # moving band-pass via short overlapping FFT blocks
    blk, hop = 1024, 256
    win = np.hanning(blk)
    for i in range(0, n - blk, hop):
        c = 500 + 5500 * (i / n) ** 1.3
        X = np.fft.rfft(noise[i:i + blk] * win)
        f = np.fft.rfftfreq(blk, 1 / SR)
        X *= np.exp(-((f - c) / (c * 0.35)) ** 2)
        out[i:i + blk] += np.fft.irfft(X, blk) * win
    shape = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 1.5
    return out / (np.abs(out).max() + 1e-9) * shape


def kira():  # sparkle for call-out boxes
    d = 0.5
    n = int(d * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for k, (f, st) in enumerate([(1568, 0.0), (2093, 0.05), (2637, 0.10)]):
        m = t >= st
        s[m] += np.sin(2 * np.pi * f * (t[m] - st)) * np.exp(-(t[m] - st) / 0.12) * (0.8 - 0.15 * k)
    return s


def boing():  # comedic fail
    d = 0.45
    n = int(d * SR)
    t = np.arange(n) / SR
    f = 220 + 120 * np.sin(2 * np.pi * 9 * t) * np.exp(-t / 0.25)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.004, 0.18)


SOUNDS = {"pop": pop, "impact": impact, "whoosh": whoosh, "kira": kira, "boing": boing}
PEAK_DB = {"pop": -17, "impact": -14, "whoosh": -20, "kira": -21, "boing": -17}  # sits at speech level after -14 LUFS normalisation


def write(path: Path, x: np.ndarray, peak_db: float) -> None:
    x = x / (np.abs(x).max() + 1e-9) * 10 ** (peak_db / 20)
    fade = int(0.004 * SR)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def ensure_sfx(force: bool = False) -> dict:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, fn in SOUNDS.items():
        p = SFX_DIR / f"{name}.wav"
        if force or not p.exists():
            write(p, fn(), PEAK_DB[name])
        paths[name] = p
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    for k, v in ensure_sfx(ap.parse_args().force).items():
        print(k, v)
