#!/usr/bin/env python3
"""Original funky tech-house loop, 124 BPM, synthesized from scratch."""
import sys
import numpy as np
from scipy.signal import butter, sosfilt
from scipy.io import wavfile

SR = 44100
BPM = 124
BEAT = 60 / BPM
STEP = BEAT / 4
BARS = 37
TAIL = 2.2
N = int((BARS * 4 * BEAT + TAIL) * SR)
rng = np.random.default_rng(3)

def buf(): return np.zeros(N)
def at(t): return int(round(t * SR))
def lp(x, f, o=2): return sosfilt(butter(o, f, 'low', fs=SR, output='sos'), x)
def hp(x, f, o=2): return sosfilt(butter(o, f, 'high', fs=SR, output='sos'), x)
def bp(x, lo, hi, o=2): return sosfilt(butter(o, [lo, hi], 'band', fs=SR, output='sos'), x)
def add(dst, src, t, g=1.0):
    i = at(t); j = min(N, i + len(src))
    if i < N: dst[i:j] += src[:j - i] * g
def note(n): return 440 * 2 ** ((n - 69) / 12)

# ---------- instruments ----------
def kick():
    d = 0.42; t = np.arange(int(d * SR)) / SR
    f = 46 + 110 * np.exp(-t * 32)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 7.5)
    click = hp(rng.standard_normal(len(t)), 3000) * np.exp(-t * 300) * .35
    return np.tanh((body + click) * 1.6)

def clap():
    d = 0.35; t = np.arange(int(d * SR)) / SR
    n = rng.standard_normal(len(t))
    env = np.zeros_like(t)
    for o in (0, .011, .022):
        m = t >= o; env[m] += np.exp(-(t[m] - o) * (60 if o < .02 else 14))
    return bp(n, 900, 3200) * env * .9

def hat(open_=False):
    d = .28 if open_ else .06; t = np.arange(int(d * SR)) / SR
    metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (3140, 4440, 5380, 6800, 8120))
    x = hp(rng.standard_normal(len(t)) * .6 + metal * .12, 7000)
    return x * np.exp(-t * (9 if open_ else 70))

def shaker():
    d = .07; t = np.arange(int(d * SR)) / SR
    return bp(rng.standard_normal(len(t)), 5000, 11000) * np.sin(np.pi * t / d) ** 2 * .5

def rim():
    d = .08; t = np.arange(int(d * SR)) / SR
    return (np.sin(2 * np.pi * 1700 * t) + .5 * np.sin(2 * np.pi * 820 * t)) * np.exp(-t * 60) * .5

def bass(n, dur):
    t = np.arange(int(dur * SR)) / SR; f = note(n)
    saw = 2 * ((f * t) % 1) - 1
    sq = np.sign(np.sin(2 * np.pi * f * t))
    sub = np.sin(2 * np.pi * f * t)
    x = .55 * saw + .25 * sq
    # filter envelope (funky "wow") via crossfading two filtered versions
    env = np.exp(-t * 18)
    x = lp(x, 260) * (1 - env) + lp(x, 1400) * env
    x = np.tanh(2.2 * x) * .6 + sub * .55
    amp = np.minimum(1, t / .003) * np.minimum(1, (dur - t) / .01)
    return x * amp

def stab(notes, dur=.22):
    t = np.arange(int(dur * SR)) / SR
    x = sum(sum(2 * ((note(n) * (1 + dt) * t) % 1) - 1 for dt in (-.006, 0, .006)) for n in notes)
    x = lp(x / (3 * len(notes)), 2600) * np.exp(-t * 11)
    return x

# ---------- arrangement ----------
drums, bassb, music, fx = buf(), buf(), buf(), buf()
K, C, H, OH = kick(), clap(), hat(), hat(True)
SH, RIM = shaker(), rim()
DROP = 4  # bar where full groove starts
FILLS = (16, 29)  # bars with kick-less second half + mini riser
kick_times = []

for bar in range(BARS):
    b0 = bar * 4 * BEAT
    full = bar >= DROP
    outro = bar >= BARS - 1
    for s in range(16):
        t = b0 + s * STEP
        if full and s % 4 == 0 and bar < BARS - 1 and not (bar in FILLS and s >= 8):
            add(drums, K, t, .95); kick_times.append(t)
        if full and s in (4, 12) and bar < BARS - 1: add(drums, C, t, .55)
        if s % 4 == 2: add(drums, OH, t, .22 if full else .16)
        if s % 2 == 1: add(drums, H, t, .1 + .05 * (s % 4 == 3))
        if full and s % 4 == 0 and bar >= 4: add(drums, H, t, .05)
        if full: add(drums, SH, t + .006 * (s % 2), .1 + .08 * (s % 4 == 2))
        if full and s in (7, 10, 15) and bar % 2: add(drums, RIM, t, .35)

