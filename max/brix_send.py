# BriX (Blender to 3ds Max Bridge) - 3ds Max to Blender
# Author: Tom Berczeller
#
# The BriX panel in 3ds Max: exports the scene to FBX with every material slot
# tagged, describes the materials in a job file and hands it to the Blender add-on.

import glob
import json
import os
import re
import subprocess
import time
import uuid

import pymxs
from pymxs import runtime as rt

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:  # older Max
    from PySide2 import QtCore, QtWidgets

import brix_max

VERSION = brix_max.VERSION
JOB_VERSION = 2
JOB_DIR = brix_max.JOB_DIR
HEARTBEAT_MAX_AGE = 10.0
TEMP_PREFIX = "BRIX"

DEFAULTS = {
    "scope": "VISIBLE",
    "new_instance": False,
    "keep_explicit_normals": False,
    "replace_previous": True,
    "reuse_materials": True,
    "layers_as": "COLLECTIONS",
    "weighted_normals": "AUTO",
    "blender_exe": "",
    "panel_open": True,
}

_MXS = r'''
global brix_usedIDs
fn brix_usedIDs node = (
    local m = snapshotAsMesh node
    local ids = #{}
    for f = 1 to m.numfaces do ids[getFaceMatID m f] = true
    try (delete m) catch ()
    ids as array
)
'''


def _log(msg):
    print("[BriX] " + msg)


# --------------------------------------------------------------------------- #
# Settings and Blender discovery
# --------------------------------------------------------------------------- #

def _settings_path():
    return os.path.join(str(rt.getDir(rt.Name("plugcfg"))), "BriX.json")


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(_settings_path(), "r", encoding="utf-8") as fh:
            s.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    try:
        with open(_settings_path(), "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1)
    except OSError as e:
        _log("could not save settings: %s" % e)


def _heartbeats():
    """[(age in seconds, data)] for every Blender heartbeat file, newest first."""
    out = []
    now = time.time()
    try:
        files = os.listdir(JOB_DIR)
    except OSError:
        return out
    for f in files:
        if f.startswith("blender_") and f.endswith(".alive"):
            p = os.path.join(JOB_DIR, f)
            try:
                age = now - os.path.getmtime(p)
                with open(p, "r", encoding="utf-8") as fh:
                    out.append((age, json.load(fh)))
            except (OSError, ValueError):
                continue
    return sorted(out, key=lambda x: x[0])


def running_blender():
    """Version string of a live Blender receiver, or None."""
    found = None
    for age, data in _heartbeats():
        if age >= HEARTBEAT_MAX_AGE:
            continue
        v = str(data.get("version", "?"))
        if v == VERSION:
            return v
        found = found or v
    return found


def _version_key(path):
    m = re.search(r"Blender (\d+)\.(\d+)", path)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def find_blender(settings):
    exe = settings.get("blender_exe", "")
    if exe and os.path.isfile(exe):
        return exe
    for _age, data in _heartbeats():  # a Blender with BriX ran here before
        exe = data.get("exe", "")
        if exe and os.path.isfile(exe):
            return exe
    hits = glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                  "Blender Foundation", "Blender *", "blender.exe"))
    return max(hits, key=_version_key) if hits else ""


def launch_blender(exe, token):
    env = dict(os.environ)
    env["BRIX_TOKEN"] = token
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe], env=env, close_fds=True, creationflags=flags, cwd=os.path.dirname(exe))


# --------------------------------------------------------------------------- #
# Scene collection
# --------------------------------------------------------------------------- #

def _is_hidden(node):
    try:
        return bool(node.isNodeHidden)  # hidden directly, by its layer or by category
    except Exception:
        return bool(node.isHidden)


def _kind(node):
    sc = rt.superClassOf(node)
    if sc == rt.GeometryClass:
        if rt.classOf(node) in (rt.Targetobject, rt.BoneGeometry):
            return None
        return "mesh" if rt.canConvertTo(node, rt.TriMeshGeometry) else None
    if sc == rt.Helper and rt.classOf(node) != rt.Targetobject:
        return "helper"
    return None


def collect_nodes(scope):
    nodes = list(rt.selection) if scope == "SELECTED" else list(rt.objects)
    return [n for n in nodes if not _is_hidden(n) and _kind(n)]


