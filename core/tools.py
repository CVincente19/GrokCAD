# SPDX-License-Identifier: MIT
"""
OpenAI-style tool definitions and FreeCAD implementations for Grok.

Every tool returns a JSON-serializable dict. Failures are returned as
``{"ok": False, "error": "..."}`` so the model can recover — they are
never raised into the chat worker unless something is internally broken.
"""

from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import document_state, log, prefs, safety, screenshot

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _s(desc: str, **extra: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {"type": "string", "description": desc}
    d.update(extra)
    return d


def _b(desc: str) -> Dict[str, Any]:
    return {"type": "boolean", "description": desc}


def _i(desc: str) -> Dict[str, Any]:
    return {"type": "integer", "description": desc}


def _n(desc: str) -> Dict[str, Any]:
    return {"type": "number", "description": desc}


def _fn(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    _fn(
        "execute_python",
        (
            "Execute FreeCAD Python in the active document. Use this for ALL "
            "geometry creation and edits (PartDesign, Part, Sketcher, Assembly). "
            "Always recompute at the end of the script. Prefer a single coherent "
            "script over many tiny ones. Units are millimetres. Wrap work in "
            "meaningful object names. On failure you will receive the traceback "
            "and must fix the script."
        ),
        {
            "code": _s("Complete Python script to run inside FreeCAD."),
            "reason": _s("One-sentence description of what this script does (shown to the user)."),
            "transaction_name": _s("Undo-stack label, e.g. 'GrokCAD: pad boss'."),
        },
        ["code"],
    ),
    _fn(
        "get_document_state",
        "Return the full feature tree, object names/types, validity, volumes, bounding boxes, selection, and open documents.",
        {
            "include_properties": _b("If true, include a property dump for every object (verbose)."),
        },
    ),
    _fn(
        "inspect_object",
        "Deep-inspect one object by Name or Label: topology, sketch constraints, body features, properties.",
        {
            "name": _s("Object Name (preferred) or Label."),
            "deep": _b("If true (default), include face/edge listing and full sketch constraints."),
        },
        ["name"],
    ),
    _fn(
        "take_screenshot",
        (
            "Capture the 3D viewport from one or more named cameras and return PNG images. "
            "Use this to visually verify geometry after changes. Views: Isometric, Front, "
            "Top, Right, Left, Bottom, Back, Dimetric, Trimetric, Current."
        ),
        {
            "views": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Named views to capture. Default: [\"Isometric\"].",
            },
            "width": _i("Pixel width (optional, uses preference default)."),
            "height": _i("Pixel height (optional)."),
            "fit": _b("Fit all objects in view before capture (default true)."),
        },
    ),
    _fn(
        "set_camera_view",
        "Move the 3D camera to a named standard view without capturing an image.",
        {
            "view": _s("Isometric, Front, Top, Right, Left, Bottom, Back, Dimetric, Trimetric."),
            "fit": _b("Fit all objects after moving the camera (default true)."),
        },
        ["view"],
    ),
    _fn(
        "recompute",
        "Recompute the active document (or a named one). Call after creating/editing features if you did not recompute inside execute_python.",
        {"document": _s("Document name. Empty = active.")},
    ),
    _fn(
        "undo",
        "Undo the last FreeCAD transaction in the active document.",
        {"steps": _i("How many undo steps (default 1).")},
    ),
    _fn(
        "redo",
        "Redo the last undone transaction in the active document.",
        {"steps": _i("How many redo steps (default 1).")},
    ),
    _fn(
        "create_new_document",
        "Create and activate a new FreeCAD document.",
        {
            "name": _s("Document name (optional)."),
            "switch_to_partdesign": _b("If true (default), activate the PartDesign workbench."),
        },
    ),
    _fn(
        "list_open_documents",
        "List every open document and which one is active.",
        {},
    ),
    _fn(
        "set_active_document",
        "Make an already-open document the active one.",
        {"name": _s("Document name as returned by list_open_documents.")},
        ["name"],
    ),
    _fn(
        "export_step",
        "Export one object or the whole document to a STEP file.",
        {
            "path": _s("Destination filesystem path. If empty, a file is written under the user GrokCAD/exports folder."),
            "object_name": _s("Object to export. Empty = all visible objects."),
        },
    ),
    _fn(
        "export_stl",
        "Export one object (or the document's solid shapes) to an STL mesh.",
        {
            "path": _s("Destination filesystem path. Empty = user GrokCAD/exports folder."),
            "object_name": _s("Object to export. Empty = all solids."),
            "linear_deflection": _n("Mesh linear deflection in mm (default 0.1)."),
        },
    ),
    _fn(
        "build_planar_part",
        (
            "Build a 2D plate from an outline + holes + thickness. Prefer this over "
            "execute_python for flat laser/milled parts (brackets, plates). Outline is "
            "XY millimetres, CCW, origin as you choose. Arcs are three points "
            "(start, mid, end). Holes are through-all cylinders. More reliable than "
            "a freeform Sketcher script."
        ),
        {
            "name": _s("Object name, e.g. Sprite_Plate."),
            "thickness": _n("Extrude thickness in mm."),
            "outline": {
                "type": "array",
                "description": "Closed outline. Each item is {type:'line',x2,y2} from the previous point, or {type:'arc',x2,y2,mx,my} with mid-point (mx,my). First item must include x1,y1 start.",
                "items": {"type": "object"},
            },
            "holes": {
                "type": "array",
                "description": "Through holes: [{x,y,d}] in mm.",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "d": {"type": "number"},
                    },
                },
            },
            "replace_existing": _b("If true, delete an existing object with the same name first."),
        },
        ["name", "thickness", "outline"],
    ),
    _fn(
        "get_selection",
        "Return the user's current 3D / tree selection, including sub-elements (Face6, Edge2, ...).",
        {},
    ),
    _fn(
        "search_objects",
        "Find objects in the active document by name, label, or type substring.",
        {
            "query": _s("Case-insensitive substring matched against name, label, and type."),
            "type_contains": _s("Optional extra filter on TypeId (e.g. 'Sketch', 'Pad')."),
        },
        ["query"],
    ),
    _fn(
        "get_workbench_info",
        "Return FreeCAD version, active workbench, available workbenches, and which CAD modules imported successfully.",
        {},
    ),
    _fn(
        "run_fem_analysis",
        (
            "Run a CalculiX FEM analysis if the FEM workbench and ccx solver are available. "
            "If analysis_name is given, runs that existing analysis. Otherwise creates a "
            "minimal static-analysis skeleton on object_name (material only — add "
            "constraints and loads via execute_python for a real study)."
        ),
        {
            "analysis_name": _s("Existing Fem::FemAnalysis object name. Empty = create or find one."),
            "object_name": _s("Solid to mesh / analyse when creating a new analysis."),
            "youngs_modulus_mpa": _n("Young's modulus in MPa (default 210000 for steel)."),
            "poisson": _n("Poisson ratio (default 0.30)."),
            "mesh_max_size": _n("Max mesh element size in mm (default 5)."),
            "run_solver": _b("If true (default), try to run CalculiX after setup."),
        },
    ),
]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

