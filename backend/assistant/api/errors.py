"""Turn module errors into the API's error answer: ``detail.code`` and ``detail.message``."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from assistant.errors import ModuleError, NotAllowedValue
from assistant.rules.sentences import is_plain_sentence

# Friendlier names for fields whose meaning is not obvious from their key.
FIELD_LABELS = {"per_day": "A daily number"}


def error_response(error: ModuleError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"detail": {"code": error.code, "message": error.message}},
    )


def _label(loc: Sequence[Any]) -> str:
    names = [str(part) for part in loc if isinstance(part, str) and part != "body"]
    if not names:
        return "This value"
    last = names[-1]
    return FIELD_LABELS.get(last, last.replace("_", " ").capitalize())


def sentence_for(errors: Sequence[Any]) -> str:
    """One plain sentence for the first thing wrong with a request."""
    if not errors:
        return NotAllowedValue.default_message
    first = errors[0]
    kind, ctx, label = first.get("type", ""), first.get("ctx") or {}, _label(first.get("loc", ()))
    if kind == "value_error":
        message = str(first.get("msg", "")).removeprefix("Value error, ")
        if is_plain_sentence(message):
            return message
    if kind == "greater_than_equal":
        return f"{label} must be at least {ctx.get('ge')}."
    if kind == "less_than_equal":
        return f"{label} must be at most {ctx.get('le')}."
    if kind in ("string_too_long", "too_long"):
        return f"{label} is too long."
    if kind == "missing":
        return f"{label} is needed."
    if kind == "extra_forbidden":
        return f"{label} is not something that can be set."
    return f"{label} is not allowed."


class ModuleErrorRoute(APIRoute):
    """A route whose module errors become 401/404/409/422/503 answers fit to show."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def answer(request: Request) -> Response:
            try:
                return await handler(request)
            except ModuleError as error:
                return error_response(error)
            except RequestValidationError as invalid:
                return error_response(NotAllowedValue(sentence_for(invalid.errors())))

        async def run(request: Request) -> Response:
            response = await answer(request)
            # Every answer is JSON holding plain text; a browser must never treat it as a page.
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        return run