def _layer_tree(nodes):
    """layer name -> parent layer name for the layers holding `nodes` (the default layer 0 is skipped)."""
    default = str(rt.LayerManager.getLayer(0).name)
    tree = {}
    for n in nodes:
        layer = n.layer
        while layer is not None and str(layer.name) != default and str(layer.name) not in tree:
            parent = layer.getParent()
            pname = str(parent.name) if parent is not None and str(parent.name) != default else None
            tree[str(layer.name)] = pname
            layer = parent
    return tree, default


# --------------------------------------------------------------------------- #
# Materials
# --------------------------------------------------------------------------- #

def _has(obj, prop):
    try:
        return bool(rt.isProperty(obj, prop))
    except Exception:
        return False


def _get(obj, *props):
    for p in props:
        if _has(obj, p):
            try:
                return getattr(obj, p)
            except Exception:
                pass
    return None


def _srgb_to_lin(c):
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _color(c):
    return [_srgb_to_lin(float(c.r) / 255.0), _srgb_to_lin(float(c.g) / 255.0),
            _srgb_to_lin(float(c.b) / 255.0), 1.0]


def _file(path):
    if not path:
        return None
    path = str(path)
    if os.path.isfile(path):
        return os.path.normpath(path)
    try:
        full = str(rt.mapPaths.getFullFilePath(path))
        if full and os.path.isfile(full):
            return os.path.normpath(full)
    except Exception:
        pass
    return None


_OSL_CHANNEL = {2: "R", 3: "G", 4: "B", 5: "A"}


def _texmap_source(tex, log, depth=0):
    """(file path, channel) behind a texture map, or (None, None).
    Understands Bitmap, OSL bitmaps, channel outputs and Color Correction rewiring."""
    if tex is None or depth > 4:
        return None, None
    cls = rt.classOf(tex)
    if cls == rt.Bitmaptexture:
        p = _file(tex.filename)
        if p is None:
            log.append("texture not found: %s" % tex.filename)
        return p, ("A" if int(tex.monoOutput) == 1 else "RGB")
    if cls == rt.Color_Correction:
        p, ch = _texmap_source(tex.map, log, depth + 1)
        if p and int(tex.rewireMode) == 3:
            ch = "RGBA"[int(tex.rewireR)] if int(tex.rewireR) < 4 else ch
        return p, ch
    if cls == rt.MultiOutputChannelTexmapToTexmap:
        p, _ch = _texmap_source(tex.sourceMap, log, depth + 1)
        return p, _OSL_CHANNEL.get(int(tex.outputChannelIndex), "RGB")
    if cls == rt.Normal_Bump:
        return _texmap_source(tex.normal_map, log, depth + 1)
    for prop in ("filename", "fileName", "HDRIMapName", "texmap_filename"):
        if _has(tex, prop):
            p = _file(getattr(tex, prop))
            if p:
                return p, "RGB"
    for prop in ("map", "map1", "sourceMap"):
        if _has(tex, prop):
            return _texmap_source(getattr(tex, prop), log, depth + 1)
    log.append("texture map type %s not supported" % cls)
    return None, None


def _map_on(m, prop):
    flag = prop + "_on"
    return not _has(m, flag) or bool(getattr(m, flag))


