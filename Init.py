# SPDX-License-Identifier: MIT
"""
GrokCAD console-mode initialisation.

FreeCAD imports this file for every session, including ``freecadcmd``.
It must NOT import FreeCADGui or PySide.
"""

import os
import sys

import FreeCAD as App  # type: ignore


def _grokcad_dir():
    """Locate this workbench. Init.py is exec()'d — ``__file__`` is often missing."""
    try:
        cand = os.path.join(App.getUserAppDataDir(), "Mod", "GrokCAD")
        if os.path.isfile(os.path.join(cand, "Init.py")):
            return cand
    except Exception:
        pass
    try:
        import inspect

        path = inspect.getfile(inspect.currentframe())
        if path and not str(path).startswith("<"):
            d = os.path.dirname(os.path.abspath(path))
            if os.path.isfile(os.path.join(d, "Init.py")):
                return d
    except Exception:
        pass
    return os.path.join(App.getUserAppDataDir(), "Mod", "GrokCAD")


_WB_DIR = _grokcad_dir()
if _WB_DIR not in sys.path:
    sys.path.insert(0, _WB_DIR)

# Expose pip --user site-packages (openai, pillow) to FreeCAD's interpreter.
try:
    import site

    user_site = site.getusersitepackages()
    if user_site and os.path.isdir(user_site) and user_site not in sys.path:
        sys.path.append(user_site)
        site.addsitedir(user_site)
except Exception:
    pass

App.Console.PrintMessage("GrokCAD: module loaded (console).\n")
