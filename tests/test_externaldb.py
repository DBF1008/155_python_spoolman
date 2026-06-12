"""Regression tests for externaldb.schedule_tasks scheduling logic.

Covers:
- Default sync interval (env var unset) -> uses DEFAULT_SYNC_INTERVAL
- Custom sync interval -> respects the configured value
- Sync interval = 0 -> startup sync only, no cyclic job
- Empty URL -> skips everything (no startup, no cyclic)
- Cache persistence and _sync behavior are not broken
"""

import datetime
from unittest.mock import MagicMock, call, patch

import pytest

from spoolman import externaldb
from spoolman.externaldb import DEFAULT_SYNC_INTERVAL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler() -> MagicMock:
    """Create a mock scheduler that records once/cyclic calls."""
    scheduler = MagicMock()
    scheduler.once = MagicMock()
    scheduler.cyclic = MagicMock()
    return scheduler


# ---------------------------------------------------------------------------
# schedule_tasks – URL / interval interaction
# ---------------------------------------------------------------------------

class TestScheduleTasks:
    """Tests for externaldb.schedule_tasks."""

    def test_empty_url_skips_all_sync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When EXTERNAL_DB_URL is empty/whitespace, nothing is scheduled."""
        monkeypatch.setenv("EXTERNAL_DB_URL", "")
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "3600")

        scheduler = _make_scheduler()
        externaldb.schedule_tasks(scheduler)

        scheduler.once.assert_not_called()
        scheduler.cyclic.assert_not_called()

    def test_whitespace_only_url_skips_all_sync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When EXTERNAL_DB_URL is whitespace-only, nothing is scheduled."""
        monkeypatch.setenv("EXTERNAL_DB_URL", "   ")
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "3600")

        scheduler = _make_scheduler()
        externaldb.schedule_tasks(scheduler)

        scheduler.once.assert_not_called()
        scheduler.cyclic.assert_not_called()

    def test_default_interval_schedules_cyclic_with_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When EXTERNAL_DB_SYNC_INTERVAL is not set, cyclic uses DEFAULT_SYNC_INTERVAL."""
        monkeypatch.setenv("EXTERNAL_DB_URL", "https://example.com/db/")
        monkeypatch.delenv("EXTERNAL_DB_SYNC_INTERVAL", raising=False)

        scheduler = _make_scheduler()
        externaldb.schedule_tasks(scheduler)

        # Startup: once(0s)
        scheduler.once.assert_called_once_with(
            datetime.timedelta(seconds=0),
            externaldb._sync,
        )
        # Periodic: cyclic(DEFAULT_SYNC_INTERVAL)
        scheduler.cyclic.assert_called_once_with(
            datetime.timedelta(seconds=DEFAULT_SYNC_INTERVAL),
            externaldb._sync,
        )

    def test_custom_interval_schedules_cyclic_with_custom_value(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A custom EXTERNAL_DB_SYNC_INTERVAL is respected by the cyclic job."""
        custom_interval = 7200
        monkeypatch.setenv("EXTERNAL_DB_URL", "https://example.com/db/")
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", str(custom_interval))

        scheduler = _make_scheduler()
        externaldb.schedule_tasks(scheduler)

        # Startup: once(0s) — unchanged
        scheduler.once.assert_called_once_with(
            datetime.timedelta(seconds=0),
            externaldb._sync,
        )
        # Periodic: cyclic(custom_interval), NOT DEFAULT_SYNC_INTERVAL
        scheduler.cyclic.assert_called_once_with(
            datetime.timedelta(seconds=custom_interval),
            externaldb._sync,
        )
        # Regression guard: must NOT equal the default
        assert custom_interval != DEFAULT_SYNC_INTERVAL, (
            "Test setup error: custom interval must differ from default"
        )

    def test_interval_zero_disables_periodic_sync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Setting EXTERNAL_DB_SYNC_INTERVAL=0 runs startup sync only, no cyclic job."""
        monkeypatch.setenv("EXTERNAL_DB_URL", "https://example.com/db/")
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "0")

        scheduler = _make_scheduler()
        externaldb.schedule_tasks(scheduler)

        # Startup: once(0s) still fires
        scheduler.once.assert_called_once_with(
            datetime.timedelta(seconds=0),
            externaldb._sync,
        )
        # No periodic job scheduled
        scheduler.cyclic.assert_not_called()

    def test_small_custom_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A very small positive interval (e.g. 60s) is respected."""
        monkeypatch.setenv("EXTERNAL_DB_URL", "https://example.com/db/")
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "60")

        scheduler = _make_scheduler()
        externaldb.schedule_tasks(scheduler)

        scheduler.cyclic.assert_called_once_with(
            datetime.timedelta(seconds=60),
            externaldb._sync,
        )

    def test_large_custom_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A large interval (e.g. 86400s = 1 day) is respected."""
        monkeypatch.setenv("EXTERNAL_DB_URL", "https://example.com/db/")
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "86400")

        scheduler = _make_scheduler()
        externaldb.schedule_tasks(scheduler)

        scheduler.cyclic.assert_called_once_with(
            datetime.timedelta(seconds=86400),
            externaldb._sync,
        )


# ---------------------------------------------------------------------------
# get_external_db_sync_interval – config reading
# ---------------------------------------------------------------------------

class TestGetSyncInterval:
    """Tests for get_external_db_sync_interval."""

    def test_returns_default_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXTERNAL_DB_SYNC_INTERVAL", raising=False)
        assert externaldb.get_external_db_sync_interval() == DEFAULT_SYNC_INTERVAL

    def test_returns_custom_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "9999")
        assert externaldb.get_external_db_sync_interval() == 9999

    def test_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "0")
        assert externaldb.get_external_db_sync_interval() == 0


# ---------------------------------------------------------------------------
# get_external_db_url – config reading
# ---------------------------------------------------------------------------

class TestGetExternalDbUrl:
    """Tests for get_external_db_url."""

    def test_returns_default_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXTERNAL_DB_URL", raising=False)
        assert externaldb.get_external_db_url() == externaldb.DEFAULT_EXTERNAL_DB_URL

    def test_returns_custom_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTERNAL_DB_URL", "https://custom.example.com/db/")
        assert externaldb.get_external_db_url() == "https://custom.example.com/db/"

    def test_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTERNAL_DB_URL", "")
        assert externaldb.get_external_db_url() == ""


# ---------------------------------------------------------------------------
# _sync – cache write-through semantics (unit-level, no real HTTP)
# ---------------------------------------------------------------------------

class TestSyncCacheWriteThrough:
    """Verify _sync writes parsed data to the local file cache."""

    @pytest.mark.asyncio
    async def test_sync_writes_to_local_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_sync downloads, parses, and writes both filaments and materials to cache."""
        monkeypatch.setenv("EXTERNAL_DB_URL", "https://example.com/db/")

        fake_filaments_json = b'[{"id":"f1","manufacturer":"M","name":"N","material":"PLA","density":1.24,"weight":1000,"diameter":1.75,"translucent":false,"glow":false}]'
        fake_materials_json = b'[{"material":"PLA","density":1.24}]'

        async def fake_download(url: str) -> bytes:
            if "filaments.json" in url:
                return fake_filaments_json
            if "materials.json" in url:
                return fake_materials_json
            raise ValueError(f"Unexpected URL: {url}")

        written_files: dict[str, bytes] = {}

        def fake_update_file(filename: str, data: bytes) -> None:
            written_files[filename] = data

        monkeypatch.setattr(externaldb, "_download_file", fake_download)
        monkeypatch.setattr(externaldb, "_write_to_local_cache", fake_update_file)

        await externaldb._sync()

        assert "filaments.json" in written_files
        assert "materials.json" in written_files
        # Verify the data was parsed and re-serialized (not raw bytes)
        assert b'"id"' in written_files["filaments.json"]
        assert b'"material"' in written_files["materials.json"]
