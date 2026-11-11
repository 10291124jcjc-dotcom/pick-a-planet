"""Music bed + sound effects + final mix, all synthesised with numpy (no audio files).

- Pad chords + a music-box arpeggio. Mood per segment: calm (major), dark (minor drone, no arpeggio,
  for the "but..." twist), sweet (brighter, for the lucky planet), space (title cards / intro).
- Ticks on the countdown, a soft whoosh into each planet title, a chime at the end.
- Music is ducked under the narration, then everything is normalised by ffmpeg to -14 LUFS.
"""
import math
import subprocess
import wave

import numpy as np

SR = 48000

NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def hz(name):
    n, o = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((NOTE[n] + 12 * (o + 1) - 69) / 12)


CHORDS = {
    "calm": [["C3", "E3", "G3", "B3"], ["A2", "C3", "E3", "G3"], ["F2", "A2", "C3", "E3"], ["G2", "B2", "D3", "F#3"]],
    "sweet": [["F3", "A3", "C4", "E4"], ["D3", "F3", "A3", "C4"], ["A#2", "D3", "F3", "A3"], ["C3", "E3", "G3", "D4"]],
    "dark": [["A2", "C3", "E3"], ["A2", "C3", "D#3"], ["F2", "A2", "C3"], ["E2", "G#2", "B2"]],
    "space": [["D3", "A3", "E4"], ["D3", "A3", "F#4"], ["C3", "G3", "D4"], ["D3", "A3", "E4"]],
}
BAR = 3.2  # seconds per chord


def read_wav(p):
    w = wave.open(p)
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    if w.getnchannels() == 2:
        a = a.reshape(-1, 2).mean(1)
    return a


def write_wav(p, a):
    w = wave.open(p, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    w.close()


def pad_layer(n, mood, t0=0.0):
    t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    prog = CHORDS[mood]
    for k in range(int(n / SR / BAR) + 2):
        a = int(k * BAR * SR)
        b = min(n, int((k + 1) * BAR * SR + 0.8 * SR))
        if a >= n:
            break
        tt = t[a:b] - t[a]
        env = np.minimum(1, tt / 0.9) * np.minimum(1, np.maximum(0, (b - a) / SR - tt) / 0.8)
        ch = prog[(k + int(t0 / BAR)) % len(prog)]
        for nm in ch:
            f = hz(nm)
            for det in (-0.12, 0.12):
                ff = f * 2 ** (det / 12 / 4)
                out[a:b] += (np.sin(2 * np.pi * ff * tt) * 0.6 + np.sin(4 * np.pi * ff * tt) * 0.12) * env * 0.05
    if mood == "dark":
        out += np.sin(2 * np.pi * hz("A1") * t) * 0.06 * (0.7 + 0.3 * np.sin(2 * np.pi * 0.15 * t))
    return out


def bell(f, dur=1.6, gain=0.08):
    t = np.arange(int(dur * SR)) / SR
    env = np.exp(-t * 3.2) * np.minimum(1, t / 0.004)
    return (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t * 6)) * env * gain


def arp_layer(n, mood, t0=0.0):
    out = np.zeros(n, np.float32)
    if mood in ("dark",):
        return out
    prog = CHORDS[mood]
    step = 0.4 if mood != "space" else 0.8
    pattern = [0, 2, 1, 3, 2, 1] if mood != "space" else [0, 2, 1]
    i = 0
    tt = 0.0
    while tt < n / SR:
        k = int((tt + t0) / BAR)
        ch = prog[k % len(prog)]
        nm = ch[pattern[i % len(pattern)] % len(ch)]
        f = hz(nm) * (4 if mood == "sweet" else 2)
        s = bell(f, gain=0.05 if mood != "space" else 0.035)
        a = int(tt * SR)
        m = min(len(s), n - a)
        if m > 0:
            out[a:a + m] += s[:m]
        tt += step
        i += 1
    return out


def music(segments, total):
    """segments: [(start, end, mood)] -> music bed with 0.6 s crossfades between moods."""
    n = int(total * SR) + SR
    out = np.zeros(n, np.float32)
    for s, e, mood in segments:
        a, b = max(0, int((s - 0.6) * SR)), min(n, int((e + 0.6) * SR))
        seg = pad_layer(b - a, mood, s) + arp_layer(b - a, mood, s)
        fade = np.ones(b - a, np.float32)
        f = int(0.6 * SR)
        fade[:f] = np.linspace(0, 1, f)
        fade[-f:] = np.linspace(1, 0, f)
        out[a:b] += seg * fade
    return out


def tick(gain=0.25, f=1800):
    t = np.arange(int(0.05 * SR)) / SR
    return np.sin(2 * np.pi * f * t) * np.exp(-t * 90) * gain


def whoosh(dur=0.9, gain=0.12):
    n = int(dur * SR)
    rnd = np.random.default_rng(3).standard_normal(n).astype(np.float32)
    # moving one-pole low-pass for a sweep
    out = np.zeros(n, np.float32)
    y = 0.0
    for i in range(n):
        u = i / n
        a = 0.02 + 0.25 * math.sin(math.pi * u)
        y += a * (rnd[i] - y)
        out[i] = y
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return out * env * gain * 3


def chime(gain=0.12):
    s = np.zeros(int(2.5 * SR), np.float32)
    for k, nm in enumerate(["E5", "G5", "C6"]):
        b = bell(hz(nm), dur=2.0, gain=gain)
        a = int(k * 0.12 * SR)
        s[a:a + len(b)] += b[:len(s) - a]
    return s


def mixdown(voice_parts, music_segments, sfx, total, out_wav):
    """voice_parts: [(t, samples)], sfx: [(t, name)] -> out_wav (before loudness normalisation)."""
    n = int(total * SR) + SR
    vo = np.zeros(n, np.float32)
    for t, a in voice_parts:
        i = int(t * SR)
        m = min(len(a), n - i)
        vo[i:i + m] += a[:m]
    mu = music(music_segments, total)[:n]
    # ducking: envelope of the voice
    env = np.abs(vo)
    k = int(0.25 * SR)
    env = np.convolve(env, np.ones(k) / k, mode="same")
    duck = 1 - 0.55 * np.clip(env / (env.max() + 1e-9) * 4, 0, 1)
    fx = np.zeros(n, np.float32)
    lib = {"tick": tick(), "tick_hi": tick(0.3, 2400), "whoosh": whoosh(), "chime": chime()}
    for t, name in sfx:
        s = lib[name]
        i = int(t * SR)
        m = min(len(s), n - i)
        if m > 0:
            fx[i:i + m] += s[:m]
    mix = vo * 1.0 + mu * duck * 0.9 + fx
    mix = mix[:int(total * SR)]
    peak = np.abs(mix).max() + 1e-9
    write_wav(out_wav, mix / peak * 0.89)


def loudnorm(src, dst, target=-14):
    """Two-pass loudnorm so the result really lands on the target."""
    import json as _json
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", src, "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = r.stderr
    m = _json.loads(txt[txt.rfind("{"):txt.rfind("}") + 1])
    af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af, "-ar", str(SR), "-ac", "2", dst], check=True)
