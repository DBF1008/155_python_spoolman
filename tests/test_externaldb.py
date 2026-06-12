"""Unit tests for externaldb sync logic with mocked HTTP."""

import asyncio
import json
from unittest.mock import patch

import pytest

from spoolman import externaldb


@pytest.fixture(autouse=True)
def reset_sync_status():
    """Reset the module-level sync status before each test."""
    externaldb._sync_status = externaldb.SyncStatus(external_db_url="https://example.com/")  # noqa: SLF001
    externaldb._sync_lock_instance = None  # noqa: SLF001
    yield
    externaldb._sync_status = externaldb.SyncStatus(external_db_url="https://example.com/")  # noqa: SLF001
    externaldb._sync_lock_instance = None  # noqa: SLF001


VALID_FILAMENTS = json.dumps([
    {
        "id": "test_pla_black_1000_175",
        "manufacturer": "TestMfg",
        "name": "Test PLA Black",
        "material": "PLA",
        "density": 1.24,
        "weight": 1000,
        "diameter": 1.75,
    },
]).encode()

VALID_MATERIALS = json.dumps([
    {
        "material": "PLA",
        "density": 1.24,
        "extruder_temp": 210,
        "bed_temp": 60,
    },
]).encode()


def _make_write_tracker(written_files: dict[str, bytes]):  # noqa: ANN202
    """Create a side_effect function that tracks file writes."""

    def _track(name: str, data: bytes) -> None:
        written_files[name] = data

    return _track


@pytest.mark.asyncio
async def test_sync_success():
    """Test that a successful sync updates the status correctly."""
    written_files: dict[str, bytes] = {}

    async def mock_download(url: str) -> bytes:
        if "filaments.json" in url:
            return VALID_FILAMENTS
        if "materials.json" in url:
            return VALID_MATERIALS
        raise ValueError(f"Unexpected URL: {url}")

    with (
        patch.object(externaldb, "_download_file", side_effect=mock_download),
        patch.object(externaldb.filecache, "update_file", side_effect=_make_write_tracker(written_files)),
    ):
        await externaldb._sync()  # noqa: SLF001

    status = externaldb.get_sync_status()
    assert status.last_sync is not None
    assert status.last_sync_success is True
    assert status.last_sync_filament_count == 1
    assert status.last_sync_material_count == 1
    assert status.last_sync_duration_seconds is not None
    assert status.last_sync_duration_seconds > 0
    assert status.last_error is None
    assert status.is_syncing is False

    # Verify files were written to cache
    assert "filaments.json" in written_files
    assert "materials.json" in written_files


@pytest.mark.asyncio
async def test_sync_failure_preserves_cache():
    """Test that a failed download does NOT overwrite the local cache files."""

    async def mock_download(_url: str) -> bytes:
        raise ConnectionError("Network unreachable")

    written_files: dict[str, bytes] = {}

    with (
        patch.object(externaldb, "_download_file", side_effect=mock_download),
        patch.object(externaldb.filecache, "update_file", side_effect=_make_write_tracker(written_files)),
    ):
        await externaldb._sync()  # noqa: SLF001

    status = externaldb.get_sync_status()
    assert status.last_sync is not None
    assert status.last_sync_success is False
    assert status.last_error is not None
    assert "Network unreachable" in status.last_error
    assert status.is_syncing is False

    # Cache files should NOT have been written
    assert len(written_files) == 0


@pytest.mark.asyncio
async def test_sync_parse_failure_preserves_cache():
    """Test that a parse failure (invalid JSON) does NOT overwrite the local cache files."""

    async def mock_download(_url: str) -> bytes:
        return b"this is not valid JSON"

    written_files: dict[str, bytes] = {}

    with (
        patch.object(externaldb, "_download_file", side_effect=mock_download),
        patch.object(externaldb.filecache, "update_file", side_effect=_make_write_tracker(written_files)),
    ):
        await externaldb._sync()  # noqa: SLF001

    status = externaldb.get_sync_status()
    assert status.last_sync is not None
    assert status.last_sync_success is False
    assert status.last_error is not None
    assert status.is_syncing is False

    # Cache files should NOT have been written (parse fails before write)
    assert len(written_files) == 0


