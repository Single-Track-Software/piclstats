"""Backward-compatible Jinja2Templates wrapper.

Starlette 1.x changed ``TemplateResponse``'s signature from the legacy
``(name, context)`` to ``(request, name, context)`` and dropped support for the
old positional order. The CVE-clearing Starlette bump pulled that in, which 500s
every page that still calls the legacy form (all of them).

This shim accepts the legacy positional call and forwards it correctly, so the
fix lives in one place instead of every route. New code can use either form.
"""

from __future__ import annotations

from typing import Any

from fastapi.templating import Jinja2Templates as _Jinja2Templates


class Jinja2Templates(_Jinja2Templates):
    def TemplateResponse(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        # Legacy form: TemplateResponse(name: str, context: dict, ...).
        # Modern form has a Request first, so a str in slot 0 means legacy.
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", {}) or {}
            request = context.get("request")
            return super().TemplateResponse(request, name, context, *args[2:], **kwargs)
        return super().TemplateResponse(*args, **kwargs)
