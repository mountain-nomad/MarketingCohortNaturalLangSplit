"""The built frontend is served with SPA fallback; the API namespace never falls back."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cohortsplit.app import create_app
from cohortsplit.config import ConfigError, load_settings


@pytest.fixture
def frontend_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id='root'>spa-index</div>")
    (dist / "assets" / "app.js").write_text("console.log('spa-asset')")
    (tmp_path / "secret.txt").write_text("outside-dist-secret")
    return dist


@pytest.fixture
def spa_client(unreachable_appdb_env: pytest.MonkeyPatch, frontend_dist: Path) -> TestClient:
    unreachable_appdb_env.setenv("COHORTSPLIT_FRONTEND_DIST", str(frontend_dist))
    return TestClient(create_app(load_settings()))


def test_root_serves_index(spa_client: TestClient) -> None:
    response = spa_client.get("/")

    assert response.status_code == 200
    assert "spa-index" in response.text


def test_client_route_falls_back_to_index(spa_client: TestClient) -> None:
    response = spa_client.get("/login")

    assert response.status_code == 200
    assert "spa-index" in response.text


def test_static_asset_is_served(spa_client: TestClient) -> None:
    response = spa_client.get("/assets/app.js")

    assert response.status_code == 200
    assert "spa-asset" in response.text


def test_unknown_api_path_is_json_404_not_index(spa_client: TestClient) -> None:
    response = spa_client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "spa-index" not in response.text


def test_path_traversal_does_not_escape_dist(spa_client: TestClient) -> None:
    for path in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        response = spa_client.get(path)
        assert "outside-dist-secret" not in response.text


def test_no_frontend_routes_without_dist(unreachable_appdb_env: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(load_settings()))

    assert client.get("/").status_code == 404


def test_frontend_dist_without_index_is_a_config_error(
    unreachable_appdb_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unreachable_appdb_env.setenv("COHORTSPLIT_FRONTEND_DIST", str(tmp_path / "missing"))

    with pytest.raises(ConfigError, match="COHORTSPLIT_FRONTEND_DIST"):
        load_settings()
