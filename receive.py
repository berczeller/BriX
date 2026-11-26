# BriX (Blender to 3ds Max Bridge)
# Author: Tom Berczeller
# Receiver: picks up jobs sent from the 3ds Max panel, imports the FBX and
# rebuilds materials, empties, collections and weighted normals.

import json
import os
import time
import traceback

import bpy
import numpy as np
from mathutils import Matrix

from . import core, textures

POLL_SECONDS = 1.0
TAG = "brix_source"  # custom property holding the 3ds Max node name

_state = {"busy": False, "last": "", "heartbeat": None}


def _log(msg):
    print("[BriX] " + msg)


def _heartbeat_path():
    return os.path.join(core.job_dir(), "blender_%d.alive" % os.getpid())


def _write_heartbeat():
    p = _heartbeat_path()
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"version": core.ADDON_VERSION, "time": time.time(), "exe": bpy.app.binary_path}, fh)
        _state["heartbeat"] = p
    except OSError:
        pass


def remove_heartbeat():
    p = _state["heartbeat"]
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
        _state["heartbeat"] = None


def _enabled():
    try:
        return bpy.context.preferences.addons[__package__].preferences.receive_from_max
    except (KeyError, AttributeError):
        return True


def tick():
    """Timer: heartbeat for the Max panel, then at most one job per call."""
    if not _enabled():
        remove_heartbeat()
        return POLL_SECONDS
    _write_heartbeat()
    if _state["busy"]:
        return POLL_SECONDS
    d = core.job_dir()
    token = os.environ.get("BRIX_TOKEN", "")
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".bjob"))
    except OSError:
        return POLL_SECONDS
    for f in files:
        path = os.path.join(d, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                job = json.load(fh)
        except (OSError, ValueError):
            continue
        target = job.get("target", "any")
        if target != "any" and target != token:
            continue
        claimed = path + ".claimed_%d" % os.getpid()
        try:
            os.rename(path, claimed)  # atomic: only one Blender wins
        except OSError:
            continue
        _state["busy"] = True
        try:
            _state["last"] = _run_in_window(job)
            _log(_state["last"])
        except Exception as e:
            _state["last"] = f"Import failed: {e}"
            _log("job %s failed:\n%s" % (job.get("id"), traceback.format_exc()))
            _popup(str(e))
        finally:
            _state["busy"] = False
            try:
                os.remove(claimed)
            except OSError:
                pass
        break
    return POLL_SECONDS


def _popup(msg):
    def draw(menu, _context):
        menu.layout.label(text=msg)
        menu.layout.label(text="See the system console for details")
    try:
        bpy.context.window_manager.popup_menu(draw, title="BriX import failed", icon='ERROR')
    except Exception:
        pass


def _run_in_window(job):
    wm = bpy.context.window_manager
    win = wm.windows[0] if wm.windows else None
    if win is None:
        raise RuntimeError("no Blender window to import into")
    area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)
    override = {"window": win, "screen": win.screen}
    if area is not None:
        override.update(area=area, region=next((r for r in area.regions if r.type == 'WINDOW'), None))
    with bpy.context.temp_override(**override):
        if bpy.context.mode != 'OBJECT' and bpy.context.object is not None:
            bpy.ops.object.mode_set(mode='OBJECT')
        return process_job(bpy.context, job)


# --------------------------------------------------------------------------- #
# Materials
# --------------------------------------------------------------------------- #

def _image(path, non_color):
    img = bpy.data.images.load(path, check_existing=True)
    if non_color and img.colorspace_settings.name != 'Non-Color':
        img.colorspace_settings.name = 'Non-Color'
    return img


