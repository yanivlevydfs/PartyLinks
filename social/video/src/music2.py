#!/usr/bin/env python3
"""Original tech-house track v2 (124 BPM, A minor). BARS / FILLS / DROP via env. Stereo, cleaner mix."""
import os, sys
import numpy as np
from scipy.signal import butter, sosfilt, fftconvolve
from scipy.io import wavfile

SR = 44100
BPM = 124
BEAT = 60 / BPM
STEP = BEAT / 4
SWING = 0.11 * STEP                      # late odd 16ths
BARS = int(os.environ.get("BARS", 38))
FILLS = tuple(int(x) for x in os.environ.get("FILLS", "16,30").split(","))
DROP = int(os.environ.get("DROP", 4))
TAIL = 2.4
N = int((BARS * 4 * BEAT + TAIL) * SR)
rng = np.random.default_rng(11)

def z(): return np.zeros((N, 2))
def at(t): return int(round(t * SR))
def flt(x, kind, f, o=2):
    return sosfilt(butter(o, f, kind, fs=SR, output='sos'), x, axis=0)
def pan(x, p):  # p -1..1
    l = np.cos((p + 1) * np.pi / 4); r = np.sin((p + 1) * np.pi / 4)
    return np.stack([x * l * 1.414, x * r * 1.414], 1)
def put(dst, src, t, g=1.0):
    if src.ndim == 1: src = pan(src, 0)
    i = at(t); j = min(N, i + len(src))
    if 0 <= i < N: dst[i:j] += src[:j - i] * g
def note(n): return 440 * 2 ** ((n - 69) / 12)
def tt(d): return np.arange(int(d * SR)) / SR
def step_t(bar, s): return bar * 4 * BEAT + s * STEP + (SWING if s % 2 else 0)

# ---------------- sounds ----------------
def kick():
    t = tt(.45)
    f = 48 + 135 * np.exp(-t * 38)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6.5)
    click = flt(rng.standard_normal(len(t)), 'high', 2500) * np.exp(-t * 400) * .25
    return np.tanh(1.25 * (body + click)) * .95

def clap():
    t = tt(.45); n = rng.standard_normal(len(t)); env = np.zeros_like(t)
    for k, o in enumerate((0, .009, .018, .027)):
        m = t >= o; env[m] += np.exp(-(t[m] - o) * (90 if k < 3 else 16))
    x = flt(n, 'band', [1000, 3500]) * env
    x += flt(n, 'band', [400, 900]) * np.exp(-t * 40) * .4
    return x * .8

def hat(d=.05, dec=85, hpf=8000):
    t = tt(d)
    metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (3140, 4440, 5380, 6800, 8120, 9320))
    return flt(rng.standard_normal(len(t)) * .5 + metal * .1, 'high', hpf) * np.exp(-t * dec)

def shaker():
    t = tt(.08); d = .08
    return flt(rng.standard_normal(len(t)), 'band', [6000, 12000]) * np.sin(np.pi * t / d) ** 3 * .5

def bongo(f0):
    t = tt(.22); f = f0 * (1 + .25 * np.exp(-t * 60))
    return (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 16) + flt(rng.standard_normal(len(t)), 'band', [f0 * 2, f0 * 6]) * np.exp(-t * 80) * .2) * .7

def rim():
    t = tt(.07)
    return (np.sin(2 * np.pi * 1750 * t) + .6 * np.sin(2 * np.pi * 830 * t)) * np.exp(-t * 70) * .4

def bass(n, dur):
    t = tt(dur); f = note(n)
    sub = np.sin(2 * np.pi * f * t)
    saw = 2 * ((f * t) % 1) - 1
    env = np.exp(-t * 22)
    mid = flt(saw, 'low', 300) * (1 - env) + flt(saw, 'low', 1600) * env
    x = sub * .85 + np.tanh(1.8 * mid) * .38
    a = np.minimum(1, t / .004) * np.minimum(1, (dur - t) / .012)
    return x * a

def stab(notes, dur=.24, cut=2800):
    t = tt(dur)
    x = sum(sum(2 * ((note(n) * (1 + dt) * t) % 1) - 1 for dt in (-.007, 0, .007)) for n in notes)
    x = flt(x / (3 * len(notes)), 'low', cut) * np.exp(-t * 10) * np.minimum(1, t / .003)
    return x

def pad(notes, dur):
    t = tt(dur)
    x = sum(sum(np.sin(2 * np.pi * note(n) * (1 + dt) * t + dt * 900) for dt in (-.004, .004)) for n in notes)
    a = np.minimum(1, t / 1.2) * np.minimum(1, (dur - t) / .8)
    return flt(x / (2 * len(notes)), 'low', 2200) * a

# ---------------- arrange ----------------
drums, perc, bassb, music, fx = z(), z(), z(), z(), z()
K, C, OH, SH, RIM = kick(), clap(), hat(.3, 9, 7000), shaker(), rim()
HC = hat()
BH, BL_ = bongo(330), bongo(220)
kick_times = []
LAST = BARS - 1
HAT_VEL = [.55, .25, .7, .3, .5, .25, .75, .35, .55, .25, .7, .3, .5, .28, .8, .4]
BONGO = {3: (BH, -.5), 6: (BL_, .5), 10: (BH, -.5), 11: (BL_, .5), 14: (BH, -.4)}

