"""Builds and renders every shot of an episode inside Blender (Eevee, GPU).

  blender -b --factory-startup -P scene.py -- job.json [--only shot_id,shot_id] [--still]

job.json is written by make.py:
  {"res": [1080, 1920], "samples": 32, "out": "renders",
   "shots":  [{"id": "p1s1", "scene": {...}, "dur": 3.4, "fps": 15}],
   "globes": [{"id": "g1", "style": {...}, "frames": 96, "size": 640}]}

A shot without "anim" renders ONE still (make.py zooms it slowly in 2D); a shot with "anim"
renders a frame sequence at its fps. The scene vocabulary is documented in docs/scene-vocab.md.
"""
import json
import math
import os
import random
import sys
import time
import zlib

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import props as P  # noqa: E402

TIMES = {
    #           sky bottom, sky top,     sun colour, energy, elevation, azimuth, ambient colour, strength, glow, stars
    "day":     ("#cfe8ff", "#4f8fd8", "#fff3e0", 3.0, 52, 140, "#c4dbf2", 0.55, False, False),
    "sunset":  ("#ffb27a", "#6c4a8e", "#ffb070", 2.4, 10, 115, "#d6a6b6", 0.45, False, False),
    "dusk":    ("#f3a7bb", "#5a4b9c", "#ffb6a6", 0.9, 4, 105, "#9586c8", 0.45, True, False),
    "night":   ("#22305e", "#060a1c", "#9fb4ff", 0.35, 38, 210, "#3c4c86", 0.32, True, True),
    "snow":    ("#f6e2e0", "#9db2cc", "#fff0e6", 2.2, 22, 130, "#d4dcea", 0.75, False, False),
    "overcast": ("#dfe4ea", "#a9b4c2", "#f4f6fa", 1.4, 45, 130, "#cdd5df", 0.85, False, False),
    "indoor":  ("#2a2420", "#151210", "#ffd9b0", 0.0, 40, 140, "#ffd9b0", 0.28, True, False),
}
GROUNDS = {"grass": "#6f9a4a", "snow": "#e8eef6", "sand": "#e3cf98", "tile": "#e8d7b4", "wood": "#6b4a32",
           "dirt": "#7a5a3a", "stone": "#8a8c92", "meadow": "#86a95a"}
GLOW_KINDS = {"house", "cabin", "lighthouse", "lamp", "table_lamp"}
LIGHTS = {  # kind -> list of (offset, colour, watts)
    "lamp": [((0, 0, 3.1), "#ffcf8a", 250)],
    "table_lamp": [((0, 0, 0.45), "#ffcf8a", 40)],
    "fireplace": [((0, -0.8, 0.5), "#ff9a4a", 220)],
    "lighthouse": [((0, 0, 9.7), "#fff2b0", 3000), ((0.62, -1.7, 1.9), "#ffd890", 60)],
    "cabin": [((0, -2.6, 1.6), "#ffc46b", 60)],
    "house": [((0, -3.0, 1.7), "#ffd27a", 80)],
}
RESERVED = {"type", "id", "at", "rot", "s", "scatter", "light", "face", "pose", "cols", "pack", "book"}

IDS = {}


def lin(h):
    return (*P.hex2lin(h), 1.0)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    P._MAT.clear()
    P._CACHE.clear()
    P._HEROES.clear()
    IDS.clear()


def setup_render(res, samples, transparent=False):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = transparent
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    ee = sc.eevee
    for k, v in (("taa_render_samples", samples), ("use_shadows", True), ("use_raytracing", True),
                 ("shadow_ray_count", 2), ("shadow_step_count", 6), ("use_gtao", True)):
        if hasattr(ee, k):
            try:
                setattr(ee, k, v)
            except Exception:
                pass
    try:
        sc.view_settings.view_transform = "AgX"
        sc.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass
    return sc


