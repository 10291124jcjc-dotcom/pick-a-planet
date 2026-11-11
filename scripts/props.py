"""Low-poly prop library, runs inside Blender (bpy + bmesh).

Every prop is built from primitives into ONE mesh with flat-colour materials, cached by its
parameters, and placed as linked copies (cheap even for hundreds of flowers or trees).
Local convention: origin at the base centre on the ground (z = 0), the front faces -Y.

Add a prop: write a function `p_<name>(b, **params)` that draws into the builder `b`,
return the height (metres, used by the camera to frame it). See docs/scene-vocab.md.
"""
import math
import random

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

CJK_FONT = "C:/Windows/Fonts/msyhbd.ttc"


# ---------------------------------------------------------------- materials
def hex2lin(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (f(r), f(g), f(b))


_MAT = {}


def M(col, emit=0.0, rough=0.85, alpha=1.0, metal=0.0):
    key = (col, round(emit, 3), rough, alpha, metal)
    if key in _MAT:
        return _MAT[key]
    m = bpy.data.materials.new(f"m{len(_MAT)}_{col}")
    try:
        m.use_nodes = True
    except Exception:
        pass
    b = m.node_tree.nodes.get("Principled BSDF")
    c = hex2lin(col)
    b.inputs["Base Color"].default_value = (*c, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = (*c, 1)
        b.inputs["Emission Strength"].default_value = emit
    if alpha < 1:
        b.inputs["Alpha"].default_value = alpha
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try:
                setattr(m, attr, val)
                break
            except Exception:
                pass
    m.diffuse_color = (*c, 1)
    _MAT[key] = m
    return m


# ---------------------------------------------------------------- builder
def _mx(loc, rot=(0, 0, 0), scale=(1, 1, 1)):
    return Matrix.LocRotScale(Vector(loc), Euler([math.radians(a) for a in rot]).to_quaternion(), Vector(scale))


class B:
    """Accumulates primitives into one bmesh. Sizes in metres, rotations in degrees."""

    def __init__(self):
        self.bm = bmesh.new()
        self.mats = []

    def _paint(self, verts, mat):
        if mat not in self.mats:
            self.mats.append(mat)
        mi = self.mats.index(mat)
        for f in {f for v in verts for f in v.link_faces}:
            f.material_index = mi

    def box(self, size, loc, col, rot=(0, 0, 0), **mk):
        r = bmesh.ops.create_cube(self.bm, size=1.0, matrix=_mx(loc, rot, size))
        self._paint(r["verts"], M(col, **mk))

    def cyl(self, r1, h, loc, col, r2=None, seg=10, rot=(0, 0, 0), **mk):
        """Cylinder / cone standing on loc (loc = centre of the bottom cap)."""
        r2 = r1 if r2 is None else r2
        m = _mx(loc, rot) @ Matrix.Translation((0, 0, h / 2))
        res = bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=seg,
                                    radius1=r1, radius2=r2, depth=h, matrix=m)
        self._paint(res["verts"], M(col, **mk))

    def ball(self, r, loc, col, scale=(1, 1, 1), seg=10, ring=7, rot=(0, 0, 0), **mk):
        res = bmesh.ops.create_uvsphere(self.bm, u_segments=seg, v_segments=ring, radius=r,
                                        matrix=_mx(loc, rot, scale))
        self._paint(res["verts"], M(col, **mk))

    def ico(self, r, loc, col, scale=(1, 1, 1), sub=1, **mk):
        res = bmesh.ops.create_icosphere(self.bm, subdivisions=sub, radius=r, matrix=_mx(loc, (0, 0, 0), scale))
        self._paint(res["verts"], M(col, **mk))

    def prism(self, w, d, h, loc, col, rot=(0, 0, 0), **mk):
        """Gable roof: triangle profile along X (width w), depth d, ridge height h, base at loc."""
        bm = self.bm
        m = _mx(loc, rot)
        pts = [(-w / 2, -d / 2, 0), (w / 2, -d / 2, 0), (0, -d / 2, h), (-w / 2, d / 2, 0), (w / 2, d / 2, 0), (0, d / 2, h)]
        vs = [bm.verts.new(m @ Vector(p)) for p in pts]
        for idx in ((0, 1, 2), (5, 4, 3), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)):
            bm.faces.new([vs[i] for i in idx])
        self._paint(vs, M(col, **mk))

    def disc(self, r, loc, col, seg=24, **mk):
        res = bmesh.ops.create_circle(self.bm, cap_ends=True, segments=seg, radius=r, matrix=_mx(loc))
        self._paint(res["verts"], M(col, **mk))

    def grid(self, sx, sy, loc, col, segs=1, bumps=0.0, seed=0, **mk):
        res = bmesh.ops.create_grid(self.bm, x_segments=segs, y_segments=segs, size=0.5, matrix=_mx(loc, (0, 0, 0), (sx, sy, 1)))
        if bumps:
            rnd = random.Random(seed)
            for v in res["verts"]:
                d = math.hypot(v.co.x, v.co.y)
                v.co.z += rnd.uniform(0, bumps) * min(1, d / 12)
        self._paint(res["verts"], M(col, **mk))

    def mesh(self, name):
        me = bpy.data.meshes.new(name)
        bmesh.ops.recalc_face_normals(self.bm, faces=self.bm.faces[:])
        self.bm.to_mesh(me)
        self.bm.free()
        for m in self.mats:
            me.materials.append(m)
        return me