for bar in range(BARS):
    full = DROP <= bar < LAST
    fill = bar in FILLS
    for s in range(16):
        t = step_t(bar, s)
        tk = bar * 4 * BEAT + s * STEP
        if full and s % 4 == 0 and not (fill and s >= 8):
            put(drums, K, tk, .8); kick_times.append(tk)
        if full and s in (4, 12) and not (fill and s >= 8): put(drums, C, tk, .8)
        if bar >= 1 and bar < LAST:
            put(drums, pan(HC, .25 if s % 2 else -.15), t, .38 * HAT_VEL[s] * (1 if full else .7))
            if s % 4 == 2: put(drums, pan(OH, .1), t, .3 if full else .16)
        if full and bar >= DROP + 2:
            put(perc, pan(SH, -.35), t, .2 + .1 * (s % 4 == 2))
            if s in BONGO and not fill:
                b, p = BONGO[s]; put(perc, pan(b, p), t, .55)
            if s in (7, 15) and bar % 2: put(perc, pan(RIM, .3), t, .3)

# rolling tech-house bassline (offbeat 16ths), 2 variations
P1 = [(2, 33), (3, 33), (6, 45), (7, 33), (10, 33), (11, 36), (14, 33), (15, 43)]
P2 = [(2, 33), (3, 45), (6, 33), (7, 40), (10, 38), (11, 38), (13, 40), (14, 41), (15, 43)]
for bar in range(DROP, LAST):
    pat = P2 if bar % 4 == 3 else P1
    if bar in FILLS: pat = [x for x in pat if x[0] < 8]
    for s, n in pat:
        put(bassb, bass(n, STEP * .9), step_t(bar, s), .62)

# chord stabs with stereo ping-pong delay
CH = [[57, 60, 64, 67, 71], [62, 65, 69, 72, 76], [60, 64, 67, 71, 74], [55, 59, 62, 65, 69]]
for bar in range(1, LAST):
    ch = CH[(bar // 2) % 4] if bar >= DROP else CH[0]
    steps = (3, 6, 11, 14) if bar >= DROP else (6, 14)
    cut = 1200 + 1800 * min(1, bar / DROP) if bar < DROP else 2800
    for s in steps:
        x = stab(ch, cut=cut); t0 = step_t(bar, s); g = .85 if bar >= DROP else .55
        put(music, pan(x, 0), t0, g)
        put(music, pan(flt(x, 'low', 1800), -.8), t0 + 3 * STEP, g * .45)
        put(music, pan(flt(x, 'low', 1300), .8), t0 + 6 * STEP, g * .25)

# pads: intro + fills + outro
for b0, L in [(0, DROP)] + [(f, 1) for f in FILLS] + [(LAST, 1)]:
    put(music, pan(pad(CH[0], L * 4 * BEAT + .8), 0), b0 * 4 * BEAT, .4)

# FX
def riser(dur):
    t = tt(dur); n = rng.standard_normal(len(t)); out = np.zeros_like(t); K_ = 10
    for k in range(K_):
        seg = slice(k * len(t) // K_, (k + 1) * len(t) // K_); fc = 500 * 2 ** (k * .5)
        out[seg] = flt(n, 'band', [fc, min(fc * 2, 19000)])[seg]
    return out * (t / dur) ** 2
def impact(g=1):
    t = tt(1.8)
    return (flt(rng.standard_normal(len(t)), 'low', 2500) * np.exp(-t * 3) * .35 + np.sin(2 * np.pi * 42 * t) * np.exp(-t * 3.5) * .65) * g

put(fx, pan(riser(2 * 4 * BEAT), 0), (DROP - 2) * 4 * BEAT, .32)
for i, s in enumerate([0, 2, 4, 6] + list(range(8, 16)) + [14.5, 15, 15.5]):
    put(fx, C, (DROP - 1) * 4 * BEAT + s * STEP, .05 + i * .018)
put(fx, impact(), DROP * 4 * BEAT)
for f in FILLS:
    put(fx, pan(riser(2 * BEAT), 0), f * 4 * BEAT + 2 * BEAT, .22)
    put(fx, impact(.75), (f + 1) * 4 * BEAT)
put(fx, impact(), LAST * 4 * BEAT)
put(music, pan(stab(CH[0], 1.8, 3200), 0), LAST * 4 * BEAT, .6)

# sidechain
sc = np.ones(N)
for kt in kick_times:
    i = at(kt); L = int(BEAT * SR); e = 1 - .7 * np.exp(-np.arange(L) / SR * 10)
    j = min(N, i + L); sc[i:j] = np.minimum(sc[i:j], e[:j - i])
sc2 = sc[:, None]

def verb(x, secs, seed, g):
    r = np.random.default_rng(seed); L = int(secs * SR)
    ir = r.standard_normal((L, 2)) * np.exp(-np.arange(L) / SR * 4)[:, None]
    ir = flt(ir, 'low', 6000)
    return np.stack([fftconvolve(x[:, k], ir[:, k])[:N] for k in (0, 1)], 1) * g

mix = drums + perc + bassb * sc2 + music * sc2 + fx
mix += verb((music * sc2) + C.mean() * 0 + fx * .4, 1.8, 3, .025) + verb(drums * .5 + perc, .9, 5, .02)
mix = flt(mix, 'high', 30)
mix = mix + .7 * flt(mix, 'high', 4500) + .25 * flt(mix, 'band', [900, 3500])  # presence + air
# gentle bus glue + soft-knee limiter
mix /= np.max(np.abs(mix)) + 1e-9
mix *= 1.6
mix = mix / (1 + np.abs(mix) ** 3) ** (1 / 3)
mix /= np.max(np.abs(mix)) / .9
fl = int(.8 * SR); mix[-fl:] *= np.linspace(1, 0, fl)[:, None]
wavfile.write(sys.argv[1], SR, (mix * 32767).astype(np.int16))
rms = np.sqrt(np.mean(mix ** 2)); print('dur', N / SR, 'rms dBFS', round(20 * np.log10(rms), 1))