class _Graph:
    """Small helper to place nodes in columns, right to left from the BSDF."""

    def __init__(self, mat):
        self.tree = mat.node_tree
        self.tree.nodes.clear()
        self.out = self.tree.nodes.new("ShaderNodeOutputMaterial")
        self.out.location = (400, 0)
        self.bsdf = self.tree.nodes.new("ShaderNodeBsdfPrincipled")
        self.bsdf.location = (0, 0)
        self.link(self.bsdf.outputs["BSDF"], self.out.inputs["Surface"])
        self.row = 0
        self.images = {}

    def link(self, a, b):
        self.tree.links.new(a, b)

    def node(self, kind, x, label=None):
        n = self.tree.nodes.new(kind)
        n.location = (x, 300 - self.row * 280)
        if label:
            n.label = label
        return n

    def image(self, path, non_color, label):
        key = (path, non_color)
        if key not in self.images:
            n = self.node("ShaderNodeTexImage", -700, label)
            n.image = _image(path, non_color)
            self.images[key] = n
            self.row += 1
        return self.images[key]

    def channel(self, path, channel, label):
        """Socket giving one channel of an image as a value."""
        img = self.image(path, True, label)
        if channel == "A":
            return img.outputs["Alpha"]
        if channel in ("R", "G", "B"):
            sep = self.node("ShaderNodeSeparateColor", -400)
            sep.location.y = img.location.y
            self.link(img.outputs["Color"], sep.inputs["Color"])
            return sep.outputs["RGB".index(channel)]
        return img.outputs["Color"]


def build_material(name, info):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    g = _Graph(mat)
    b = g.bsdf
    b.inputs["Base Color"].default_value = info.get("base_color", [0.8, 0.8, 0.8, 1.0])
    b.inputs["Metallic"].default_value = float(info.get("metallic", 0.0))
    b.inputs["Roughness"].default_value = float(info.get("roughness", 0.5))

    tx = dict(info.get("textures", {}))
    if not info.get("explicit"):
        for p in list(tx.values()):
            if os.path.isfile(p):
                textures.find_siblings(p, tx)
    tx = {k: p for k, p in tx.items() if os.path.isfile(p)}
    ch = textures.resolve(tx)
    for value, src in info.get("channels", {}).items():  # what Max actually used wins
        if os.path.isfile(src[0]):
            ch[value] = tuple(src)
    if "rough" in info.get("channels", {}):
        ch.pop("gloss", None)
    elif "gloss" in info.get("channels", {}):
        ch.pop("rough", None)

    if "bc" in tx:
        g.link(g.image(tx["bc"], False, "Base Color").outputs["Color"], b.inputs["Base Color"])
    if "metal" in ch:
        g.link(g.channel(*ch["metal"], "Metallic"), b.inputs["Metallic"])
    if "rough" in ch:
        g.link(g.channel(*ch["rough"], "Roughness"), b.inputs["Roughness"])
    elif "gloss" in ch:
        inv = g.node("ShaderNodeMath", -200, "Glossiness to Roughness")
        inv.operation = 'SUBTRACT'
        inv.inputs[0].default_value = 1.0
        g.link(g.channel(*ch["gloss"], "Glossiness"), inv.inputs[1])
        g.link(inv.outputs[0], b.inputs["Roughness"])
    if "ao" in ch:
        # Principled has no AO input: kept in the material, unconnected, for exporters and bakes
        g.channel(*ch["ao"], "AO")

    normal = tx.get("nrm") or tx.get("nrm_dx")
    if normal:
        img = g.image(normal, True, "Normal")
        nm = g.node("ShaderNodeNormalMap", -200)
        nm.location.y = img.location.y
        color = img.outputs["Color"]
        if "nrm" not in tx:  # DirectX normal map: flip green for Blender (OpenGL)
            sep = g.node("ShaderNodeSeparateColor", -450)
            inv = g.node("ShaderNodeMath", -450, "Flip Green")
            inv.operation = 'SUBTRACT'
            inv.inputs[0].default_value = 1.0
            comb = g.node("ShaderNodeCombineColor", -300)
            g.link(color, sep.inputs["Color"])
            g.link(sep.outputs[0], comb.inputs[0])
            g.link(sep.outputs[1], inv.inputs[1])
            g.link(inv.outputs[0], comb.inputs[1])
            g.link(sep.outputs[2], comb.inputs[2])
            color = comb.outputs["Color"]
            g.row += 1
        g.link(color, nm.inputs["Color"])
        g.link(nm.outputs["Normal"], b.inputs["Normal"])

    if "emit" in tx:
        g.link(g.image(tx["emit"], False, "Emission").outputs["Color"], b.inputs["Emission Color"])
        b.inputs["Emission Strength"].default_value = 1.0
    if "opacity" in tx:
        g.link(g.channel(tx["opacity"], "RGB", "Opacity"), b.inputs["Alpha"])
    mat.diffuse_color = info.get("base_color", [0.8, 0.8, 0.8, 1.0])
    mat[TAG] = info.get("name", name)
    return mat


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

