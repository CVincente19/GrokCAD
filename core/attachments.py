# SPDX-License-Identifier: MIT
"""
Load user drawings (images + PDF pages) for Grok vision.

Returns the same image dicts as screenshot.capture_views() so the chat
client can attach them as ``image_url`` parts. PDF pages are rasterized
when a renderer is available; embedded text is always extracted when
possible (dimension callouts often live in the text layer).
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import log
from .paths import screenshot_dir

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
MAX_PDF_PAGES = 8
MAX_IMAGE_EDGE = 2000


def is_supported(path: str) -> bool:
    ext = Path(path).suffix.lower()
    return ext in IMAGE_EXTS or ext in PDF_EXTS


def load_paths(paths: Sequence[str], *, max_pdf_pages: int = MAX_PDF_PAGES) -> Dict[str, Any]:
    """Load many files. Returns {ok, images, text, errors, files}."""
    images: List[Dict[str, Any]] = []
    texts: List[str] = []
    errors: List[str] = []
    files: List[str] = []
    for raw in paths:
        p = os.path.expanduser(str(raw))
        if not os.path.isfile(p):
            errors.append("Not a file: %s" % p)
            continue
        ext = Path(p).suffix.lower()
        files.append(p)
        if ext in IMAGE_EXTS:
            rec = load_image(p)
            if rec.get("ok"):
                images.append(rec)
            else:
                errors.append("%s: %s" % (os.path.basename(p), rec.get("error")))
        elif ext in PDF_EXTS:
            rec = load_pdf(p, max_pages=max_pdf_pages)
            images.extend(rec.get("images") or [])
            if rec.get("text"):
                texts.append("----- PDF %s -----\n%s" % (os.path.basename(p), rec["text"]))
            if rec.get("error"):
                errors.append("%s: %s" % (os.path.basename(p), rec["error"]))
        else:
            errors.append("Unsupported type: %s" % os.path.basename(p))
    return {
        "ok": bool(images or texts) and not (errors and not images and not texts),
        "images": images,
        "text": "\n\n".join(texts).strip(),
        "errors": errors,
        "files": files,
    }


def load_image(path: str) -> Dict[str, Any]:
    """Read an image file, normalize to PNG, optionally downscale."""
    src = Path(path)
    try:
        from .qtcompat import QtGui, keep_aspect, require_qt, smooth_transform

        require_qt()
        img = QtGui.QImage(str(src))
        if img.isNull():
            return {"ok": False, "error": "Could not decode image"}
        if img.width() > MAX_IMAGE_EDGE or img.height() > MAX_IMAGE_EDGE:
            img = img.scaled(MAX_IMAGE_EDGE, MAX_IMAGE_EDGE, keep_aspect(), smooth_transform())
        out = screenshot_dir() / ("attach_%s.png" % src.stem)
        img.save(str(out), "PNG")
        raw = out.read_bytes()
        return {
            "ok": True,
            "view": src.name,
            "mime": "image/png",
            "width": img.width(),
            "height": img.height(),
            "png_base64": base64.b64encode(raw).decode("ascii"),
            "path": str(out),
            "source": str(src),
            "bytes": len(raw),
        }
    except Exception as exc:  # noqa: BLE001
        log.warn("Qt image load failed (%s), trying Pillow" % exc)
    try:
        from PIL import Image  # type: ignore

        im = Image.open(src)
        im = im.convert("RGBA")
        im.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        out = screenshot_dir() / ("attach_%s.png" % src.stem)
        im.save(out, format="PNG")
        raw = out.read_bytes()
        return {
            "ok": True,
            "view": src.name,
            "mime": "image/png",
            "width": im.width,
            "height": im.height,
            "png_base64": base64.b64encode(raw).decode("ascii"),
            "path": str(out),
            "source": str(src),
            "bytes": len(raw),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def load_pdf(path: str, *, max_pages: int = MAX_PDF_PAGES) -> Dict[str, Any]:
    """Rasterize up to *max_pages* and extract text."""
    text = _pdf_text(path)
    images: List[Dict[str, Any]] = []
    err = None
    for loader in (_pdf_fitz, _pdf_qt, _pdf_pdftoppm):
        try:
            images = loader(path, max_pages=max_pages)
            if images:
                break
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            log.warn("PDF renderer %s failed: %s" % (loader.__name__, exc))
    if not images and not text:
        return {
            "ok": False,
            "images": [],
            "text": "",
            "error": err
            or (
                "Could not read this PDF. Install pymupdf in FreeCAD's Python "
                "(flatpak run --command=python3 org.freecad.FreeCAD -m pip install --user pymupdf) "
                "or attach a PNG/JPG of the drawing."
            ),
        }
    hint = ""
    if not images and text:
        hint = (
            "PDF pages could not be rasterized; sending extracted text only. "
            "For dimensioned drawings, also attach a screenshot of the page."
        )
    return {
        "ok": True,
        "images": images,
        "text": text,
        "error": hint or None,
        "pages": len(images),
    }


def _pdf_text(path: str) -> str:
    try:
        import fitz  # type: ignore

        doc = fitz.open(path)
        parts = []
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break
            t = page.get_text("text") or ""
            if t.strip():
                parts.append("--- page %d ---\n%s" % (i + 1, t.strip()))
        doc.close()
        return "\n\n".join(parts)
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(path)
        parts = []
        for i, page in enumerate(reader.pages[:MAX_PDF_PAGES]):
            t = page.extract_text() or ""
            if t.strip():
                parts.append("--- page %d ---\n%s" % (i + 1, t.strip()))
        return "\n\n".join(parts)
    except Exception:
        return ""


def _pdf_fitz(path: str, *, max_pages: int) -> List[Dict[str, Any]]:
    import fitz  # type: ignore

    doc = fitz.open(path)
    images = []
    # ~150 dpi
    matrix = fitz.Matrix(2.0, 2.0)
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out = screenshot_dir() / ("%s_p%d.png" % (Path(path).stem, i + 1))
        pix.save(str(out))
        raw = out.read_bytes()
        images.append(
            {
                "ok": True,
                "view": "%s p%d" % (Path(path).name, i + 1),
                "mime": "image/png",
                "width": pix.width,
                "height": pix.height,
                "png_base64": base64.b64encode(raw).decode("ascii"),
                "path": str(out),
                "source": path,
                "bytes": len(raw),
            }
        )
    doc.close()
    return images


def _pdf_qt(path: str, *, max_pages: int) -> List[Dict[str, Any]]:
    from .qtcompat import QtCore, QtGui, require_qt

    require_qt()
    try:
        from PySide6.QtPdf import QPdfDocument  # type: ignore
        from PySide6.QtCore import QSize
    except Exception:
        from PySide.QtPdf import QPdfDocument  # type: ignore
        from PySide.QtCore import QSize  # type: ignore

    doc = QPdfDocument()
    status = doc.load(path)
    # 0 / QPdfDocument.Status.Ready depending on binding
    images = []
    count = int(doc.pageCount())
    for i in range(min(count, max_pages)):
        size = QSize(1600, 2200)
        qimg = doc.render(i, size)
        if qimg is None or qimg.isNull():
            continue
        out = screenshot_dir() / ("%s_p%d.png" % (Path(path).stem, i + 1))
        qimg.save(str(out), "PNG")
        raw = out.read_bytes()
        images.append(
            {
                "ok": True,
                "view": "%s p%d" % (Path(path).name, i + 1),
                "mime": "image/png",
                "width": qimg.width(),
                "height": qimg.height(),
                "png_base64": base64.b64encode(raw).decode("ascii"),
                "path": str(out),
                "source": path,
                "bytes": len(raw),
            }
        )
    return images


def _pdf_pdftoppm(path: str, *, max_pages: int) -> List[Dict[str, Any]]:
    exe = shutil.which("pdftoppm")
    if not exe:
        return []
    tmp = Path(tempfile.mkdtemp(prefix="grokcad_pdf_"))
    try:
        subprocess.run(
            [exe, "-png", "-r", "140", "-f", "1", "-l", str(max_pages), path, str(tmp / "page")],
            check=True,
            capture_output=True,
            timeout=60,
        )
        images = []
        for i, png in enumerate(sorted(tmp.glob("page*.png")), start=1):
            rec = load_image(str(png))
            if rec.get("ok"):
                rec["view"] = "%s p%d" % (Path(path).name, i)
                rec["source"] = path
                images.append(rec)
        return images
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
