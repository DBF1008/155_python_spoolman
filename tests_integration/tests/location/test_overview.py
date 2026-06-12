"""Integration tests for the GET /location/overview endpoint."""

# ruff: noqa: ARG002

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from ..conftest import URL


@dataclass
class Fixture:
    """Test fixture data for location overview tests."""

    spools: list[dict[str, Any]]
    filament: dict[str, Any]


@pytest.fixture(scope="module")
def location_spools(random_filament_mod: dict[str, Any]) -> Iterable[Fixture]:
    """Create spools across various locations for overview testing."""
    # Spool 0: in "Shelf A"
    r = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": random_filament_mod["id"],
            "remaining_weight": 800,
            "location": "Shelf A",
        },
    )
    r.raise_for_status()
    spool_1 = r.json()

    # Spool 1: another in "Shelf A"
    r = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": random_filament_mod["id"],
            "remaining_weight": 600,
            "location": "Shelf A",
        },
    )
    r.raise_for_status()
    spool_2 = r.json()

    # Spool 2: in "Printer 1"
    r = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": random_filament_mod["id"],
            "remaining_weight": 500,
            "location": "Printer 1",
        },
    )
    r.raise_for_status()
    spool_3 = r.json()

    # Spool 3: archived in "Shelf A"
    r = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": random_filament_mod["id"],
            "remaining_weight": 100,
            "location": "Shelf A",
            "archived": True,
        },
    )
    r.raise_for_status()
    spool_4 = r.json()

    # Spool 4: no location (NULL)
    r = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": random_filament_mod["id"],
            "remaining_weight": 900,
        },
    )
    r.raise_for_status()
    spool_5 = r.json()

    # Spool 5: explicit empty string location
    r = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": random_filament_mod["id"],
            "remaining_weight": 700,
            "location": "",
        },
    )
    r.raise_for_status()
    spool_6 = r.json()

    yield Fixture(
        spools=[spool_1, spool_2, spool_3, spool_4, spool_5, spool_6],
        filament=random_filament_mod,
    )

    # Cleanup spools
    for s in [spool_1, spool_2, spool_3, spool_4, spool_5, spool_6]:
        httpx.delete(f"{URL}/api/v1/spool/{s['id']}").raise_for_status()

    # Cleanup settings (reset to defaults)
    httpx.post(f"{URL}/api/v1/setting/locations", content="null").raise_for_status()
    httpx.post(f"{URL}/api/v1/setting/locations_spoolorders", content="null").raise_for_status()


def _get_overview(*, allow_archived: bool = False) -> list[dict[str, Any]]:
    """Call the overview endpoint."""
    params = {}
    if allow_archived:
        params["allow_archived"] = "true"
    r = httpx.get(f"{URL}/api/v1/location/overview", params=params)
    r.raise_for_status()
    return r.json()


def _set_locations(locations: list[str]) -> None:
    """Set the locations setting."""
    r = httpx.post(f"{URL}/api/v1/setting/locations", content=json.dumps(locations))
    r.raise_for_status()


def _set_spool_orders(orders: dict[str, list[int]]) -> None:
    """Set the locations_spoolorders setting."""
    r = httpx.post(f"{URL}/api/v1/setting/locations_spoolorders", content=json.dumps(orders))
    r.raise_for_status()


def _reset_settings() -> None:
    """Reset location-related settings to defaults."""
    httpx.post(f"{URL}/api/v1/setting/locations", content="null").raise_for_status()
    httpx.post(f"{URL}/api/v1/setting/locations_spoolorders", content="null").raise_for_status()


