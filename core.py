# BriX (Blender to 3ds Max Bridge)
# Author: Tom Berczeller
# Export core: builds temporary, fully baked copies of the scene meshes,
# exports them to FBX and writes a job file for the 3ds Max listener.

import json
import os
import re
import shutil
import tempfile
import time
import tomllib
import uuid

import bpy
import numpy as np
from mathutils import Matrix

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml"), "rb") as _fh:
    ADDON_VERSION = tomllib.load(_fh)["version"]

JOB_VERSION = 1
GEOMETRY_TYPES = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}
TEMP_PREFIX = "BRIX_"
HEARTBEAT_MAX_AGE = 10.0  # seconds; a Max listener older than this is considered gone
SPLIT_DOT = 0.9999        # corner normals closer than this (~0.8 deg) count as "the same"

TEX_SUFFIXES = ("bc", "ao", "nrm", "mg")
IMAGE_EXTS = (".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg", ".exr", ".dds", ".bmp", ".psd", ".hdr")
_SUFFIX_RE = re.compile(r"(?:^|_)(bc|ao|nrm|mg)(?=_|$)", re.IGNORECASE)


def job_dir():
    d = os.path.join(tempfile.gettempdir(), "BriX")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Max listener discovery
# --------------------------------------------------------------------------- #

def running_listener():
    """Version string of a live 3ds Max listener ("?" if it doesn't report one), or None."""
    d = job_dir()
    now = time.time()
    found = None
    for f in os.listdir(d):
        if f.startswith("listener_") and f.endswith(".alive"):
            p = os.path.join(d, f)
            try:
                if now - os.path.getmtime(p) >= HEARTBEAT_MAX_AGE:
                    continue
                with open(p, "r", encoding="utf-8") as fh:
                    version = str(json.load(fh).get("version", "?"))
            except (OSError, ValueError, AttributeError):
                version = "?"
            if version == ADDON_VERSION:
                return version
            found = found or version
    return found


def cleanup_old_jobs(max_age_hours=24):
    d = job_dir()
    now = time.time()
    for f in os.listdir(d):
        p = os.path.join(d, f)
        try:
            if now - os.path.getmtime(p) <= max_age_hours * 3600:
                continue
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)  # <job>_tex folders
            else:
                os.remove(p)  # also heartbeats left behind by a Max that crashed
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Textures
# --------------------------------------------------------------------------- #

