# SPDX-License-Identifier: MIT
"""
Safe-ish execution of FreeCAD Python produced by Grok.

This is *not* a security sandbox against a hostile model. It is a
human-in-the-loop guardrail:

* Static scan for obviously dangerous patterns (process spawn, network,
  filesystem wipe, ctypes, etc.).
* Restricted ``__builtins__`` that still allow real CAD work.
* Execution wrapped in a FreeCAD undo transaction.
* Optional modal confirmation dialog (always on the GUI thread).

FreeCAD itself stays fully available: ``App``, ``FreeCAD``, ``Part``,
``PartDesign``, ``Sketcher``, ``Draft``, ``Mesh``, etc. can be imported.
"""

from __future__ import annotations

import ast
import io
import re
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import log
from .qtcompat import QtWidgets, require_qt

# ---------------------------------------------------------------------------
# Static analysis
# ---------------------------------------------------------------------------

_DANGEROUS_IMPORTS = {
    "ctypes",
    "cffi",
    "subprocess",
    "multiprocessing",
    "socket",
    "http",
    "http.client",
    "urllib",
    "urllib.request",
    "urllib.parse",
    "requests",
    "ftplib",
    "smtplib",
    "telnetlib",
    "pickle",
    "shelve",
    "pathlib",  # still allowed via allow-list of methods after review
    "importlib",
    "runpy",
    "code",
    "codeop",
    "pty",
    "fcntl",
    "mmap",
    "signal",
    "sysconfig",
}

# These modules are allowed because FreeCAD CAD code needs them.
_ALLOWED_IMPORT_PREFIXES = (
    "FreeCAD",
    "FreeCADGui",
    "Part",
    "PartDesign",
    "Sketcher",
    "Draft",
    "DraftVecUtils",
    "DraftGeomUtils",
    "Mesh",
    "MeshPart",
    "Fem",
    "ObjectsFem",
    "femsolver",
    "femtools",
    "femmesh",
    "Spreadsheet",
    "SpreadsheetGui",
    "SketcherGui",
    "PartGui",
    "PartDesignGui",
    "DraftGui",
    "MeshGui",
    "TechDraw",
    "TechDrawGui",
    "Import",
    "ImportGui",
    "Assembly",
    "A2plus",
    "Fasteners",
    "FastenerBase",
    "SheetMetal",
    "Path",
    "PathScripts",
    "CAM",
    "Arch",
    "BIM",
    "Show",
    "WorkingPlane",
    "math",
    "cmath",
    "json",
    "re",
    "collections",
    "itertools",
    "functools",
    "operator",
    "copy",
    "decimal",
    "fractions",
    "statistics",
    "datetime",
    "time",
    "uuid",
    "string",
    "textwrap",
    "pprint",
    "typing",
    "enum",
    "dataclasses",
    "abc",
    "numbers",
    "io",
    "struct",
    "array",
    "heapq",
    "bisect",
    "weakref",
    "types",
)

_DANGEROUS_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "exit",
    "quit",
    "breakpoint",
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.execl",
    "os.spawn",
    "os.spawnl",
    "os.spawnv",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "os.removedirs",
    "shutil.rmtree",
    "shutil.move",
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_output",
}

_DANGEROUS_ATTR = {
    "__subclasses__",
    "__globals__",
    "__code__",
    "__reduce__",
    "__reduce_ex__",
    "system",
    "popen",
    "rmtree",
}

_DANGEROUS_REGEX = [
    re.compile(r"\bos\.system\s*\("),
    re.compile(r"\bsubprocess\."),
    re.compile(r"\bshutil\.rmtree\s*\("),
    re.compile(r"\b__import__\s*\("),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bsocket\."),
    re.compile(r"\bctypes\."),
    re.compile(r"open\s*\(\s*['\"]\/etc\/"),
    re.compile(r"open\s*\(\s*['\"]\/home\/"),
    re.compile(r"Path\.home\s*\("),
]