# ---------------------------------------------------------------- props
# Each returns the prop height. `glow` = windows / bulbs light up (night scenes).
def p_house(b, wall="#e9e2f0", roof="#5b4b6e", trim="#f7f4fa", porch="#6b3b2b", glow=False, snow=False,
            w=6, d=4.5, h=3.2, porch_on=True, chimney=True, **_):
    b.box((w, d, h), (0, 0, h / 2), wall)
    b.box((w + 0.1, d + 0.1, 0.18), (0, 0, h), trim)
    b.prism(w + 0.7, d + 0.7, 2.0, (0, 0, h + 0.09), "#f3f6fb" if snow else roof)
    if chimney:
        b.box((0.6, 0.6, 1.6), (w * 0.28, d * 0.15, h + 1.3), "#7a4a3a")
    win = "#ffd27a" if glow else "#2c3550"
    em = 4.0 if glow else 0
    for x in (-w * 0.3, w * 0.3):
        for z in (h * 0.38, h * 0.78) if h > 4 else (h * 0.55,):
            b.box((1.0, 0.08, 1.1), (x, -d / 2 - 0.02, z), win, emit=em)
            b.box((1.2, 0.1, 0.12), (x, -d / 2 - 0.03, z - 0.62), trim)
    b.box((1.0, 0.1, 2.0), (0, -d / 2 - 0.03, 1.0), porch)
    if porch_on:
        pd = 1.8
        b.box((w, pd, 0.25), (0, -d / 2 - pd / 2, 0.12), porch)
        b.box((w + 0.2, pd + 0.2, 0.15), (0, -d / 2 - pd / 2, 2.6), trim)
        for x in (-w / 2 + 0.2, -w / 6, w / 6, w / 2 - 0.2):
            b.cyl(0.09, 2.5, (x, -d / 2 - pd + 0.15, 0.2), trim, seg=8)
        for x in [i * 0.35 - w / 2 + 0.2 for i in range(int(w / 0.35))]:
            if abs(x) > 0.7:
                b.box((0.06, 0.06, 0.7), (x, -d / 2 - pd + 0.15, 0.6), trim)
        b.box((w, 0.08, 0.08), (0, -d / 2 - pd + 0.15, 0.95), trim)
    return h + 2.0


def p_cabin(b, wood="#6e4630", roof="#3b2a22", glow=False, snow=False, w=4.5, d=4, h=2.6, **_):
    for i in range(int(h / 0.3)):
        b.box((w, d, 0.28), (0, 0, 0.15 + i * 0.3), wood if i % 2 else "#7b5038")
    b.prism(w + 0.8, d + 0.8, 1.6, (0, 0, h), "#f3f6fb" if snow else roof)
    win = "#ffc46b" if glow else "#2b2f3a"
    for x in (-w * 0.28, w * 0.28):
        b.box((0.8, 0.08, 0.8), (x, -d / 2 - 0.02, h * 0.55), win, emit=4.0 if glow else 0)
    b.box((0.9, 0.08, 1.8), (0, -d / 2 - 0.02, 0.9), "#4a2e20")
    b.box((w, 1.2, 0.2), (0, -d / 2 - 0.6, 0.1), "#5a3a28")
    return h + 1.6


