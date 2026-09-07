# SPDX-License-Identifier: MIT
"""
xAI Grok client (OpenAI-compatible) and the agentic tool loop.

Network I/O lives on a QThread. FreeCAD document mutations never happen
here — the worker emits ``tool_calls_ready`` and the GUI thread runs
``core.tools.execute_tool``, then calls ``submit_tool_results``.

The public surface:

* :class:`GrokWorker` — QThread that performs one chat-completions call
  (optionally streaming) and emits tokens / tool calls / errors.
* :class:`Conversation` — message list + system prompt + serialization.
* :func:`load_system_prompt` — engineered mechanical-design prompt.
* :func:`make_client` / :func:`discover_models` — API helpers.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence

from . import log, prefs
from .paths import SYSTEM_PROMPT_FILE
from .qtcompat import QtCore, Signal, require_qt
from .tools import TOOL_DEFINITIONS, compact_tool_result

PREFERRED_MODELS = list(prefs.KNOWN_MODELS)


def load_system_prompt() -> str:
    extra = prefs.get_extra_instructions().strip()
    try:
        text = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warn(f"Could not read system prompt file: {exc}")
        text = _FALLBACK_PROMPT
    if extra:
        text = text + "\n\n# Additional user instructions\n" + extra + "\n"
    return text


_FALLBACK_PROMPT = """You are Grok CAD Agent, an expert mechanical design engineer working inside FreeCAD.
Units are millimetres. Prefer PartDesign Bodies, fully constrained sketches, meaningful names,
and parametric features. Use the provided tools. After geometry changes, take a screenshot
and visually verify the result. If Python fails, read the traceback and fix the script.
"""


def _ensure_user_site() -> None:
    """Make ``pip install --user`` packages visible to FreeCAD's Python."""
    import site
    import sys

    try:
        user = site.getusersitepackages()
        if user and user not in sys.path:
            sys.path.insert(0, user)
        # Fedora sometimes uses this extra location.
        try:
            for p in site.getsitepackages():
                if p not in sys.path:
                    sys.path.append(p)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def make_client(api_key: Optional[str] = None, base_url: Optional[str] = None):
    """Return an OpenAI-compatible client pointed at xAI."""
    _ensure_user_site()
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "The 'openai' Python package is not installed in FreeCAD's "
            "Python. On Fedora run:  pip install --user openai pillow\n"
            f"Import error: {exc}"
        ) from exc

    key = (api_key if api_key is not None else prefs.get_api_key()).strip()
    if not key:
        raise RuntimeError(
            "No xAI API key. Open Edit → Preferences → Grok CAD Agent "
            "and paste your key from https://console.x.ai/  "
            "(or export XAI_API_KEY)."
        )
    url = (base_url if base_url is not None else prefs.get_base_url()).rstrip("/")
    timeout = prefs.get_request_timeout()
    # Split connect vs read: a hung TLS handshake should fail in ~20s, not 180s.
    try:
        import httpx  # type: ignore

        timeout_obj = httpx.Timeout(timeout, connect=20.0, write=60.0, read=timeout)
    except Exception:  # noqa: BLE001
        timeout_obj = timeout
    # max_retries=0: surface errors immediately instead of sitting on
    # "Contacting Grok" for three sequential 180s timeouts.
    return OpenAI(api_key=key, base_url=url, timeout=timeout_obj, max_retries=0)


def discover_models(client=None) -> List[str]:
    """Ask the API which models exist; fall back to the known list."""
    close = False
    if client is None:
        try:
            client = make_client()
            close = True
        except Exception as exc:  # noqa: BLE001
            log.warn(f"Model discovery skipped: {exc}")
            return list(PREFERRED_MODELS)
    names: List[str] = []
    try:
        page = client.models.list()
        for m in getattr(page, "data", []) or []:
            mid = getattr(m, "id", None) or (m.get("id") if isinstance(m, dict) else None)
            if mid:
                names.append(str(mid))
    except Exception as exc:  # noqa: BLE001
        log.warn(f"models.list failed: {exc}")
    # Prefer grok-* and keep the curated list first.
    grok = [n for n in names if n.startswith("grok")]
    ordered: List[str] = []
    for n in PREFERRED_MODELS + grok + names:
        if n not in ordered:
            ordered.append(n)
    if close:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    return ordered or list(PREFERRED_MODELS)