def world(sky0, sky1, amb, amb_s, sky_s=1.0):
    sc = bpy.context.scene
    w = bpy.data.worlds.new("w")
    sc.world = w
    try:
        w.use_nodes = True
    except Exception:
        pass
    nt = w.node_tree
    nt.nodes.clear()
    N, L = nt.nodes.new, nt.links.new
    out = N("ShaderNodeOutputWorld")
    lp = N("ShaderNodeLightPath")
    tc = N("ShaderNodeTexCoord")
    sep = N("ShaderNodeSeparateXYZ")
    ramp = N("ShaderNodeValToRGB")
    mx = N("ShaderNodeMath")
    mx.operation = "MAXIMUM"
    bsky = N("ShaderNodeBackground")
    bamb = N("ShaderNodeBackground")
    mix = N("ShaderNodeMixShader")
    L(tc.outputs["Window"], sep.inputs[0])
    L(sep.outputs["Y"], ramp.inputs["Fac"])
    ramp.color_ramp.elements[0].position = 0.3
    ramp.color_ramp.elements[0].color = lin(sky0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = lin(sky1)
    L(ramp.outputs["Color"], bsky.inputs["Color"])
    bsky.inputs["Strength"].default_value = sky_s
    bamb.inputs["Color"].default_value = lin(amb)
    bamb.inputs["Strength"].default_value = amb_s
    L(lp.outputs["Is Camera Ray"], mx.inputs[0])
    L(lp.outputs["Is Glossy Ray"], mx.inputs[1])
    L(mx.outputs[0], mix.inputs["Fac"])
    L(bamb.outputs[0], mix.inputs[1])
    L(bsky.outputs[0], mix.inputs[2])
    L(mix.outputs[0], out.inputs["Surface"])


def add_light(kind, loc, col, energy, coll, size=0.3, rot=None):
    ld = bpy.data.lights.new(kind, type=kind)
    ld.energy = energy
    ld.color = P.hex2lin(col)
    if kind == "SUN":
        ld.angle = math.radians(4)
    else:
        ld.shadow_soft_size = size
    ob = bpy.data.objects.new(kind, ld)
    ob.location = loc
    if rot:
        ob.rotation_euler = rot
    coll.objects.link(ob)
    return ob


def scatter_points(sc, rnd):
    n = sc.get("n", 50)
    cx, cy = sc.get("center", [0, 0])
    pts = []
    avoid = sc.get("avoid", [])
    tries = 0
    while len(pts) < n and tries < n * 30:
        tries += 1
        if "box" in sc:
            x0, y0, x1, y1 = sc["box"]
            x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        else:
            r0, r1 = sc.get("r", [5, 40])
            a = rnd.uniform(*[math.radians(v) for v in sc.get("arc", [0, 360])])
            r = math.sqrt(rnd.uniform(r0 * r0, r1 * r1))
            x, y = cx + math.sin(a) * r, cy + math.cos(a) * r
        if any(math.hypot(x - ax, y - ay) < ar for ax, ay, ar in avoid):
            continue
        if "grid" in sc:  # rows like a field
            g = sc["grid"]
            x, y = round(x / g) * g + rnd.uniform(-g * 0.25, g * 0.25), round(y / g) * g + rnd.uniform(-g * 0.25, g * 0.25)
        pts.append((x, y, sc.get("z", 0)))
    return pts


def place(spec, coll, time_glow):
    kind = spec["type"]
    if kind in ("hero", "mannequin"):
        return place_hero(spec, coll)
    if kind in ("text", "sign"):
        ob = P.place_text(spec.get("text", ""), spec.get("at", [0, 0, 2]), rot=spec.get("rot3", (90, 0, spec.get("rot", 0))),
                          size=spec.get("size", 0.8), col=spec.get("col", "#ffe9a8"), glow=spec.get("glow", 3.0), coll=coll)
        if spec.get("id"):
            IDS[spec["id"]] = (Vector(ob.location), spec.get("size", 0.8))
        return [ob]
    params = {k: v for k, v in spec.items() if k not in RESERVED}
    if kind in GLOW_KINDS and "glow" not in params and time_glow:
        params["glow"] = True
    me, h = P.prop_mesh(kind, params)
    rnd = random.Random(spec.get("seed", zlib.crc32(kind.encode()) % 1000))
    pts = scatter_points(spec["scatter"], rnd) if "scatter" in spec else [tuple(spec.get("at", [0, 0, 0]))]
    smin, smax = (spec["scatter"].get("s", [0.8, 1.25]) if "scatter" in spec else (spec.get("s", 1), spec.get("s", 1)))
    obs = []
    for i, pt in enumerate(pts):
        ob = bpy.data.objects.new(kind, me)
        coll.objects.link(ob)
        s = rnd.uniform(smin, smax) if "scatter" in spec else spec.get("s", 1)
        ob.location = pt
        rz = rnd.uniform(0, 360) if ("scatter" in spec and spec["scatter"].get("spin", True)) else spec.get("rot", 0)
        if "scatter" in spec and "face" in spec["scatter"]:
            rz = spec["scatter"]["face"] + rnd.uniform(-15, 15)
        ob.rotation_euler = (0, 0, math.radians(rz))
        ob.scale = (s, s, s)
        obs.append(ob)
        lights = spec.get("light", LIGHTS.get(kind) if (params.get("glow", True) or kind == "fireplace") else None)
        if lights and "scatter" not in spec:
            for off, col, watt in lights:
                v = ob.matrix_world @ Vector(off)
                add_light("POINT", v, col, watt * s * s, coll, size=0.4 * s)
    if spec.get("id"):
        IDS[spec["id"]] = (Vector(pts[0]), h * (spec.get("s", 1)))
        IDS[spec["id"] + ".obs"] = obs
    return obs


def place_hero(spec, coll):
    cols = P.MANNEQUIN if spec["type"] == "mannequin" else spec.get("cols")
    root = P.make_hero(coll, cols=cols, backpack=spec.get("pack", spec["type"] == "hero"), book=spec.get("book", spec.get("pose") == "read"))
    pose = spec.get("pose", "stand")
    dz = P.pose_hero(root, pose)
    x, y, z = spec.get("at", [0, 0, 0])
    s = spec.get("s", 1)
    root.scale = (s, s, s)
    face = spec.get("face", 180 if pose == "back" else 0)
    if pose == "lie":
        root.rotation_euler = (math.radians(-90), 0, math.radians(face))
        root.location = (x, y, z + 0.14 * s)
    else:
        root.rotation_euler = (0, 0, math.radians(face))
        root.location = (x, y, z + dz * s)
    if spec["type"] == "mannequin":
        b = P.B()
        b.cyl(0.4, 0.12, (0, 0, 0), "#5a5e66", seg=16)
        ob = bpy.data.objects.new("pedestal", b.mesh("pedestal"))
        ob.location = (x, y, z)
        coll.objects.link(ob)
        root.location.z += 0.12 * s
    hid = spec.get("id", "hero" if spec["type"] == "hero" else None)
    if hid:
        IDS[hid] = (Vector((x, y, z)), 1.8 * s)
        IDS[hid + ".root"] = root
    return [root]


def stars(coll, n=350, seed=3):
    b = P.B()
    rnd = random.Random(seed)
    for _ in range(n):
        a = rnd.uniform(0, 6.283)
        e = rnd.uniform(0.08, 1.4)
        r = 260
        b.ico(rnd.uniform(0.25, 0.7), (math.cos(a) * math.cos(e) * r, math.sin(a) * math.cos(e) * r, math.sin(e) * r),
              "#ffffff", sub=0, emit=rnd.choice([3, 6, 10]))
    ob = bpy.data.objects.new("stars", b.mesh("stars"))
    coll.objects.link(ob)


def build(scene, res, samples):
    reset()
    sc = setup_render(res, samples)
    coll = sc.collection
    t = scene.get("time", "day")
    sky0, sky1, sun_c, sun_e, elev, az, amb, amb_s, glow, has_stars = TIMES[t]
    if scene.get("sky"):
        sky0, sky1 = scene["sky"]
    world(sky0, sky1, scene.get("ambient", amb), scene.get("ambient_strength", amb_s))
    if sun_e > 0 and scene.get("sun", True):
        add_light("SUN", (0, 0, 50), scene.get("sun_color", sun_c), scene.get("sun_energy", sun_e), coll,
                  rot=(math.radians(90 - scene.get("sun_elev", elev)), 0, math.radians(scene.get("sun_az", az))))
    g = scene.get("ground", "grass")
    if g and g != "none":
        b = P.B()
        b.grid(600, 600, (0, 0, scene.get("ground_z", 0)), scene.get("ground_color", GROUNDS.get(g, g)), segs=60,
               bumps=scene.get("bumps", 0.0 if g in ("tile", "wood") else 0.35), seed=2)
        ob = bpy.data.objects.new("ground", b.mesh("ground"))
        coll.objects.link(ob)
    if scene.get("water"):
        wv = scene["water"]
        b = P.B()
        b.grid(800, 800, (0, 0, wv.get("z", 0)), wv.get("col", "#3a6f9a"), rough=wv.get("rough", 0.06))
        ob = bpy.data.objects.new("water", b.mesh("water"))
        coll.objects.link(ob)
        IDS["water"] = (Vector((0, 0, wv.get("z", 0))), 0)
        IDS["water.obs"] = [ob]
    if scene.get("stars", has_stars):
        stars(coll)
    for p in scene.get("props", []):
        place(p, coll, scene.get("glow", glow))
    cam = scene.get("camera", {})
    camera(cam, res)
    return sc


def aim_point(a, aim_z=None):
    if isinstance(a, str):
        if a not in IDS:
            raise SystemExit(f"x camera aims at unknown id '{a}'. Known: {[k for k in IDS if '.' not in k]}")
        loc, h = IDS[a]
        z = loc.z + (aim_z if aim_z is not None else h * 0.55)
        return Vector((loc.x, loc.y, z)), max(h, 1.0)
    v = Vector(a if a else (0, 0, 1.2))
    if aim_z is not None:
        v.z = aim_z
    return v, 3.0


def camera(c, res):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("cam")
    cd.lens = c.get("lens", 28)
    cd.sensor_fit = "VERTICAL"
    cd.sensor_height = 36
    cd.clip_end = 3000
    cd.clip_start = 0.05
    ob = bpy.data.objects.new("cam", cd)
    sc.collection.objects.link(ob)
    sc.camera = ob
    if c.get("pos"):
        loc = Vector(c["pos"])
        tgt = Vector(c.get("look", (0, 0, 1)))
    else:
        tgt, h = aim_point(c.get("aim", "hero" if "hero" in IDS else [0, 0, 1.2]), c.get("aim_z"))
        dist = c.get("dist", max(6.0, h * 2.6))
        yaw, pitch = math.radians(c.get("yaw", -20)), math.radians(c.get("pitch", 10))
        loc = tgt + Vector((math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch))) * dist
        if c.get("lift"):
            tgt = tgt + Vector((0, 0, c["lift"]))
    ob.location = loc
    ob.rotation_euler = (tgt - loc).to_track_quat("-Z", "Y").to_euler()
    if c.get("roll"):
        ob.rotation_euler.rotate_axis("Z", math.radians(c["roll"]))
    return ob


