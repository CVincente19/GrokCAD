# SPDX-License-Identifier: MIT
"""
Rich extraction of the active FreeCAD document for Grok.

The goal is to give the model a compact but complete picture of:

* Open documents and which one is active
* Feature tree (name, label, type, visibility, validity, Tip)
* Shape metrics (volume, area, bounding box, topology counts)
* Sketcher constraint status
* Selection
* Active body / workbench
* Dependency edges (InList / OutList)
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from . import log


def _app():
    import FreeCAD  # type: ignore

    return FreeCAD


def _gui():
    try:
        import FreeCADGui  # type: ignore

        return FreeCADGui
    except Exception:  # noqa: BLE001
        return None


def list_open_documents() -> List[Dict[str, Any]]:
    App = _app()
    docs = []
    listing = App.listDocuments() or {}
    active = App.ActiveDocument
    for name, doc in listing.items():
        docs.append(
            {
                "name": name,
                "label": getattr(doc, "Label", name),
                "file": getattr(doc, "FileName", "") or "",
                "object_count": len(getattr(doc, "Objects", []) or []),
                "recomputes_required": bool(getattr(doc, "RecomputesRequired", False)),
                "active": active is not None and doc.Name == active.Name,
            }
        )
    return docs


def get_active_document():
    App = _app()
    return App.ActiveDocument


def summarize_document(doc=None, *, include_properties: bool = False) -> Dict[str, Any]:
    """Return a JSON-serializable snapshot of *doc* (or the active document)."""
    App = _app()
    Gui = _gui()
    if doc is None:
        doc = App.ActiveDocument
    if doc is None:
        return {
            "ok": False,
            "error": "No active document. Call create_new_document first.",
            "open_documents": list_open_documents(),
        }

    objects = []
    for obj in doc.Objects:
        try:
            objects.append(_summarize_object(obj, include_properties=include_properties))
        except Exception as exc:  # noqa: BLE001
            objects.append(
                {
                    "name": getattr(obj, "Name", "?"),
                    "error": f"Failed to summarize: {exc}",
                }
            )

    tree = _feature_tree(doc)
    selection = get_selection()
    active_body = None
    try:
        if Gui and Gui.ActiveDocument:
            body = getattr(Gui.ActiveDocument, "ActiveView", None)
            # Active body lives on the UI document in PartDesign
            ui_doc = Gui.ActiveDocument
            if hasattr(ui_doc, "ActiveView"):
                pass
            # FreeCAD 1.0: Gui.ActiveDocument.ActiveView.getActiveObject("pdbody")
            view = ui_doc.ActiveView
            if view and hasattr(view, "getActiveObject"):
                b = view.getActiveObject("pdbody")
                if b:
                    active_body = b.Name
    except Exception:  # noqa: BLE001
        active_body = None

    wb = None
    try:
        if Gui:
            wb = Gui.activeWorkbench().name()
    except Exception:  # noqa: BLE001
        wb = None

    return {
        "ok": True,
        "name": doc.Name,
        "label": getattr(doc, "Label", doc.Name),
        "file": getattr(doc, "FileName", "") or "",
        "object_count": len(doc.Objects),
        "recomputes_required": bool(getattr(doc, "RecomputesRequired", False)),
        "undo_available": True,
        "active_body": active_body,
        "active_workbench": wb,
        "units": "mm (FreeCAD internal unit)",
        "objects": objects,
        "feature_tree": tree,
        "selection": selection,
        "open_documents": list_open_documents(),
    }


def _summarize_object(obj, *, include_properties: bool = False) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "name": obj.Name,
        "label": getattr(obj, "Label", obj.Name),
        "type": getattr(obj, "TypeId", type(obj).__name__),
        "visibility": _visibility(obj),
        "state": _state_list(obj),
        "valid": _is_valid(obj),
        "inlist": [o.Name for o in getattr(obj, "InList", [])],
        "outlist": [o.Name for o in getattr(obj, "OutList", [])],
    }

    if hasattr(obj, "Tip") and obj.Tip is not None:
        try:
            info["tip"] = obj.Tip.Name
        except Exception:  # noqa: BLE001
            info["tip"] = str(obj.Tip)

    if hasattr(obj, "Group"):
        try:
            info["group"] = [o.Name for o in (obj.Group or [])]
        except Exception:  # noqa: BLE001
            pass

    shape = getattr(obj, "Shape", None)
    if shape is not None:
        info["shape"] = _shape_metrics(shape)

    if obj.TypeId == "Sketcher::SketchObject" or "Sketch" in obj.TypeId:
        info["sketch"] = _sketch_summary(obj)

    if include_properties:
        info["properties"] = _property_dump(obj)

    # Placement
    try:
        pl = obj.Placement
        info["placement"] = {
            "base": [pl.Base.x, pl.Base.y, pl.Base.z],
            "rotation_axis": [pl.Rotation.Axis.x, pl.Rotation.Axis.y, pl.Rotation.Axis.z],
            "rotation_angle_deg": math.degrees(pl.Rotation.Angle),
        }
    except Exception:  # noqa: BLE001
        pass

    return info


def inspect_object(name: str, *, deep: bool = True) -> Dict[str, Any]:
    """Deep inspection of a single object by Name or Label."""
    App = _app()
    doc = App.ActiveDocument
    if doc is None:
        return {"ok": False, "error": "No active document."}
    obj = doc.getObject(name)
    if obj is None:
        # Fall back to label search (first match).
        matches = [o for o in doc.Objects if o.Label == name]
        if not matches:
            return {
                "ok": False,
                "error": f"No object named or labelled {name!r}.",
                "available": [o.Name for o in doc.Objects],
            }
        obj = matches[0]

    info = _summarize_object(obj, include_properties=True)
    info["ok"] = True

    if deep:
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            info["topology"] = _topology_detail(shape)
        if obj.TypeId == "Sketcher::SketchObject" or "Sketch" in obj.TypeId:
            info["sketch"] = _sketch_summary(obj, deep=True)
        if "PartDesign::Body" in getattr(obj, "TypeId", ""):
            info["body_features"] = [
                {"name": f.Name, "type": f.TypeId, "label": f.Label}
                for f in getattr(obj, "Group", []) or []
            ]
            if getattr(obj, "Tip", None):
                info["tip"] = obj.Tip.Name

    return info


def get_selection() -> List[Dict[str, Any]]:
    Gui = _gui()
    if Gui is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for sel in Gui.Selection.getSelectionEx():
            item: Dict[str, Any] = {
                "document": sel.DocumentName,
                "object": sel.ObjectName,
                "sub_elements": list(sel.SubElementNames or []),
                "has_sub_objects": bool(sel.HasSubObjects),
            }
            try:
                if sel.Object:
                    item["type"] = sel.Object.TypeId
                    item["label"] = sel.Object.Label
            except Exception:  # noqa: BLE001
                pass
            pnts = []
            try:
                for p in sel.PickedPoints or []:
                    pnts.append([p.x, p.y, p.z])
            except Exception:  # noqa: BLE001
                pass
            if pnts:
                item["picked_points"] = pnts
            out.append(item)
    except Exception as exc:  # noqa: BLE001
        log.warn(f"get_selection failed: {exc}")
    return out


def search_objects(query: str, *, type_contains: str = "") -> List[Dict[str, str]]:
    App = _app()
    doc = App.ActiveDocument
    if doc is None:
        return []
    q = (query or "").lower()
    tq = (type_contains or "").lower()
    hits = []
    for obj in doc.Objects:
        if tq and tq not in obj.TypeId.lower():
            continue
        blob = f"{obj.Name} {obj.Label} {obj.TypeId}".lower()
        if not q or q in blob:
            hits.append({"name": obj.Name, "label": obj.Label, "type": obj.TypeId})
    return hits


def _visibility(obj) -> Optional[bool]:
    try:
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            return bool(view.Visibility)
    except Exception:  # noqa: BLE001
        return None
    return None


def _state_list(obj) -> List[str]:
    try:
        st = obj.State
        if isinstance(st, (list, tuple)):
            return [str(s) for s in st]
        return [str(st)]
    except Exception:  # noqa: BLE001
        return []


def _is_valid(obj) -> bool:
    state = _state_list(obj)
    if any(s.lower() in {"invalid", "touch", "error"} or "err" in s.lower() for s in state):
        # "Touched" is not fatal; "Invalid" is.
        if any("invalid" in s.lower() or "error" in s.lower() for s in state):
            return False
    shape = getattr(obj, "Shape", None)
    if shape is not None:
        try:
            if hasattr(shape, "isValid") and not shape.isValid():
                return False
            if getattr(shape, "isNull", lambda: False)():
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


def _shape_metrics(shape) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    try:
        if getattr(shape, "isNull", lambda: False)():
            data["null"] = True
            return data
        data["null"] = False
        data["valid"] = bool(shape.isValid()) if hasattr(shape, "isValid") else None
        data["solids"] = len(shape.Solids)
        data["shells"] = len(shape.Shells)
        data["faces"] = len(shape.Faces)
        data["edges"] = len(shape.Edges)
        data["wires"] = len(shape.Wires)
        data["vertices"] = len(shape.Vertexes)
        try:
            data["volume_mm3"] = float(shape.Volume)
        except Exception:  # noqa: BLE001
            data["volume_mm3"] = None
        try:
            data["area_mm2"] = float(shape.Area)
        except Exception:  # noqa: BLE001
            data["area_mm2"] = None
        bb = shape.BoundBox
        data["bounding_box_mm"] = {
            "xmin": bb.XMin,
            "xmax": bb.XMax,
            "ymin": bb.YMin,
            "ymax": bb.YMax,
            "zmin": bb.ZMin,
            "zmax": bb.ZMax,
            "dx": bb.XLength,
            "dy": bb.YLength,
            "dz": bb.ZLength,
            "diagonal": bb.DiagonalLength,
        }
        try:
            com = shape.CenterOfMass
            data["center_of_mass"] = [com.x, com.y, com.z]
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        data["error"] = str(exc)
    return data


def _topology_detail(shape, limit: int = 40) -> Dict[str, Any]:
    """Per-face / per-edge summary, capped so the prompt stays small."""
    faces = []
    edges = []
    try:
        for i, face in enumerate(shape.Faces[:limit], start=1):
            item: Dict[str, Any] = {
                "index": i,
                "name": f"Face{i}",
                "area": _safe_float(getattr(face, "Area", None)),
            }
            try:
                surf = face.Surface
                item["surface"] = type(surf).__name__
            except Exception:  # noqa: BLE001
                pass
            faces.append(item)
        for i, edge in enumerate(shape.Edges[:limit], start=1):
            item = {
                "index": i,
                "name": f"Edge{i}",
                "length": _safe_float(getattr(edge, "Length", None)),
            }
            try:
                curve = edge.Curve
                item["curve"] = type(curve).__name__
            except Exception:  # noqa: BLE001
                pass
            edges.append(item)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {
        "faces": faces,
        "edges": edges,
        "truncated": len(shape.Faces) > limit or len(shape.Edges) > limit,
    }


def _sketch_summary(obj, *, deep: bool = False) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    try:
        geoms = list(obj.Geometry or [])
        cons = list(obj.Constraints or [])
        info["geometry_count"] = len(geoms)
        info["constraint_count"] = len(cons)
        try:
            info["fully_constrained"] = bool(obj.FullyConstrained)
        except Exception:  # noqa: BLE001
            info["fully_constrained"] = None
        try:
            info["missing_constraints"] = int(obj.MissingConstraints)
        except Exception:  # noqa: BLE001
            pass
        try:
            info["solver_status"] = str(getattr(obj, "Status", ""))
        except Exception:  # noqa: BLE001
            pass
        if deep:
            info["geometry"] = []
            for i, g in enumerate(geoms):
                info["geometry"].append(
                    {
                        "index": i,
                        "type": type(g).__name__,
                        "construction": bool(getattr(g, "Construction", False)),
                    }
                )
            info["constraints"] = []
            for i, c in enumerate(cons):
                rec = {
                    "index": i,
                    "type": getattr(c, "Type", type(c).__name__),
                    "name": getattr(c, "Name", "") or "",
                }
                try:
                    rec["value"] = float(c.Value)
                except Exception:  # noqa: BLE001
                    pass
                info["constraints"].append(rec)
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


def _property_dump(obj, limit: int = 80) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    names: Sequence[str]
    try:
        names = list(obj.PropertiesList)
    except Exception:  # noqa: BLE001
        return props
    skip = {
        "Shape",
        "Proxy",
        "ExpressionEngine",
        "ViewObject",
        "AttacherType",
    }
    count = 0
    for name in names:
        if name in skip:
            continue
        if count >= limit:
            props["_truncated"] = True
            break
        try:
            val = getattr(obj, name)
            props[name] = _stringify_prop(val)
            count += 1
        except Exception:  # noqa: BLE001
            props[name] = "<unreadable>"
    return props


def _stringify_prop(val: Any) -> Any:
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    # Quantity
    try:
        if hasattr(val, "Value") and hasattr(val, "UserString"):
            return {"value": float(val.Value), "user": str(val.UserString)}
    except Exception:  # noqa: BLE001
        pass
    # Vector
    try:
        if hasattr(val, "x") and hasattr(val, "y") and hasattr(val, "z") and not hasattr(val, "Objects"):
            return [float(val.x), float(val.y), float(val.z)]
    except Exception:  # noqa: BLE001
        pass
    # Linked object
    try:
        if hasattr(val, "Name") and hasattr(val, "TypeId"):
            return {"ref": val.Name, "type": val.TypeId}
    except Exception:  # noqa: BLE001
        pass
    if isinstance(val, (list, tuple)):
        return [_stringify_prop(v) for v in list(val)[:30]]
    text = str(val)
    return text if len(text) < 200 else text[:200] + "…"


def _feature_tree(doc) -> List[Dict[str, Any]]:
    """Hierarchical tree of root objects (those with empty InList of documents)."""
    roots = []
    try:
        named = {o.Name for o in doc.Objects}
        for obj in doc.Objects:
            parents = [p for p in obj.InList if p.Name in named]
            if not parents:
                roots.append(_tree_node(obj, seen=set()))
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]
    return roots


def _tree_node(obj, seen: set, depth: int = 0) -> Dict[str, Any]:
    node: Dict[str, Any] = {
        "name": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
    }
    if depth > 20 or obj.Name in seen:
        node["children"] = []
        return node
    seen = set(seen)
    seen.add(obj.Name)
    children = []
    group = list(getattr(obj, "Group", []) or [])
    # Also include OutList members that are not already in Group.
    extra = [o for o in getattr(obj, "OutList", []) or [] if o not in group]
    for child in group + extra:
        try:
            children.append(_tree_node(child, seen, depth + 1))
        except Exception:  # noqa: BLE001
            continue
    if children:
        node["children"] = children
    return node


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:  # noqa: BLE001
        return None


def get_workbench_info() -> Dict[str, Any]:
    Gui = _gui()
    App = _app()
    info: Dict[str, Any] = {
        "freecad_version": getattr(App, "Version", lambda: [])(),
    }
    try:
        info["version_string"] = ".".join(str(x) for x in App.Version()[:4])
    except Exception:  # noqa: BLE001
        info["version_string"] = "unknown"
    if Gui:
        try:
            info["active_workbench"] = Gui.activeWorkbench().name()
        except Exception:  # noqa: BLE001
            info["active_workbench"] = None
        try:
            info["available_workbenches"] = sorted(Gui.listWorkbenches().keys())
        except Exception:  # noqa: BLE001
            info["available_workbenches"] = []
    info["modules"] = _probe_modules()
    return info


def _probe_modules() -> Dict[str, bool]:
    names = [
        "Part",
        "PartDesign",
        "Sketcher",
        "Draft",
        "Mesh",
        "MeshPart",
        "Fem",
        "ObjectsFem",
        "Spreadsheet",
        "TechDraw",
        "Assembly",
        "Import",
        "CAM",
        "Path",
        "Arch",
        "BIM",
    ]
    out = {}
    for n in names:
        try:
            __import__(n)
            out[n] = True
        except Exception:  # noqa: BLE001
            out[n] = False
    return out