def pick_model(requested: str, available: Optional[Sequence[str]] = None) -> str:
    """Return *requested* if it looks usable, else the first preferred model."""
    req = (requested or "").strip()
    if not available:
        return req or prefs.DEFAULT_MODEL
    if req in available:
        return req
    # Fuzzy: requested prefix
    for a in available:
        if req and (a.startswith(req) or req.startswith(a)):
            return a
    for pref in PREFERRED_MODELS:
        if pref in available:
            return pref
    return available[0] if available else (req or prefs.DEFAULT_MODEL)


class Conversation:
    """In-memory multi-turn transcript plus helpers for export."""

    def __init__(self, system_prompt: Optional[str] = None) -> None:
        self.system_prompt = system_prompt if system_prompt is not None else load_system_prompt()
        self.messages: List[Dict[str, Any]] = []
        self.created = time.time()
        self.title = "New design session"
        self.model = prefs.get_model()
        self.usage_prompt_tokens = 0
        self.usage_completion_tokens = 0

    def reset(self, *, keep_system: bool = True) -> None:
        self.messages.clear()
        if not keep_system:
            self.system_prompt = load_system_prompt()
        else:
            self.system_prompt = load_system_prompt()
        self.created = time.time()
        self.title = "New design session"
        self.usage_prompt_tokens = 0
        self.usage_completion_tokens = 0

    def api_messages(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        out.extend(_prune_old_images(self.messages, keep_last=1))
        return out

    def add_user(self, text: str, images: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if images:
            content: Any = [{"type": "text", "text": text}]
            from .screenshot import images_to_openai_content

            content.extend(images_to_openai_content(images))
        else:
            content = text
        msg = {"role": "user", "content": content}
        self.messages.append(msg)
        if self.title == "New design session" and text.strip():
            self.title = text.strip().splitlines()[0][:80]
        return msg

    def add_assistant(
        self,
        text: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": "assistant", "content": text or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        return msg

    def add_tool_result(
        self,
        tool_call_id: str,
        name: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        compact = compact_tool_result(name, result, keep_images=False)
        msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": json.dumps(compact, default=str),
        }
        self.messages.append(msg)
        return msg

    def add_tool_images_as_user(self, images: List[Dict[str, Any]], note: str = "") -> None:
        """Attach screenshots as a user vision message so Grok can actually see them."""
        if not images:
            return
        from .screenshot import images_to_openai_content

        text = note or "Viewport screenshots from the last tool call. Inspect the geometry visually."
        content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend(images_to_openai_content(images))
        self.messages.append({"role": "user", "content": content})

    def to_markdown(self) -> str:
        lines = [
            f"# Grok CAD Agent — {self.title}",
            "",
            f"- Model: `{self.model}`",
            f"- Created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created))}",
            f"- Tokens: prompt={self.usage_prompt_tokens} completion={self.usage_completion_tokens}",
            "",
        ]
        for msg in self.messages:
            role = msg.get("role", "?")
            lines.append(f"## {role}")
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        lines.append(part.get("text", ""))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        lines.append("*[image attached]*")
                    else:
                        lines.append(str(part))
            else:
                lines.append(str(content or ""))
            if msg.get("tool_calls"):
                lines.append("\n```json")
                lines.append(json.dumps(msg["tool_calls"], indent=2, default=str))
                lines.append("```")
            lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        payload = {
            "title": self.title,
            "model": self.model,
            "created": self.created,
            "usage": {
                "prompt_tokens": self.usage_prompt_tokens,
                "completion_tokens": self.usage_completion_tokens,
            },
            "system": self.system_prompt,
            "messages": self.messages,
        }
        return json.dumps(payload, indent=2, default=str)


class GrokWorker(QtCore.QThread if QtCore is not None else object):  # type: ignore[misc]
    """
    One HTTP round-trip.

    Signals (GUI thread):

    * ``chunk`` (str) — streamed assistant text
    * ``tool_calls_ready`` (list) — OpenAI tool_call dicts
    * ``completed`` (dict) — {content, tool_calls, finish_reason, usage, model}
    * ``failed`` (str) — error message
    """

    if QtCore is not None:
        started = Signal(str)
        chunk = Signal(str)
        tool_calls_ready = Signal(object)
        completed = Signal(object)
        failed = Signal(str)

    def __init__(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = True,
        plan_only: bool = False,
        parent=None,
    ) -> None:
        require_qt()
        super().__init__(parent)
        self._messages = messages
        self._model = model or prefs.get_model()
        self._temperature = prefs.get_temperature() if temperature is None else temperature
        self._max_tokens = prefs.get_max_tokens() if max_tokens is None else max_tokens
        self._tools = None if plan_only else (tools if tools is not None else TOOL_DEFINITIONS)
        self._stream = stream
        self._plan_only = plan_only

    def run(self) -> None:  # noqa: D401
        est = estimate_prompt_tokens(self._messages, self._tools)
        self.started.emit(
            "Sending to {model} (est. {n:,} input tokens; {cap:,} max output)…".format(
                model=self._model, n=est, cap=int(self._max_tokens)
            )
        )
        try:
            client = make_client()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        try:
            result = self._call(client)
            if self.isInterruptionRequested():
                self.failed.emit("Cancelled.")
                return
            if result.get("tool_calls"):
                self.tool_calls_ready.emit(result["tool_calls"])
            self.completed.emit(result)
        except Exception as exc:  # noqa: BLE001
            log.error("Grok API call failed", exc)
            self.failed.emit(_friendly_api_error(exc))
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    def _call(self, client) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            "temperature": float(self._temperature),
        }
        # Newer OpenAI-compatible APIs prefer max_completion_tokens.
        # xAI accepts max_tokens; send the new name and keep a fallback.
        kwargs["max_completion_tokens"] = int(self._max_tokens)
        if self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto"
        try:
            if self._stream:
                return self._stream_call(client, kwargs)
            return self._sync_call(client, kwargs)
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if "max_completion_tokens" in text or "max_tokens" in text:
                kwargs.pop("max_completion_tokens", None)
                kwargs["max_tokens"] = int(self._max_tokens)
                if self._stream:
                    return self._stream_call(client, kwargs)
                return self._sync_call(client, kwargs)
            raise

    def _sync_call(self, client, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        resp = client.chat.completions.create(**kwargs, stream=False)
        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""
        if content:
            self.chunk.emit(content)
        tool_calls = _normalize_tool_calls(getattr(msg, "tool_calls", None))
        usage = _usage_dict(getattr(resp, "usage", None))
        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": getattr(choice, "finish_reason", None),
            "usage": usage,
            "model": getattr(resp, "model", self._model),
        }

    def _stream_call(self, client, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            stream = client.chat.completions.create(
                **kwargs, stream=True, stream_options={"include_usage": True}
            )
        except TypeError:
            stream = client.chat.completions.create(**kwargs, stream=True)
        except Exception as exc:  # noqa: BLE001
            if "stream_options" in str(exc).lower():
                stream = client.chat.completions.create(**kwargs, stream=True)
            else:
                raise
        content_parts: List[str] = []
        acc: Dict[int, Dict[str, Any]] = {}
        finish = None
        usage = {}
        model = self._model
        for event in stream:
            if self.isInterruptionRequested():
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
                break
            if getattr(event, "model", None):
                model = event.model
            if getattr(event, "usage", None):
                usage = _usage_dict(event.usage)
            if not event.choices:
                continue
            choice = event.choices[0]
            finish = getattr(choice, "finish_reason", None) or finish
            delta = choice.delta
            if delta is None:
                continue
            piece = getattr(delta, "content", None)
            if piece:
                content_parts.append(piece)
                self.chunk.emit(piece)
            tcs = getattr(delta, "tool_calls", None)
            if tcs:
                _accumulate_tool_delta(acc, tcs)
        content = "".join(content_parts)
        tool_calls = _finalize_tool_acc(acc)
        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": finish,
            "usage": usage,
            "model": model,
        }


def _accumulate_tool_delta(acc: Dict[int, Dict[str, Any]], tcs: Any) -> None:
    for tc in tcs:
        idx = getattr(tc, "index", 0) or 0
        slot = acc.setdefault(
            idx,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if getattr(tc, "id", None):
            slot["id"] = tc.id
        if getattr(tc, "type", None):
            slot["type"] = tc.type
        fn = getattr(tc, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                slot["function"]["name"] += fn.name
            if getattr(fn, "arguments", None):
                slot["function"]["arguments"] += fn.arguments


def _finalize_tool_acc(acc: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx in sorted(acc):
        slot = acc[idx]
        if not slot["id"]:
            slot["id"] = f"call_{idx}"
        out.append(slot)
    return out


def _normalize_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    out: List[Dict[str, Any]] = []
    for tc in raw:
        if isinstance(tc, dict):
            out.append(tc)
            continue
        fn = getattr(tc, "function", None)
        out.append(
            {
                "id": getattr(tc, "id", "") or "",
                "type": getattr(tc, "type", "function") or "function",
                "function": {
                    "name": getattr(fn, "name", "") if fn is not None else "",
                    "arguments": getattr(fn, "arguments", "") if fn is not None else "",
                },
            }
        )
    return out


def _usage_dict(usage: Any) -> Dict[str, int]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _prune_old_images(messages: List[Dict[str, Any]], keep_last: int = 1) -> List[Dict[str, Any]]:
    """Keep only the last *keep_last* vision messages. Older images become a stub.

    Re-sending every screenshot on every turn is what pushed the Sprite session
    to ~100k input tokens.
    """
    vision_idxs = []
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            vision_idxs.append(i)
    drop = set(vision_idxs[:-keep_last] if keep_last > 0 else vision_idxs)
    if not drop:
        return list(messages)
    out: List[Dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i not in drop:
            out.append(msg)
            continue
        texts = []
        for p in msg.get("content") or []:
            if isinstance(p, dict) and p.get("type") == "text":
                texts.append(p.get("text") or "")
        stub = (texts[0] if texts else "[earlier screenshot omitted to save tokens]")
        stub += " (image omitted; a newer screenshot follows)"
        out.append({"role": msg.get("role", "user"), "content": stub})
    return out


def estimate_prompt_tokens(messages: List[Dict[str, Any]], tools: Optional[List[Any]] = None) -> int:
    """Rough input-token estimate (chars/4 + a lump per image). Not billed usage."""
    n = 0
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, str):
            n += max(1, len(content) // 4)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    n += max(1, len(str(part.get("text") or "")) // 4)
                elif part.get("type") in {"image_url", "image"}:
                    n += 1200
        extra = msg.get("tool_calls")
        if extra:
            n += max(1, len(json.dumps(extra, default=str)) // 4)
    if tools:
        n += max(1, len(json.dumps(tools, default=str)) // 4)
    return n


def parse_tool_arguments(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return {}
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        # Models sometimes emit trailing junk; try a brace slice.
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                val = json.loads(text[start : end + 1])
                if isinstance(val, dict):
                    return val
            except json.JSONDecodeError:
                pass
        return {"_raw": text}


def _friendly_api_error(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc)
    if "401" in text or "Unauthorized" in text or "invalid_api_key" in text:
        return (
            "xAI rejected the API key (401). Check Edit → Preferences → "
            "Grok CAD Agent, or the XAI_API_KEY environment variable."
        )
    if "429" in text or "rate" in text.lower():
        return f"Rate limited by xAI. Wait a moment and retry. ({text})"
    if "ConnectError" in name or "Connection" in name or "timed out" in text.lower():
        return (
            "Could not reach https://api.x.ai/v1. Check your network / DNS / "
            f"proxy. Details: {text}"
        )
    if "model" in text.lower() and ("not found" in text.lower() or "404" in text):
        return (
            f"Model not available: {text}  "
            "Open the model dropdown and pick another grok-* model."
        )
    return f"{name}: {text}"