def material_info(m, log):
    """The same description the Blender add-on sends to Max: values, maps by type and
    the image channel that holds each packed value."""
    cls = rt.classOf(m)
    info = {"name": str(m.name), "base_color": [0.8, 0.8, 0.8, 1.0], "metallic": 0.0,
            "roughness": 0.5, "textures": {}, "channels": {},
            # PBR materials list their maps explicitly; older ones may rely on file names
            "explicit": cls in (rt.PBRMetalRough, rt.PhysicalMaterial)}
    tx, ch = info["textures"], info["channels"]

    def tex(prop):
        t = _get(m, prop) if _map_on(m, prop) else None
        return _texmap_source(t, log) if t is not None else (None, None)

    col = _get(m, "baseColor", "base_color", "diffuse", "Diffuse_Color")
    if col is not None and rt.classOf(col) == rt.Color:
        info["base_color"] = _color(col)
    met = _get(m, "metalness")
    if met is not None:
        info["metallic"] = float(met)
    rough = _get(m, "roughness")
    if _has(m, "useGlossiness"):  # PBR material: 1 = glossiness, 2 = roughness
        gloss_mode = int(m.useGlossiness) == 1
    else:
        gloss_mode = bool(_get(m, "roughness_inv") or False)
    if rough is not None:
        info["roughness"] = 1.0 - float(rough) if gloss_mode else float(rough)
    elif _has(m, "glossiness"):  # Standard material, 0-100
        info["roughness"] = 1.0 - float(m.glossiness) / 100.0

    p, _c = tex("base_color_map") if _has(m, "base_color_map") else tex("diffuseMap")
    if p:
        tx["bc"] = p

    normal = _get(m, "norm_map") if _map_on(m, "norm_map") else None
    flip = bool(_get(m, "normal_flip_green") or False)
    if normal is None:
        bump = _get(m, "bump_map", "bumpMap")
        if bump is not None and _map_on(m, "bump_map") and rt.classOf(bump) == rt.Normal_Bump:
            normal, flip = bump, bool(bump.flipgreen)
    if normal is not None:
        p, _c = _texmap_source(normal, log)
        if p:
            # Max reads normal maps as DirectX: flip green on means the file is OpenGL
            tx["nrm" if flip else "nrm_dx"] = p

    for value, prop in (("metal", "metalness_map"), ("ao", "ao_map")):
        p, c = tex(prop)
        if p:
            ch[value] = [p, c]
            tx.setdefault(value, p)
    p, c = tex("roughness_map")
    if p:
        ch["gloss" if gloss_mode else "rough"] = [p, c]

    p, _c = tex("emit_color_map")
    if p:
        tx["emit"] = p
    p, _c = tex("opacity_map") if _has(m, "opacity_map") else tex("cutout_map")
    if not p:
        p, _c = tex("opacityMap")
    if p:
        tx["opacity"] = p
    return info


def _sub_for_id(mat, mid):
    """The sub-material Max draws for face ID `mid`."""
    if mat is None or rt.classOf(mat) != rt.Multimaterial:
        return mat
    ids = list(mat.materialIDList)
    subs = list(mat.materialList)
    if not subs:
        return None
    if mid in ids:
        return subs[ids.index(mid)]
    return subs[(mid - 1) % len(subs)]  # Max wraps IDs past the last sub-material


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def _fbx_param(name, value=None):
    try:
        if value is None:
            rt.FBXExporterSetParam(name)
        else:
            rt.FBXExporterSetParam(name, value)
    except Exception as e:
        _log("FBX export param %s not set: %s" % (name, e))


def _setup_fbx_export():
    _fbx_param("ResetExport")
    _fbx_param("SmoothingGroups", True)
    _fbx_param("NormalsPerPoly", False)
    _fbx_param("TangentSpaceExport", False)
    _fbx_param("SmoothMeshExport", False)   # TurboSmooth and friends are baked
    _fbx_param("Preserveinstances", False)
    _fbx_param("GeomAsBone", False)
    _fbx_param("Animation", False)
    _fbx_param("Cameras", False)
    _fbx_param("Lights", False)
    _fbx_param("EmbedTextures", False)
    _fbx_param("UpAxis", "Z")               # both apps are Z-up: no axis conversion
    _fbx_param("ConvertUnit", "m")          # meters, so Blender doesn't add a 0.01 scale
    _fbx_param("ScaleFactor", 1.0)
    _fbx_param("ASCII", False)
    _fbx_param("GenerateLog", False)
    _fbx_param("ShowWarnings", False)


def _weighted_info(node, mode):
    if mode == "OFF":
        return None
    for mod in node.modifiers:
        if rt.classOf(mod) == rt.Weighted_Normals and mod.enabled:
            area, angle = bool(mod.useAreaWeight), bool(mod.useAngleWeight)
            m = {(True, False): "FACE_AREA", (False, True): "CORNER_ANGLE"}.get((area, angle),
                                                                            "FACE_AREA_WITH_ANGLE")
            return {"mode": m}
    if mode == "ALL":
        return {"mode": "FACE_AREA_WITH_ANGLE"}
    return None


def _helper_info(node, meters):
    cls = rt.classOf(node)
    if cls == rt.Dummy:
        b = node.boxsize
        return {"display": "CUBE", "size": max(float(b.x), float(b.y), float(b.z)) / meters}
    if cls == rt.Point:
        display = "CUBE" if node.Box else ("ARROWS" if node.axistripod else "PLAIN_AXES")
        return {"display": display, "size": float(node.size) / meters}
    return {"display": "PLAIN_AXES", "size": 1.0}


