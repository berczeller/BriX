# BriX (Blender to 3ds Max Bridge) - 3ds Max side
# Author: Tom Berczeller
# Version: 0.1.0
#
# Listens for jobs written by the Blender add-on, merges the FBX into the
# current scene and rebuilds materials as Multi/Sub-Object + PBR sub-materials.

import json
import os
import tempfile
import time
import traceback

import pymxs
from pymxs import runtime as rt

try:
    from PySide6.QtCore import QTimer
except ImportError:  # older Max
    from PySide2.QtCore import QTimer

VERSION = "0.1.0"
JOB_VERSION = 1  # newest job format this listener understands
JOB_DIR = os.path.join(tempfile.gettempdir(), "BriX")
POLL_MS = 1000
APPDATA_ID = 0x42726958  # "BriX": per-node AppData slot holding the Blender object name

# Blender empty display type -> Point helper flags (cross, axis tripod, box)
_POINT_STYLE = {
    "PLAIN_AXES": (True, False, False),
    "ARROWS": (False, True, False),
    "SINGLE_ARROW": (False, True, False),
    "CONE": (False, True, False),
    "CUBE": (False, False, True),
    "SPHERE": (True, False, True),
    "CIRCLE": (True, False, False),
    "IMAGE": (False, False, True),
}

_state = {"timer": None, "busy": False}

# --------------------------------------------------------------------------- #
# MAXScript helpers (things that are faster or only reliable in MAXScript)
# --------------------------------------------------------------------------- #

_MXS = r'''
global brix_resetNormals
fn brix_resetNormals obj = (
    local ok = true
    try (
        if getCommandPanelTaskMode() != #modify do setCommandPanelTaskMode #modify
        select obj
        local m = Edit_Normals()
        addModifier obj m
        modPanel.setCurrentObject m
        local n = m.GetNumNormals()
        if n > 0 do m.Reset selection:#{1..n}
        convertToPoly obj
    ) catch (
        ok = false
        format "BriX: normal reset failed on %: %\n" obj.name (getCurrentException())
    )
    ok
)

global brix_remapIDs
fn brix_remapIDs obj idmap = (
    local nf = polyop.getNumFaces obj
    local groups = for i = 1 to idmap.count collect #{}
    for f = 1 to nf do (
        local id = polyop.getFaceMatID obj f
        if id <= idmap.count do groups[id][f] = true
    )
    for i = 1 to idmap.count where idmap[i] > 0 and idmap[i] != i and not groups[i].isEmpty do
        polyop.setFaceMatID obj groups[i] idmap[i]
    ok
)

global brix_setSub
fn brix_setSub mm i mat nm = (
    mm.materialList[i] = mat
    mm.materialIDList[i] = i
    mm.names[i] = nm
    ok
)
'''


def _log(msg):
    print("[BriX] " + msg)


# --------------------------------------------------------------------------- #
# Listener
# --------------------------------------------------------------------------- #

def _heartbeat_path():
    return os.path.join(JOB_DIR, "listener_%d.alive" % os.getpid())


def start_listener():
    if _state["timer"] is not None:
        return
    os.makedirs(JOB_DIR, exist_ok=True)
    rt.execute(_MXS)
    timer = QTimer()
    timer.setInterval(POLL_MS)
    timer.timeout.connect(_tick)
    timer.start()
    _state["timer"] = timer
    try:
        rt.callbacks.removeScripts(id=rt.Name("BriX"))
        rt.callbacks.addScript(
            rt.Name("preSystemShutdown"),
            "python.Execute \"import brix_max; brix_max.stop_listener()\"",
            id=rt.Name("BriX"))
    except Exception:
        pass
    _log("listener v%s running (job folder: %s)" % (VERSION, JOB_DIR))
    _tick()


def stop_listener():
    t = _state["timer"]
    if t is not None:
        t.stop()
        _state["timer"] = None
    try:
        os.remove(_heartbeat_path())
    except OSError:
        pass


