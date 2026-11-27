"""Turn module errors into the API's error answer: ``detail.code`` and ``detail.message``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from assistant.errors import ModuleError


def error_response(error: ModuleError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"detail": {"code": error.code, "message": error.message}},
    )


class ModuleErrorRoute(APIRoute):
    """A route whose module errors become 401/404/409/422/503 answers fit to show."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            try:
                return await handler(request)
            except ModuleError as error:
                return error_response(error)

        return run