def p_lighthouse(b, body="#f4f1ea", stripe="#c9473f", glow=True, h=9, **_):
    seg = 12
    n = 6
    for i in range(n):
        z = i * h / n
        r1 = 1.3 - 0.45 * z / h
        r2 = 1.3 - 0.45 * (z + h / n) / h
        b.cyl(r1, h / n, (0, 0, z), stripe if i % 2 else body, r2=r2, seg=seg)
    b.cyl(1.15, 0.2, (0, 0, h), "#2d2d33", seg=seg)
    b.cyl(0.65, 1.0, (0, 0, h + 0.2), "#fff2b0" if glow else "#9fb3c8", seg=seg, emit=8.0 if glow else 0, rough=0.2)
    b.cyl(0.85, 0.7, (0, 0, h + 1.2), "#2d2d33", r2=0.1, seg=seg)
    b.box((0.8, 0.1, 1.6), (0, -1.28, 0.8), "#4a5f74")
    b.box((0.9, 0.12, 0.08), (0, -1.3, 1.62), "#2d2d33")
    b.box((0.18, 0.18, 0.24), (0.62, -1.3, 1.9), "#ffd890", emit=6 if glow else 0)
    for z in (h * 0.4, h * 0.7):
        b.box((0.4, 0.1, 0.55), (0, -1.3 + 0.45 * z / h, z), "#ffd27a" if glow else "#2c3550", emit=3 if glow else 0)
    return h + 1.9


def p_island(b, sand="#e8d39a", grass="#7fae5a", r=9, **_):
    b.cyl(r * 1.15, 2.6, (0, 0, -2.4), sand, r2=r * 0.86, seg=18)
    b.cyl(r * 0.62, 0.35, (0, 0, 0.2), grass, r2=r * 0.5, seg=14)
    return 0.6


def p_pine(b, h=5, col="#2f5241", snow=False, **_):
    b.cyl(0.18, h * 0.25, (0, 0, 0), "#5a3d2a", seg=6)
    for i, (r, z) in enumerate(((1.0, 0.18), (0.78, 0.42), (0.55, 0.64))):
        b.cyl(r * h * 0.32, h * 0.38, (0, 0, z * h), col, r2=0.02, seg=8)
        if snow:
            b.cyl(r * h * 0.2, h * 0.16, (0, 0, z * h + h * 0.24), "#f2f6fb", r2=0.02, seg=8)
    return h


def p_tree(b, h=4, col="#5f9a4a", **_):
    b.cyl(0.2, h * 0.45, (0, 0, 0), "#6b4a32", seg=6)
    b.ico(h * 0.3, (0, 0, h * 0.62), col, scale=(1, 1, 0.9))
    b.ico(h * 0.2, (h * 0.18, 0.1, h * 0.5), col)
    return h


def p_rock(b, s=1.0, col="#7d8088", snow=False, **_):
    b.ico(0.6 * s, (0, 0, 0.25 * s), col, scale=(1.2, 1, 0.65), sub=1)
    if snow:
        b.ico(0.5 * s, (0, 0, 0.42 * s), "#f2f6fb", scale=(1.1, 0.9, 0.35), sub=1)
    return 0.6 * s


def p_mountain(b, h=40, r=None, col="#8d8fa3", snow=True, **_):
    r = r or h * 0.7
    b.cyl(r, h, (0, 0, 0), col, r2=0.0, seg=7)
    if snow:
        b.cyl(r * 0.36, h * 0.36, (0, 0, h * 0.64), "#f4f6fb", r2=0.0, seg=7)
    return h


def p_flower(b, h=1.6, petal="#f2c12e", center="#5a3a1a", head=0.35, **_):
    b.cyl(0.04, h, (0, 0, 0), "#4f7a2e", seg=5)
    b.box((0.35, 0.05, 0.15), (0.15, 0, h * 0.5), "#4f7a2e", rot=(0, 30, 0))
    b.cyl(head, 0.06, (0, -0.02, h), petal, seg=12, rot=(80, 0, 0))
    b.cyl(head * 0.5, 0.09, (0, -0.05, h), center, seg=10, rot=(80, 0, 0))
    return h + head


def p_bush(b, s=1.0, col="#4f8a3e", **_):
    b.ico(0.6 * s, (0, 0, 0.4 * s), col, scale=(1.2, 1, 0.8))
    return 0.8 * s