def _classify(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    hits = _SUFFIX_RE.findall(stem)
    return hits[-1].lower() if hits else None


def _image_nodes(tree, seen=None):
    seen = seen if seen is not None else set()
    if tree is None or tree.name_full in seen:
        return
    seen.add(tree.name_full)
    for n in tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image is not None:
            yield n.image
        elif n.type == 'GROUP' and n.node_tree is not None:
            yield from _image_nodes(n.node_tree, seen)


def _unique_path(out_dir, name, ext):
    dst = os.path.join(out_dir, name + ext)
    k = 2
    while os.path.exists(dst):
        dst = os.path.join(out_dir, f"{name}_{k}{ext}")
        k += 1
    return dst


def _image_path(img, out_dir, log, written):
    """Absolute file path for an image. Packed and memory-only images are written
    to the job folder; `written` maps image names to files already written."""
    if img.name_full in written:
        return written[img.name_full]
    stem = bpy.path.clean_name(os.path.splitext(img.name)[0]) or "image"
    if img.packed_file is not None:
        ext = os.path.splitext(img.filepath or img.name)[1] or ".png"
        dst = _unique_path(out_dir, stem, ext)
        try:
            with open(dst, "wb") as fh:
                fh.write(img.packed_file.data)
        except OSError as e:
            log.append(f"Could not unpack image '{img.name}': {e}")
            return None
        written[img.name_full] = dst
        return dst

    if img.source in {'FILE', 'TILED', 'SEQUENCE'} and img.filepath:
        p = os.path.normpath(bpy.path.abspath(img.filepath, library=img.library))
        if os.path.isfile(p):
            return p
        if img.source != 'FILE' or not img.has_data:
            log.append(f"Texture not found on disk: {p}")
            return None

    if img.source in {'FILE', 'GENERATED'} and img.has_data:
        # generated, painted or moved image: save a copy so the original keeps its state
        dst = _unique_path(out_dir, stem, ".png")
        tmp = img.copy()
        try:
            tmp.filepath_raw = dst
            tmp.file_format = 'PNG'
            tmp.save()
        except RuntimeError as e:
            log.append(f"Could not write image '{img.name}': {e}")
            return None
        finally:
            bpy.data.images.remove(tmp)
        log.append(f"Image '{img.name}' is not saved to disk, sent a copy from memory")
        written[img.name_full] = dst
        return dst

    log.append(f"Image '{img.name}' has no file or pixel data, skipped")
    return None


def _find_siblings(path, found):
    """Fill missing map types with files next to a known map: rock_bc.png -> rock_mg.tga."""
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    m = list(_SUFFIX_RE.finditer(stem))
    if not m:
        return
    m = m[-1]
    prefix, rest = stem[:m.start(1)], stem[m.end(1):]
    try:
        files = os.listdir(folder)
    except OSError:
        return
    lower = {f.lower(): f for f in files}
    for kind in TEX_SUFFIXES:
        if kind in found:
            continue
        for ext in IMAGE_EXTS:
            cand = (prefix + kind + rest + ext).lower()
            if cand in lower:
                found[kind] = os.path.join(folder, lower[cand])
                break


def material_info(mat, out_dir, log, written):
    info = {"name": mat.name, "base_color": [0.8, 0.8, 0.8, 1.0],
            "metallic": 0.0, "roughness": 0.5, "textures": {}}
    tree = mat.node_tree
    if tree is not None:
        for n in tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                info["base_color"] = list(n.inputs["Base Color"].default_value)
                info["metallic"] = float(n.inputs["Metallic"].default_value)
                info["roughness"] = float(n.inputs["Roughness"].default_value)
                break
        for img in _image_nodes(tree):
            p = _image_path(img, out_dir, log, written)
            kind = _classify(p) if p else None
            if kind and kind not in info["textures"]:
                info["textures"][kind] = p
    else:
        info["base_color"] = list(mat.diffuse_color)
    for p in list(info["textures"].values()):
        _find_siblings(p, info["textures"])
    return info


# --------------------------------------------------------------------------- #
# Mesh processing
# --------------------------------------------------------------------------- #

def _corner_normals(mesh):
    n = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    mesh.corner_normals.foreach_get("vector", n)
    return n.reshape(-1, 3)


def _mark_split_edges_sharp(mesh, normals):
    """Mark every edge where the corner normals differ across faces as sharp.
    This turns sharp edges, flat faces and custom-normal splits into hard edges
    so the FBX exporter can derive matching smoothing groups."""
    nl = len(mesh.loops)
    ne = len(mesh.edges)
    if nl == 0 or ne == 0:
        return 0
    lv = np.empty(nl, dtype=np.int64); mesh.loops.foreach_get("vertex_index", lv)
    le = np.empty(nl, dtype=np.int64); mesh.loops.foreach_get("edge_index", le)
    ps = np.empty(len(mesh.polygons), dtype=np.int64); mesh.polygons.foreach_get("loop_start", ps)
    pt = np.empty(len(mesh.polygons), dtype=np.int64); mesh.polygons.foreach_get("loop_total", pt)

    # next loop inside the same polygon
    nxt = np.arange(nl) + 1
    last = ps + pt - 1
    nxt[last] = ps

    # each loop gives its edge two (vertex, normal) samples: start and end
    e = np.concatenate([le, le])
    v = np.concatenate([lv, lv[nxt]])
    n = np.concatenate([normals, normals[nxt]])
    key = e * (int(lv.max()) + 1) + v
    order = np.argsort(key, kind="stable")
    key, e, n = key[order], e[order], n[order]
    same = key[1:] == key[:-1]
    dots = np.einsum("ij,ij->i", n[1:], n[:-1])
    split = same & (dots < SPLIT_DOT)

    sharp = np.zeros(ne, dtype=bool)
    attr = mesh.attributes.get("sharp_edge")
    if attr is not None and attr.domain == 'EDGE':
        attr.data.foreach_get("value", sharp)
    before = int(sharp.sum())
    sharp[e[1:][split]] = True
    if attr is None:
        attr = mesh.attributes.new("sharp_edge", 'BOOLEAN', 'EDGE')
    attr.data.foreach_set("value", sharp)
    mesh.update()
    return int(sharp.sum()) - before


def _clear_custom_normals(mesh):
    if mesh.has_custom_normals:
        a = mesh.attributes.get("custom_normal")
        if a is not None:
            mesh.attributes.remove(a)
    mesh.update()


def _loop_keys(mesh):
    nl = len(mesh.loops)
    lv = np.empty(nl, dtype=np.int64); mesh.loops.foreach_get("vertex_index", lv)
    pt = np.empty(len(mesh.polygons), dtype=np.int64); mesh.polygons.foreach_get("loop_total", pt)
    poly_of = np.repeat(np.arange(len(pt)), pt)
    return poly_of * (len(mesh.vertices) + 1) + lv


def build_export_mesh(obj_eval, mw, label, keep_explicit, log, depsgraph, geometry_instance=False):
    """Return (mesh, location, sharp edges added) with modifiers applied, rotation+scale
    from `mw` baked in, pivot location preserved and shading converted.
    Returns None when the object has no faces (a curve without bevel, for example)."""
    if geometry_instance:
        # Geometry Nodes instance: its data is the instanced geometry, while
        # new_from_object would re-evaluate the owner object instead
        mesh = obj_eval.data.copy()
    else:
        mesh = bpy.data.meshes.new_from_object(obj_eval, preserve_all_data_layers=True, depsgraph=depsgraph)
    if not mesh.polygons:
        bpy.data.meshes.remove(mesh)
        return None
    try:
        return _bake_mesh(mesh, mw, label, keep_explicit, log)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise


def _bake_mesh(mesh, mw, label, keep_explicit, log):
    mesh.name = TEMP_PREFIX + "mesh"
    loc, _rot, _scale = mw.decompose()
    rs = mw.to_3x3()

    normals = _corner_normals(mesh)
    added = _mark_split_edges_sharp(mesh, normals)

    # normals into the new (rotation/scale baked) space
    nmat = np.array(rs.inverted_safe().transposed(), dtype=np.float64)
    wn = normals.astype(np.float64) @ nmat.T
    ln = np.linalg.norm(wn, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    wn = (wn / ln).astype(np.float32)

    keys_before = _loop_keys(mesh) if rs.determinant() < 0 else None

    mesh.transform(rs.to_4x4())
    if rs.determinant() < 0:
        # mirrored object: fix the winding, then re-map the per-corner normals
        mesh.flip_normals()
        keys_after = _loop_keys(mesh)
        src = np.argsort(keys_before, kind="stable")
        pos = np.searchsorted(keys_before[src], keys_after)
        pos = np.clip(pos, 0, len(src) - 1)
        wn = wn[src[pos]]
        log.append(f"'{label}': negative scale, winding flipped")

    _clear_custom_normals(mesh)
    if keep_explicit:
        mesh.normals_split_custom_set(wn.tolist())
    mesh.update()
    return mesh, loc, added


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #

def collect_objects(context, scope):
    vl = context.view_layer
    objs = []
    for ob in vl.objects:
        if ob.type not in GEOMETRY_TYPES and ob.type != 'EMPTY':
            continue
        if ob.name.startswith(TEMP_PREFIX) or not ob.visible_get(view_layer=vl):
            continue
        if scope == 'SELECTED' and not ob.select_get(view_layer=vl):
            continue
        objs.append(ob)
    return objs


def _layer_name(ob, scene):
    for c in ob.users_collection:
        if c != scene.collection:
            return c.name
    return None


def _unique_name(base, used):
    name, k = base, 2
    while name in used:
        name = f"{base}.{k:03d}"
        k += 1
    used.add(name)
    return name


def _empty_info(ob, unit):
    """Empties are rebuilt in Max as Point helpers from their full world matrix."""
    mw = ob.matrix_world
    # columns of the 4x4 (X, Y, Z axes and translation) are the rows of a Max Matrix3
    rows = [[mw[r][c] for r in range(3)] for c in range(4)]
    rows[3] = [v * unit for v in rows[3]]
    return {"display": ob.empty_display_type,
            "size": ob.empty_display_size * 2.0 * unit,  # Blender size is a radius
            "matrix": rows}


def export_job(context, settings):
    """Export the scene and write a job file. Returns (job dict, report dict)."""
    log = []
    scope = getattr(settings, "scope", 'VISIBLE')
    keep_explicit = bool(getattr(settings, "keep_explicit_normals", False))
    replace_prev = bool(getattr(settings, "replace_previous", True))
    include_instances = bool(getattr(settings, "include_instances", True))
    use_layers = bool(getattr(settings, "collections_as_layers", True))

    sources = collect_objects(context, scope)
    if not sources:
        raise RuntimeError("Nothing to send: no visible mesh, curve, text or empty objects")

    cleanup_old_jobs()
    job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    short = job_id[-6:]
    out_dir = job_dir()
    fbx_path = os.path.join(out_dir, job_id + ".fbx")
    tex_dir = os.path.join(out_dir, job_id + "_tex")
    os.makedirs(tex_dir, exist_ok=True)

    depsgraph = context.evaluated_depsgraph_get()
    src_scene = context.scene
    unit = src_scene.unit_settings.scale_length or 1.0

    temp_scene = bpy.data.scenes.new(TEMP_PREFIX + "Export_" + short)
    temp_coll = bpy.data.collections.new(TEMP_PREFIX + "Export_" + short)
    temp_scene.collection.children.link(temp_coll)
    us_src, us_dst = src_scene.unit_settings, temp_scene.unit_settings
    us_dst.system = us_src.system
    us_dst.scale_length = us_src.scale_length
    us_dst.length_unit = us_src.length_unit

    temp_objs, temp_meshes, temp_mats = [], [], []
    mat_infos = {}
    written_images = {}
    objects_out = []    # meshes, sent through the FBX
    empties_out = []    # rebuilt in Max as Point helpers
    parent_of = {}      # sent name -> source parent name, resolved at the end
    used = set()
    counter = {"mat": 0, "obj": 0, "sharp": 0}

    def add_mesh(name, ob_eval, mw, parent, layer, geometry_instance=False):
        res = build_export_mesh(ob_eval, mw, name, keep_explicit, log, depsgraph, geometry_instance)
        if res is None:
            log.append(f"'{name}': no faces, skipped")
            return
        mesh, loc, added = res
        counter["sharp"] += added
        temp_meshes.append(mesh)

        # one unique token material per slot, so IDs can never be merged or reordered
        slots = []
        mi = np.zeros(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("material_index", mi)
        mesh.materials.clear()  # note: this resets material_index, restored below
        for slot in list(ob_eval.material_slots) or [None]:
            counter["mat"] += 1
            token = f"BRIX{short}_M{counter['mat']:05d}"
            tm = bpy.data.materials.new(token)
            temp_mats.append(tm)
            mesh.materials.append(tm)
            real = slot.material if slot is not None else None
            if real is not None and real.name not in mat_infos:
                mat_infos[real.name] = material_info(real, tex_dir, log, written_images)
            slots.append({"token": token, "material": real.name if real else None})
        # clamp out-of-range face indices to the last slot (Blender does the same when drawing)
        np.clip(mi, 0, len(slots) - 1, out=mi)
        mesh.polygons.foreach_set("material_index", mi)

        counter["obj"] += 1
        token_name = f"BRIX{short}_O{counter['obj']:05d}"
        tob = bpy.data.objects.new(token_name, mesh)
        tob.matrix_world = Matrix.Translation(loc)
        temp_coll.objects.link(tob)
        temp_objs.append(tob)

        name = _unique_name(name, used)
        parent_of[name] = parent
        objects_out.append({"token": token_name, "name": name, "layer": layer, "slots": slots})

    try:
        for ob in sources:
            layer = _layer_name(ob, src_scene) if use_layers else None
            parent = ob.parent.name if ob.parent else None
            if ob.type == 'EMPTY':
                info = _empty_info(ob, unit)
                info.update(name=_unique_name(ob.name, used), layer=layer)
                parent_of[info["name"]] = parent
                empties_out.append(info)
            else:
                ob_eval = ob.evaluated_get(depsgraph)
                add_mesh(ob.name, ob_eval, ob_eval.matrix_world.copy(), parent, layer)

        if include_instances:
            instancers = {ob.name: ob for ob in sources}
            for inst in depsgraph.object_instances:
                if not inst.is_instance or inst.parent is None:
                    continue
                owner = instancers.get(inst.parent.original.name)
                iob = inst.object
                if owner is None or iob.type not in GEOMETRY_TYPES:
                    continue
                own_geometry = iob.original == owner
                if own_geometry and (owner.type != 'MESH' or iob.type != 'MESH'):
                    continue  # a curve or text object's own mesh, already sent above
                # instance data is only valid inside this loop, so bake it right away
                layer = _layer_name(owner, src_scene) if use_layers else None
                base = f"{owner.name}_instance" if own_geometry else f"{owner.name}_{iob.original.name}"
                add_mesh(base, iob, inst.matrix_world.copy(), owner.name, layer, own_geometry)

        for o in objects_out + empties_out:
            p = parent_of.get(o["name"])
            o["parent"] = p if p in parent_of and p != o["name"] else None

        if objects_out:
            with context.temp_override(scene=temp_scene, view_layer=temp_scene.view_layers[0]):
                bpy.ops.export_scene.fbx(
                    filepath=fbx_path,
                    use_selection=False,
                    use_visible=False,
                    use_active_collection=False,
                    collection=temp_coll.name,
                    object_types={'MESH'},
                    use_mesh_modifiers=False,
                    mesh_smooth_type='SMOOTH_GROUP',
                    use_mesh_edges=False,
                    use_tspace=False,
                    use_triangles=False,
                    use_custom_props=False,
                    global_scale=1.0,
                    apply_unit_scale=True,
                    apply_scale_options='FBX_SCALE_UNITS',
                    bake_space_transform=False,
                    axis_forward='Y',   # identity: Blender and Max are both Z-up, right-handed
                    axis_up='Z',
                    path_mode='ABSOLUTE',
                    embed_textures=False,
                    add_leaf_bones=False,
                    bake_anim=False,
                    colors_type='SRGB',
                )
        else:
            fbx_path = None
    finally:
        for o in temp_objs:
            bpy.data.objects.remove(o, do_unlink=True)
        for m in temp_meshes:
            if m.users == 0:
                bpy.data.meshes.remove(m)
        for m in temp_mats:
            if m.users == 0:
                bpy.data.materials.remove(m)
        bpy.data.collections.remove(temp_coll)
        bpy.data.scenes.remove(temp_scene)

    if not objects_out and not empties_out:
        raise RuntimeError("Nothing to send: the selected objects have no faces")

    job = {
        "version": JOB_VERSION,
        "addon_version": ADDON_VERSION,
        "id": job_id,
        "fbx": fbx_path,
        "blend": bpy.data.filepath,
        "keep_explicit_normals": keep_explicit,
        "replace_previous": replace_prev,
        "objects": objects_out,
        "empties": empties_out,
        "materials": mat_infos,
        "log": log,
    }
    report = {"objects": len(objects_out), "empties": len(empties_out),
              "materials": len(mat_infos), "sharp_added": counter["sharp"],
              "log": log, "fbx": fbx_path}
    return job, report


def write_job(job, target):
    """Write the job atomically. target: 'any' or a launch token."""
    job["target"] = target
    d = job_dir()
    final = os.path.join(d, job["id"] + ".job")
    tmp = final + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=1)
    os.replace(tmp, final)
    return final
