# SPDX-License-Identifier: MIT
"""
GrokCAD GUI initialisation.

FreeCAD execs this file (it is NOT imported as a module) and injects
``Gui`` and ``Workbench`` into the namespace. Do not wrap this file in a
package import — the class must be defined at top level.

FreeCAD 1.1 execs this file *without* ``__file__`` and may evaluate class
attributes in a restricted namespace. Do not reference extra module
globals from the class body (``Icon = _ICON`` will crash).
"""

import os
import sys

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore


def _grokcad_dir():
    """Locate this workbench without needing ``__file__``."""
    try:
        cand = os.path.join(App.getUserAppDataDir(), "Mod", "GrokCAD")
        if os.path.isfile(os.path.join(cand, "InitGui.py")):
            return cand
    except Exception:
        pass
    try:
        import inspect

        path = inspect.getfile(inspect.currentframe())
        if path and not str(path).startswith("<"):
            d = os.path.dirname(os.path.abspath(path))
            if os.path.isfile(os.path.join(d, "InitGui.py")):
                return d
    except Exception:
        pass
    try:
        cand = os.path.join(App.getHomePath(), "Mod", "GrokCAD")
        if os.path.isfile(os.path.join(cand, "InitGui.py")):
            return cand
    except Exception:
        pass
    return os.path.join(App.getUserAppDataDir(), "Mod", "GrokCAD")


_WB_DIR = _grokcad_dir()
if _WB_DIR not in sys.path:
    sys.path.insert(0, _WB_DIR)

try:
    import site

    user_site = site.getusersitepackages()
    if user_site and os.path.isdir(user_site) and user_site not in sys.path:
        sys.path.append(user_site)
        site.addsitedir(user_site)
except Exception:
    pass

class GrokCADWorkbench(Workbench):  # noqa: F821
    """Native workbench: Grok CAD Agent."""

    MenuText = "Grok CAD Agent"
    ToolTip = "Agentic mechanical design powered by xAI Grok"
    Icon = """/* XPM */
static char * grokcad_xpm[] = {
"16 16 4 1",
"  c None",
". c #10161F",
"X c #7EE0C6",
"o c #3AA88F",
"................",
"................",
"......XXXX......",
".....XXooXX.....",
"....XXooooXX....",
"...XXooooooXX...",
"...XooooooooX...",
"...XoooXXoooX...",
"...XoooXXoooX...",
"...XooooooooX...",
"....XXooooXX....",
".....XXooXX.....",
"......XXXX......",
"................",
"................",
"................"};
"""

    def __init__(self):
        try:
            svg = os.path.join(
                App.getUserAppDataDir(),
                "Mod",
                "GrokCAD",
                "resources",
                "icons",
                "GrokCAD.svg",
            )
            if os.path.isfile(svg):
                self.__class__.Icon = svg
        except Exception:
            pass

    def Initialize(self):
        try:
            from commands import MENU_COMMANDS, TOOLBAR_COMMANDS, register_commands
            from core import prefs
            from ui.PreferencesPage import GrokCADPreferencesPage

            register_commands()
            prefs.ensure_defaults()
            self.appendToolbar("Grok CAD Agent", list(TOOLBAR_COMMANDS))
            self.appendMenu("Grok CAD Agent", list(MENU_COMMANDS))
            try:
                Gui.addPreferencePage(GrokCADPreferencesPage, "Grok CAD Agent")
            except Exception as exc:
                App.Console.PrintWarning("GrokCAD: preference page not added: %s\n" % exc)
        except Exception as exc:
            import traceback

            App.Console.PrintError(
                "GrokCAD workbench failed to initialise:\n%s\n%s\n"
                % (traceback.format_exc(), exc)
            )

    def Activated(self):
        App.Console.PrintMessage("GrokCAD: workbench activated.\n")
        try:
            from ui.GrokChatPanel import ensure_panel

            ensure_panel()
        except Exception as exc:
            App.Console.PrintWarning("GrokCAD: chat panel not opened: %s\n" % exc)

    def Deactivated(self):
        pass

    def ContextMenu(self, recipient):
        try:
            self.appendContextMenu("Grok CAD Agent", ["GrokCAD_OpenChat", "GrokCAD_Screenshot"])
        except Exception:
            pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


try:
    Gui.addWorkbench(GrokCADWorkbench())
    App.Console.PrintMessage("GrokCAD: workbench registered.\n")
except Exception:
    import traceback

    App.Console.PrintError("GrokCAD: addWorkbench failed:\n%s\n" % traceback.format_exc())
