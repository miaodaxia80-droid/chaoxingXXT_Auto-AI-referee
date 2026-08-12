from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def settings(root: Path, frontend_dir: Path | None) -> AppSettings:
    return AppSettings(
        environment="test",
        data_dir=root / "data",
        database_url=f"sqlite+pysqlite:///{root / 'app.db'}",
        frontend_dir=frontend_dir,
    )


def make_dist(root: Path) -> Path:
    dist = root / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>Chaoxing SPA</title>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.appLoaded = true", encoding="utf-8")
    return dist


def test_serves_built_frontend_and_spa_routes_without_shadowing_api(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path, make_dist(tmp_path)))

    with TestClient(app) as client:
        root = client.get("/")
        client_route = client.get("/tasks/active")
        asset = client.get("/assets/app.js")
        missing_asset = client.get("/assets/missing.js")
        health = client.get("/api/v1/health")
        missing_api = client.get("/api/v1/does-not-exist")

    assert root.status_code == 200
    assert "Chaoxing SPA" in root.text
    assert root.headers["cache-control"] == "no-cache"
    assert client_route.status_code == 200
    assert "Chaoxing SPA" in client_route.text
    assert asset.status_code == 200
    assert asset.text == "window.appLoaded = true"
    assert missing_asset.status_code == 404
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert missing_api.status_code == 404
    assert "Chaoxing SPA" not in missing_api.text


def test_api_only_mode_does_not_claim_unknown_routes(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path, None))

    with TestClient(app) as client:
        response = client.get("/tasks")

    assert response.status_code == 404


def test_configured_frontend_directory_must_exist(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="does not exist"):
        create_app(settings(tmp_path, tmp_path / "missing-dist"))