def _remove_previous(names):
    """Delete objects from earlier sends. Their materials stay: they are matched by name below."""
    old = [ob for ob in bpy.data.objects if ob.get(TAG) in names]
    meshes = {ob.data for ob in old if ob.type == 'MESH'}
    for ob in old:
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in meshes:
        if me.users == 0:
            bpy.data.meshes.remove(me)
    return len(old)


def _collection(name, parents, root, cache):
    if name in cache:
        return cache[name]
    parent_name = parents.get(name)
    parent = _collection(parent_name, parents, root, cache) if parent_name else root
    c = bpy.data.collections.get(name)
    if c is None or c.library is not None:
        c = bpy.data.collections.new(name)
    if c.name not in parent.children and c != parent:
        try:
            parent.children.link(c)
        except RuntimeError:
            pass  # already linked elsewhere in the tree
    cache[name] = c
    return c


def _move_to(ob, coll):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)


def _set_slots(ob, slots, mats):
    """Material slot N = Max material ID order, each slot with its rebuilt material."""
    me = ob.data
    order = {s["token"]: i for i, s in enumerate(slots)}
    old = [m.name.split(".")[0] if m else "" for m in me.materials]
    # FBX slot -> our slot (token names may come back with a .001 suffix)
    remap = np.array([order.get(n, 0) for n in old] or [0], dtype=np.int32)
    mi = np.zeros(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("material_index", mi)
    np.clip(mi, 0, len(remap) - 1, out=mi)
    tokens = [m for m in me.materials if m is not None]
    me.materials.clear()
    for s in slots:
        me.materials.append(mats.get(s["material"]) if s["material"] else None)
    me.polygons.foreach_set("material_index", remap[mi])
    for m in tokens:
        if m.users == 0:
            bpy.data.materials.remove(m)


def _add_weighted(ob, info):
    md = ob.modifiers.new("Weighted Normals", 'WEIGHTED_NORMAL')
    md.mode = info.get("mode", 'FACE_AREA_WITH_ANGLE')
    md.keep_sharp = True  # hard edges from Max smoothing groups stay hard


def _matrix(rows, unit):
    """Max Matrix3 rows (axes, then position in meters) to a Blender world matrix."""
    t = [v / unit for v in rows[3]]
    return Matrix([[rows[0][r], rows[1][r], rows[2][r], t[r]] for r in range(3)] + [[0, 0, 0, 1]])


def _depth(ob):
    d = 0
    while ob.parent is not None:
        ob, d = ob.parent, d + 1
    return d


def _fix_shading(me, keep_explicit):
    """Smoothing groups come through the FBX as normals only: mark every edge where they
    split as sharp, then drop the explicit normals unless they were asked for."""
    if not me.polygons:
        return
    core._mark_split_edges_sharp(me, core._corner_normals(me))
    if not keep_explicit:
        core._clear_custom_normals(me)


def process_job(context, job):
    t0 = time.time()
    if int(job.get("version", 1)) > core.JOB_VERSION:
        raise RuntimeError("this job comes from a newer BriX in 3ds Max (%s). Update the add-on"
                           % job.get("max_version", "?"))
    fbx = job.get("fbx")
    if not fbx or not os.path.isfile(fbx):
        raise RuntimeError("FBX not found: %s" % fbx)
    objects = job.get("objects", [])
    helpers = job.get("helpers", [])
    everything = objects + helpers
    by_token = {o["token"]: o for o in everything}
    by_name = {o["name"]: o for o in everything}
    names = {o["name"] for o in everything}
    layers = job.get("layers", {})
    layers_as = job.get("layers_as", 'COLLECTIONS')
    scene = context.scene
    unit = scene.unit_settings.scale_length or 1.0

    if job.get("replace_previous"):
        n = _remove_previous(names | set(layers))
        if n:
            _log(f"replaced {n} previously sent objects")

    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(
        filepath=fbx,
        use_custom_normals=True,  # needed to rebuild hard edges, dropped later if not wanted
        use_image_search=False,
        use_manual_orientation=True,
        axis_forward='Y',   # identity: Blender and Max are both Z-up, right-handed
        axis_up='Z',
        global_scale=1.0,
        bake_space_transform=False,
        use_anim=False,
        ignore_leaf_bones=True,
        automatic_bone_orientation=False,
    )
    new = [ob for ob in bpy.data.objects if ob not in before]

    mats, reused = {}, 0
    reuse = job.get("reuse_materials", True)
    for key, info in job.get("materials", {}).items():
        found = bpy.data.materials.get(key) if reuse else None
        if found is not None and not found.name.startswith("BRIX"):
            mats[key] = found  # same name already in this file: used as it is
            reused += 1
        else:
            mats[key] = build_material(key, info)

    placed = {}
    for ob in new:
        info = by_token.get(ob.name.split(".")[0])
        if info is None:
            continue
        if ob.type == 'MESH' and "slots" in info:
            if ob.data.users > 1:
                ob.data = ob.data.copy()
            _set_slots(ob, info["slots"], mats)
            _fix_shading(ob.data, bool(job.get("keep_explicit_normals")))
            ob.data.name = info["name"]
            if info.get("weighted"):
                _add_weighted(ob, info["weighted"])
        elif ob.type == 'EMPTY':
            ob.empty_display_type = info.get("display", 'PLAIN_AXES')
            sx = max(abs(v) for v in _matrix(info["matrix"], unit).to_scale()) or 1.0
            ob.empty_display_size = max(float(info.get("size", 1.0)) / unit / 2.0 / sx, 0.001)
        ob.name = info["name"]
        ob[TAG] = info["name"]
        placed[info["name"]] = ob

    # Put every object exactly on its Max transform, pivot included: the FBX importer
    # bakes Max pivot offsets differently. Mesh data moves the other way, so nothing shifts.
    world = {ob: ob.matrix_world.copy() for ob in placed.values()}
    targets = {ob: _matrix(by_name[n]["matrix"], unit) for n, ob in placed.items() if "matrix" in by_name[n]}
    for ob, m in targets.items():
        if ob.type == 'MESH':
            ob.data.transform(m.inverted_safe() @ world[ob])
    for ob in sorted(targets, key=_depth):
        ob.matrix_world = targets[ob]

    root = context.view_layer.active_layer_collection.collection
    cache = {}
    use_colls = layers_as in ('COLLECTIONS', 'BOTH')
    use_empties = layers_as in ('EMPTIES', 'BOTH')
    layer_empty = {}
    if use_empties:
        for lname in layers:
            e = bpy.data.objects.new(lname, None)
            e.empty_display_type = 'CUBE'
            e.empty_display_size = 0.5 / unit
            e[TAG] = lname
            (_collection(lname, layers, root, cache) if use_colls else root).objects.link(e)
            layer_empty[lname] = e
        for lname, e in layer_empty.items():
            parent = layer_empty.get(layers.get(lname))
            if parent is not None:
                e.parent = parent
    for info in everything:
        ob = placed.get(info["name"])
        if ob is None:
            continue
        layer = info.get("layer")
        if use_colls and layer:
            _move_to(ob, _collection(layer, layers, root, cache))
        if use_empties and layer and info.get("parent") is None:
            mw = ob.matrix_world.copy()
            ob.parent = layer_empty[layer]
            ob.matrix_world = mw

    prefix = "BRIX" + job.get("id", "")[-6:]
    for m in [m for m in bpy.data.materials if m.name.startswith(prefix) and m.users == 0]:
        bpy.data.materials.remove(m)

    for ob in context.view_layer.objects:
        ob.select_set(False)
    for ob in placed.values():
        if ob.name in context.view_layer.objects:
            ob.select_set(True)
    if placed:
        context.view_layer.objects.active = next(iter(placed.values()))
    missing = [o["name"] for o in everything if o["name"] not in placed]
    if missing:
        _log("WARNING: not found after import: " + ", ".join(missing))
    for line in job.get("log", []):
        _log("Max: " + line)
    src = os.path.basename(job.get("max_file") or "") or "unsaved Max scene"
    return "received %d objects, %d materials (%d reused) from %s in %.1fs" % (
        len(placed), len(mats), reused, src, time.time() - t0)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def register():
    if not bpy.app.background and not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=POLL_SECONDS, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    remove_heartbeat()
