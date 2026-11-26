# BriX (Blender to 3ds Max Bridge)
# Author: Tom Berczeller
# Texture map types, recognized by the suffix in the file name.

import os
import re

IMAGE_EXTS = (".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg", ".exr", ".dds", ".bmp", ".psd", ".hdr")

# map type -> file name suffixes. Studio short forms, Substance, Quixel, Unreal and Unity names.
ALIASES = {
    "bc":      ["bc", "basecolor", "base_color", "albedo", "alb", "diffuse", "diff", "dif",
                "col", "color", "colour"],
    "ao":      ["ao", "occlusion", "ambientocclusion", "ambient_occlusion", "occ"],
    "nrm":     ["nrm", "normal", "norm", "nor", "nml", "nm", "normalgl", "normal_gl", "nrm_gl"],
    "nrm_dx":  ["normaldx", "normal_dx", "nrmdx", "nrm_dx", "ndx"],
    "mg":      ["mg"],
    "metal":   ["metallic", "metalness", "metal", "met", "mtl"],
    "rough":   ["roughness", "rough", "rgh"],
    "gloss":   ["glossiness", "gloss", "gls", "smoothness", "smooth"],
    "orm":     ["orm", "arm", "aorm", "occlusionroughnessmetallic", "occlusion_roughness_metallic"],
    "mra":     ["mra"],
    "rma":     ["rma"],
    "mask":    ["maskmap", "mask_map"],
    "emit":    ["emissive", "emission", "emit", "glow"],
    "opacity": ["opacity", "alpha", "transparency", "opac"],
}

# one-letter suffixes (Unreal style, T_Rock_N) only count as the last part of the name
LETTERS = {"d": "bc", "n": "nrm", "m": "metal", "r": "rough"}

# packed maps: which image channel holds which value ("A" = alpha)
PACKED = {
    "mg":   {"metal": "RGB", "gloss": "A"},
    "orm":  {"ao": "R", "rough": "G", "metal": "B"},
    "mra":  {"metal": "R", "rough": "G", "ao": "B"},
    "rma":  {"rough": "R", "metal": "G", "ao": "B"},
    "mask": {"metal": "R", "ao": "G", "gloss": "A"},
}

KINDS = tuple(ALIASES)

_ALIAS_KIND = {a: k for k, names in ALIASES.items() for a in names}
_SEP = r"[_\-. ]"
_ALIAS_RE = re.compile(
    r"(?:^|(?<=" + _SEP + r"))(" + "|".join(sorted(map(re.escape, _ALIAS_KIND), key=len, reverse=True))
    + r")(?=" + _SEP + r"|$)", re.IGNORECASE)
_LAST_RE = re.compile(r"(?:^|(?<=" + _SEP + r"))([a-z])$", re.IGNORECASE)


def _match(stem):
    """(kind, start, end) of the suffix that decides the map type, or None.
    The first part of a name never counts, so metal_plate.png stays unclassified."""
    stem = re.sub(_SEP, "_", stem)
    m = _LAST_RE.search(stem)
    if m and m.group(1).lower() in LETTERS and m.start() > 0:
        return LETTERS[m.group(1).lower()], m.start(1), m.end(1)
    hits = [h for h in _ALIAS_RE.finditer(stem) if h.start() > 0]
    if not hits:
        return None
    h = hits[-1]
    return _ALIAS_KIND[h.group(1).lower()], h.start(1), h.end(1)


def classify(path):
    """Map type of an image file from its name: rock_BaseColor.png -> 'bc'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _match(stem)
    return m[0] if m else None


def find_siblings(path, found):
    """Fill missing map types with files next to a known map: rock_bc.png -> rock_nrm.tga."""
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _match(stem)
    if not m:
        return
    prefix, rest = stem[:m[1]], stem[m[2]:]
    try:
        files = os.listdir(folder)
    except OSError:
        return
    lower = {f.lower(): f for f in files}
    letters = {v: k for k, v in LETTERS.items()}
    for kind, names in ALIASES.items():
        if kind in found:
            continue
        names = names + ([letters[kind]] if kind in letters and not rest else [])
        for alias in names:
            hit = next((lower[c] for ext in IMAGE_EXTS
                        if (c := (prefix + alias + rest + ext).lower()) in lower), None)
            if hit:
                found[kind] = os.path.join(folder, hit)
                break


def resolve(textures):
    """Pick one source per material value from the maps found.
    Returns {value: (path, channel)} for 'metal', 'rough', 'gloss' and 'ao'.
    Single maps win over packed ones; a roughness map wins over a glossiness one."""
    out = {}
    for value, kind in (("metal", "metal"), ("rough", "rough"), ("ao", "ao")):
        if kind in textures:
            out[value] = (textures[kind], "RGB")
    if "rough" not in out and "gloss" in textures:
        out["gloss"] = (textures["gloss"], "RGB")
    for kind in ("orm", "mra", "rma", "mg", "mask"):
        if kind not in textures:
            continue
        for value, channel in PACKED[kind].items():
            if value in out or (value in ("rough", "gloss") and ("rough" in out or "gloss" in out)):
                continue
            out[value] = (textures[kind], channel)
    return out