def p_mushroom(b, h=3, cap="#d8483f", dots="#fff4e6", stem="#efe6d2", glow=False, **_):
    b.cyl(h * 0.12, h * 0.75, (0, 0, 0), stem, r2=h * 0.09, seg=10)
    em = 1.5 if glow else 0
    b.ball(h * 0.42, (0, 0, h * 0.72), cap, scale=(1, 1, 0.55), seg=14, ring=8, emit=em)
    rnd = random.Random(int(h * 100))
    for _ in range(7):
        a = rnd.uniform(0, 6.28)
        rr = h * rnd.uniform(0.1, 0.32)
        b.ball(h * 0.05, (math.cos(a) * rr, math.sin(a) * rr, h * 0.72 + h * 0.2), dots, seg=6, ring=4)
    return h


def p_cloud(b, s=3, col="#f6d6e2", **_):
    rnd = random.Random(int(s * 10))
    for i in range(5):
        b.ico(s * rnd.uniform(0.35, 0.55), (s * (i - 2) * 0.4, rnd.uniform(-0.2, 0.2) * s, rnd.uniform(0, 0.2) * s), col,
              scale=(1, 0.8, 0.7), sub=2, emit=0.4)
    return s


def p_moon(b, r=6, col="#f4f1dc", **_):
    b.ball(r, (0, 0, 0), col, seg=16, ring=10, emit=2.5)
    return r


def p_sun(b, r=8, col="#fff1c4", **_):
    b.ball(r, (0, 0, 0), col, seg=16, ring=10, emit=12)
    return r


def p_fence(b, n=8, col="#f3efe6", **_):
    for i in range(n):
        b.box((0.1, 0.1, 1.0), (i * 0.6, 0, 0.5), col)
    b.box((n * 0.6, 0.06, 0.1), ((n - 1) * 0.3, 0, 0.75), col)
    b.box((n * 0.6, 0.06, 0.1), ((n - 1) * 0.3, 0, 0.4), col)
    return 1.0


def p_dock(b, l=14, w=2.4, col="#5d4a3c", **_):
    for i in range(int(l / 0.45)):
        b.box((w, 0.4, 0.12), (0, -i * 0.45, 0.5), col if i % 3 else "#54443a")
    for i in range(0, int(l / 0.45), 4):
        for x in (-w / 2, w / 2):
            b.cyl(0.12, 1.6, (x, -i * 0.45, -1.0), "#3e322a", seg=6)
    return 0.6


def p_boat(b, col="#a8473a", l=3.2, **_):
    b.box((0.9, l, 0.35), (0, 0, 0.18), col)
    b.box((0.7, l * 0.9, 0.1), (0, 0, 0.32), "#6b3a2a")
    b.box((0.9, 0.3, 0.25), (0, l / 2, 0.3), col, rot=(25, 0, 0))
    return 0.4


def p_lamp(b, h=3.2, col="#2f3338", glow=True, **_):
    b.cyl(0.07, h, (0, 0, 0), col, seg=6)
    b.box((0.4, 0.4, 0.5), (0, 0, h), "#ffe2a0" if glow else "#c8ccd2", emit=6 if glow else 0)
    b.cyl(0.32, 0.18, (0, 0, h + 0.25), col, r2=0.05, seg=6)
    return h + 0.4


def p_table_lamp(b, col="#e9dcc0", glow=True, **_):
    b.cyl(0.12, 0.05, (0, 0, 0), "#7b5a3a", seg=8)
    b.cyl(0.03, 0.35, (0, 0, 0), "#7b5a3a", seg=6)
    b.cyl(0.22, 0.28, (0, 0, 0.32), col, r2=0.13, seg=10, emit=2.5 if glow else 0)
    return 0.6


def p_bed(b, sheet="#7ac1b0", blanket="#d86b7a", wood="#8a5a3a", **_):
    b.box((1.6, 2.2, 0.35), (0, 0, 0.3), wood)
    b.box((1.5, 2.1, 0.22), (0, 0, 0.55), sheet)
    b.box((1.55, 1.3, 0.1), (0, -0.4, 0.68), blanket)
    b.box((1.1, 0.4, 0.15), (0, 0.75, 0.72), "#f4f1ea")
    b.box((1.7, 0.12, 1.1), (0, 1.12, 0.55), wood)
    return 1.1


