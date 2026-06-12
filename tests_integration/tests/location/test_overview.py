"""Integration tests for the location overview endpoint and related regressions.

Covers the new ``GET /location-overview`` merge endpoint, plus regression checks that the
existing ``GET /location`` query, setting modification, and spool archiving still behave as
before.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from ..conftest import URL

P1 = "OV Printer 1"
P2 = "OV Printer 2"
STORAGE = "OV Storage"
EMPTY_CONFIGURED = "OV Empty Configured"
ARCHIVED_LOC = "OV Archived"
DEFAULT = ""
CONFIGURED = [P1, EMPTY_CONFIGURED, P2]


def _create_spool(filament_id: int, location: str | None = None, *, archived: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"filament_id": filament_id}
    if location is not None:
        body["location"] = location
    if archived:
        body["archived"] = True
    result = httpx.post(f"{URL}/api/v1/spool", json=body)
    result.raise_for_status()
    return result.json()


def _set_setting(key: str, value: object) -> dict[str, Any]:
    result = httpx.post(f"{URL}/api/v1/setting/{key}", json=json.dumps(value))
    result.raise_for_status()
    return result.json()


def _unset_setting(key: str) -> None:
    httpx.post(f"{URL}/api/v1/setting/{key}", json="").raise_for_status()


def _get_overview() -> list[dict[str, Any]]:
    result = httpx.get(f"{URL}/api/v1/location-overview")
    result.raise_for_status()
    return result.json()


@dataclass
class OverviewEnv:
    filament: dict[str, Any]
    p1a_id: int
    p1b_id: int
    p2_id: int
    storage_id: int
    unlocated_id: int
    archived_id: int
    spool_ids: list[int] = field(default_factory=list)


@pytest.fixture
def overview_env(random_filament: dict[str, Any]) -> Iterator[OverviewEnv]:
    """Create a known set of spools and location settings, cleaned up afterwards."""
    fid = random_filament["id"]
    p1a = _create_spool(fid, P1)
    p1b = _create_spool(fid, P1)
    p2 = _create_spool(fid, P2)
    storage = _create_spool(fid, STORAGE)
    unlocated = _create_spool(fid)
    archived = _create_spool(fid, ARCHIVED_LOC, archived=True)

    # Configure locations, and reverse the spool order in P1 so ordering is observable.
    _set_setting("locations", CONFIGURED)
    _set_setting("locations_spoolorders", {P1: [p1b["id"], p1a["id"]]})

    env = OverviewEnv(
        filament=random_filament,
        p1a_id=p1a["id"],
        p1b_id=p1b["id"],
        p2_id=p2["id"],
        storage_id=storage["id"],
        unlocated_id=unlocated["id"],
        archived_id=archived["id"],
        spool_ids=[p1a["id"], p1b["id"], p2["id"], storage["id"], unlocated["id"], archived["id"]],
    )
    yield env

    # Teardown: reset settings and delete spools (best effort).
    _unset_setting("locations")
    _unset_setting("locations_spoolorders")
    for sid in env.spool_ids:
        httpx.delete(f"{URL}/api/v1/spool/{sid}")


def test_location_overview_merges_configured_and_real(overview_env: OverviewEnv):
    """The overview merges configured locations with real spool locations correctly."""
    overview = _get_overview()
    by_name = {entry["name"]: entry for entry in overview}

    # Every entry is well-formed and the count matches the number of ordered ids.
    for entry in overview:
        assert isinstance(entry["name"], str)
        assert isinstance(entry["is_default"], bool)
        assert entry["spool_count"] == len(entry["spool_orders"])

    # Default empty location is first, flagged, and holds our unlocated spool.
    assert overview[0]["name"] == DEFAULT
    assert overview[0]["is_default"] is True
    assert overview_env.unlocated_id in overview[0]["spool_orders"]
    assert overview[0]["spool_count"] >= 1

    # Configured location with two spools, ordered per the locations_spoolorders setting.
    assert by_name[P1]["is_default"] is False
    assert by_name[P1]["spool_count"] == 2
    assert by_name[P1]["spool_orders"] == [overview_env.p1b_id, overview_env.p1a_id]

    # A configured location with no spools is still present (count 0).
    assert by_name[EMPTY_CONFIGURED]["spool_count"] == 0
    assert by_name[EMPTY_CONFIGURED]["spool_orders"] == []

    # Other configured + an unconfigured spool location.
    assert by_name[P2]["spool_orders"] == [overview_env.p2_id]
    assert by_name[STORAGE]["spool_orders"] == [overview_env.storage_id]

    # A location that only has an archived spool is excluded.
    assert ARCHIVED_LOC not in by_name

    # Relative order: default first, then configured (in setting order), then unconfigured.
    my_names = {DEFAULT, P1, EMPTY_CONFIGURED, P2, STORAGE}
    relative = [entry["name"] for entry in overview if entry["name"] in my_names]
    assert relative == [DEFAULT, P1, EMPTY_CONFIGURED, P2, STORAGE]


@pytest.mark.usefixtures("overview_env")
def test_location_endpoint_unchanged():
    """Regression: GET /location still returns the flat, spool-derived distinct list."""
    result = httpx.get(f"{URL}/api/v1/location")
    result.raise_for_status()
    locations = result.json()

    assert isinstance(locations, list)
    assert all(isinstance(loc, str) for loc in locations)
    # Spool-derived, including the archived spool's location (existing behavior: no archived filter).
    assert P1 in locations
    assert P2 in locations
    assert STORAGE in locations
    assert ARCHIVED_LOC in locations
    # Not configuration-derived: a configured location with no spools does not appear here.
    assert EMPTY_CONFIGURED not in locations
    # Spools without a location are not surfaced as an empty string.
    assert "" not in locations


def test_archived_spool_excluded_but_archive_persisted(overview_env: OverviewEnv):
    """Regression: archiving still persists, and archived spools are absent from the overview."""
    result = httpx.get(f"{URL}/api/v1/spool/{overview_env.archived_id}")
    result.raise_for_status()
    assert result.json()["archived"] is True

    overview_names = {entry["name"] for entry in _get_overview()}
    assert ARCHIVED_LOC not in overview_names


def test_archive_via_patch_updates_overview(random_filament: dict[str, Any]):
    """Regression: PATCH archived=true still works and removes the spool from the overview."""
    loc = "OV PatchArchive"
    spool = _create_spool(random_filament["id"], loc)
    sid = spool["id"]
    try:
        by_name = {entry["name"]: entry for entry in _get_overview()}
        assert loc in by_name
        assert sid in by_name[loc]["spool_orders"]

        result = httpx.patch(f"{URL}/api/v1/spool/{sid}", json={"archived": True})
        result.raise_for_status()
        assert result.json()["archived"] is True

        overview_names = {entry["name"] for entry in _get_overview()}
        assert loc not in overview_names
    finally:
        httpx.delete(f"{URL}/api/v1/spool/{sid}")


def test_location_settings_roundtrip():
    """Regression: the locations and locations_spoolorders settings still set and read back."""
    locations_value = ["RT Loc A", "RT Loc B"]
    orders_value = {"RT Loc A": [1, 2], "RT Loc B": [3]}
    try:
        assert _set_setting("locations", locations_value) == {
            "value": json.dumps(locations_value),
            "is_set": True,
            "type": "array",
        }
        get_locations = httpx.get(f"{URL}/api/v1/setting/locations")
        get_locations.raise_for_status()
        assert get_locations.json() == {
            "value": json.dumps(locations_value),
            "is_set": True,
            "type": "array",
        }

        assert _set_setting("locations_spoolorders", orders_value) == {
            "value": json.dumps(orders_value),
            "is_set": True,
            "type": "object",
        }
        get_orders = httpx.get(f"{URL}/api/v1/setting/locations_spoolorders")
        get_orders.raise_for_status()
        assert get_orders.json() == {
            "value": json.dumps(orders_value),
            "is_set": True,
            "type": "object",
        }
    finally:
        _unset_setting("locations")
        _unset_setting("locations_spoolorders")
