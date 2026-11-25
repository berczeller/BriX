# BriX (Blender to 3ds Max Bridge)
# Author: Tom Berczeller
# Version: 0.1.1

import glob
import os
import shutil
import subprocess
import uuid

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty

from . import core

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_DIR = os.path.join(ADDON_DIR, "max")
DEFAULT_MAX_EXE = r"C:\Program Files\Autodesk\3ds Max 2027\3dsmax.exe"


def _detect_max_scripts_dir():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Autodesk", "3dsMax", "2027 - 64bit")
    hits = sorted(glob.glob(os.path.join(base, "*", "scripts")))
    for h in hits:
        if os.path.basename(os.path.dirname(h)).upper() == "ENU":
            return h
    return hits[0] if hits else ""


def _remove_legacy_listener(root):
    """Delete the listener from before the rename, so two listeners never run in one Max."""
    removed = []
    for path in (os.path.join(root, "startup", "bm_bridge_startup.ms"),
                 os.path.join(root, "BMBridge")):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
            else:
                continue
            removed.append(path)
        except OSError:
            pass
    return removed


def prefs(context):
    return context.preferences.addons[__package__].preferences


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

class BRIX_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    max_exe: StringProperty(
        name="3ds Max executable", subtype='FILE_PATH', default=DEFAULT_MAX_EXE,
        description="Path to 3dsmax.exe")
    max_scripts_dir: StringProperty(
        name="Max user scripts folder", subtype='DIR_PATH', default="",
        description="Your 3ds Max 2027 user 'scripts' folder. Leave empty to auto-detect "
                    "(%LOCALAPPDATA%\\Autodesk\\3dsMax\\2027 - 64bit\\ENU\\scripts)")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "max_exe")
        col.prop(self, "max_scripts_dir")
        detected = self.max_scripts_dir or _detect_max_scripts_dir()
        col.label(text=f"Scripts folder in use: {detected or 'not found'}")
        row = col.row()
        row.scale_y = 1.3
        row.operator("brix.install_listener", icon='IMPORT')


class BRIX_Settings(bpy.types.PropertyGroup):
    scope: EnumProperty(
        name="Send",
        items=[('VISIBLE', "Visible Objects", "All visible meshes, curves, text and empties in the view layer"),
               ('SELECTED', "Selected Objects", "Only visible, selected meshes, curves, text and empties")],
        default='VISIBLE')
    include_instances: BoolProperty(
        name="Include instances",
        description="Send collection instances and Geometry Nodes instances as real objects, "
                    "parented to the object that instances them",
        default=True)
    collections_as_layers: BoolProperty(
        name="Collections as layers",
        description="Put each object in a 3ds Max layer named after its Blender collection",
        default=True)
    new_instance: BoolProperty(
        name="Open in new 3ds Max",
        description="Launch a fresh 3ds Max and import there, instead of merging into the "
                    "running one. Used automatically when no running Max is found",
        default=False)
    keep_explicit_normals: BoolProperty(
        name="Keep custom normals (explicit)",
        description="Also bring custom split normals (weighted normals etc.) into Max as "
                    "explicit normals. They look exact, but are lost if you edit topology in Max. "
                    "Off: shading comes from smoothing groups only",
        default=False)
    replace_previous: BoolProperty(
        name="Replace previous sends",
        description="In Max, delete objects from earlier BriX sends with the same name "
                    "before merging, so re-sending updates them instead of duplicating",
        default=True)


# --------------------------------------------------------------------------- #
# Operators
# --------------------------------------------------------------------------- #

def launch_max(context, token):
    exe = bpy.path.abspath(prefs(context).max_exe)
    if not os.path.isfile(exe):
        raise RuntimeError(f"3ds Max not found at '{exe}'. Set the path in the add-on preferences")
    boot = os.path.join(MAX_DIR, "brix_boot.ms")
    env = dict(os.environ)
    env["BRIX_TOKEN"] = token
    env["BRIX_DIR"] = MAX_DIR
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe, "-U", "MAXScript", boot], env=env, close_fds=True,
                     creationflags=flags, cwd=os.path.dirname(exe))


