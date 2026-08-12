from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SPAStaticFiles(StaticFiles):
    """Serve built assets while falling back to index.html for client routes."""

    def __init__(self, *, directory: str, api_prefix: str) -> None:
        super().__init__(directory=directory, html=True, check_dir=True)
        self._api_path = api_prefix.strip("/")

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not self._can_fallback(path, scope):
                raise
        else:
            if response.status_code != 404 or not self._can_fallback(path, scope):
                if response.status_code == 200 and path.strip("/") in {"", ".", "index.html"}:
                    response.headers["Cache-Control"] = "no-cache"
                return response

        index = await super().get_response("index.html", scope)
        index.headers["Cache-Control"] = "no-cache"
        return index

    def _can_fallback(self, path: str, scope: Scope) -> bool:
        if scope.get("method") not in {"GET", "HEAD"}:
            return False
        normalized = path.strip("/")
        scope_path = str(scope.get("path", "")).strip("/")
        root_path = str(scope.get("root_path", "")).strip("/")
        raw_path = scope.get("raw_path", b"")
        raw_text = (
            raw_path.decode("ascii", errors="ignore").partition("?")[0].strip("/")
            if isinstance(raw_path, bytes)
            else ""
        )
        candidates = {normalized, scope_path, raw_text, f"{root_path}/{scope_path}".strip("/")}
        if any(
            candidate == self._api_path
            or candidate.startswith(f"{self._api_path}/")
            for candidate in candidates
        ):
            return False
        return not PurePosixPath(normalized).suffix


def mount_frontend(app: FastAPI, *, directory: str, api_prefix: str) -> None:
    app.mount(
        "/",
        SPAStaticFiles(directory=directory, api_prefix=api_prefix),
        name="frontend",
    )
