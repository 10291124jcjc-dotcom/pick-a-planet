"""Pick-a-planet episode: episode.json -> narration -> Blender renders -> captions + music -> mp4.

  python make.py EP                 everything (voice, render, compose)
  python make.py EP --preview       half resolution, stills only, fewer samples (fast look)
  python make.py EP --voice         only synthesise narration + write timeline / job
  python make.py EP --render [--only p1s2,p2s1]   only (re)render shots in Blender
  python make.py EP --compose       only composite + mux from existing renders

EP is a folder holding episode.json. Outputs go to EP/build/ and EP/<folder name>.mp4 (+ _cover.jpg).
Voice: "edge" (free placeholder, edge-tts) or "volc" (your own cloned voice, Volcano Engine);
keys VOLC_API_KEY + VOLC_SPEAKER_ID come from the environment or a .env file (EP or any parent,
or ~/.pick-a-planet/.env). Never print or commit keys.
"""
import argparse
import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio as A  # noqa: E402

BLENDER_CANDIDATES = [
    os.environ.get("BLENDER", ""),
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "blender",
]
CN = "零一二三四五六七八九十"


def blender_path():
    for p in BLENDER_CANDIDATES:
        if p and (os.path.exists(p) or shutil.which(p)):
            return p
    raise SystemExit("x Blender not found. Install Blender 4.2+ (free, blender.org) or set BLENDER=path/to/blender")


# ---------------------------------------------------------------- voice
def env_lookup(ep):
    e = {}
    d = os.path.abspath(ep)
    paths = []
    while True:
        paths.append(os.path.join(d, ".env"))
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    paths.append(os.path.join(os.path.expanduser("~"), ".pick-a-planet", ".env"))
    for p in reversed(paths):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    e[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("VOLC_API_KEY", "VOLC_SPEAKER_ID", "VOLC_RESOURCE_ID", "VOLC_APP_ID", "VOLC_ACCESS_TOKEN"):
        if os.environ.get(k):
            e[k] = os.environ[k]
    return e


def synth_volc(text, e, speed=1.0):
    if not e.get("VOLC_SPEAKER_ID") or not (e.get("VOLC_API_KEY") or e.get("VOLC_APP_ID")):
        raise SystemExit("x Volcano keys missing (VOLC_API_KEY + VOLC_SPEAKER_ID). Use \"provider\": \"edge\" for a placeholder.")
    auth = {"X-Api-Key": e["VOLC_API_KEY"]} if e.get("VOLC_API_KEY") else \
        {"X-Api-App-Id": e["VOLC_APP_ID"], "X-Api-Access-Key": e["VOLC_ACCESS_TOKEN"]}
    headers = {**auth, "X-Api-Resource-Id": e.get("VOLC_RESOURCE_ID", "seed-icl-2.0"),
               "X-Api-Request-Id": str(uuid.uuid4()), "Content-Type": "application/json"}
    body = {"user": {"uid": "pick-a-planet"}, "req_params": {
        "text": text, "speaker": e["VOLC_SPEAKER_ID"],
        "audio_params": {"format": "mp3", "sample_rate": 24000, "speech_rate": int((speed - 1) * 100)}}}
    req = urllib.request.Request("https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                                 data=json.dumps(body).encode(), headers=headers, method="POST")
    content = urllib.request.urlopen(req, timeout=120).read()
    chunks = []
    for line in content.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        d = json.loads(line)
        if d.get("code", 0) not in (0, 20000000):
            raise SystemExit(f"x synthesis error code={d.get('code')}: {d.get('message')}")
        if d.get("data"):
            chunks.append(base64.b64decode(d["data"]))
    if not chunks:
        raise SystemExit("x no audio returned")
    return b"".join(chunks)


def synth_edge(text, voice, rate, out):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(out))


def to_wav(src, wav):
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
            "silenceremove=start_periods=1:start_threshold=-45dB,areverse")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", trim, "-ar", str(A.SR), "-ac", "1", wav], check=True)