def p_table(b, col="#8a5a3a", w=1.2, d=0.8, **_):
    b.box((w, d, 0.08), (0, 0, 0.75), col)
    for x in (-w / 2 + 0.08, w / 2 - 0.08):
        for y in (-d / 2 + 0.08, d / 2 - 0.08):
            b.box((0.07, 0.07, 0.75), (x, y, 0.37), col)
    return 0.8


def p_chair(b, col="#c2463c", **_):
    b.box((0.9, 0.85, 0.45), (0, 0, 0.32), col)
    b.box((0.9, 0.2, 0.95), (0, 0.38, 0.75), col)
    for x in (-0.48, 0.48):
        b.box((0.16, 0.85, 0.35), (x, 0, 0.6), col)
    return 1.2


def p_mug(b, col="#e9e2d0", fill="#6b3d22", **_):
    b.cyl(0.06, 0.13, (0, 0, 0), col, seg=10)
    b.cyl(0.05, 0.01, (0, 0, 0.12), fill, seg=10)
    return 0.13


def p_bookshelf(b, w=2.4, h=3.2, wood="#6a4430", seed=1, **_):
    b.box((w, 0.45, h), (0, 0.2, h / 2), wood)
    rnd = random.Random(seed)
    cols = ["#c0392b", "#2e6b9e", "#e2b33b", "#3f8a5a", "#8e44ad", "#d97a3a", "#e8e0cc", "#2c3e50"]
    rows = int(h / 0.55)
    for r in range(rows):
        z = 0.12 + r * h / rows
        x = -w / 2 + 0.08
        while x < w / 2 - 0.15:
            bw = rnd.uniform(0.06, 0.13)
            bh = rnd.uniform(0.3, 0.42)
            b.box((bw, 0.32, bh), (x + bw / 2, -0.05, z + bh / 2), rnd.choice(cols))
            x += bw + 0.01
        b.box((w, 0.46, 0.05), (0, 0.2, z), "#5a3826")
    return h


def p_fireplace(b, stone="#8a8c90", fire=True, **_):
    b.box((2.0, 0.7, 1.6), (0, 0, 0.8), stone)
    b.box((1.2, 0.2, 0.9), (0, -0.3, 0.5), "#1a1414")
    b.box((2.3, 0.85, 0.15), (0, 0, 1.65), "#6a4430")
    b.box((1.2, 0.6, 3.0), (0, 0.1, 3.0), stone)
    if fire:
        for x, s in ((-0.25, 0.32), (0.05, 0.42), (0.3, 0.28)):
            b.cyl(0.18, s, (x, -0.32, 0.12), "#ff8a2a", r2=0.0, seg=6, emit=10)
        b.box((0.9, 0.2, 0.1), (0, -0.32, 0.1), "#4a2a1a")
    return 4.5


def p_snowman(b, s=1.0, scarf="#c0392b", **_):
    b.ball(0.55 * s, (0, 0, 0.5 * s), "#f7f9fc", seg=12, ring=8)
    b.ball(0.4 * s, (0, 0, 1.25 * s), "#f7f9fc", seg=12, ring=8)
    b.ball(0.28 * s, (0, 0, 1.82 * s), "#f7f9fc", seg=12, ring=8)
    b.cyl(0.05 * s, 0.25 * s, (0, -0.25 * s, 1.85 * s), "#e67e22", r2=0.0, seg=6, rot=(90, 0, 0))
    for x in (-0.09, 0.09):
        b.ball(0.035 * s, (x * s, -0.25 * s, 1.92 * s), "#1b1b1b", seg=6, ring=4)
    b.cyl(0.3 * s, 0.1 * s, (0, 0, 1.55 * s), scarf, seg=10)
    b.cyl(0.2 * s, 0.3 * s, (0, 0, 2.02 * s), "#1f1f24", seg=10)
    b.cyl(0.3 * s, 0.04 * s, (0, 0, 2.02 * s), "#1f1f24", seg=10)
    return 2.3 * s