def export_job(settings):
    """Export the FBX and build the job (not written yet). Returns (job dict, report dict)."""
    rt.execute(_MXS)
    log = []
    nodes = collect_nodes(settings["scope"])
    if not nodes:
        raise RuntimeError("Nothing to send: no visible geometry or helper objects")

    brix_max.cleanup_old_jobs()
    job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    short = job_id[-6:]
    os.makedirs(JOB_DIR, exist_ok=True)
    fbx_path = os.path.join(JOB_DIR, job_id + ".fbx")
    meters = float(rt.units.decodeValue("1m"))
    weighted_mode = settings["weighted_normals"]
    layers, default_layer = _layer_tree(nodes)

    objects_out, helpers_out, mat_infos = [], [], {}
    mat_keys = {}       # material anim handle -> key in mat_infos
    used = set()
    restore = []        # (node, name, material)
    pairs = []          # (node, job entry)
    count = {"mat": 0}

    def mat_key(mat):
        h = rt.getHandleByAnim(mat)
        if h not in mat_keys:
            base = str(mat.name) or "Material"
            key, k = base, 1
            while key in mat_infos:
                key, k = "%s.%03d" % (base, k), k + 1
            mat_keys[h] = key
            mat_infos[key] = material_info(mat, log)
            mat_infos[key]["name"] = key
        return mat_keys[h]

    def unique(name):
        n, k = name, 1
        while n in used:
            n, k = "%s.%03d" % (name, k), k + 1
        used.add(n)
        return n

    sel = list(rt.selection)
    with pymxs.undo(False):
        try:
            for i, node in enumerate(nodes, 1):
                name = unique(str(node.name))
                layer = str(node.layer.name)
                tm3 = node.transform
                rows = [[float(v.x), float(v.y), float(v.z)] for v in (tm3.row1, tm3.row2, tm3.row3, tm3.row4)]
                rows[3] = [v / meters for v in rows[3]]
                entry = {"name": name, "layer": None if layer == default_layer else layer, "matrix": rows}
                restore.append((node, str(node.name), node.material))
                pairs.append((node, entry))
                if _kind(node) == "helper":
                    token = "%s%s_E%05d" % (TEMP_PREFIX, short, i)
                    entry.update(_helper_info(node, meters), token=token)
                    helpers_out.append(entry)
                    node.name = token
                    continue
                token = "%s%s_O%05d" % (TEMP_PREFIX, short, i)
                mat = node.material
                ids = sorted(int(x) for x in rt.brix_usedIDs(node)) or [1]
                # one temporary sub-material per face ID, named with a token, so Blender
                # can tell which slot is which whatever the FBX does with them
                tm = rt.Multimaterial(numsubs=len(ids))
                slots = []
                for k, mid in enumerate(ids, 1):
                    count["mat"] += 1
                    stoken = "%s%s_M%05d" % (TEMP_PREFIX, short, count["mat"])
                    sub = rt.StandardMaterial(name=stoken)
                    rt.brix_setSub(tm, k, sub, stoken)
                    tm.materialIDList[k - 1] = mid
                    real = _sub_for_id(mat, mid)
                    slots.append({"token": stoken, "id": mid,
                                  "material": mat_key(real) if real is not None else None})
                node.material = tm
                node.name = token
                entry.update(token=token, slots=slots, weighted=_weighted_info(node, weighted_mode))
                objects_out.append(entry)

            _setup_fbx_export()
            rt.select(brix_max._mxs_array(nodes))
            ok = rt.exportFile(fbx_path, rt.Name("noPrompt"), selectedOnly=True, using=rt.FBXEXP)
            if not ok:
                raise RuntimeError("3ds Max FBX exporter reported a failure")
        finally:
            for node, name, mat in restore:
                node.name = name
                node.material = mat
            rt.clearSelection()
            if sel:
                rt.select(brix_max._mxs_array(sel))

    # parents by sent name; a parent that isn't sent leaves its child at the root
    by_handle = {rt.getHandleByAnim(node): entry["name"] for node, entry in pairs}
    for node, entry in pairs:
        p = node.parent
        entry["parent"] = by_handle.get(rt.getHandleByAnim(p)) if p is not None else None

    job = {
        "version": JOB_VERSION,
        "max_version": VERSION,
        "id": job_id,
        "fbx": fbx_path,
        "max_file": os.path.join(str(rt.maxFilePath), str(rt.maxFileName)) if str(rt.maxFileName) else "",
        "keep_explicit_normals": bool(settings["keep_explicit_normals"]),
        "replace_previous": bool(settings["replace_previous"]),
        "reuse_materials": bool(settings["reuse_materials"]),
        "layers_as": settings["layers_as"],
        "objects": objects_out,
        "helpers": helpers_out,
        "layers": layers,
        "materials": mat_infos,
        "log": log,
    }
    report = {"objects": len(objects_out), "helpers": len(helpers_out), "materials": len(mat_infos),
              "log": log}
    return job, report


