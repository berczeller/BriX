# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog and the project uses Semantic Versioning.

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
