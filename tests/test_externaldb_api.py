"""Tests for the external database API endpoints (status + on-demand refresh)."""

import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spoolman import externaldb, filecache
from spoolman.api.v1 import externaldb as externaldb_api

from .conftest import (
    FILAMENTS_JSON,
    MATERIALS_JSON,
    OLD_FILAMENTS_JSON,
    OLD_MATERIALS_JSON,
    install_fake_download,
)

_app = FastAPI()
_app.include_router(externaldb_api.router)
client = TestClient(_app)


def test_status_endpoint_initial() -> None:
    response = client.get("/external/status")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is None
    assert body["is_syncing"] is False
    assert body["is_stale"] is True


def test_refresh_success(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_download(monkeypatch, filaments=FILAMENTS_JSON, materials=MATERIALS_JSON)

    response = client.post("/external/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["filament_count"] == 2
    assert body["material_count"] == 1
    assert body["is_stale"] is False
    assert body["filaments_cached"] is True
    assert body["materials_cached"] is True

    # The read-only endpoint now serves the freshly synced data.
    filaments = client.get("/external/filament")
    assert filaments.status_code == 200
    assert len(filaments.json()) == 2


def test_failed_refresh_preserves_read_endpoints(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed a known-good cache, then make the refresh fail mid-way.
    filecache.update_file("filaments.json", OLD_FILAMENTS_JSON)
    filecache.update_file("materials.json", OLD_MATERIALS_JSON)

    install_fake_download(monkeypatch, fail_on="materials")

    response = client.post("/external/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"]

    # Fallback intact: the read-only endpoints still serve the OLD cached data.
    filaments = client.get("/external/filament")
    assert filaments.status_code == 200
    assert filaments.json()[0]["id"] == "old"

    materials = client.get("/external/material")
    assert materials.status_code == 200
    assert materials.json()[0]["material"] == "OLD"


def test_status_reflects_successful_refresh(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_download(monkeypatch, filaments=FILAMENTS_JSON, materials=MATERIALS_JSON)
    client.post("/external/refresh").raise_for_status()

    body = client.get("/external/status").json()
    assert body["success"] is True
    assert body["filament_count"] == 2
    assert body["last_success"] is not None


def test_status_is_stale_when_last_success_is_old(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "60")
    status = externaldb.get_sync_status()
    status.success = True
    status.last_success = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=10)

    body = client.get("/external/status").json()
    assert body["is_stale"] is True
    assert body["seconds_since_success"] > 60