def write_job(job, target):
    job["target"] = target
    final = os.path.join(JOB_DIR, job["id"] + ".bjob")
    tmp = final + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=1)
    os.replace(tmp, final)
    return final


def send(settings):
    """Export and hand the job to Blender. Returns a one-line summary."""
    use_new = settings["new_instance"] or not running_blender()
    exe = find_blender(settings) if use_new else ""
    if use_new and not exe:
        raise RuntimeError("No running Blender with BriX found and blender.exe not found. "
                           "Set the Blender path in the BriX panel settings")
    job, rep = export_job(settings)
    for line in rep["log"]:
        _log(line)
    if use_new:
        token = uuid.uuid4().hex
        path = write_job(job, token)
        try:
            launch_blender(exe, token)
        except Exception:
            os.remove(path)
            raise
        where = "a new Blender"
    else:
        write_job(job, "any")
        where = "the running Blender"
    notes = ", %d note(s) in the MAXScript Listener" % len(rep["log"]) if rep["log"] else ""
    helpers = ", %d helpers" % rep["helpers"] if rep["helpers"] else ""
    return "sent %d objects%s, %d materials to %s%s" % (rep["objects"], helpers, rep["materials"], where, notes)


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #

_panel = {"dock": None, "closing": False}

_SCOPES = [("VISIBLE", "Visible Objects"), ("SELECTED", "Selected Objects")]
_LAYERS_AS = [("COLLECTIONS", "Collections"), ("EMPTIES", "Empties"),
              ("BOTH", "Collections and Empties"), ("NONE", "Nothing")]
_WEIGHTED = [("AUTO", "From Modifier"), ("ALL", "All Meshes"), ("OFF", "Off")]


def _combo(items, value, tip):
    c = QtWidgets.QComboBox()
    for key, label in items:
        c.addItem(label, key)
    c.setCurrentIndex(max(0, [k for k, _ in items].index(value) if value in dict(items) else 0))
    c.setToolTip(tip)
    return c


class BrixPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = load_settings()
        s = self.settings
        lay = QtWidgets.QVBoxLayout(self)

        self.scope = _combo(_SCOPES, s["scope"], "Which objects to send")
        lay.addWidget(self.scope)

        self.new_instance = QtWidgets.QCheckBox("Open in new Blender")
        self.new_instance.setToolTip("Launch a fresh Blender and import there. Used automatically "
                                     "when no running Blender with BriX is found")
        self.keep_explicit = QtWidgets.QCheckBox("Keep custom normals (explicit)")
        self.keep_explicit.setToolTip("Bring the exact Max normals into Blender as custom normals. "
                                      "Off: shading comes from smoothing groups only")
        self.replace = QtWidgets.QCheckBox("Replace previous sends")
        self.replace.setToolTip("In Blender, delete objects from earlier BriX sends with the same "
                                "name before importing, so re-sending updates them")
        self.reuse = QtWidgets.QCheckBox("Reuse materials by name")
        self.reuse.setToolTip("If Blender already has a material with the same name, use it as it is "
                              "instead of building a new one. Off: always rebuild from Max")
        for box, key in ((self.new_instance, "new_instance"), (self.keep_explicit, "keep_explicit_normals"),
                         (self.replace, "replace_previous"), (self.reuse, "reuse_materials")):
            box.setChecked(bool(s[key]))
            lay.addWidget(box)

        form = QtWidgets.QFormLayout()
        self.layers_as = _combo(_LAYERS_AS, s["layers_as"],
                                "What 3ds Max layers become in Blender: collections (nested like the "
                                "layers), empties at the origin with the objects parented, or both")
        self.weighted = _combo(_WEIGHTED, s["weighted_normals"],
                               "Add a Blender Weighted Normal modifier, so the normals stay weighted "
                               "while you edit the mesh in Blender")
        form.addRow("Layers", self.layers_as)
        form.addRow("Weighted normals", self.weighted)
        lay.addLayout(form)

        self.send_btn = QtWidgets.QPushButton("Send to Blender")
        self.send_btn.setMinimumHeight(36)
        self.send_btn.clicked.connect(self.on_send)
        lay.addWidget(self.send_btn)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        box = QtWidgets.QGroupBox("Settings")
        bl = QtWidgets.QHBoxLayout(box)
        self.exe = QtWidgets.QLineEdit(s["blender_exe"])
        self.exe.setPlaceholderText("blender.exe (auto-detected)")
        self.exe.setToolTip("Blender used for 'Open in new Blender'. Leave empty to auto-detect")
        browse = QtWidgets.QPushButton("...")
        browse.setFixedWidth(28)
        browse.clicked.connect(self.on_browse)
        bl.addWidget(self.exe)
        bl.addWidget(browse)
        lay.addWidget(box)
        lay.addStretch(1)

        for w in (self.scope, self.layers_as, self.weighted):
            w.currentIndexChanged.connect(self.store)
        for w in (self.new_instance, self.keep_explicit, self.replace, self.reuse):
            w.toggled.connect(self.store)
        self.exe.editingFinished.connect(self.store)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def store(self, *_args):
        s = self.settings
        s["scope"] = self.scope.currentData()
        s["layers_as"] = self.layers_as.currentData()
        s["weighted_normals"] = self.weighted.currentData()
        s["new_instance"] = self.new_instance.isChecked()
        s["keep_explicit_normals"] = self.keep_explicit.isChecked()
        s["replace_previous"] = self.replace.isChecked()
        s["reuse_materials"] = self.reuse.isChecked()
        s["blender_exe"] = self.exe.text().strip()
        save_settings(s)

    def refresh(self):
        v = running_blender()
        if v is None:
            self.status.setText("Blender: not detected")
        elif v == VERSION:
            self.status.setText("Blender: running (v%s)" % v)
        else:
            self.status.setText("Blender add-on v%s doesn't match this listener (v%s). "
                                "Update both to the same version" % (v, VERSION))

    def on_browse(self):
        path, _f = QtWidgets.QFileDialog.getOpenFileName(self, "Blender executable", self.exe.text(),
                                                         "blender.exe (blender.exe)")
        if path:
            self.exe.setText(os.path.normpath(path))
            self.store()

    def on_send(self):
        self.store()
        self.send_btn.setEnabled(False)
        try:
            msg = send(self.settings)
            _log(msg)
            rt.displayTempPrompt("BriX: " + msg, 5000)
            self.status.setText(msg[0].upper() + msg[1:])
        except Exception as e:
            _log("send failed: %s" % e)
            rt.messageBox("BriX: %s" % e, title="BriX")
        finally:
            self.send_btn.setEnabled(True)


def _main_window():
    from qtmax import GetQMaxMainWindow
    return GetQMaxMainWindow()


def show_panel():
    dock = _panel["dock"]
    if dock is None:
        main = _main_window()
        dock = QtWidgets.QDockWidget("BriX", main)
        dock.setObjectName("BriXDock")
        dock.setWidget(BrixPanel(dock))
        dock.visibilityChanged.connect(_remember_visibility)
        main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        _panel["dock"] = dock
    dock.show()
    dock.raise_()


def _remember_visibility(visible):
    if _panel["closing"]:  # Max shutting down hides every dock: keep the last choice
        return
    s = load_settings()
    if s.get("panel_open") != bool(visible):
        s["panel_open"] = bool(visible)
        save_settings(s)


def toggle_panel():
    dock = _panel["dock"]
    if dock is not None and dock.isVisible():
        dock.hide()
    else:
        show_panel()


_MACRO = r'''
macroScript BriX_Panel category:"BriX" buttonText:"BriX" tooltip:"BriX: Send to Blender panel"
(
    python.Execute "import brix_send; brix_send.toggle_panel()"
)
'''


def startup():
    """Called by the listener when Max starts: registers the macro and restores the panel."""
    try:
        rt.execute(_MACRO)
    except Exception as e:
        _log("could not register the BriX macro: %s" % e)
    if load_settings().get("panel_open", True):
        try:
            show_panel()
        except Exception as e:
            _log("could not open the BriX panel: %s" % e)
