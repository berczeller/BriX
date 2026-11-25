-- BriX (Blender to 3ds Max Bridge) v0.1.1 - Tom Berczeller
-- Startup script: installed into <Max user scripts>\startup by the Blender add-on.
-- Starts the BriX listener every time 3ds Max opens, so "Send to 3ds Max"
-- from Blender merges straight into this session.
(
    local dir = substituteString ((getDir #userScripts) + "\\BriX") "\\" "/"
    python.Execute ("import sys\np = '" + dir + "'\nif p not in sys.path: sys.path.insert(0, p)\nimport brix_max\nbrix_max.start_listener()")
)