def interp(keys, t):
    """keys: [[t, v...], ...] -> linear interpolation (ease in-out) of the values at time t."""
    if t <= keys[0][0]:
        return keys[0][1:]
    for a, b in zip(keys, keys[1:]):
        if a[0] <= t <= b[0]:
            u = (t - a[0]) / max(1e-6, b[0] - a[0])
            u = u * u * (3 - 2 * u)
            return [x + (y - x) * u for x, y in zip(a[1:], b[1:])]
    return keys[-1][1:]


def apply_anim(anims, t):
    for an in anims:
        tid = an["id"]
        obs = IDS.get(tid + ".obs") or ([IDS[tid + ".root"]] if tid + ".root" in IDS else [])
        if tid == "camera":
            obs = [bpy.context.scene.camera]
        for ob in obs:
            if "base" not in ob:
                ob["base"] = list(ob.location) + list(ob.scale)
            bx, by, bz, sx, sy, sz = ob["base"]
            if "loc" in an:
                ob.location = interp(an["loc"], t)
            if "z" in an:
                ob.location.z = bz + interp(an["z"], t)[0]
            if "move" in an:
                dx, dy, dz = interp(an["move"], t)
                ob.location = (bx + dx, by + dy, bz + dz)
            if "scale" in an:
                k = interp(an["scale"], t)[0]
                ob.scale = (sx * k, sy * k, sz * k)
            if "spin" in an:
                ob.rotation_euler.z = math.radians(interp(an["spin"], t)[0])
            if an.get("walk") and tid + ".root" in IDS:
                P.pose_hero(ob, "walk", phase=t * an.get("cadence", 6.5))