def _tick():
    if _state["busy"]:
        return
    try:
        with open(_heartbeat_path(), "w", encoding="utf-8") as fh:
            json.dump({"version": VERSION, "time": time.time()}, fh)
    except OSError:
        pass

    token = os.environ.get("BRIX_TOKEN", "")
    try:
        files = sorted(f for f in os.listdir(JOB_DIR) if f.endswith(".job"))
    except OSError:
        return
    for f in files:
        path = os.path.join(JOB_DIR, f)
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
            os.rename(path, claimed)  # atomic: only one Max instance wins
        except OSError:
            continue
        _state["busy"] = True
        try:
            process_job(job)
        except Exception:
            _log("job %s failed:\n%s" % (job.get("id"), traceback.format_exc()))
            try:
                rt.messageBox("BriX import failed. See the MAXScript Listener for details.",
                              title="BriX")
            except Exception:
                pass
        finally:
            _state["busy"] = False
            try:
                os.remove(claimed)
            except OSError:
                pass
        break  # one job per tick


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

_UNIT_MAP = {"inches": "in", "feet": "ft", "miles": "mi", "millimeters": "mm",
             "centimeters": "cm", "meters": "m", "kilometers": "km"}


def _fbx_param(name, value):
    try:
        rt.FBXImporterSetParam(name, value)
    except Exception as e:
        _log("FBX param %s not set: %s" % (name, e))


def _setup_fbx_import():
    try:
        rt.FBXImporterSetParam("ResetImport")  # a command, takes no value
    except Exception as e:
        _log("FBX importer reset failed: %s" % e)
    _fbx_param("Mode", rt.Name("create"))       # add to (merge into) the current scene
    _fbx_param("UpAxis", "Z")
    _fbx_param("SmoothingGroups", True)
    _fbx_param("Animation", False)
    _fbx_param("Cameras", False)
    _fbx_param("Lights", False)
    _fbx_param("FillTimeline", False)
    _fbx_param("GenerateLog", False)
    unit = str(rt.units.SystemType).lstrip("#").lower()
    _fbx_param("ScaleConversion", True)          # real-world size kept, whatever the system units
    _fbx_param("ConvertUnit", _UNIT_MAP.get(unit, "cm"))


def _mxs_array(values):
    arr = rt.Array()
    for v in values:
        rt.append(arr, v)
    return arr


def _tag(node, name):
    rt.setAppData(node, APPDATA_ID, name)


def _tagged_name(node):
    v = rt.getAppData(node, APPDATA_ID)
    return None if v is None else str(v)


def _put_on_layer(node, name):
    if not name:
        return
    try:
        layer = rt.LayerManager.getLayerFromName(name)
        if layer is None:
            layer = rt.LayerManager.newLayerFromName(name)
        layer.addNode(node)
    except Exception as e:
        _log("could not put '%s' on layer '%s': %s" % (node.name, name, e))


def _make_point(info, meters):
    node = rt.Point()
    cross, tripod, box = _POINT_STYLE.get(info.get("display"), (True, False, False))
    node.cross = cross
    node.axistripod = tripod
    node.box = box
    node.centermarker = False
    node.size = max(float(info.get("size", 1.0)) * meters, 0.001)
    rows = info["matrix"]
    rows = rows[:3] + [[v * meters for v in rows[3]]]
    node.transform = rt.Matrix3(*[rt.Point3(*r) for r in rows])
    node.name = info["name"]
    return node