class ToolContext:
    """Optional hooks the UI injects (confirmation, screenshot preview)."""

    def __init__(self) -> None:
        self.confirm: Optional[Callable[[str, str, bool], bool]] = None
        self.on_images: Optional[Callable[[List[Dict[str, Any]]], None]] = None
        self.parent_widget = None
        self.agent_mode: str = prefs.MODE_APPROVE_CODE


def execute_tool(name: str, arguments: Dict[str, Any], ctx: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Run a single tool by name. Always returns a dict."""
    ctx = ctx or ToolContext()
    fn = _REGISTRY.get(name)
    if fn is None:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        return fn(arguments or {}, ctx)
    except Exception as exc:  # noqa: BLE001
        log.error(f"Tool {name} crashed", exc)
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


def tool_is_safe(name: str) -> bool:
    return name in prefs.SAFE_TOOLS


def needs_confirmation(name: str, arguments: Dict[str, Any], mode: str) -> bool:
    if mode == prefs.MODE_PLAN_ONLY:
        return True  # never auto-run in plan-only (caller should skip)
    if mode == prefs.MODE_APPROVE_EVERY:
        return True
    if mode == prefs.MODE_APPROVE_CODE:
        if name == "execute_python":
            return True
        if name in prefs.SAFE_TOOLS:
            return False
        return True
    # full auto: only confirm dangerous python
    if name == "execute_python":
        report = safety.scan_code(str(arguments.get("code", "")))
        return bool(report.dangerous)
    return False


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

def _args(arguments: Any) -> Dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        try:
            return json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return {"_raw": arguments}
    if isinstance(arguments, dict):
        return arguments
    return {}


def _tool_execute_python(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    args = _args(arguments)
    code = str(args.get("code") or "")
    reason = str(args.get("reason") or "GrokCAD execute_python")
    txn = str(args.get("transaction_name") or f"GrokCAD: {reason[:60]}")
    report = safety.scan_code(code)
    result = safety.execute_python(code, transaction_name=txn)
    result["reason"] = reason
    result["safety"] = {
        "dangerous": report.dangerous,
        "issues": report.issues,
        "imports": report.imports,
    }
    # Auto-recompute if the script forgot and the doc is dirty.
    if result.get("ok"):
        try:
            import FreeCAD as App  # type: ignore

            doc = App.ActiveDocument
            if doc is not None and getattr(doc, "RecomputesRequired", False):
                doc.recompute()
                result["auto_recomputed"] = True
        except Exception:  # noqa: BLE001
            pass
        if prefs.get_auto_screenshot():
            try:
                cap = screenshot.capture_views(prefs.get_auto_screenshot_views())
                result["screenshots"] = [
                    {
                        "view": im.get("view"),
                        "path": im.get("path"),
                        "width": im.get("width"),
                        "height": im.get("height"),
                        "png_base64": im.get("png_base64"),
                    }
                    for im in cap.get("images", [])
                ]
                if ctx.on_images:
                    ctx.on_images(result["screenshots"])
            except Exception as exc:  # noqa: BLE001
                result["screenshot_error"] = str(exc)
    return result


def _tool_get_document_state(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    args = _args(arguments)
    include = bool(args.get("include_properties", False))
    return document_state.summarize_document(include_properties=include)


def _tool_inspect_object(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    args = _args(arguments)
    name = str(args.get("name") or "")
    deep = bool(args.get("deep", True))
    if not name:
        return {"ok": False, "error": "name is required"}
    return document_state.inspect_object(name, deep=deep)


def _tool_take_screenshot(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    args = _args(arguments)
    views = args.get("views") or ["Isometric"]
    if isinstance(views, str):
        views = [v.strip() for v in views.split(",") if v.strip()]
    width = args.get("width")
    height = args.get("height")
    fit = bool(args.get("fit", True))
    cap = screenshot.capture_views(
        views,
        width=int(width) if width else None,
        height=int(height) if height else None,
        fit=fit,
    )
    if ctx.on_images and cap.get("images"):
        ctx.on_images(cap["images"])
    # Keep base64 in the tool result so Grok can "see" via a follow-up
    # user message that the client will attach. We also include a compact
    # summary without repeating megabytes in the textual history when the
    # client strips png_base64 for the stored tool message.
    return cap


def _tool_set_camera_view(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    args = _args(arguments)
    return screenshot.set_camera_view(str(args.get("view") or "Isometric"), fit=bool(args.get("fit", True)))


def _tool_recompute(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    import FreeCAD as App  # type: ignore

    args = _args(arguments)
    name = str(args.get("document") or "")
    doc = App.getDocument(name) if name else App.ActiveDocument
    if doc is None:
        return {"ok": False, "error": "No document to recompute."}
    try:
        failed = doc.recompute()
        # FreeCAD returns the number of objects that failed, or None.
        return {
            "ok": True,
            "document": doc.Name,
            "failed_count": int(failed) if isinstance(failed, int) else failed,
            "recomputes_required": bool(getattr(doc, "RecomputesRequired", False)),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _tool_undo(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    import FreeCAD as App  # type: ignore

    doc = App.ActiveDocument
    if doc is None:
        return {"ok": False, "error": "No active document."}
    steps = max(1, int(_args(arguments).get("steps") or 1))
    done = 0
    for _ in range(steps):
        try:
            doc.undo()
            done += 1
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "undone": done}
    try:
        doc.recompute()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "undone": done}


def _tool_redo(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    import FreeCAD as App  # type: ignore

    doc = App.ActiveDocument
    if doc is None:
        return {"ok": False, "error": "No active document."}
    steps = max(1, int(_args(arguments).get("steps") or 1))
    done = 0
    for _ in range(steps):
        try:
            doc.redo()
            done += 1
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "redone": done}
    try:
        doc.recompute()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "redone": done}


def _tool_create_new_document(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    import FreeCAD as App  # type: ignore

    args = _args(arguments)
    name = str(args.get("name") or "").strip()
    try:
        doc = App.newDocument(name) if name else App.newDocument()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    switched = False
    if bool(args.get("switch_to_partdesign", True)):
        try:
            import FreeCADGui as Gui  # type: ignore

            Gui.activateWorkbench("PartDesignWorkbench")
            switched = True
        except Exception:  # noqa: BLE001
            switched = False
    return {
        "ok": True,
        "name": doc.Name,
        "label": doc.Label,
        "switched_to_partdesign": switched,
    }


def _tool_list_open_documents(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    return {"ok": True, "documents": document_state.list_open_documents()}


def _tool_set_active_document(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    import FreeCAD as App  # type: ignore

    name = str(_args(arguments).get("name") or "")
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        doc = App.getDocument(name)
        App.setActiveDocument(doc.Name)
        try:
            import FreeCADGui as Gui  # type: ignore

            Gui.ActiveDocument = Gui.getDocument(doc.Name)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "name": doc.Name}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "open": [d["name"] for d in document_state.list_open_documents()],
        }


def _default_export_path(suffix: str, object_name: str = "") -> Path:
    from .paths import user_data_dir
    import time

    folder = user_data_dir() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = object_name or "document"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)
    return folder / f"{safe}_{stamp}{suffix}"


def _tool_export_step(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    import FreeCAD as App  # type: ignore

    args = _args(arguments)
    doc = App.ActiveDocument
    if doc is None:
        return {"ok": False, "error": "No active document."}
    obj_name = str(args.get("object_name") or "")
    path = str(args.get("path") or "")
    if not path:
        path = str(_default_export_path(".step", obj_name or doc.Name))
    try:
        import Import  # type: ignore

        if obj_name:
            obj = doc.getObject(obj_name)
            if obj is None:
                return {"ok": False, "error": f"No object {obj_name!r}"}
            Import.export([obj], path)
        else:
            objs = [o for o in doc.Objects if getattr(o, "Shape", None) is not None]
            if not objs:
                return {"ok": False, "error": "No shapes to export."}
            Import.export(objs, path)
        return {"ok": True, "path": path}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _tool_export_stl(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    import FreeCAD as App  # type: ignore

    args = _args(arguments)
    doc = App.ActiveDocument
    if doc is None:
        return {"ok": False, "error": "No active document."}
    obj_name = str(args.get("object_name") or "")
    path = str(args.get("path") or "")
    deflection = float(args.get("linear_deflection") or 0.1)
    if not path:
        path = str(_default_export_path(".stl", obj_name or doc.Name))
    try:
        import Mesh  # type: ignore

        if obj_name:
            obj = doc.getObject(obj_name)
            if obj is None:
                return {"ok": False, "error": f"No object {obj_name!r}"}
            shape = getattr(obj, "Shape", None)
            if shape is None:
                return {"ok": False, "error": f"{obj_name} has no Shape"}
            mesh = Mesh.Mesh(shape.tessellate(deflection))
            mesh.write(path)
        else:
            # Combine all solids.
            import MeshPart  # type: ignore

            meshes = []
            for obj in doc.Objects:
                shape = getattr(obj, "Shape", None)
                if shape is None or getattr(shape, "isNull", lambda: True)():
                    continue
                try:
                    meshes.append(MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection))
                except Exception:  # noqa: BLE001
                    continue
            if not meshes:
                return {"ok": False, "error": "No solids to mesh."}
            combined = meshes[0]
            for extra in meshes[1:]:
                combined.addMesh(extra)
            combined.write(path)
        return {"ok": True, "path": path, "linear_deflection": deflection}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _tool_build_planar_part(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    """Deterministic 2D plate builder — no nested Python from the model."""
    args = _args(arguments)
    name = str(args.get("name") or "Plate").strip() or "Plate"
    try:
        thick = float(args.get("thickness") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "thickness must be a number"}
    if thick <= 0:
        return {"ok": False, "error": "thickness must be > 0"}
    outline = args.get("outline") or []
    holes = args.get("holes") or []
    if not isinstance(outline, list) or len(outline) < 3:
        return {"ok": False, "error": "outline needs at least 3 segments"}
    try:
        import FreeCAD as App  # type: ignore
        import Part  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    doc = App.ActiveDocument
    if doc is None:
        doc = App.newDocument(name)
    if args.get("replace_existing"):
        old = doc.getObject(name)
        if old is not None:
            try:
                doc.removeObject(old.Name)
            except Exception:  # noqa: BLE001
                pass
    try:
        wire = _outline_to_wire(outline)
        face = Part.Face(wire)
        solid = face.extrude(App.Vector(0, 0, thick))
        for h in holes:
            if not isinstance(h, dict):
                continue
            x, y, d = float(h["x"]), float(h["y"]), float(h["d"])
            if d <= 0:
                continue
            cyl = Part.makeCylinder(
                d / 2.0, thick + 2.0, App.Vector(x, y, -1.0), App.Vector(0, 0, 1)
            )
            solid = solid.cut(cyl)
        solid = solid.removeSplitter()
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = solid
        obj.Label = name
        doc.recompute()
        bb = obj.Shape.BoundBox
        return {
            "ok": True,
            "name": obj.Name,
            "bbox_mm": [bb.XLength, bb.YLength, bb.ZLength],
            "volume_mm3": float(obj.Shape.Volume),
            "holes": len(holes),
            "valid": bool(obj.Shape.isValid()),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}


def _outline_to_wire(outline: List[Any]):
    import FreeCAD as App  # type: ignore
    import Part  # type: ignore

    first = outline[0]
    if not isinstance(first, dict):
        raise ValueError("outline[0] must be an object with x1,y1")
    x = float(first.get("x1", first.get("x", 0)))
    y = float(first.get("y1", first.get("y", 0)))
    start = App.Vector(x, y, 0)
    cur = start
    edges = []
    segs = outline if first.get("type") else outline
    # If first item is only a start point, skip it as a segment.
    items = outline
    if first.get("type") in (None, "", "start") and "x2" not in first and "x" in first:
        items = outline[1:]
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValueError("segment %d is not an object" % i)
        typ = str(raw.get("type") or "line").lower()
        if typ == "start":
            cur = App.Vector(float(raw.get("x", raw.get("x1", 0))), float(raw.get("y", raw.get("y1", 0))), 0)
            start = cur
            continue
        x2 = float(raw.get("x2", raw.get("x")))
        y2 = float(raw.get("y2", raw.get("y")))
        nxt = App.Vector(x2, y2, 0)
        if typ == "arc":
            mx = float(raw["mx"] if "mx" in raw else raw["mid_x"])
            my = float(raw["my"] if "my" in raw else raw["mid_y"])
            mid = App.Vector(mx, my, 0)
            edges.append(Part.Arc(cur, mid, nxt).toShape())
        else:
            if (nxt - cur).Length < 1e-9:
                continue
            edges.append(Part.makeLine(cur, nxt))
        cur = nxt
    if (cur - start).Length > 1e-6:
        edges.append(Part.makeLine(cur, start))
    return Part.Wire(edges)


def _tool_get_selection(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    return {"ok": True, "selection": document_state.get_selection()}


def _tool_search_objects(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    args = _args(arguments)
    hits = document_state.search_objects(
        str(args.get("query") or ""),
        type_contains=str(args.get("type_contains") or ""),
    )
    return {"ok": True, "matches": hits, "count": len(hits)}


def _tool_get_workbench_info(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    info = document_state.get_workbench_info()
    info["ok"] = True
    return info


def _tool_run_fem_analysis(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    """Best-effort CalculiX wrapper. Never crashes the workbench if FEM is absent."""
    args = _args(arguments)
    try:
        import Fem  # type: ignore
        import ObjectsFem  # type: ignore
        import FreeCAD as App  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"FEM workbench is not available: {exc}",
            "hint": "On Fedora: sudo dnf install freecad (FEM is included) and CalculiX (ccx).",
        }

    ccx = shutil.which("ccx")
    doc = App.ActiveDocument
    if doc is None:
        return {"ok": False, "error": "No active document."}

    analysis_name = str(args.get("analysis_name") or "")
    object_name = str(args.get("object_name") or "")
    E = float(args.get("youngs_modulus_mpa") or 210000.0)
    nu = float(args.get("poisson") or 0.30)
    mesh_size = float(args.get("mesh_max_size") or 5.0)
    run_solver = bool(args.get("run_solver", True))

    analysis = doc.getObject(analysis_name) if analysis_name else None
    created = []
    if analysis is None:
        # Find an existing analysis or create a skeleton.
        for obj in doc.Objects:
            if obj.TypeId == "Fem::FemAnalysis":
                analysis = obj
                break
    if analysis is None:
        try:
            analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
            created.append(analysis.Name)
            solver = ObjectsFem.makeSolverCalculixCcxTools(doc)
            analysis.addObject(solver)
            created.append(solver.Name)
            mat = ObjectsFem.makeMaterialSolid(doc, "Steel")
            # Material card as a dict of strings — CalculiX tools expect this.
            mat.Material = {
                "Name": "Steel",
                "YoungsModulus": f"{E} MPa",
                "PoissonRatio": str(nu),
                "Density": "7900 kg/m^3",
            }
            analysis.addObject(mat)
            created.append(mat.Name)
            if object_name:
                target = doc.getObject(object_name)
                if target is None:
                    return {"ok": False, "error": f"No object {object_name!r} to mesh."}
                try:
                    mesh = ObjectsFem.makeMeshGmsh(doc, "FEMMeshGmsh")
                    mesh.Shape = target
                    mesh.CharacteristicLengthMax = f"{mesh_size} mm"
                    analysis.addObject(mesh)
                    created.append(mesh.Name)
                except Exception as exc:  # noqa: BLE001
                    return {
                        "ok": False,
                        "error": f"Created analysis skeleton but meshing failed: {exc}",
                        "created": created,
                        "analysis": analysis.Name,
                        "hint": "Install gmsh (sudo dnf install gmsh) or mesh via execute_python.",
                    }
            doc.recompute()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Failed to create FEM analysis: {exc}"}

    if not run_solver:
        return {
            "ok": True,
            "ran_solver": False,
            "analysis": analysis.Name,
            "created": created,
            "ccx_found": bool(ccx),
            "hint": (
                "Skeleton ready. Add Fem::ConstraintFixed and Fem::ConstraintForce "
                "via execute_python, then call run_fem_analysis again with run_solver=true."
            ),
        }

    if not ccx:
        return {
            "ok": False,
            "error": "CalculiX solver (ccx) not found on PATH.",
            "analysis": analysis.Name,
            "created": created,
            "hint": "On Fedora: sudo dnf install CalculiX",
        }

    # Run via femtools.ccxtools if present.
    try:
        from femtools import ccxtools  # type: ignore

        fea = ccxtools.FemToolsCcx(analysis)
        fea.update_objects()
        fea.setup_working_dir()
        fea.setup_ccx()
        msg = fea.check_prerequisites()
        if msg:
            return {
                "ok": False,
                "error": f"FEM prerequisites not met: {msg}",
                "analysis": analysis.Name,
                "created": created,
                "hint": "Typically missing constraints, loads, or a mesh. Add them with execute_python.",
            }
        fea.write_inp_file()
        fea.ccx_run()
        fea.load_results()
        summary: Dict[str, Any] = {
            "ok": True,
            "ran_solver": True,
            "analysis": analysis.Name,
            "created": created,
            "results": [],
        }
        for obj in analysis.Group:
            if "Result" in obj.TypeId or "Result" in obj.Name:
                rec: Dict[str, Any] = {"name": obj.Name, "type": obj.TypeId}
                for attr in ("Stats", "DisplacementLengths", "vonMises"):
                    if hasattr(obj, attr):
                        try:
                            val = getattr(obj, attr)
                            rec[attr] = _summarize_result_field(val)
                        except Exception:  # noqa: BLE001
                            pass
                summary["results"].append(rec)
        return summary
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"CalculiX run failed: {exc}",
            "traceback": traceback.format_exc(),
            "analysis": analysis.Name,
            "created": created,
        }


def _summarize_result_field(val: Any) -> Any:
    try:
        if val is None:
            return None
        if isinstance(val, (int, float, str, bool)):
            return val
        seq = list(val)
        if not seq:
            return []
        nums = [float(x) for x in seq if isinstance(x, (int, float))]
        if nums:
            return {"min": min(nums), "max": max(nums), "count": len(nums)}
        return str(val)[:200]
    except Exception:  # noqa: BLE001
        return str(type(val))


_REGISTRY: Dict[str, Callable[[Dict[str, Any], ToolContext], Dict[str, Any]]] = {
    "execute_python": _tool_execute_python,
    "get_document_state": _tool_get_document_state,
    "inspect_object": _tool_inspect_object,
    "take_screenshot": _tool_take_screenshot,
    "set_camera_view": _tool_set_camera_view,
    "recompute": _tool_recompute,
    "undo": _tool_undo,
    "redo": _tool_redo,
    "create_new_document": _tool_create_new_document,
    "list_open_documents": _tool_list_open_documents,
    "set_active_document": _tool_set_active_document,
    "export_step": _tool_export_step,
    "export_stl": _tool_export_stl,
    "get_selection": _tool_get_selection,
    "build_planar_part": _tool_build_planar_part,
    "search_objects": _tool_search_objects,
    "get_workbench_info": _tool_get_workbench_info,
    "run_fem_analysis": _tool_run_fem_analysis,
}


def compact_tool_result(name: str, result: Dict[str, Any], *, keep_images: bool = False) -> Dict[str, Any]:
    """
    Shrink a tool result before stuffing it into the model transcript.

    Screenshots keep a short pointer; the vision payload is attached as
    image_url parts by the client, not as megabytes of base64 in the
    tool-result JSON.
    """
    if not isinstance(result, dict):
        return {"ok": False, "error": "non-dict result"}
    trimmed = dict(result)
    if name == "take_screenshot" or "images" in trimmed or "screenshots" in trimmed:
        for key in ("images", "screenshots"):
            imgs = trimmed.get(key)
            if not isinstance(imgs, list):
                continue
            slim = []
            for im in imgs:
                if not isinstance(im, dict):
                    continue
                rec = {
                    "view": im.get("view"),
                    "path": im.get("path"),
                    "width": im.get("width"),
                    "height": im.get("height"),
                    "bytes": im.get("bytes"),
                    "ok": im.get("ok", True),
                }
                if keep_images and im.get("png_base64"):
                    rec["png_base64"] = im["png_base64"]
                else:
                    rec["png_base64_omitted"] = True
                slim.append(rec)
            trimmed[key] = slim
    # Cap huge dumps.
    text = json.dumps(trimmed, default=str)
    if len(text) > 60_000:
        trimmed = {
            "ok": trimmed.get("ok", True),
            "truncated": True,
            "preview": text[:40_000],
            "original_chars": len(text),
        }
    return trimmed
