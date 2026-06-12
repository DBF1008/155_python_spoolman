"""Regression tests for external DB sync scheduling (``spoolman.externaldb.schedule_tasks``).

These guard the four configuration scenarios that previously misbehaved or were
easy to confuse:

* startup first-sync always runs once (independent of the periodic interval),
* the default interval is used when none is configured,
* a custom ``EXTERNAL_DB_SYNC_INTERVAL`` is actually honored (the original bug),
* a non-positive interval disables periodic sync without affecting startup sync,

while preserving the "empty URL -> skip scheduling entirely" semantic.
"""

import datetime
from unittest.mock import MagicMock

import pytest

from spoolman import externaldb


@pytest.fixture
def scheduler() -> MagicMock:
    """Return a stand-in scheduler that records ``once``/``cyclic`` registrations."""
    return MagicMock()


@pytest.fixture(autouse=True)
def _set_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test at a non-empty external DB URL by default.

    Individual tests override this when they exercise the empty-URL path.
    """
    monkeypatch.setenv("EXTERNAL_DB_URL", externaldb.DEFAULT_EXTERNAL_DB_URL)


def _cyclic_interval(scheduler: MagicMock) -> datetime.timedelta:
    """Return the timedelta passed to ``scheduler.cyclic``."""
    timing, _handle = scheduler.cyclic.call_args.args
    return timing


def _assert_startup_sync_scheduled(scheduler: MagicMock) -> None:
    """Assert the startup first-sync is scheduled exactly once at t=0."""
    scheduler.once.assert_called_once()
    timing = scheduler.once.call_args.args[0]
    assert timing == datetime.timedelta(seconds=0)


def test_default_interval_schedules_startup_and_default_cyclic(
    scheduler: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no interval configured, startup sync runs and periodic uses the default."""
    monkeypatch.delenv("EXTERNAL_DB_SYNC_INTERVAL", raising=False)

    externaldb.schedule_tasks(scheduler)

    _assert_startup_sync_scheduled(scheduler)
    scheduler.cyclic.assert_called_once()
    assert _cyclic_interval(scheduler) == datetime.timedelta(seconds=externaldb.DEFAULT_SYNC_INTERVAL)


def test_custom_interval_is_respected(
    scheduler: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A custom interval must drive the periodic schedule (the original bug)."""
    monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "300")

    externaldb.schedule_tasks(scheduler)

    _assert_startup_sync_scheduled(scheduler)
    scheduler.cyclic.assert_called_once()
    assert _cyclic_interval(scheduler) == datetime.timedelta(seconds=300)
    # Regression guard: it must not silently fall back to the default cadence.
    assert _cyclic_interval(scheduler) != datetime.timedelta(seconds=externaldb.DEFAULT_SYNC_INTERVAL)


def test_zero_interval_disables_periodic_but_keeps_startup(
    scheduler: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interval 0 disables periodic sync, but the startup first-sync still runs.

    This is the "0 must not be confused with startup behavior" case: disabling the
    cycle is independent from the one-shot sync performed on startup.
    """
    monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "0")

    externaldb.schedule_tasks(scheduler)

    _assert_startup_sync_scheduled(scheduler)
    scheduler.cyclic.assert_not_called()


def test_negative_interval_disables_periodic_but_keeps_startup(
    scheduler: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative interval is treated as "disabled", same as 0."""
    monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "-1")

    externaldb.schedule_tasks(scheduler)

    _assert_startup_sync_scheduled(scheduler)
    scheduler.cyclic.assert_not_called()


@pytest.mark.parametrize("url", ["", "   ", "\t\n"])
def test_empty_url_skips_all_scheduling(
    scheduler: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    """An empty/blank URL skips scheduling entirely, regardless of the interval."""
    monkeypatch.setenv("EXTERNAL_DB_URL", url)
    monkeypatch.setenv("EXTERNAL_DB_SYNC_INTERVAL", "300")

    externaldb.schedule_tasks(scheduler)

    scheduler.once.assert_not_called()
    scheduler.cyclic.assert_not_called()