class BRIX_OT_send(bpy.types.Operator):
    bl_idname = "brix.send_to_max"
    bl_label = "Send to 3ds Max"
    bl_description = "Bake and export the objects, then merge them into 3ds Max with materials rebuilt"
    bl_options = {'REGISTER'}

    def execute(self, context):
        s = context.scene.brix
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, "Switch to Object Mode first")
            return {'CANCELLED'}
        use_new = s.new_instance or not core.running_listener()
        if use_new and not os.path.isfile(bpy.path.abspath(prefs(context).max_exe)):
            self.report({'ERROR'}, "No running 3ds Max listener and 3dsmax.exe not found. "
                                   "Set the path in the add-on preferences")
            return {'CANCELLED'}
        try:
            job, rep = core.export_job(context, s)
        except Exception as e:
            self.report({'ERROR'}, f"BriX export failed: {e}")
            return {'CANCELLED'}

        for line in rep["log"]:
            print("[BriX]", line)

        try:
            if use_new:
                token = uuid.uuid4().hex
                job_file = core.write_job(job, token)
                try:
                    launch_max(context, token)
                except Exception:
                    os.remove(job_file)
                    raise
                where = "a new 3ds Max" if s.new_instance else "a new 3ds Max (no running Max listener found)"
            else:
                core.write_job(job, "any")
                where = "the running 3ds Max"
        except Exception as e:
            self.report({'ERROR'}, f"BriX: {e}")
            return {'CANCELLED'}

        warn = f", {len(rep['log'])} note(s) in console" if rep["log"] else ""
        empties = f", {rep['empties']} empties" if rep["empties"] else ""
        self.report({'INFO'}, f"BriX: sent {rep['objects']} objects{empties}, "
                              f"{rep['materials']} materials to {where}{warn}")
        return {'FINISHED'}


class BRIX_OT_install_listener(bpy.types.Operator):
    bl_idname = "brix.install_listener"
    bl_label = "Install 3ds Max Listener"
    bl_description = ("Copy the BriX listener into your 3ds Max 2027 user scripts so a "
                      "running Max picks up sends automatically. Restart Max afterwards")

    def execute(self, context):
        root = bpy.path.abspath(prefs(context).max_scripts_dir) or _detect_max_scripts_dir()
        if not root or not os.path.isdir(root):
            self.report({'ERROR'}, "Max user scripts folder not found. Set it in the preferences")
            return {'CANCELLED'}
        try:
            dst = os.path.join(root, "BriX")
            os.makedirs(dst, exist_ok=True)
            shutil.copy2(os.path.join(MAX_DIR, "brix_max.py"), dst)
            startup = os.path.join(root, "startup")
            os.makedirs(startup, exist_ok=True)
            shutil.copy2(os.path.join(MAX_DIR, "brix_startup.ms"), startup)
        except OSError as e:
            self.report({'ERROR'}, f"Install failed: {e}")
            return {'CANCELLED'}
        removed = _remove_legacy_listener(root)
        if removed:
            print("[BriX] removed old listener files:", ", ".join(removed))
        self.report({'INFO'}, f"Listener installed in {root}. Restart 3ds Max to activate it")
        return {'FINISHED'}


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

class BRIX_PT_panel(bpy.types.Panel):
    bl_label = "BriX"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BriX"

    def draw(self, context):
        s = context.scene.brix
        col = self.layout.column()
        col.prop(s, "scope", text="")
        col.prop(s, "new_instance")
        col.prop(s, "keep_explicit_normals")
        col.prop(s, "replace_previous")
        col.prop(s, "include_instances")
        col.prop(s, "collections_as_layers")
        col.separator()
        row = col.row()
        row.scale_y = 1.6
        row.operator("brix.send_to_max", icon='EXPORT')
        version = core.running_listener()
        if version is None:
            col.label(text="Max listener: not detected", icon='INFO')
        elif version == core.ADDON_VERSION:
            col.label(text=f"Max listener: running (v{version})", icon='CHECKMARK')
        else:
            col.label(text=f"Max listener v{version} is outdated", icon='ERROR')
            col.label(text="Reinstall it in the add-on preferences and restart Max")


classes = (BRIX_Preferences, BRIX_Settings, BRIX_OT_send, BRIX_OT_install_listener, BRIX_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.brix = PointerProperty(type=BRIX_Settings)


def unregister():
    del bpy.types.Scene.brix
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
