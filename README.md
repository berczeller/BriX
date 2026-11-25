# BriX

### Blender to 3ds Max bridge with automatic PBR materials, for Windows.

![Blender](https://img.shields.io/badge/Blender-5.2%2B-7fd3ff?style=flat-square) ![3ds Max](https://img.shields.io/badge/3ds%20Max-2027-ff8fa3?style=flat-square) ![Windows](https://img.shields.io/badge/Windows-x64-1a9fff?style=flat-square) ![Python](https://img.shields.io/badge/Python-3-c4a7ff?style=flat-square) ![Version](https://img.shields.io/badge/version-0.1.2-ffc36b?style=flat-square) ![License](https://img.shields.io/badge/license-GPL--3.0--or--later-6fd6a0?style=flat-square)

---

One button in Blender sends your visible objects to 3ds Max. Modifiers and transforms are baked, pivots stay where they are, shading becomes smoothing groups, empties become Dummy helpers, and materials are rebuilt as a Multi/Sub-Object with PBR sub-materials and textures assigned. Your Blender scene is never modified.

## Features

- Sends visible or selected objects into a running 3ds Max, or launches a new one
- Meshes, curves, text, surfaces and metaballs, converted with all modifiers applied
- Empties become Dummy helpers with their full transform, and the hierarchy is rebuilt
- Collection instances and Geometry Nodes instances come in as real objects
- Blender collections become Max layers
- Sharp edges, flat faces and custom normal splits become smoothing groups
- Material IDs follow Blender slot order, one shared Max material per Blender material
- PBR Material (Metal/Rough) sub-materials with textures picked up by suffix
- Re-sending replaces the previous copies instead of duplicating them

## Requirements

- Windows x64
- Blender 5.2 or newer
- 3ds Max 2027

## Install

### 1. Blender add-on

1. Get `BriX-0.1.2.zip` from the Releases page, or build it (see [Building from source](#building-from-source)).
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
2. Restart 3ds Max. The MAXScript Listener should print `[BriX] listener v0.1.2 running`.

This copies `scripts\BriX\brix_max.py` and `scripts\startup\brix_startup.ms`. After updating the add-on, click the button again so Max gets the new version.

## Usage

1. Open the **BriX** tab in the sidebar.
2. Pick **Visible Objects** (default) or **Selected Objects**.
3. Click **Send to 3ds Max**.

| Option | What it does |
|---|---|
| Open in new 3ds Max | Launches a fresh Max and imports there. Happens automatically when no running Max with the listener is found. |
| Keep custom normals (explicit) | Off: shading comes from smoothing groups only (clean, editable). On: custom normals like weighted normals also come in as explicit normals, which look exact but get lost if you edit the topology in Max. |
| Replace previous sends | Before merging, deletes objects and helpers from earlier BriX sends with the same name, so re-sending updates them. |
| Include instances | Sends collection instances and Geometry Nodes instances as real objects, parented to the object that instances them. |
| Collections as layers | Puts each object on a Max layer named after its Blender collection. Objects in the scene's root collection go to the current layer. |

The panel shows whether a running Max listener is detected and warns when its version doesn't match the add-on.

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

Missing maps are also searched for next to the found ones: if only `rock_bc.png` is connected, `rock_mg.tga` in the same folder is picked up too. Packed images, and images that only exist in memory (generated or painted and never saved), are written out next to the job. Materials without textures keep their Principled base color, metallic and roughness values.

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
5. Restores parenting between sent objects and puts them on layers named after their collections.

```
__init__.py          Blender UI, preferences, send and install operators
core.py              mesh baking, material and texture collection, FBX export, job files
max/brix_max.py      3ds Max listener and importer (pymxs)
max/brix_boot.ms     loads the listener in a Max launched by Blender
max/brix_startup.ms  starts the listener whenever Max opens
```

## Known limits

- **Object types:** cameras, lights, armatures and grease pencil aren't sent. Curves and text without faces (no bevel or extrude) are skipped.
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
| Other errors | Blender notes go to the system console (**Window > Toggle System Console**). Max errors go to the MAXScript Listener (**F11**). |

## Building from source

Blender 5.2 or newer.

    blender --command extension validate
    blender --command extension build --output-dir release

The build writes `brix-0.1.2.zip` to `release/`.

## Credits

**Author:** Tom Berczeller

## License

Released under the [GNU General Public License v3.0 or later](LICENSE), © 2026 Tom Berczeller.

Blender is a trademark of the Blender Foundation. 3ds Max is a trademark of Autodesk, Inc. BriX is not affiliated with either.
