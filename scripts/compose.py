"""2D compositing: renders + starfield + globes + pixel captions + particles -> frames -> ffmpeg (with the mix)."""
import glob
import math
import os
import subprocess
import zlib
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import audio as A

FPS = 30
PIXEL_FONT = "C:/Windows/Fonts/simsun.ttc"   # its 12 px bitmap strike gives the retro pixel look
FALLBACK_FONTS = ["C:/Windows/Fonts/msyh.ttc", "/System/Library/Fonts/PingFang.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]


def font(px):
    for p in [PIXEL_FONT] + FALLBACK_FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


# ---------------------------------------------------------------- pixel text
def wrap(text, maxc):
    if len(text) <= maxc:
        return [text]
    if " " in text:  # latin
        words, lines, cur = text.split(" "), [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > maxc:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        return lines + [cur]
    n = math.ceil(len(text) / maxc)
    size = math.ceil(len(text) / n)
    lines = [text[i:i + size] for i in range(0, len(text), size)]
    if len(lines) > 1 and len(lines[-1]) == 1 and lines[-1] in "，。！？、":
        lines[-2] += lines.pop()
    return lines


@lru_cache(maxsize=256)
def pixel_text(text, k, maxc=11, px=12, color=(255, 255, 255)):
    """Bitmap text, k x nearest-neighbour upscale, 1-px black outline -> RGBA numpy."""
    f = font(px)
    lines = wrap(text, maxc)
    w = max(int(f.getlength(l)) for l in lines) + 4
    lh = px + 3
    h = lh * len(lines) + 3
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    for i, l in enumerate(lines):
        lw = int(f.getlength(l))
        d.text(((w - lw) // 2, 1 + i * lh), l, font=f, fill=255)
    a = np.array(im) > 127
    o = a.copy()
    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]:
        o |= np.roll(np.roll(a, dy, 0), dx, 1)
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[o] = (0, 0, 0, 255)
    rgba[a] = (*color, 255)
    return np.array(Image.fromarray(rgba).resize((w * k, h * k), Image.NEAREST))


def paste(dst, rgba, cx, top, alpha=1.0, anchor="center"):
    h, w = rgba.shape[:2]
    x0 = int(cx - w / 2) if anchor == "center" else int(cx - w)
    y0 = int(top)
    H, W = dst.shape[:2]
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if xa >= xb or ya >= yb:
        return
    src = rgba[ya - y0:yb - y0, xa - x0:xb - x0]
    al = src[..., 3:4].astype(np.float32) / 255 * alpha
    dst[ya:yb, xa:xb] = (dst[ya:yb, xa:xb] * (1 - al) + src[..., :3] * al).astype(np.uint8)


# ---------------------------------------------------------------- backgrounds
def starfield(W, H, seed=7):
    rnd = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.float32)
    # faint nebula
    noise = cv2.resize(rnd.random((H // 60 + 2, W // 60 + 2)).astype(np.float32), (W, H), interpolation=cv2.INTER_CUBIC)
    img += np.stack([noise * 10, noise * 6, noise * 16], -1)
    pts = []
    for _ in range(int(W * H / 2500)):
        x, y = rnd.integers(0, W), rnd.integers(0, H)
        b = rnd.uniform(60, 255)
        r = 1 if rnd.random() < 0.85 else 2
        cv2.circle(img, (int(x), int(y)), r, (b, b, b * 0.95), -1)
        pts.append((x, y, b, rnd.uniform(0, 6.28)))
    return np.clip(img, 0, 255).astype(np.uint8), pts


def sun_glow(W, H, cx, cy, r):
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
    g = np.clip(1.6 - d, 0, None) ** 2 * 0.5 + np.exp(-d * d * 6) * 1.2
    return np.stack([g * 255, g * 240, g * 200], -1).astype(np.float32)


# ---------------------------------------------------------------- effects
class FX:
    def __init__(self, W, H, kind, seed=1):
        rnd = np.random.default_rng(seed)
        self.W, self.H, self.kind = W, H, kind
        n = {"fireflies": 140, "snow": 170, "rain": 220, "dust": 60}.get(kind, 0)
        self.p = rnd.random((n, 5)).astype(np.float32)

    def draw(self, img, t):
        W, H, p = self.W, self.H, self.p
        if self.kind == "fireflies":
            over = np.zeros_like(img, dtype=np.float32)
            for x, y, ph, sp, sz in p:
                px = (x + 0.03 * math.sin(t * (0.3 + sp) + ph * 6)) * W
                py = (0.3 + 0.7 * y + 0.02 * math.cos(t * (0.4 + sp) + ph * 5)) * H
                a = 0.5 + 0.5 * math.sin(t * (2 + sp * 3) + ph * 6.28)
                cv2.circle(over, (int(px), int(py)), int(2 + sz * 3 * W / 1080), (200 * a, 255 * a, 120 * a), -1)
            glow = cv2.GaussianBlur(over, (0, 0), 6 * W / 1080)
            return np.clip(img + over * 0.9 + glow * 1.8, 0, 255).astype(np.uint8)
        if self.kind == "snow":
            out = img.copy()
            for x, y, ph, sp, sz in p:
                py = ((y + t * (0.05 + sp * 0.06)) % 1.0) * H
                px = (x + 0.02 * math.sin(t + ph * 6)) * W
                cv2.circle(out, (int(px), int(py)), int(2 + sz * 4 * W / 1080), (245, 248, 255), -1, cv2.LINE_AA)
            return cv2.addWeighted(out, 0.8, img, 0.2, 0)
        if self.kind == "rain":
            out = img.copy()
            for x, y, ph, sp, sz in p:
                py = ((y + t * (0.9 + sp)) % 1.0) * H
                px = x * W
                cv2.line(out, (int(px), int(py)), (int(px - 6), int(py + 40 * W / 1080)), (200, 210, 230), 1, cv2.LINE_AA)
            return cv2.addWeighted(out, 0.6, img, 0.4, 0)
        if self.kind == "dust":
            out = img.astype(np.float32)
            for x, y, ph, sp, sz in p:
                px = (x + 0.02 * math.sin(t * 0.3 + ph * 6)) * W
                py = ((y - t * 0.01 * sp) % 1.0) * H
                cv2.circle(out, (int(px), int(py)), 2, (255, 240, 200), -1)
            return np.clip(out, 0, 255).astype(np.uint8)
        return img


def bloom(img, strength=0.55, thresh=200):
    f = img.astype(np.float32)
    small = cv2.resize(f, (img.shape[1] // 4, img.shape[0] // 4), interpolation=cv2.INTER_AREA)
    bright = np.clip(small - thresh, 0, None) * (255 / (255 - thresh))
    blur = cv2.GaussianBlur(bright, (0, 0), img.shape[1] / 90)
    blur = cv2.resize(blur, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    return np.clip(f + blur * strength, 0, 255).astype(np.uint8)


def vignette(W, H, k=0.28):
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    return (1 - k * np.clip(d - 0.55, 0, None) ** 1.6)[..., None].astype(np.float32)


def load_rgb(p, W, H):
    im = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    if im.shape[1] != W:
        im = cv2.resize(im, (W, H), interpolation=cv2.INTER_AREA)
    return im


@lru_cache(maxsize=600)
def globe_frame(path, size):
    im = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
    im = cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)
    return cv2.resize(im, (size, size), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- frame renderer
class Comp:
    def __init__(self, tl, renders, E, preview):
        self.tl, self.renders, self.E = tl, renders, E
        self.W, self.H = tl["res"]
        self.k = max(2, round(self.W / 1080 * 5))
        self.maxc = E.get("caption_max", 11 if E.get("lang", "zh") == "zh" else 26)
        self.stars, self.star_pts = starfield(self.W, self.H)
        self.vig = vignette(self.W, self.H)
        self.cache = {}
        self.fx = {}
        self.globes = {}
        for g in sorted(glob.glob(os.path.join(renders, "g*"))):
            if os.path.isdir(g):
                self.globes[os.path.basename(g)] = sorted(glob.glob(os.path.join(g, "f_*.png")))
        self.handle = E.get("handle", "")

    def space_bg(self, t, sun=None):
        img = self.stars.astype(np.float32)
        if sun is not None:
            img += sun
        return img

    def put_globe(self, img, gid, cx, cy, size, t, speed=4.0):
        frames = self.globes.get(gid)
        if not frames:
            return
        idx = int(t * speed) % len(frames)
        g = globe_frame(frames[idx], int(size))
        h, w = g.shape[:2]
        x0, y0 = int(cx - w / 2), int(cy - h / 2)
        H, W = img.shape[:2]
        xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
        src = g[ya - y0:yb - y0, xa - x0:xb - x0].astype(np.float32)
        al = src[..., 3:4] / 255
        img[ya:yb, xa:xb] = img[ya:yb, xa:xb] * (1 - al) + src[..., :3] * al

    def caption(self, img, caps, t, top=0.19):
        for c in caps:
            if c["s"] <= t < c["e"]:
                paste(img, pixel_text(c["text"], self.k, self.maxc), self.W / 2, self.H * top)

    def watermark(self, img):
        if self.handle:
            paste(img, pixel_text(self.handle, max(2, self.k // 2), 40), self.W * 0.95, self.H * 0.275, alpha=0.75, anchor="right")

    def shot_base(self, seg, t):
        sid = seg["id"]
        u = (t - seg["s"]) / max(1e-6, seg["e"] - seg["s"])
        d = os.path.join(self.renders, sid)
        if seg.get("anim") and os.path.isdir(d):
            frames = sorted(glob.glob(os.path.join(d, "f_*.png")))
            idx = min(len(frames) - 1, int((t - seg["s"]) * 15))
            key = (sid, idx)
            if key not in self.cache:
                self.cache = {k: v for k, v in self.cache.items() if k[0] == sid and abs(k[1] - idx) < 3}
                self.cache[key] = bloom(load_rgb(frames[idx], self.W, self.H))
            base = self.cache[key]
        else:
            key = (sid, 0)
            if key not in self.cache:
                p = os.path.join(self.renders, f"{sid}.png")
                if not os.path.exists(p):
                    alt = sorted(glob.glob(os.path.join(d, "f_*.png")))
                    p = alt[0] if alt else None
                self.cache = {k: v for k, v in self.cache.items() if k[0] == sid}
                self.cache[key] = bloom(load_rgb(p, self.W, self.H)) if p else np.zeros((self.H, self.W, 3), np.uint8)
            base = self.cache[key]
        s = 1 + seg.get("push", 0.07) * (u * u * (3 - 2 * u) * 0.6 + u * 0.4)
        fx_, fy_ = seg.get("focus", [0.5, 0.5])
        cx, cy = fx_ * self.W, fy_ * self.H
        M = np.float32([[s, 0, (1 - s) * cx], [0, s, (1 - s) * cy]])
        out = cv2.warpAffine(base, M, (self.W, self.H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        for kind in seg.get("fx", []):
            if (sid, kind) not in self.fx:
                self.fx[(sid, kind)] = FX(self.W, self.H, kind, seed=zlib.crc32(sid.encode()) % 1000)
            out = self.fx[(sid, kind)].draw(out, t)
        return out

    def frame(self, t):
        W, H = self.W, self.H
        seg = next((s for s in self.tl["segments"] if s["s"] <= t < s["e"]), self.tl["segments"][-1])
        kind = seg["kind"]
        if kind == "intro":
            if "sun" not in self.cache:
                self.cache["sun"] = sun_glow(W, H, W * 0.1, H * 0.29, W * 0.12)
            img = self.space_bg(t, self.cache["sun"])
            pos = [(0.29, 0.40), (0.71, 0.40), (0.29, 0.645), (0.71, 0.645)]
            n = len(self.globes)
            for i, (gx, gy) in enumerate(pos[:n]):
                self.put_globe(img, f"g{i + 1}", gx * W, gy * H, W * 0.33, t + i * 3)
                paste_f = img
                lab = pixel_text(f"{i + 1:02d}", max(2, self.k // 2), 6)
                tmp = np.clip(paste_f, 0, 255).astype(np.uint8)
                paste(tmp, lab, gx * W, gy * H + W * 0.19)
                img = tmp.astype(np.float32)
            img = np.clip(img, 0, 255).astype(np.uint8)
            cd = next((c for c in seg.get("countdown", []) if c["s"] <= t < c["e"]), None)
            if cd:
                paste(img, pixel_text(str(cd["n"]), int(self.k * 2.6), 4), W / 2, H * 0.12)
            else:
                self.caption(img, seg["caps"], t, top=0.13)
            self.watermark(img)
            return img
        if kind == "title":
            img = self.space_bg(t)
            u = (t - seg["s"]) / (seg["e"] - seg["s"])
            self.put_globe(img, seg["globe"], W / 2, H * 0.56, W * (0.62 + 0.04 * u), t)
            img = np.clip(img, 0, 255).astype(np.uint8)
            for ti in seg["titles"]:
                if ti["s"] <= t < ti["e"]:
                    paste(img, pixel_text(ti["text"], self.k, 20), W / 2, H * 0.19)
            fade = min(1, (t - seg["s"]) / 0.25)
            return (img * fade).astype(np.uint8)
        if kind == "shot":
            img = self.shot_base(seg, t)
            img = (img.astype(np.float32) * self.vig).astype(np.uint8)
            self.caption(img, seg["caps"], t)
            self.watermark(img)
            return img
        img = np.zeros((H, W, 3), np.uint8)  # outro
        self.caption(img, seg.get("caps", []), t, top=0.4)
        if seg.get("text"):
            paste(img, pixel_text(seg["text"], self.k, 30), W / 2, H * 0.48)
        return img


def run(tl, renders, build, out, E, preview=False):
    W, H = tl["res"]
    raw = os.path.join(build, "mix_raw.wav")
    mix = os.path.join(build, "mix.wav")
    vparts = [(t, A.read_wav(p)) for t, p in tl["voice"]]
    A.mixdown(vparts, tl["music"], tl["sfx"], tl["duration"], raw)
    A.loudnorm(raw, mix)
    comp = Comp(tl, renders, E, preview)
    n = int(math.ceil(tl["duration"] * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", mix, "-c:v", "libx264", "-preset", "veryfast" if preview else "medium",
           "-crf", "20" if preview else "17", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-shortest", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cover_t = 1.2
    for f in range(n):
        t = f / FPS
        img = comp.frame(t)
        if abs(t - cover_t) < 0.5 / FPS:
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
            buf.tofile(out.replace(".mp4", "_cover.jpg"))
        p.stdin.write(img.tobytes())
        if f % (FPS * 10) == 0:
            print(f"  compose {t:5.1f}/{tl['duration']:.1f}s", flush=True)
    p.stdin.close()
    p.wait()
    print(f"ok  {out}")
