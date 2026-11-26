# BriX

### Blender to 3ds Max bridge (and back) with automatic PBR materials, for Windows.

![Blender](https://img.shields.io/badge/Blender-5.2%2B-7fd3ff?style=flat-square) ![3ds Max](https://img.shields.io/badge/3ds%20Max-2027-ff8fa3?style=flat-square) ![Windows](https://img.shields.io/badge/Windows-x64-1a9fff?style=flat-square) ![Python](https://img.shields.io/badge/Python-3-c4a7ff?style=flat-square) ![Version](https://img.shields.io/badge/version-0.2.0-ffc36b?style=flat-square) ![License](https://img.shields.io/badge/license-GPL--3.0--or--later-6fd6a0?style=flat-square)

---

One button in Blender sends your visible objects to 3ds Max. Modifiers and transforms are baked, pivots stay where they are, shading becomes smoothing groups, empties become Dummy helpers, and materials are rebuilt as a Multi/Sub-Object with PBR sub-materials and textures assigned. A BriX panel in 3ds Max does the same in the other direction. Neither scene is modified by sending.

## Features

- Sends visible or selected objects into a running 3ds Max, or launches a new one
- Sends from 3ds Max back to Blender with the same options, from a dockable panel
- Meshes, curves, text, surfaces and metaballs, converted with all modifiers applied
- Empties become Dummy helpers with their full transform, and the hierarchy is rebuilt
- Collection instances and Geometry Nodes instances come in as real objects
- Blender collections become nested Max layers, Dummy helpers or both, and Max layers become collections, empties or both
- Weighted normals stay editable on both sides through each app's own Weighted Normals modifier
- Sharp edges, flat faces and custom normal splits become smoothing groups
- Material IDs follow Blender slot order, one shared Max material per Blender material
- PBR Material (Metal/Rough) sub-materials with textures picked up by suffix, from studio short forms to Substance, Quixel, Unreal and Unity names, packed ORM/MRA/MaskMap maps included
- Re-sending replaces the previous copies instead of duplicating them
- Materials that already exist in the receiving scene are matched by name and reused as they are, in both directions

## Requirements

- Windows x64
- Blender 5.2 or newer
- 3ds Max 2027

## Install

### 1. Blender add-on

1. Get `BriX-0.2.0.zip` from the Releases page, or build it (see [Building from source](#building-from-source)).
2. In Blender, go to **Edit > Preferences > Get Extensions**, open the dropdown at the top right and pick **Install from Disk...**.
3. Select the zip. The panel shows up in the 3D Viewport sidebar (**N**) under the **BriX** tab.

> [!NOTE]
> GitHub's automatic "Source code" archives are not installable add-ons. Use the release zip.

### 2. Set the 3ds Max paths

1. Open **Edit > Preferences > Add-ons > BriX** and expand it.
2. Set **3ds Max executable** to your `3dsmax.exe`. The default is `C:\Program Files\Autodesk\3ds Max 2027\3dsmax.exe`.
3. **Max user scripts folder** can usually stay empty. It's auto-detected as `%LOCALAPPDATA%\Autodesk\3dsMax\2027 - 64bit\ENU\scripts`. If your Max uses another language folder, or the label says "not found", point it at your `scripts` folder by hand.

### 3. Install the 3ds Max listener (once)

1. In the same preferences panel, click **Install 3ds Max Listener**.
2. Restart 3ds Max. The MAXScript Listener should print `[BriX] listener v0.2.0 running` and the BriX panel docks on the right.

This copies `scripts\BriX\brix_max.py`, `scripts\BriX\brix_send.py` and `scripts\startup\brix_startup.ms`. After updating the add-on, click the button again so Max gets the new version.

If you close the panel, bring it back with the **BriX** macro (category **BriX** in **Customize > Hotkey Editor** or **Customize User Interface**), which you can put on a toolbar, menu or hotkey.

## Usage

1. Open the **BriX** tab in the sidebar.
2. Pick **Visible Objects** (default) or **Selected Objects**.
3. Click **Send to 3ds Max**.

| Option | What it does |
|---|---|
| Open in new 3ds Max | Launches a fresh Max and imports there. Happens automatically when no running Max with the listener is found. |
| Keep custom normals (explicit) | Off: shading comes from smoothing groups only (clean, editable). On: custom normals like weighted normals also come in as explicit normals, which look exact but get lost if you edit the topology in Max. |
| Replace previous sends | Before merging, deletes objects and helpers from earlier BriX sends with the same name, so re-sending updates them. |
| Reuse materials by name | If Max already has a material with the same name as a Blender one, that material is used as it is, untouched, instead of building a new one. Off: always rebuilt from Blender. |
| Include instances | Sends collection instances and Geometry Nodes instances as real objects, parented to the object that instances them. |
| Collections | **Layers**: each collection becomes a Max layer, nested like the collections. **Dummies**: each collection becomes a Dummy helper at the origin, with its objects and child collections parented to it. **Layers and Dummies**: both. **Nothing**: everything goes to the current layer. Objects in the scene's root collection always go to the current layer. |
| Weighted normals | **From Modifier**: objects with a Weighted Normal modifier get the 3ds Max Weighted Normals modifier. **All Meshes**: every mesh gets it. **Off**: no modifier. |

The panel shows whether a running Max listener is detected and warns when its version doesn't match the add-on.

### Weighted normals

Explicit normals in 3ds Max break as soon as you edit the topology. BriX avoids that: the mesh comes in as an Editable Poly with smoothing groups only, and the native **Weighted Normals** modifier goes on top of it with the mode taken from Blender (face area, corner angle or both) and smoothing groups respected. Edit the Editable Poly below the modifier as much as you want, the weighted shading is recomputed every time. Collapse the stack only when you want the normals frozen.

### Matching materials

With **Reuse materials by name** on (the default), every material is looked up by its exact name in the receiving scene before anything is built. If `SecurityStation` exists in both apps, sending in either direction assigns the existing one and changes nothing in it: no new copy in Max, no `SecurityStation.001` in Blender. Materials from earlier sends count too, so edits you made to them after the first send are kept when you send again. In Max, every material on a scene node is checked, including sub-materials of Multi/Sub-Objects.

Turn the option off when you changed the material in the source app and want it rebuilt. Names are case-sensitive and must match exactly.

### Empties

Empties don't go through the FBX. BriX rebuilds each one in Max as a **Dummy** helper with the exact world transform (rotation and scale included). The Dummy box is as wide as the empty's display size, whatever its display type in Blender.

Parenting works in every direction: meshes under empties, empties under meshes, empties under empties. A collection instance empty comes in as a Dummy with its instanced objects as children.

### Texture suffixes

Textures are taken from the material's Image Texture nodes, node groups included, and sorted by the suffix in the file name:

| Suffix | PBR slot | Notes |
|---|---|---|
| `_bc` | Base Color | sRGB |
| `_ao` | AO | linear |
| `_nrm` | Normal | linear, Flip Green on (OpenGL to Max) |
| `_mg` | Metalness + Roughness | Metalness reads RGB, roughness reads the alpha with the material in Glossiness mode |

More names are recognized for each slot, so textures from Substance, Quixel, Unreal or Unity work as they are. Single letters (`_D`, `_N`, `_M`, `_R`) only count as the last part of the name, and the first part never counts, so `metal_plate.png` isn't taken for a metalness map.

| Slot | Suffixes |
|---|---|
| Base Color | `_basecolor`, `_base_color`, `_albedo`, `_alb`, `_diffuse`, `_diff`, `_dif`, `_col`, `_color`, `_colour`, `_d` |
| AO | `_occlusion`, `_ambientocclusion`, `_occ` |
| Normal (OpenGL) | `_normal`, `_norm`, `_nor`, `_nml`, `_nm`, `_normalgl`, `_n` |
| Normal (DirectX) | `_normaldx`, `_normal_dx`, `_nrmdx`, `_ndx`. Flip Green stays off |
| Metalness | `_metallic`, `_metalness`, `_metal`, `_met`, `_mtl`, `_m` |
| Roughness | `_roughness`, `_rough`, `_rgh`, `_r` |
| Glossiness | `_glossiness`, `_gloss`, `_gls`, `_smoothness`, `_smooth`. Used as inverted roughness when there's no roughness map |
| Emission | `_emissive`, `_emission`, `_emit`, `_glow` |
| Opacity | `_opacity`, `_alpha`, `_transparency`, `_opac` |

Packed maps are split into their channels with Color Correction maps, which every renderer supports. A single map always wins over a packed one.

| Suffix | Red | Green | Blue | Alpha |
|---|---|---|---|---|
| `_orm`, `_arm`, `_occlusionroughnessmetallic` | AO | Roughness | Metalness | |
| `_mra` | Metalness | Roughness | AO | |
| `_rma` | Roughness | Metalness | AO | |
| `_maskmap` (Unity HDRP) | Metalness | AO | | Smoothness |
| `_mg` | Metalness (RGB) | | | Glossiness |

Missing maps are also searched for next to the found ones: if only `rock_bc.png` is connected, `rock_mg.tga` or `rock_Normal.png` in the same folder is picked up too. Packed images, and images that only exist in memory (generated or painted and never saved), are written out next to the job. Materials without textures keep their Principled base color, metallic and roughness values.

## 3ds Max to Blender

The BriX panel in Max has the same controls as the one in Blender:

| Option | What it does |
|---|---|
| Visible / Selected Objects | Geometry and helpers to send. Hidden objects are skipped. |
| Open in new Blender | Launches a fresh Blender and imports there. Happens automatically when no running Blender with BriX is found. |
| Keep custom normals (explicit) | Off: shading comes from smoothing groups only. On: the exact Max normals come in as custom normals. |
| Replace previous sends | Before importing, deletes objects from earlier BriX sends with the same name. |
| Reuse materials by name | If Blender already has a material with the same name as a Max one, it's used as it is instead of making a `.001` copy. Off: always rebuilt from Max. |
| Layers | **Collections** (nested like the layers), **Empties** at the origin with the objects parented, both, or nothing. The default layer `0` goes to the active collection. |
| Weighted normals | **From Modifier**: meshes with a Weighted Normals modifier in Max get a Weighted Normal modifier in Blender. **All Meshes** or **Off**. |
| Settings | Path to `blender.exe`. Leave it empty to auto-detect: the last Blender that ran BriX, or the newest one in Program Files. |

In Blender, receiving is on by default and can be turned off with **Receive from 3ds Max** in the add-on preferences. The BriX sidebar panel shows the result of the last receive.

What comes across:

- Geometry with modifiers applied, at the exact Max transform, pivot position and orientation included
- Smoothing groups as sharp edges (or exact custom normals)
- Dummy and Point helpers as empties with a matching display type and size, with the hierarchy
- One material slot per used material ID, in ID order, each with a Principled BSDF material rebuilt from the Max one: PBR Material (Metal/Rough), Physical Material and Standard. Maps split through Color Correction or OSL channel outputs are read back as the right image channel, glossiness becomes roughness through an inverting node, and DirectX normal maps get their green channel flipped. AO maps are kept in the material, unconnected, since Principled has no AO input
- Standard materials with only a diffuse map also pick up the other maps next to it by suffix

## How it works

**In Blender**

1. Collects visible (or selected) meshes, curves, text, surfaces, metaballs and empties, plus the instances they generate.
2. Builds temporary meshes from the evaluated objects, so every modifier is applied.
3. Bakes rotation and scale into the vertices and keeps the location, so each pivot stays where it is (aligned to world axes). Mirrored objects get their winding fixed.
4. Marks every edge where the normals split as sharp, so the FBX gets matching smoothing groups.
5. Gives every material slot a unique temporary material, so material IDs can't be merged or reordered on the way.
6. Exports the FBX with identity axis conversion (both apps are Z-up, right-handed) and real unit scaling.
7. Writes a job file to `%TEMP%\BriX` and deletes all temporary data.

**In 3ds Max**

1. Merges the FBX into the current scene with automatic unit conversion, so 1 m stays 1 m whatever your system units are.
2. Converts to Editable Poly and resets normals to smoothing groups (unless explicit normals are on).
3. Renames objects back to their Blender names and builds a Dummy helper for every empty.
4. Builds one Multi/Sub-Object per object. ID N is Blender slot N, and each sub-material is named after the Blender material.
5. Adds the Weighted Normals modifier where asked, restores parenting between sent objects and builds layers and Dummies for the collections.

**From 3ds Max**

1. Renames the sent nodes and gives each one a temporary Multi/Sub-Object with one tagged sub-material per used material ID, exports the FBX, then puts every name and material back.
2. Describes the real materials, layers, helpers and transforms in a job file for Blender.
3. Blender imports the FBX, turns normal splits into sharp edges, rebuilds the materials, puts each object on its exact Max transform and builds the collections.

```
__init__.py          Blender UI, preferences, send and install operators
core.py              mesh baking, material and texture collection, FBX export, job files
receive.py           Blender receiver for jobs sent from 3ds Max
textures.py          texture suffixes and packed channel layouts
max/brix_max.py      3ds Max listener and importer (pymxs)
max/brix_send.py     3ds Max panel and exporter for sending to Blender
max/brix_boot.ms     loads the listener in a Max launched by Blender
max/brix_startup.ms  starts the listener whenever Max opens
```

## Known limits

- **Object types:** cameras, lights, armatures and grease pencil aren't sent. Curves and text without faces (no bevel or extrude) are skipped. From Max, shapes (splines, text) aren't sent; add an Extrude or make them renderable and convert them first.
- **Hierarchy** is only kept between sent objects. If a parent isn't sent (hidden, or not selected), its children come in at the root with their world position.
- **Pivots** of meshes keep their position but are world-aligned, because rotation is baked into the mesh. Empties keep their full rotation.
- **Custom normals:** smoothing groups can't represent smooth custom normals such as weighted normals. Use the explicit normals option for those.
- **Busy Max:** if Max is busy for a long time (rendering, for example), the listener heartbeat goes stale and Blender launches a new Max. Wait for Max to be idle, or tick **Open in new 3ds Max**.
- **PBR property names:** if a property name differs in your Max build, BriX logs which slot it couldn't set, along with the material's property list.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No running 3ds Max listener and 3dsmax.exe not found" | Set the executable path in the add-on preferences. |
| Nothing happens in Max | Check that the listener is running (MAXScript Listener, or the status line in the Blender panel). Job files sit in `%TEMP%\BriX`. |
| Nothing happens in Blender | Check **Receive from 3ds Max** in the add-on preferences and the status line in the Max panel. A Blender busy rendering doesn't pick up jobs until it's idle. |
| Other errors | Blender notes go to the system console (**Window > Toggle System Console**). Max errors go to the MAXScript Listener (**F11**). |

## Building from source

Blender 5.2 or newer.

    blender --command extension validate
    blender --command extension build --output-dir release

The build writes `brix-0.2.0.zip` to `release/`.

## Credits

**Author:** Tom Berczeller

## License

Released under the [GNU General Public License v3.0 or later](LICENSE), © 2026 Tom Berczeller.

Blender is a trademark of the Blender Foundation. 3ds Max is a trademark of Autodesk, Inc. BriX is not affiliated with either.
