"""Unit tests for spoolman.externaldb sync status and on-demand refresh."""

from pathlib import Path

import httpx
import pytest

from spoolman import externaldb, filecache

from .conftest import (
    FILAMENTS_JSON,
    MATERIALS_JSON,
    OLD_FILAMENTS_JSON,
    OLD_MATERIALS_JSON,
    install_fake_download,
)


async def test_refresh_now_success_writes_files_and_records_status(
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_download(monkeypatch, filaments=FILAMENTS_JSON, materials=MATERIALS_JSON)

    status = await externaldb.refresh_now()

    assert externaldb.get_filaments_file().exists()
    assert externaldb.get_materials_file().exists()
    assert status.success is True
    assert status.error is None
    assert status.filament_count == 2
    assert status.material_count == 1
    assert status.last_success is not None
    assert status.is_syncing is False


async def test_sync_failure_is_atomic_and_preserves_cache(
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Seed an existing known-good cache that differs from what a successful sync would write.
    filecache.update_file("filaments.json", OLD_FILAMENTS_JSON)
    filecache.update_file("materials.json", OLD_MATERIALS_JSON)

    # Filaments downloads fine, but materials fails -> nothing should be written.
    install_fake_download(monkeypatch, fail_on="materials")

    with pytest.raises(httpx.HTTPStatusError):
        await externaldb._sync()  # noqa: SLF001

    # All-or-nothing: filaments must NOT have been overwritten even though its download succeeded.
    assert externaldb.get_filaments_file().read_bytes() == OLD_FILAMENTS_JSON
    assert externaldb.get_materials_file().read_bytes() == OLD_MATERIALS_JSON

    status = externaldb.get_sync_status()
    assert status.success is False
    assert status.error is not None
    assert status.last_success is None
    assert status.is_syncing is False


async def test_refresh_now_swallows_failure(
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_download(monkeypatch, fail_on="filaments")

    status = await externaldb.refresh_now()  # must not raise

    assert status.success is False
    assert status.error is not None


def test_initial_status_is_unset() -> None:
    status = externaldb.get_sync_status()
    assert status.success is None
    assert status.last_success is None
    assert status.is_syncing is False