def p_balloon(b, col="#e05a47", stripe="#f3c64a", basket="#8a5a32", r=3.0, **_):
    cz = 3.4 + r * 1.15
    for i in range(8):
        b.ball(r, (0, 0, cz), col if i % 2 else stripe, seg=8, ring=10, scale=(1, 1, 1.15), rot=(0, 0, i * 45 / 8))
    b.cyl(r * 0.3, 1.2, (0, 0, 3.0), col, r2=r * 0.55, seg=8)
    for x, y in ((-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7)):
        b.box((0.04, 0.04, 2.3), (x, y, 2.05), "#3a2a1a")
    b.box((1.6, 1.6, 0.9), (0, 0, 0.45), basket)
    b.box((1.7, 1.7, 0.1), (0, 0, 0.9), "#6b4424")
    return cz + r * 1.15


def p_footprints(b, n=10, col="#b9a77a", step=0.7, **_):
    for i in range(n):
        x = 0.18 if i % 2 else -0.18
        b.box((0.16, 0.32, 0.02), (x, -i * step, 0.01), col)
    return 0.02


def p_window(b, w=1.6, h=1.8, frame="#f4f1ea", glass="#9fc3e6", glow=False, **_):
    """Free-standing window frame (put it in a wall of a room)."""
    b.box((w, 0.12, 0.1), (0, 0, 0), frame)
    b.box((w, 0.12, 0.1), (0, 0, h), frame)
    b.box((0.1, 0.12, h), (-w / 2, 0, h / 2), frame)
    b.box((0.1, 0.12, h), (w / 2, 0, h / 2), frame)
    b.box((w, 0.1, 0.06), (0, 0, h / 2), frame)
    b.box((0.06, 0.1, h), (0, 0, h / 2), frame)
    b.box((w, 0.03, h), (0, 0.03, h / 2), glass, emit=2 if glow else 0, rough=0.15, alpha=0.35 if not glow else 1)
    return h


def p_room(b, w=6, d=5, h=3.2, wall="#cfc3b0", floor="#7a5236", window=True, outside="#1f2a4a", ceiling=True, **_):
    """Interior: floor + back/left/right walls (+ window hole look on the back wall). Camera from -Y."""
    b.box((w, d, 0.1), (0, 0, -0.05), floor)
    b.box((w, 0.15, h), (0, d / 2, h / 2), wall)
    b.box((0.15, d, h), (-w / 2, 0, h / 2), wall)
    b.box((0.15, d, h), (w / 2, 0, h / 2), wall)
    if ceiling:
        b.box((w, d, 0.1), (0, 0, h), "#e8e0d2")
    if window:
        b.box((1.8, 0.05, 1.5), (0, d / 2 - 0.09, 1.7), outside, emit=0.6)
        for z in (0.95, 2.45):
            b.box((2.0, 0.14, 0.1), (0, d / 2 - 0.1, z), "#f4f1ea")
        for x in (-0.95, 0, 0.95):
            b.box((0.1, 0.14, 1.6), (x, d / 2 - 0.1, 1.7), "#f4f1ea")
        b.box((1.8, 0.14, 0.08), (0, d / 2 - 0.1, 1.7), "#f4f1ea")
    return h


def p_crate(b, s=0.8, col="#a07848", **_):
    b.box((s, s, s), (0, 0, s / 2), col)
    return s


def p_pool(b, r=2.5, water="#4cc3c8", rock="#8b8f97", level=1.0, snow=False, **_):
    rnd = random.Random(int(r * 10))
    n = int(r * 5)
    for i in range(n):
        a = i / n * 6.283
        b.ico(0.42, (math.cos(a) * r, math.sin(a) * r, 0.15), rock, scale=(1.2, 0.9, 0.6))
        if snow:
            b.ico(0.32, (math.cos(a) * r, math.sin(a) * r, 0.33), "#f2f6fb", scale=(1.1, 0.8, 0.3))
    b.cyl(r, 0.05, (0, 0, -0.8), "#3a3f46", seg=20)
    if level > 0:
        b.cyl(r * 0.98, 0.05, (0, 0, -0.8 + 0.85 * level), water, seg=20, rough=0.1, emit=0.2)
    return 0.5


def p_steam(b, h=6, col="#eef3f7", **_):
    for i in range(6):
        z = i * h / 6
        b.ico(0.5 + i * 0.25, (math.sin(i) * 0.2, 0, z + 0.5), col, scale=(1, 1, 1.3), alpha=0.55, emit=0.3)
    return h