class SafetyReport:
    """Result of a static scan of generated Python."""

    def __init__(self) -> None:
        self.issues: List[str] = []
        self.dangerous: bool = False
        self.parsed: bool = True
        self.imports: List[str] = []

    def add(self, message: str, dangerous: bool = True) -> None:
        self.issues.append(message)
        if dangerous:
            self.dangerous = True

    def summary(self) -> str:
        if not self.issues:
            return "No static safety issues."
        return "; ".join(self.issues)


def scan_code(code: str) -> SafetyReport:
    """Statically inspect *code* for dangerous patterns."""
    report = SafetyReport()
    if not isinstance(code, str) or not code.strip():
        report.add("Empty code.", dangerous=False)
        return report

    for rx in _DANGEROUS_REGEX:
        if rx.search(code):
            report.add(f"Matched dangerous pattern: {rx.pattern}")

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        report.parsed = False
        report.add(f"Syntax error (scan incomplete): {exc}", dangerous=False)
        return report

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import(alias.name, report)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            _check_import(mod, report)
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _DANGEROUS_CALLS:
                report.add(f"Dangerous call: {name}()")
        elif isinstance(node, ast.Attribute):
            if node.attr in _DANGEROUS_ATTR:
                report.add(f"Dangerous attribute access: .{node.attr}")

    return report


def _check_import(name: str, report: SafetyReport) -> None:
    if not name:
        return
    report.imports.append(name)
    top = name.split(".")[0]
    if name in _DANGEROUS_IMPORTS or top in _DANGEROUS_IMPORTS:
        report.add(f"Dangerous import: {name}")
        return
    if top == "os":
        report.add("Import of 'os' is flagged (process / filesystem).")
        return
    if top == "sys":
        # sys is often used for path hacks; flag but not always block.
        report.add("Import of 'sys' is flagged.", dangerous=True)
        return
    allowed = any(
        name == p or name.startswith(p + ".") or top == p
        for p in _ALLOWED_IMPORT_PREFIXES
    )
    if not allowed and top not in {"App", "Gui", "FreeCAD", "FreeCADGui"}:
        # Unknown imports are suspicious but CAD addons vary. Flag, don't auto-block
        # in full-auto unless combined with other issues.
        report.add(f"Unlisted import: {name}", dangerous=True)