def process_job(job):
    t0 = time.time()
    if int(job.get("version", 1)) > JOB_VERSION:
        raise RuntimeError("this job comes from a newer BriX add-on (%s). Reinstall the listener "
                           "from the Blender add-on preferences and restart 3ds Max"
                           % job.get("addon_version", "?"))
    fbx = job.get("fbx")
    if fbx and not os.path.isfile(fbx):
        raise RuntimeError("FBX not found: " + fbx)
    objects = job.get("objects", [])
    empties = job.get("empties", [])
    by_token = {o["token"]: o for o in objects}
    everything = objects + empties
    names = set(o["name"] for o in everything)
    _log("importing %d objects and %d empties from %s"
         % (len(objects), len(empties), job.get("blend") or "unsaved .blend"))

    builder = MaterialBuilder(job.get("materials", {}))
    placed = {}
    rt.disableSceneRedraw()
    try:
        if job.get("replace_previous"):
            old = [n for n in rt.objects if _tagged_name(n) in names]
            if old:
                rt.delete(_mxs_array(old))
                _log("replaced %d previously sent objects" % len(old))

        if fbx:
            before = set(n.inode.handle for n in rt.objects)
            _setup_fbx_import()
            ok = rt.importFile(fbx, rt.Name("noPrompt"), using=rt.FBXIMP)
            if not ok:
                raise RuntimeError("3ds Max FBX importer reported a failure")
            new_nodes = [n for n in rt.objects if n.inode.handle not in before]

            for node in new_nodes:
                info = by_token.get(str(node.name))
                if info is None:
                    continue
                rt.convertToPoly(node)
                if not job.get("keep_explicit_normals"):
                    rt.brix_resetNormals(node)
                _apply_materials(node, info, builder)
                node.name = info["name"]
                placed[info["name"]] = node

        if empties:
            meters = float(rt.units.decodeValue("1m"))
            for info in empties:
                placed[info["name"]] = _make_point(info, meters)

        for info in everything:
            node = placed.get(info["name"])
            if node is None:
                continue
            _tag(node, info["name"])
            _put_on_layer(node, info.get("layer"))
            p = info.get("parent")
            if p in placed:
                node.parent = placed[p]  # Max keeps the world transform when parenting

        rt.clearSelection()
        if placed:
            rt.select(_mxs_array(list(placed.values())))
        missing = [o["name"] for o in everything if o["name"] not in placed]
        if missing:
            _log("WARNING: not found after import: " + ", ".join(missing))
    finally:
        rt.enableSceneRedraw()
        rt.redrawViews()
    msg = "imported %d objects, %d materials in %.1fs" % (len(placed), len(builder.cache), time.time() - t0)
    _log(msg)
    try:
        rt.displayTempPrompt("BriX: " + msg, 5000)
    except Exception:
        pass


def _apply_materials(node, info, builder):
    slots = info["slots"]
    token_slot = {s["token"]: i + 1 for i, s in enumerate(slots)}
    imp = node.material

    # Re-map face IDs so that ID N == Blender slot N, whatever the importer did
    if imp is not None and rt.classOf(imp) == rt.Multimaterial:
        ids = list(imp.materialIDList)
        subs = list(imp.materialList)
        idmap = [0] * (max(ids) if ids else 0)
        for mid, sub in zip(ids, subs):
            if sub is not None and str(sub.name) in token_slot:
                idmap[mid - 1] = token_slot[str(sub.name)]
        if any(v and v != i + 1 for i, v in enumerate(idmap)):
            rt.brix_remapIDs(node, _mxs_array(idmap))
    elif imp is not None:
        sid = token_slot.get(str(imp.name), 1)
        rt.polyop.setFaceMatID(node, rt.Name("all"), sid)

    mm = rt.Multimaterial(numsubs=len(slots))
    mm.name = info["name"] + "_Multi"
    for i, s in enumerate(slots, 1):
        mname = s["material"]
        mat = builder.get(mname) if mname else builder.empty_slot()
        rt.brix_setSub(mm, i, mat, mname or "Empty Slot")
    node.material = mm


# --------------------------------------------------------------------------- #
# Materials
# --------------------------------------------------------------------------- #

def _has(obj, prop):
    try:
        return bool(rt.isProperty(obj, prop))
    except Exception:
        return False


