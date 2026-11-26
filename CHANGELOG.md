# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog and the project uses Semantic Versioning.

## [0.2.0] - 2026-11-26

### Added
- Send from 3ds Max to Blender: a dockable BriX panel in Max with the same options as the Blender one, and a receiver in the Blender add-on that imports with materials, pivots, smoothing, helpers and layers
- Max materials (PBR Metal/Rough, Physical, Standard) rebuilt in Blender as Principled BSDF, packed channels and DirectX normal maps included
- Many more texture suffixes: Substance, Quixel, Unreal and Unity names such as `_BaseColor`, `_Albedo`, `_D`, `_Normal`, `_N`, `_NormalDX`, `_Roughness`, `_Metallic`, `_Glossiness`, `_Emissive` and `_Opacity`
- Packed maps: ORM/ARM, MRA, RMA and Unity MaskMap, split into their channels in Max with Color Correction maps
- Collections can become Max layers (now nested like the collections), Dummy helpers, or both
- Weighted normals stay editable: meshes with a Weighted Normal modifier get the native 3ds Max Weighted Normals modifier on top of the Editable Poly, and the other way around
- Materials are matched by name in both directions: a material that already exists in the receiving scene is used as it is, so sends no longer create duplicates (copies with the same name in Max, `.001` copies in Blender). Can be turned off with **Reuse materials by name**

### Fixed
- The glossiness switch of PBR materials is now set with its real values (1 glossiness, 2 roughness)

## [0.1.2] - 2026-11-25

### Changed
- Empties now come into 3ds Max as Dummy helpers instead of Point helpers, with the box sized to the empty's display size

## [0.1.1] - 2026-11-25

### Fixed
- Texture folders of old jobs in `%TEMP%\BriX` are now cleaned up after 24 hours instead of piling up

## [0.1.0] - 2026-11-25

### Added
- One-click send of visible or selected objects from Blender to 3ds Max 2027
- Meshes, curves, text, surfaces and metaballs, with modifiers, rotation and scale baked and pivots kept in place
- Empties as Point helpers with their full transform, display style and size, with the hierarchy rebuilt in Max
- Collection instances and Geometry Nodes instances sent as real objects
- Blender collections as Max layers
- Normal splits converted to smoothing groups, with optional explicit normals
- Multi/Sub-Object materials with PBR Metal/Rough sub-materials and textures found by suffix (`_bc`, `_ao`, `_nrm`, `_mg`), including packed and unsaved images
- Listener for a running 3ds Max, or a fresh Max launched when none is found, with a version check in the Blender panel
- Replace previous sends by object name