class TestBasicOverview:
    """Test basic overview functionality without settings."""

    def test_returns_all_actual_locations(self, location_spools: Fixture):
        """Without any settings configured, returns only locations that have spools."""
        _reset_settings()
        overview = _get_overview()
        names = [loc["name"] for loc in overview]
        assert "Shelf A" in names
        assert "Printer 1" in names
        assert "" in names  # empty location

    def test_spool_counts_exclude_archived(self, location_spools: Fixture):
        """Archived spools should not be counted by default."""
        _reset_settings()
        overview = _get_overview()
        shelf_a = next(loc for loc in overview if loc["name"] == "Shelf A")
        # 2 non-archived spools in Shelf A (spool_1, spool_2); spool_4 is archived
        assert shelf_a["spool_count"] == 2
        assert len(shelf_a["spool_ids"]) == 2

    def test_spool_counts_include_archived(self, location_spools: Fixture):
        """With allow_archived=true, archived spools should be counted."""
        _reset_settings()
        overview = _get_overview(allow_archived=True)
        shelf_a = next(loc for loc in overview if loc["name"] == "Shelf A")
        # 3 spools total in Shelf A including archived
        assert shelf_a["spool_count"] == 3
        assert len(shelf_a["spool_ids"]) == 3

    def test_empty_location_is_flagged(self, location_spools: Fixture):
        """The empty location should have is_empty_location=True."""
        _reset_settings()
        overview = _get_overview()
        empty_loc = next(loc for loc in overview if loc["name"] == "")
        assert empty_loc["is_empty_location"] is True
        assert empty_loc["spool_count"] == 2  # spool_5 (NULL) + spool_6 ("")

    def test_non_empty_locations_not_flagged(self, location_spools: Fixture):
        """Named locations should have is_empty_location=False."""
        _reset_settings()
        overview = _get_overview()
        for loc in overview:
            if loc["name"] != "":
                assert loc["is_empty_location"] is False

    def test_null_and_empty_string_locations_merged(self, location_spools: Fixture):
        """NULL location and '' location should be merged into one bucket."""
        _reset_settings()
        overview = _get_overview()
        empty_locs = [loc for loc in overview if loc["name"] == ""]
        assert len(empty_locs) == 1
        assert empty_locs[0]["spool_count"] == 2

    def test_default_excludes_archived_matches_explicit(self, location_spools: Fixture):
        """Default behavior should be same as explicit allow_archived=false."""
        _reset_settings()
        overview_default = _get_overview()
        overview_explicit = _get_overview(allow_archived=False)
        assert overview_default == overview_explicit


class TestConfiguredLocations:
    """Test merging with the 'locations' setting."""

    def test_configured_location_without_spools_appears(self, location_spools: Fixture):
        """A configured location with no spools should still appear with count 0."""
        try:
            _set_locations(["Shelf A", "Warehouse"])
            overview = _get_overview()
            names = [loc["name"] for loc in overview]
            assert "Warehouse" in names
            warehouse = next(loc for loc in overview if loc["name"] == "Warehouse")
            assert warehouse["spool_count"] == 0
            assert warehouse["spool_ids"] == []
        finally:
            _reset_settings()

    def test_configured_order_preserved(self, location_spools: Fixture):
        """Configured locations should appear first in the configured order."""
        try:
            _set_locations(["Printer 1", "Shelf A"])
            overview = _get_overview()
            names = [loc["name"] for loc in overview]
            # Configured locations first in order, then unconfigured ones
            assert names.index("Printer 1") < names.index("Shelf A")
        finally:
            _reset_settings()

    def test_unconfigured_locations_appended(self, location_spools: Fixture):
        """Locations with spools but not in config should appear after configured ones."""
        try:
            _set_locations(["Printer 1"])
            overview = _get_overview()
            names = [loc["name"] for loc in overview]
            # Printer 1 (configured) should come before Shelf A (unconfigured)
            assert names.index("Printer 1") < names.index("Shelf A")
        finally:
            _reset_settings()

    def test_empty_configured_locations(self, location_spools: Fixture):
        """When locations setting is empty array, only actual locations appear."""
        try:
            _set_locations([])
            overview = _get_overview()
            names = [loc["name"] for loc in overview]
            assert "Shelf A" in names
            assert "Printer 1" in names
        finally:
            _reset_settings()

    def test_duplicate_configured_locations_deduplicated(self, location_spools: Fixture):
        """Duplicate entries in the locations setting should be deduplicated."""
        try:
            _set_locations(["Shelf A", "Shelf A", "Warehouse"])
            overview = _get_overview()
            names = [loc["name"] for loc in overview]
            assert names.count("Shelf A") == 1
        finally:
            _reset_settings()


