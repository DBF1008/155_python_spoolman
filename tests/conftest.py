"""Fixtures and helpers for Spoolman unit tests."""

import os
import tempfile
from pathlib import Path

# A writable data dir must exist before importing spoolman.externaldb, since that module sets up a
# file-based HTTP cache at import time. Individual tests redirect file I/O via the `cache_dir` fixture.
os.environ.setdefault("SPOOLMAN_DIR_DATA", tempfile.mkdtemp(prefix="spoolman-test-"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from spoolman import externaldb  # noqa: E402

# Minimal valid payloads matching the required fields of ExternalFilament / ExternalMaterial.
FILAMENTS_JSON = (
    b'[{"id":"a","manufacturer":"M","name":"N1","material":"PLA","density":1.24,"weight":1000,"diameter":1.75},'
    b'{"id":"b","manufacturer":"M","name":"N2","material":"PETG","density":1.27,"weight":1000,"diameter":1.75}]'
)
MATERIALS_JSON = b'[{"material":"PLA","density":1.24}]'

# Distinct "previously cached" payloads, used to prove a failed sync does not overwrite the cache.
OLD_FILAMENTS_JSON = (
    b'[{"id":"old","manufacturer":"Old","name":"Old","material":"PLA","density":1.0,"weight":1,"diameter":1.75}]'
)
OLD_MATERIALS_JSON = b'[{"material":"OLD","density":9.99}]'


def install_fake_download(
    monkeypatch: pytest.MonkeyPatch,
    *,
    filaments: bytes = FILAMENTS_JSON,
    materials: bytes = MATERIALS_JSON,
    fail_on: str | None = None,
) -> None:
    """Patch externaldb._download_file to serve bytes per file, or raise for the file named in `fail_on`."""

    async def fake_download(url: str) -> bytes:
        name = "filaments" if url.endswith("filaments.json") else "materials"
        if fail_on == name:
            request = httpx.Request("GET", url)
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("Service Unavailable", request=request, response=response)
        return filaments if name == "filaments" else materials

    monkeypatch.setattr(externaldb, "_download_file", fake_download)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Spoolman's data dir at an isolated temp dir and return its cache subdir."""
    monkeypatch.setenv("SPOOLMAN_DIR_DATA", str(tmp_path))
    return tmp_path / "cache"


@pytest.fixture(autouse=True)
def reset_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the in-memory sync status before each test for isolation."""
    monkeypatch.setattr(externaldb, "_sync_status", externaldb.SyncStatus())
