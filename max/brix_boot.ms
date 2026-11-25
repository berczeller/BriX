-- BriX (Blender to 3ds Max Bridge) v0.1.1 - Tom Berczeller
-- Boot script: Blender launches 3ds Max with  -U MAXScript brix_boot.ms
-- It loads the listener from the add-on folder (BRIX_DIR), which then
-- picks up the job that Blender wrote for this new instance.
(
    python.Execute "import os, sys\np = os.environ.get('BRIX_DIR', '')\nif p and p not in sys.path: sys.path.insert(0, p)\nimport brix_max\nbrix_max.start_listener()"
)