class TestSpoolOrdering:
    """Test spool ID ordering via the locations_spoolorders setting."""

    def test_custom_order_applied(self, location_spools: Fixture):
        """Spool IDs should follow the configured order for a location."""
        spool_1_id = location_spools.spools[0]["id"]
        spool_2_id = location_spools.spools[1]["id"]
        try:
            _reset_settings()
            # Reverse the natural order
            _set_spool_orders({"Shelf A": [spool_2_id, spool_1_id]})
            overview = _get_overview()
            shelf_a = next(loc for loc in overview if loc["name"] == "Shelf A")
            assert shelf_a["spool_ids"][0] == spool_2_id
            assert shelf_a["spool_ids"][1] == spool_1_id
        finally:
            _reset_settings()

    def test_new_spools_appended_after_ordered(self, location_spools: Fixture):
        """Spools not in the order list should appear after ordered ones."""
        spool_1_id = location_spools.spools[0]["id"]
        try:
            _reset_settings()
            # Only order one spool; the other should be appended
            _set_spool_orders({"Shelf A": [spool_1_id]})
            overview = _get_overview()
            shelf_a = next(loc for loc in overview if loc["name"] == "Shelf A")
            assert shelf_a["spool_ids"][0] == spool_1_id
            assert len(shelf_a["spool_ids"]) == 2
        finally:
            _reset_settings()

    def test_order_references_nonexistent_spools_filtered(self, location_spools: Fixture):
        """Order entries referencing spools not in this location should be ignored."""
        try:
            _reset_settings()
            _set_spool_orders({"Shelf A": [99999, location_spools.spools[0]["id"]]})
            overview = _get_overview()
            shelf_a = next(loc for loc in overview if loc["name"] == "Shelf A")
            assert 99999 not in shelf_a["spool_ids"]
            assert location_spools.spools[0]["id"] in shelf_a["spool_ids"]
        finally:
            _reset_settings()


class TestResponseStructure:
    """Test response structure and edge cases."""

    def test_response_structure(self, location_spools: Fixture):
        """Verify every item in the response has the correct schema."""
        _reset_settings()
        overview = _get_overview()
        for loc in overview:
            assert isinstance(loc["name"], str)
            assert isinstance(loc["spool_count"], int)
            assert isinstance(loc["spool_ids"], list)
            assert isinstance(loc["is_empty_location"], bool)
            assert loc["spool_count"] == len(loc["spool_ids"])
            assert all(isinstance(sid, int) for sid in loc["spool_ids"])

    def test_returns_list(self, location_spools: Fixture):
        """Response should always be a list."""
        _reset_settings()
        overview = _get_overview()
        assert isinstance(overview, list)


class TestRegressionGuard:
    """Verify existing endpoints are not affected by the new overview endpoint."""

    def test_existing_get_location_still_works(self, location_spools: Fixture):
        """GET /location should still return a plain list of strings."""
        r = httpx.get(f"{URL}/api/v1/location")
        r.raise_for_status()
        data = r.json()
        assert isinstance(data, list)
        assert all(isinstance(item, str) for item in data)
        assert "Shelf A" in data
        assert "Printer 1" in data

    def test_existing_get_spool_still_filters_archived(self, location_spools: Fixture):
        """GET /spool should still exclude archived spools by default."""
        r = httpx.get(f"{URL}/api/v1/spool")
        r.raise_for_status()
        spools = r.json()
        ids = [s["id"] for s in spools]
        # Archived spool (spool_4) should NOT be in default results
        assert location_spools.spools[3]["id"] not in ids
        # Non-archived spools should be present
        assert location_spools.spools[0]["id"] in ids

    def test_existing_rename_location_still_works(self, location_spools: Fixture):
        """PATCH /location/{name} should still rename across all spools."""
        try:
            # Create a dedicated spool for rename testing
            r = httpx.post(
                f"{URL}/api/v1/spool",
                json={
                    "filament_id": location_spools.filament["id"],
                    "remaining_weight": 100,
                    "location": "RenameTestLoc",
                },
            )
            r.raise_for_status()
            test_spool = r.json()

            # Rename the location
            r = httpx.patch(
                f"{URL}/api/v1/location/RenameTestLoc",
                json={"name": "RenamedLoc"},
            )
            r.raise_for_status()

            # Verify the spool's location was updated
            r = httpx.get(f"{URL}/api/v1/spool/{test_spool['id']}")
            r.raise_for_status()
            updated_spool = r.json()
            assert updated_spool["location"] == "RenamedLoc"

            # Cleanup
            httpx.delete(f"{URL}/api/v1/spool/{test_spool['id']}").raise_for_status()
        finally:
            # Rename back in case of failure
            httpx.patch(
                f"{URL}/api/v1/location/RenamedLoc",
                json={"name": "RenameTestLoc"},
            )