class Voice:
    def __init__(self, ep, cfg, lang):
        self.dir = os.path.join(ep, "build", "voice")
        os.makedirs(self.dir, exist_ok=True)
        self.prov = cfg.get("provider", "edge")
        self.voice = cfg.get("edge_voice", "zh-CN-YunxiNeural" if lang == "zh" else "en-US-AndrewNeural")
        self.rate = cfg.get("rate", "-6%")
        self.speed = cfg.get("speed", 0.95)
        self.e = env_lookup(ep) if self.prov == "volc" else {}

    def get(self, text):
        """-> (wav path, duration seconds)"""
        h = hashlib.md5(f"{self.prov}|{self.voice}|{self.rate}|{self.speed}|{text}".encode()).hexdigest()[:10]
        mp3 = os.path.join(self.dir, f"{h}.mp3")
        wav = os.path.join(self.dir, f"{h}.wav")
        if not os.path.exists(wav):
            if self.prov == "volc":
                open(mp3, "wb").write(synth_volc(text, self.e, self.speed))
            else:
                synth_edge(text, self.voice, self.rate, mp3)
            to_wav(mp3, wav)
            print("  voice", text, flush=True)
        a = A.read_wav(wav)
        return wav, len(a) / A.SR


# ---------------------------------------------------------------- captions
PUNCT = "，。！？、；：,.!?;:"


def chunks_of(say, maxlen):
    """'a|b' -> explicit chunks; otherwise split after punctuation into pieces <= maxlen chars."""
    if "|" in say:
        return [c.strip() for c in say.split("|") if c.strip()]
    parts = re.findall(r"[^，。！？；,.!?;]+[，。！？；,.!?;]?", say)
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) > maxlen:
            out.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        out.append(cur)
    return out


def spoken(say):
    return say.replace("|", "")


def weight(s):
    return max(1, len([c for c in s if c not in PUNCT and not c.isspace()]))


# ---------------------------------------------------------------- timeline
def deep_merge(base, over):
    if over.get("replace"):  # this shot ignores the planet's set completely
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k == "props":
            out["props"] = out.get("props", []) + v
        elif isinstance(v, dict) and isinstance(out.get(k), dict) and k != "camera":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def build_timeline(ep, E, voice):
    lang = E.get("lang", "zh")
    maxc = E.get("caption_max", 11 if lang == "zh" else 26)
    segs, vparts, sfx, music = [], [], [], []
    shots, globes = [], []
    t = 0.0

    # intro: say + countdown
    intro = E.get("intro", {})
    say = intro.get("say", "选一颗星球。|你要在这里活一年。" if lang == "zh" else "Choose a planet.|You must survive one year here.")
    wav, d = voice.get(spoken(say))
    lead = 0.25
    caps = timed_caps(chunks_of(say, maxc), t + lead, d)
    vparts.append((t + lead, wav))
    cd = intro.get("countdown", 5)
    t_cd = t + lead + d + 0.35
    nums = []
    for k in range(cd):
        n = cd - k
        word = CN[n] if lang == "zh" else ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][n]
        w2, _ = voice.get(word)
        vparts.append((t_cd + k + 0.05, w2))
        sfx.append((t_cd + k, "tick_hi" if n == 1 else "tick"))
        nums.append({"n": n, "s": t_cd + k, "e": t_cd + k + 1})
    end = t_cd + cd
    caps[-1]["e"] = t_cd
    segs.append({"kind": "intro", "s": t, "e": end, "caps": caps, "countdown": nums})
    music.append((t, end, "space"))
    t = end

    for i, pl in enumerate(E["planets"]):
        n = i + 1
        gid = f"g{n}"
        globes.append({"id": gid, "style": pl.get("globe", {}), "frames": E.get("globe_frames", 120), "size": 720})
        # title card
        label = pl.get("label", f"{n} 号星球" if lang == "zh" else f"Planet {n}")
        tsay = pl.get("title_say", f"{CN[n]}号星球，{pl['name']}。" if lang == "zh" else f"Planet {n}, {pl['name']}.")
        wav, d = voice.get(spoken(tsay))
        dur = max(2.6, d + 0.9)
        sfx.append((t, "whoosh"))
        vparts.append((t + 0.35, wav))
        half = t + 0.35 + d * 0.42
        segs.append({"kind": "title", "s": t, "e": t + dur, "globe": gid,
                     "titles": [{"text": label, "s": t, "e": half}, {"text": pl["name"], "s": half, "e": t + dur}]})
        music.append((t, t + dur, "space"))
        t += dur
        base = pl.get("set", {})
        for j, sh in enumerate(pl["shots"]):
            sid = f"p{n}s{j + 1}"
            say = sh["say"]
            wav, d = voice.get(spoken(say))
            lead = sh.get("lead", 0.12)
            gap = sh.get("gap", 0.6 if j == len(pl["shots"]) - 1 else 0.3)
            dur = lead + d + gap
            scene = deep_merge(base, sh.get("scene", {}))
            vparts.append((t + lead, wav))
            caps = timed_caps(chunks_of(say, maxc), t + lead, d)
            caps[-1]["e"] = t + dur
            segs.append({"kind": "shot", "id": sid, "s": t, "e": t + dur, "caps": caps,
                         "push": sh.get("push", 0.07), "focus": sh.get("focus", [0.5, 0.5]),
                         "fx": sh.get("fx", scene.get("fx", [])), "anim": bool(scene.get("anim"))})
            mood = sh.get("mood", pl.get("mood", "calm"))
            music.append((t, t + dur, mood))
            shots.append({"id": sid, "scene": scene, "dur": dur, "fps": sh.get("fps", 15)})
            t += dur

    outro = E.get("outro", {})
    odur = outro.get("dur", 2.8)
    caps = []
    if outro.get("say"):
        wav, d = voice.get(spoken(outro["say"]))
        vparts.append((t + 0.2, wav))
        caps = timed_caps(chunks_of(outro["say"], maxc), t + 0.2, d)
        odur = max(odur, d + 1.2)
        caps[-1]["e"] = t + odur
    sfx.append((t + 0.1, "chime"))
    segs.append({"kind": "outro", "s": t, "e": t + odur, "caps": caps, "text": outro.get("text", E.get("handle", ""))})
    music.append((t, t + odur, "space"))
    t += odur
    return {"duration": round(t, 3), "segments": segs, "voice": vparts, "sfx": sfx, "music": music}, shots, globes