# funky bassline (A minor), (step, midi, length in steps)
BL = [[(0, 33, 2), (3, 33, 1), (6, 45, 1), (7, 33, 2), (10, 36, 1), (11, 33, 2), (14, 43, 1), (15, 45, 1)],
      [(0, 33, 2), (3, 33, 1), (6, 45, 1), (7, 33, 2), (10, 38, 2), (13, 40, 1), (14, 41, 1), (15, 43, 1)]]
BL.append([(0, 33, 1), (2, 45, 1), (3, 33, 1), (6, 43, 1), (7, 45, 1), (8, 33, 2), (11, 36, 1), (12, 38, 1), (14, 40, 2)])
for bar in range(DROP, BARS - 1):
    if bar in FILLS: pat = [x for x in BL[1] if x[0] < 8]
    elif 17 <= bar < 29: pat = BL[2] if bar % 2 else BL[0]
    else: pat = BL[bar % 2]
    for s, n, L in pat:
        add(bassb, bass(n, L * STEP * .92), bar * 4 * BEAT + s * STEP, .9)

# chord stabs Am9 / Dm9, syncopated + delay
CH = [[57, 60, 64, 67, 71], [62, 65, 69, 72, 76]]
for bar in range(BARS):
    if bar < 1 or bar == BARS - 1: continue
    ch = CH[(bar // 2) % 2] if bar >= DROP else CH[0]
    for s in ((3, 6, 11, 14) if bar >= DROP else (6, 14)):
        x = stab(ch)
        t = bar * 4 * BEAT + s * STEP
        for k, g in enumerate((.55, .28, .14)):
            add(music, x if k == 0 else lp(x, 1800 - 400 * k), t + k * 3 * STEP, g * (.7 if bar < DROP else 1))

# riser into drop + downlifter at end
rl = DROP * 4 * BEAT
t = np.arange(int(rl * SR)) / SR
noise = rng.standard_normal(len(t))
rise = np.zeros_like(t)
for k in range(8):
    seg = slice(k * len(t) // 8, (k + 1) * len(t) // 8)
    fc = 600 * 2 ** (k * .55)
    rise[seg] = bp(noise, fc, min(fc * 2.2, 18000))[seg]
add(fx, rise * (t / rl) ** 2 * .35, 0)
# snare roll building in bar 1
roll = [0, 2, 4, 6] + list(range(8, 16)) + [14.5, 15, 15.5]
for i, st_ in enumerate(roll):
    add(fx, C, (DROP - 1) * 4 * BEAT + st_ * STEP, .07 + i * .022)
# impact at drop
imp_t = np.arange(int(1.5 * SR)) / SR
impact = lp(rng.standard_normal(len(imp_t)), 3000) * np.exp(-imp_t * 3) * .4 + np.sin(2 * np.pi * 45 * imp_t) * np.exp(-imp_t * 4) * .6
add(fx, impact, rl)
# end hit
add(fx, impact, (BARS - 1) * 4 * BEAT, .9)
add(fx, stab(CH[0], 1.6) * 1.2, (BARS - 1) * 4 * BEAT)
# mini riser + impact around fill
ft = np.arange(int(2 * BEAT * SR)) / SR
for FILL in FILLS:
    add(fx, hp(rng.standard_normal(len(ft)), 2000) * (ft / ft[-1]) ** 2 * .25, FILL * 4 * BEAT + 2 * BEAT)
    add(fx, impact, (FILL + 1) * 4 * BEAT, .7)

# sidechain pump on bass + music
sc = np.ones(N)
for kt in kick_times:
    i = at(kt); L = int(BEAT * SR)
    e = 1 - .75 * np.exp(-np.arange(L) / SR * 11)
    j = min(N, i + L); sc[i:j] = np.minimum(sc[i:j], e[:j - i])

# simple reverb (stereo)
def reverb(x, secs=1.6, seed=0):
    r = np.random.default_rng(seed); L = int(secs * SR)
    ir = r.standard_normal(L) * np.exp(-np.arange(L) / SR * 4.5)
    ir = lp(ir, 5000)
    from scipy.signal import fftconvolve
    return fftconvolve(x, ir)[:N] * .03

mus_sc = music * sc
L = drums + bassb * sc * .9 + mus_sc + fx + reverb(mus_sc + fx * .5, seed=1) + reverb(drums * .3, 0.8, 5)
R = drums + bassb * sc * .9 + np.roll(mus_sc, int(.012 * SR)) + fx + reverb(mus_sc + fx * .5, seed=2) + reverb(drums * .3, 0.8, 6)
st = np.stack([L, R], 1)
st = hp(st.T, 28).T
st = np.tanh(st * 0.95)
st /= np.max(np.abs(st)) / .93
fade = np.ones(N); fl = int(.6 * SR); fade[-fl:] = np.linspace(1, 0, fl)
st *= fade[:, None]
wavfile.write(sys.argv[1], SR, (st * 32767).astype(np.int16))
print("dur", N / SR, "beat", BEAT)