def _call_name(node: ast.Call) -> str:
    func = node.func
    parts: List[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    parts.reverse()
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Confirmation UI
# ---------------------------------------------------------------------------

def confirm_execution(
    title: str,
    body: str,
    *,
    dangerous: bool = False,
    parent: Any = None,
) -> bool:
    """
    Modal Yes/No dialog. Must be called on the GUI thread.

    Returns True if the user approved.
    """
    require_qt()
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setTextFormat(QtCore_TextFormat_RichText())
    # Keep the body readable; QMessageBox wraps long text poorly so we use
    # a detailed-text area for the code / payload.
    headline = "Grok wants to run the following action."
    if dangerous:
        headline = (
            "<b>Potentially dangerous action.</b> Review carefully before approving."
        )
    box.setText(headline)
    box.setInformativeText(body[:800] + ("…" if len(body) > 800 else ""))
    box.setDetailedText(body)
    box.setIcon(
        QtWidgets.QMessageBox.Warning if dangerous else QtWidgets.QMessageBox.Question
    )
    box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
    box.setDefaultButton(
        QtWidgets.QMessageBox.No if dangerous else QtWidgets.QMessageBox.Yes
    )
    return box.exec_() == QtWidgets.QMessageBox.Yes


def QtCore_TextFormat_RichText() -> Any:
    from .qtcompat import QtCore

    try:
        return QtCore.Qt.TextFormat.RichText
    except AttributeError:
        return QtCore.Qt.RichText


# ---------------------------------------------------------------------------
# Restricted exec
# ---------------------------------------------------------------------------

_SAFE_BUILTIN_NAMES = [
    "abs",
    "all",
    "any",
    "ascii",
    "bin",
    "bool",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "complex",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "hash",
    "hex",
    "id",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "oct",
    "ord",
    "pow",
    "print",
    "property",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "vars",
    "zip",
    "True",
    "False",
    "None",
    "Exception",
    "BaseException",
    "ValueError",
    "TypeError",
    "RuntimeError",
    "AttributeError",
    "KeyError",
    "IndexError",
    "StopIteration",
    "AssertionError",
    "ArithmeticError",
    "ZeroDivisionError",
    "OverflowError",
    "NotImplementedError",
    "isinstance",
    "__build_class__",
    "__name__",
]


def _restricted_builtins() -> Dict[str, Any]:
    env: Dict[str, Any] = {}
    bi = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
    for name in _SAFE_BUILTIN_NAMES:
        if name in bi:
            env[name] = bi[name]
    # Provide a guarded importer that only allows CAD / stdlib-safe modules.
    env["__import__"] = _guarded_import
    env["__name__"] = "grokcad_exec"
    env["__doc__"] = None
    return env


def _guarded_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    report = SafetyReport()
    _check_import(name, report)
    if report.dangerous and name.split(".")[0] not in {
        "math",
        "json",
        "re",
        "collections",
        "itertools",
        "functools",
        "copy",
        "datetime",
        "time",
        "uuid",
        "typing",
        "enum",
        "dataclasses",
        "io",
        "Part",
        "PartDesign",
        "Sketcher",
        "Draft",
        "Mesh",
        "MeshPart",
        "Fem",
        "ObjectsFem",
        "Spreadsheet",
        "SpreadsheetGui",
        "SketcherGui",
        "PartGui",
        "PartDesignGui",
        "DraftGui",
        "MeshGui",
        "TechDraw",
        "TechDrawGui",
        "Import",
        "ImportGui",
        "Assembly",
        "FreeCAD",
        "FreeCADGui",
        "WorkingPlane",
        "Show",
        "DraftVecUtils",
        "DraftGeomUtils",
        "femsolver",
        "femtools",
        "femmesh",
        "Path",
        "CAM",
        "Arch",
        "BIM",
        "SheetMetal",
        "Fasteners",
        "FastenerBase",
        "A2plus",
    }:
        raise ImportError(
            f"GrokCAD blocked import of {name!r}. Use a FreeCAD / approved module."
        )
    return __import__(name, globals, locals, fromlist, level)


def cad_globals() -> Dict[str, Any]:
    """Build the execution namespace with FreeCAD modules pre-bound."""
    g: Dict[str, Any] = {"__builtins__": _restricted_builtins()}
    try:
        import FreeCAD as App  # type: ignore
        import FreeCAD  # type: ignore

        g["App"] = App
        g["FreeCAD"] = FreeCAD
        g["Vector"] = App.Vector
        g["Placement"] = App.Placement
        g["Rotation"] = App.Rotation
        g["Base"] = getattr(App, "Base", None)
    except Exception as exc:  # noqa: BLE001
        log.warn(f"FreeCAD not available in exec globals: {exc}")

    try:
        import FreeCADGui as Gui  # type: ignore
        import FreeCADGui  # type: ignore

        g["Gui"] = Gui
        g["FreeCADGui"] = FreeCADGui
    except Exception:  # noqa: BLE001
        pass

    for mod_name, alias in (
        ("Part", "Part"),
        ("PartDesign", "PartDesign"),
        ("Sketcher", "Sketcher"),
        ("Draft", "Draft"),
        ("Mesh", "Mesh"),
        ("MeshPart", "MeshPart"),
        ("Spreadsheet", "Spreadsheet"),
        ("math", "math"),
        ("json", "json"),
    ):
        try:
            g[alias] = __import__(mod_name)
        except Exception:  # noqa: BLE001
            pass

    # Convenience: active document
    try:
        import FreeCAD as App  # type: ignore

        g["doc"] = App.ActiveDocument
    except Exception:  # noqa: BLE001
        g["doc"] = None

    def V(x, y=0.0, z=0.0):
        import FreeCAD as App  # type: ignore

        return App.Vector(float(x), float(y), float(z))

    def vars_get(name, default=None):
        """Read a cell from the 'Vars' spreadsheet if it exists."""
        import FreeCAD as App  # type: ignore

        doc = App.ActiveDocument
        if doc is None:
            return default
        sh = doc.getObject("Vars")
        if sh is None:
            return default
        try:
            return sh.get(name)
        except Exception:
            try:
                return getattr(sh, name)
            except Exception:
                return default

    g["V"] = V
    g["vars_get"] = vars_get
    return g


def execute_python(
    code: str,
    *,
    transaction_name: str = "GrokCAD",
    extra_globals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute *code* in the current FreeCAD document context.

    Returns a dict with keys: ok, stdout, stderr, result, traceback, objects_changed.
    """
    result: Dict[str, Any] = {
        "ok": False,
        "stdout": "",
        "stderr": "",
        "result": None,
        "traceback": "",
        "objects_changed": [],
        "transaction": transaction_name,
    }
    if not isinstance(code, str) or not code.strip():
        result["stderr"] = "No code provided."
        return result

    g = cad_globals()
    if extra_globals:
        g.update(extra_globals)
    # IMPORTANT: use the SAME dict for globals and locals.
    # exec(..., g, loc) makes nested def/lambda look up names in g, so
    # `sheet = ...; def f(): return sheet` raises NameError. That is what
    # burned an hour of API credits on the Sprite plate ("sheet is not defined").
    loc = g

    doc = g.get("doc")
    before_ids = _object_snapshot(doc)

    opened_txn = False
    if doc is not None:
        try:
            doc.openTransaction(transaction_name)
            opened_txn = True
        except Exception:  # noqa: BLE001
            opened_txn = False

    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(compile(code, "<grokcad>", "exec"), g, loc)  # noqa: S102
        result["ok"] = True
        # Prefer an explicit `result` binding if the script set one.
        if "result" in g:
            result["result"] = _safe_repr(g["result"])
        elif "out" in g:
            result["result"] = _safe_repr(g["out"])
        if opened_txn and doc is not None:
            doc.commitTransaction()
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["traceback"] = traceback.format_exc()
        result["stderr"] = f"{type(exc).__name__}: {exc}"
        if opened_txn and doc is not None:
            try:
                doc.abortTransaction()
            except Exception:  # noqa: BLE001
                pass
        log.error(f"execute_python failed: {exc}")
    finally:
        result["stdout"] = stdout.getvalue()
        err_txt = stderr.getvalue()
        if err_txt:
            result["stderr"] = (result["stderr"] + "\n" + err_txt).strip()

    after_ids = _object_snapshot(g.get("doc") or doc)
    result["objects_changed"] = _diff_snapshots(before_ids, after_ids)
    return result


def _object_snapshot(doc: Any) -> Dict[str, str]:
    if doc is None:
        return {}
    try:
        return {obj.Name: obj.TypeId for obj in doc.Objects}
    except Exception:  # noqa: BLE001
        return {}


def _diff_snapshots(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    notes: List[str] = []
    for name, typ in after.items():
        if name not in before:
            notes.append(f"+ {name} ({typ})")
        elif before[name] != typ:
            notes.append(f"~ {name} ({before[name]} -> {typ})")
    for name, typ in before.items():
        if name not in after:
            notes.append(f"- {name} ({typ})")
    return notes


def _safe_repr(value: Any, limit: int = 800) -> str:
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001
        text = f"<{type(value).__name__}>"
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def is_safe_tool(name: str) -> bool:
    from . import prefs

    return name in prefs.SAFE_TOOLS