def render_shot(shot, cfg, out):
    t0 = time.time()
    scn = build(shot["scene"], cfg["res"], cfg.get("samples", 32))
    anims = shot["scene"].get("anim", [])
    if not anims or cfg.get("still_only"):
        scn.render.filepath = os.path.join(out, f"{shot['id']}.png")
        bpy.ops.render.render(write_still=True)
        print(f"  still {shot['id']}  {time.time() - t0:.1f}s", flush=True)
        return
    fps = shot.get("fps", 15)
    n = max(2, int(math.ceil(shot["dur"] * fps)) + 1)
    d = os.path.join(out, shot["id"])
    os.makedirs(d, exist_ok=True)
    for f in range(n):
        apply_anim(anims, f / fps)
        scn.render.filepath = os.path.join(d, f"f_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
    print(f"  anim  {shot['id']}  {n} frames  {time.time() - t0:.1f}s", flush=True)


def render_globe(g, cfg, out):
    t0 = time.time()
    reset()
    size = g.get("size", 640)
    sc = setup_render((size, size), 24, transparent=True)
    coll = sc.collection
    st = g.get("style", {})
    world("#000000", "#000000", "#8a93b8", st.get("ambient", 0.75))
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=32, radius=1)
    sph = bpy.context.object
    bpy.ops.object.shade_smooth()
    sph.data.materials.append(globe_mat(st))
    sph.rotation_euler = (math.radians(st.get("tilt", 18)), 0, 0)
    extras = []
    if st.get("ring"):
        bpy.ops.mesh.primitive_torus_add(major_radius=1.55, minor_radius=0.07, major_segments=64, minor_segments=8)
        r = bpy.context.object
        r.scale = (1, 1, 0.25)
        r.rotation_euler = (math.radians(70), math.radians(12), 0)
        r.data.materials.append(P.M(st.get("ring_col", "#5fb3a8"), emit=0.3))
    if st.get("moon"):
        b = P.B()
        b.ico(0.16, (0, 0, 0), "#d9d6cf", sub=2)
        mo = bpy.data.objects.new("moon", b.mesh("moon"))
        coll.objects.link(mo)
        extras.append(mo)
    if st.get("spikes"):  # geysers / jets sticking out
        b = P.B()
        rnd = random.Random(5)
        for _ in range(st["spikes"]):
            v = Vector((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))).normalized()
            q = v.to_track_quat("Z", "Y")
            res = P.bmesh.ops.create_cone(b.bm, cap_ends=True, cap_tris=False, segments=8, radius1=0.08, radius2=0.22, depth=0.6,
                                          matrix=P.Matrix.LocRotScale(v * 1.2, q, Vector((1, 1, 1))))
            b._paint(res["verts"], P.M("#f2f5f8", emit=1.2, alpha=0.85))
        sp = bpy.data.objects.new("spikes", b.mesh("spikes"))
        sp.parent = sph
        coll.objects.link(sp)
    add_light("SUN", (0, 0, 10), "#fff4e0", st.get("light", 3.5), coll, rot=(math.radians(55), math.radians(-35), math.radians(-30)))
    cd = bpy.data.cameras.new("c")
    cd.type = "ORTHO"
    cd.ortho_scale = 3.4 if st.get("ring") else 2.5
    cam = bpy.data.objects.new("c", cd)
    cam.location = (0, -10, 0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    coll.objects.link(cam)
    sc.camera = cam
    n = g.get("frames", 96)
    d = os.path.join(out, g["id"])
    os.makedirs(d, exist_ok=True)
    for f in range(n):
        a = f / n * 2 * math.pi
        sph.rotation_euler = (math.radians(st.get("tilt", 18)), 0, a)
        for mo in extras:
            mo.location = (math.cos(a * 1) * 1.55, math.sin(a * 1) * 0.6, 0.55)
        sc.render.filepath = os.path.join(d, f"f_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
    print(f"  globe {g['id']}  {n} frames  {time.time() - t0:.1f}s", flush=True)


def globe_mat(st):
    m = bpy.data.materials.new("globe")
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    N, L = nt.nodes.new, nt.links.new
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.75
    pat = st.get("pattern", "noise")
    base, spot = st.get("base", "#3d7bd9"), st.get("spots", "#e8d39a")
    ramp = N("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    ramp.color_ramp.elements[0].color = lin(base)
    ramp.color_ramp.elements[1].position = st.get("amount", 0.55)
    ramp.color_ramp.elements[1].color = lin(spot)
    if st.get("third"):
        e = ramp.color_ramp.elements.new(st.get("amount", 0.55) + 0.12)
        e.color = lin(st["third"])
    if pat == "noise":
        tex = N("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = st.get("scale", 3.0)
        tex.inputs["Detail"].default_value = 8
        L(tex.outputs["Fac"], ramp.inputs["Fac"])
    elif pat == "tiles":
        tex = N("ShaderNodeTexChecker")
        tex.inputs["Scale"].default_value = st.get("scale", 12)
        tex.inputs["Color1"].default_value = (1, 1, 1, 1)
        tex.inputs["Color2"].default_value = (0, 0, 0, 1)
        L(tex.outputs["Fac"], ramp.inputs["Fac"])
        ramp.color_ramp.elements[1].position = 0.5
    elif pat == "bands":
        tex = N("ShaderNodeTexWave")
        tex.inputs["Scale"].default_value = st.get("scale", 2.5)
        tex.inputs["Distortion"].default_value = st.get("distortion", 6)
        L(tex.outputs["Fac"], ramp.inputs["Fac"])
    elif pat == "dots":
        tex = N("ShaderNodeTexVoronoi")
        tex.inputs["Scale"].default_value = st.get("scale", 40)
        L(tex.outputs["Distance"], ramp.inputs["Fac"])
        ramp.color_ramp.elements[0].color = lin(spot)
        ramp.color_ramp.elements[1].position = 0.12
        ramp.color_ramp.elements[1].color = lin(base)
    L(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    if st.get("glow"):
        L(ramp.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = st["glow"]
    return m


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    job = json.load(open(argv[0], encoding="utf-8"))
    only = None
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))
    if "--still" in argv:
        job["still_only"] = True
    out = job["out"]
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    for g in job.get("globes", []):
        if only and g["id"] not in only:
            continue
        if not only and os.path.isdir(os.path.join(out, g["id"])) and len(os.listdir(os.path.join(out, g["id"]))) >= g.get("frames", 96):
            continue
        render_globe(g, job, out)
    for s in job["shots"]:
        if only and s["id"] not in only:
            continue
        render_shot(s, job, out)
    print(f"ok  rendered in {time.time() - t0:.0f}s", flush=True)


main()