def p_bucket(b, col="#7f8fa3", fish=True, **_):
    b.cyl(0.2, 0.36, (0, 0, 0), col, r2=0.26, seg=12)
    b.cyl(0.24, 0.02, (0, 0, 0.3), "#3f8fb0", seg=12, rough=0.1)
    if fish:
        b.box((0.16, 0.05, 0.07), (0.05, 0, 0.33), "#f08a3a", rot=(0, 0, 30))
        b.box((0.13, 0.04, 0.06), (-0.07, 0.07, 0.33), "#f2b13a", rot=(0, 0, -50))
    return 0.36


def p_openbook(b, glow=False, cover="#7a2e2e", page="#efe6cf", **_):
    em = 1.6 if glow else 0
    b.box((0.62, 0.42, 0.02), (0, 0, 0.01), cover)
    b.box((0.29, 0.38, 0.04), (-0.15, 0, 0.035), page, rot=(0, -6, 0), emit=em)
    b.box((0.29, 0.38, 0.04), (0.15, 0, 0.035), page, rot=(0, 6, 0), emit=em)
    for i in range(6):
        b.box((0.2, 0.01, 0.005), (-0.15, -0.14 + i * 0.05, 0.06), "#5a4a3a")
        b.box((0.2, 0.01, 0.005), (0.15, -0.14 + i * 0.05, 0.06), "#5a4a3a")
    return 0.06


def p_sign(b, **_):
    return 0  # text signs are real text objects, see place_text()


PROPS = {k[2:]: v for k, v in dict(globals()).items() if k.startswith("p_") and callable(v)}

_CACHE = {}


def prop_mesh(kind, params):
    key = (kind, tuple(sorted((k, str(v)) for k, v in params.items())))
    if key in _CACHE:
        return _CACHE[key]
    if kind not in PROPS:
        raise SystemExit(f"x unknown prop '{kind}'. Known: {', '.join(sorted(PROPS))}")
    b = B()
    h = PROPS[kind](b, **params)
    me = b.mesh(kind)
    _CACHE[key] = (me, h)
    return me, h


def place_text(text, loc, rot=(90, 0, 0), size=0.8, col="#ffe9a8", glow=3.0, extrude=0.03, coll=None):
    cu = bpy.data.curves.new("txt", type="FONT")
    cu.body = text
    try:
        cu.font = bpy.data.fonts.load(CJK_FONT, check_existing=True)
    except Exception:
        pass
    cu.size = size
    cu.extrude = extrude
    cu.align_x = "CENTER"
    cu.align_y = "CENTER"
    ob = bpy.data.objects.new("txt", cu)
    ob.data.materials.append(M(col, emit=glow))
    ob.location = loc
    ob.rotation_euler = [math.radians(a) for a in rot]
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


# ---------------------------------------------------------------- hero (posable)
HERO_COLS = dict(skin="#c98e6a", hair="#2b1d14", shirt="#2f8f9d", pants="#5d5a3c", shoe="#2a2420", pack="#3d5a9e")
MANNEQUIN = dict(skin="#e9e9ee", hair="#e9e9ee", shirt="#e9e9ee", pants="#e9e9ee", shoe="#e9e9ee", pack=None)


def _part(name, builder_fn, coll):
    b = B()
    builder_fn(b)
    ob = bpy.data.objects.new(name, b.mesh(name))
    coll.objects.link(ob)
    return ob