def timed_caps(chunks, t0, d):
    total = sum(weight(c) for c in chunks)
    out, cur = [], t0
    for c in chunks:
        dd = d * weight(c) / total
        out.append({"text": c, "s": round(cur, 3), "e": round(cur + dd, 3)})
        cur += dd
    out[0]["s"] = round(t0 - 0.1, 3)
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ep")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--voice", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--compose", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()
    ep = os.path.abspath(a.ep)
    E = json.load(open(os.path.join(ep, "episode.json"), encoding="utf-8"))
    build = os.path.join(ep, "build")
    os.makedirs(build, exist_ok=True)
    res = E.get("res", [1080, 1920])
    if a.preview:
        res = [res[0] // 2, res[1] // 2]
    voice = Voice(ep, E.get("voice", {}), E.get("lang", "zh"))
    tl, shots, globes = build_timeline(ep, E, voice)
    tl["res"] = res
    json.dump(tl, open(os.path.join(build, "timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    renders = os.path.join(build, "renders_preview" if a.preview else "renders")
    job = {"res": res, "samples": 16 if a.preview else E.get("samples", 48), "out": renders, "shots": shots,
           "globes": [dict(g, size=g["size"] // (2 if a.preview else 1)) for g in globes]}
    jp = os.path.join(build, "job.json")
    json.dump(job, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"timeline {tl['duration']:.1f}s, {len(shots)} shots, {len(globes)} planets")
    only_steps = a.voice or a.render or a.compose
    if a.voice:
        return
    if a.render or not only_steps:
        cmd = [blender_path(), "-b", "--factory-startup", "-P", os.path.join(HERE, "scene.py"), "--", jp]
        if a.only:
            cmd += ["--only", a.only]
        if a.preview:
            cmd += ["--still"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in r.stdout.splitlines():
            if line.startswith(("  still", "  anim", "  globe", "ok", "x ")) or "Error" in line or "Traceback" in line:
                print(line)
        if r.returncode != 0 or "Traceback" in r.stdout + r.stderr:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit("x Blender failed")
    if a.compose or not only_steps:
        import compose
        name = os.path.basename(ep.rstrip("\\/"))
        out = os.path.join(ep, f"{name}{'_preview' if a.preview else ''}.mp4")
        compose.run(tl, renders, build, out, E, preview=a.preview)


if __name__ == "__main__":
    main()