def _set(obj, candidates, value):
    for p in candidates:
        if _has(obj, p):
            try:
                setattr(obj, p, value)
                return p
            except Exception:
                pass
    return None


def _lin_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _bitmap(path, linear, mono_alpha=None, name=None):
    tex = None
    try:
        bm = rt.openBitMap(path, gamma=(1.0 if linear else rt.Name("auto")))
        if bm is not None:
            tex = rt.Bitmaptexture()
            tex.bitmap = bm
    except Exception:
        tex = None
    if tex is None:
        tex = rt.Bitmaptexture(fileName=path)
    if mono_alpha is not None:
        tex.monoOutput = 1 if mono_alpha else 0   # 0 = RGB intensity, 1 = alpha
        tex.alphaSource = 0                       # alpha from the image file
    if name:
        tex.name = name
    return tex


class MaterialBuilder(object):
    def __init__(self, infos):
        self.infos = infos
        self.cache = {}
        self._empty = None
        self._warned = False

    def empty_slot(self):
        if self._empty is None:
            self._empty = self._new_mat("Empty Slot")
        return self._empty

    def _new_mat(self, name):
        try:
            m = rt.PBRMetalRough()
        except Exception:
            m = rt.PhysicalMaterial()
            if not self._warned:
                _log("PBR Material (Metal/Rough) not available, using Physical Material")
                self._warned = True
        m.name = name
        return m

    def get(self, name):
        if name in self.cache:
            return self.cache[name]
        info = self.infos.get(name, {"name": name, "textures": {}})
        m = self._new_mat(name)
        self._fill(m, info)
        self.cache[name] = m
        return m

    def _fill(self, m, info):
        tx = info.get("textures", {})
        missing = []

        bc = info.get("base_color", [0.8, 0.8, 0.8, 1.0])
        col = rt.Color(*[_lin_to_srgb(c) * 255.0 for c in bc[:3]])
        _set(m, ["base_color", "basecolor", "baseColor"], col)
        _set(m, ["metalness"], float(info.get("metallic", 0.0)))
        _set(m, ["roughness"], float(info.get("roughness", 0.5)))

        if "bc" in tx:
            if not _set(m, ["base_color_map", "basecolor_map"], _bitmap(tx["bc"], False, name=info["name"] + "_bc")):
                missing.append("base_color_map")

        if "ao" in tx:
            if not _set(m, ["ao_map", "ambient_occlusion_map"], _bitmap(tx["ao"], True, name=info["name"] + "_ao")):
                missing.append("ao_map")

        if "nrm" in tx:
            nt = _bitmap(tx["nrm"], True, name=info["name"] + "_nrm")
            if _set(m, ["norm_map", "normal_map"], nt):
                if not _set(m, ["norm_flip_green", "normal_flip_green", "flip_green"], True):
                    missing.append("norm_flip_green")
            else:
                # fallback: Normal Bump map in the bump slot, green flipped there
                nb = rt.Normal_Bump()
                nb.normal_map = nt
                nb.flipgreen = True
                if not _set(m, ["bump_map"], nb):
                    missing.append("norm_map/bump_map")

        if "mg" in tx:
            met = _bitmap(tx["mg"], True, mono_alpha=False, name=info["name"] + "_mg_metal")
            glo = _bitmap(tx["mg"], True, mono_alpha=True, name=info["name"] + "_mg_gloss")
            if not _set(m, ["metalness_map"], met):
                missing.append("metalness_map")
            if not _set(m, ["roughness_map"], glo):
                missing.append("roughness_map")
            # read the alpha as glossiness, not roughness
            if not _set(m, ["useGlossiness", "use_glossiness", "roughness_inv"], True):
                missing.append("glossiness toggle")

        if missing:
            _log("material '%s': could not set %s. Available properties: %s"
                 % (info["name"], ", ".join(missing),
                    ", ".join(str(p) for p in rt.getPropNames(m))))