def make_hero(coll, cols=None, backpack=True, book=False):
    """Returns root empty; pose with pose_hero(). Root origin at the feet, facing -Y."""
    c = dict(HERO_COLS, **(cols or {}))
    root = bpy.data.objects.new("hero", None)
    coll.objects.link(root)

    def body(b):
        b.box((0.42, 0.24, 0.62), (0, 0, 1.25), c["shirt"])
        b.box((0.44, 0.26, 0.12), (0, 0, 0.96), c["pants"])
        b.cyl(0.07, 0.1, (0, 0, 1.55), c["skin"], seg=8)
        b.box((0.25, 0.27, 0.29), (0, 0, 1.79), c["skin"])
        b.box((0.27, 0.29, 0.1), (0, 0.01, 1.95), c["hair"])
        b.box((0.27, 0.08, 0.2), (0, 0.11, 1.84), c["hair"])
        b.box((0.03, 0.02, 0.03), (-0.06, -0.14, 1.82), "#1b1b1b")
        b.box((0.03, 0.02, 0.03), (0.06, -0.14, 1.82), "#1b1b1b")
        if backpack and c.get("pack"):
            b.box((0.34, 0.16, 0.44), (0, 0.2, 1.25), c["pack"])
        if book:
            pass

    bod = _part("hero_body", body, coll)
    bod.parent = root
    parts = {"body": bod}

    def limb(name, top, upper, lower, w, col_u, col_l, end_col, pivot):
        u = _part(name + "_u", lambda b: b.box((w, w, upper), (0, 0, -upper / 2), col_u), coll)
        u.parent = root
        u.location = pivot
        lo = _part(name + "_l", lambda b: (b.box((w * 0.95, w * 0.95, lower), (0, 0, -lower / 2), col_l),
                                           b.box((w * 1.05, w * 1.6 if name.startswith('leg') else w, 0.1),
                                                 (0, -0.05 if name.startswith('leg') else 0, -lower - 0.03), end_col)), coll)
        lo.parent = u
        lo.location = (0, 0, -upper)
        parts[name] = (u, lo)

    limb("leg_l", 0.95, 0.45, 0.46, 0.16, c["pants"], c["pants"], c["shoe"], (-0.11, 0, 0.95))
    limb("leg_r", 0.95, 0.45, 0.46, 0.16, c["pants"], c["pants"], c["shoe"], (0.11, 0, 0.95))
    limb("arm_l", 1.5, 0.32, 0.3, 0.11, c["shirt"], c["shirt"], c["skin"], (-0.28, 0, 1.52))
    limb("arm_r", 1.5, 0.32, 0.3, 0.11, c["shirt"], c["shirt"], c["skin"], (0.28, 0, 1.52))
    if book:
        bk = _part("book", lambda b: (b.box((0.42, 0.3, 0.04), (0, 0, 0), "#e9dfc6"), b.box((0.44, 0.32, 0.02), (0, 0, -0.03), "#7a2e2e")), coll)
        bk.parent = root
        parts["book"] = bk
    _HEROES[root.name] = parts
    return root


_HEROES = {}


def _r(ob, x=0, y=0, z=0):
    ob.rotation_euler = (math.radians(x), math.radians(y), math.radians(z))


POSES = {
    #        leg_l(u,l)   leg_r(u,l)   arm_l(u x, u y, l)   arm_r       root dz
    "stand": ((0, 0), (0, 0), (0, -6, 0), (0, 6, 0), 0),
    "back": ((0, 0), (0, 0), (0, -6, 0), (0, 6, 0), 0),
    "walk": ((-24, 18), (20, 8), (18, -6, -10), (-18, 6, -10), 0),
    "sit": ((-90, 90), (-90, 90), (-30, -4, -50), (-30, 4, -50), -0.45),
    "read": ((-90, 90), (-90, 90), (-40, -10, -75), (-40, 10, -75), -0.45),
    "swim": ((0, 0), (0, 0), (0, -80, 0), (0, 80, 0), 0),
    "wave": ((0, 0), (0, 0), (0, -6, 0), (0, 150, 10), 0),
    "lie": ((0, 0), (0, 0), (0, -10, 0), (0, 10, 0), 0),
    "point": ((0, 0), (0, 0), (0, -6, 0), (-80, 0, 0), 0),
}


def pose_hero(root, pose="stand", phase=None):
    parts = _HEROES[root.name]
    p = POSES.get(pose, POSES["stand"])
    (lu, ll), (ru, rl), al, ar, dz = p
    if phase is not None:  # walking cycle, phase in radians
        s = math.sin(phase)
        lu, ll = -26 * s, 20 * max(0, s)
        ru, rl = 26 * s, 20 * max(0, -s)
        al = (20 * s, -6, -12)
        ar = (-20 * s, 6, -12)
    _r(parts["leg_l"][0], lu); _r(parts["leg_l"][1], ll)
    _r(parts["leg_r"][0], ru); _r(parts["leg_r"][1], rl)
    _r(parts["arm_l"][0], al[0], al[1]); _r(parts["arm_l"][1], al[2])
    _r(parts["arm_r"][0], ar[0], ar[1]); _r(parts["arm_r"][1], ar[2])
    if "book" in parts:
        parts["book"].location = (0, -0.42, 1.12 + dz + 0.45 if pose in ("read",) else 1.1)
        _r(parts["book"], 35, 0, 0)
    return dz
