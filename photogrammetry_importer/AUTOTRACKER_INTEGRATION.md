# AutoTracker integration notes

This directory contains the user-supplied Photogrammetry Importer 3.2.1 addon.
Original authorship and licensing remain in the accompanying source and license files.

Local compatibility changes:
- opengl/utility.py: preserve OpenGL point handle data in background exports and defer draw callbacks until a graphical session opens the file.
- importers/point_utility.py: identify geometry sockets by name and use Blender 5.2 typed modifier socket values; preserve the older Blender API path.
- file_handlers/utility.py: format the actual radial distortion values in warnings.

The application loads this addon from its distribution directory for the current Blender process. It does not install a copy into the user's global Blender scripts directory or save global preferences.