@pytest.mark.asyncio
async def test_sync_partial_failure_preserves_cache():
    """Test that if filaments download succeeds but materials fails, cache is NOT written."""

    async def mock_download(url: str) -> bytes:
        if "filaments.json" in url:
            return VALID_FILAMENTS
        raise ConnectionError("Materials download failed")

    written_files: dict[str, bytes] = {}

    with (
        patch.object(externaldb, "_download_file", side_effect=mock_download),
        patch.object(externaldb.filecache, "update_file", side_effect=_make_write_tracker(written_files)),
    ):
        await externaldb._sync()  # noqa: SLF001

    status = externaldb.get_sync_status()
    assert status.last_sync_success is False
    assert status.last_error is not None
    assert "Materials download failed" in status.last_error

    # Neither file should be written because materials failed before writes
    assert len(written_files) == 0


@pytest.mark.asyncio
async def test_refresh_returns_status():
    """Test that refresh() returns the sync status after completion."""

    async def mock_download(url: str) -> bytes:
        if "filaments.json" in url:
            return VALID_FILAMENTS
        return VALID_MATERIALS

    with (
        patch.object(externaldb, "_download_file", side_effect=mock_download),
        patch.object(externaldb.filecache, "update_file"),
        patch.object(externaldb, "get_external_db_url", return_value="https://example.com/"),
    ):
        status = await externaldb.refresh()

    assert status.last_sync_success is True
    assert status.last_sync_filament_count == 1
    assert status.last_sync_material_count == 1


@pytest.mark.asyncio
async def test_refresh_concurrent_rejected():
    """Test that a concurrent refresh call is rejected with RuntimeError."""
    # Manually acquire the lock to simulate an in-progress sync
    lock = externaldb._get_sync_lock()  # noqa: SLF001
    await lock.acquire()

    try:
        with pytest.raises(RuntimeError, match="already in progress"):
            await externaldb.refresh()
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_refresh_timeout():
    """Test that refresh respects the timeout parameter."""

    async def slow_download(_url: str) -> bytes:
        await asyncio.sleep(10)
        return VALID_FILAMENTS

    with (
        patch.object(externaldb, "_download_file", side_effect=slow_download),
        patch.object(externaldb.filecache, "update_file"),
        patch.object(externaldb, "get_external_db_url", return_value="https://example.com/"),
        pytest.raises(asyncio.TimeoutError),
    ):
        await externaldb.refresh(timeout=0.1)


@pytest.mark.asyncio
async def test_get_sync_status_returns_copy():
    """Test that get_sync_status() returns a copy, not the original object."""
    status1 = externaldb.get_sync_status()
    status2 = externaldb.get_sync_status()

    # They should be equal but not the same object
    assert status1.model_dump() == status2.model_dump()
    assert status1 is not status2


@pytest.mark.asyncio
async def test_status_is_syncing_during_sync():
    """Test that is_syncing is True while a sync is in progress."""
    sync_started = asyncio.Event()

    async def mock_download(_url: str) -> bytes:
        sync_started.set()
        await asyncio.sleep(0.2)
        return VALID_FILAMENTS

    with (
        patch.object(externaldb, "_download_file", side_effect=mock_download),
        patch.object(externaldb.filecache, "update_file"),
        patch.object(externaldb, "get_external_db_url", return_value="https://example.com/"),
    ):
        task = asyncio.create_task(externaldb._sync())  # noqa: SLF001
        await sync_started.wait()

        # While sync is running, is_syncing should be True
        status = externaldb.get_sync_status()
        assert status.is_syncing is True

        await task

    # After sync completes, is_syncing should be False
    status = externaldb.get_sync_status()
    assert status.is_syncing is False
