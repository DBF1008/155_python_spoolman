"""Integration tests for the External DB sync API endpoints."""

import concurrent.futures
import time

import httpx

from .conftest import URL


def test_external_status():
    """Test that GET /external/status returns a valid status structure."""
    result = httpx.get(f"{URL}/api/v1/external/status")
    result.raise_for_status()
    data = result.json()

    # All expected fields must be present
    expected_keys = {
        "last_sync",
        "last_sync_success",
        "last_sync_duration_seconds",
        "last_sync_filament_count",
        "last_sync_material_count",
        "external_db_url",
        "last_error",
        "is_syncing",
    }
    assert expected_keys.issubset(set(data.keys())), f"Missing keys: {expected_keys - set(data.keys())}"

    # external_db_url must be a string (may be empty if not configured)
    assert isinstance(data["external_db_url"], str)

    # is_syncing must be a boolean
    assert isinstance(data["is_syncing"], bool)

    # Before any sync, last_sync_success can be None, True, or False
    assert data["last_sync_success"] is None or isinstance(data["last_sync_success"], bool)


def test_external_refresh():
    """Test that POST /external/refresh triggers a sync and returns updated status."""
    # Trigger a manual refresh
    result = httpx.post(f"{URL}/api/v1/external/refresh", timeout=60)
    result.raise_for_status()
    data = result.json()

    # After a successful refresh, these fields should be populated
    assert data["last_sync"] is not None, "last_sync should be set after refresh"
    assert data["last_sync_success"] is True, "Refresh should succeed"
    assert data["last_sync_duration_seconds"] is not None
    assert data["last_sync_duration_seconds"] > 0
    assert isinstance(data["last_sync_filament_count"], int)
    assert isinstance(data["last_sync_material_count"], int)
    assert data["last_error"] is None
    assert data["is_syncing"] is False


def test_external_refresh_updates_status():
    """Test that a refresh updates the status endpoint with fresh data."""
    # Trigger refresh
    refresh_result = httpx.post(f"{URL}/api/v1/external/refresh", timeout=60)
    refresh_result.raise_for_status()
    refresh_data = refresh_result.json()

    # Check that the status endpoint reflects the refresh
    status_result = httpx.get(f"{URL}/api/v1/external/status")
    status_result.raise_for_status()
    status_data = status_result.json()

    assert status_data["last_sync"] is not None
    assert status_data["last_sync_success"] is True
    assert status_data["last_sync_filament_count"] == refresh_data["last_sync_filament_count"]
    assert status_data["last_sync_material_count"] == refresh_data["last_sync_material_count"]


def test_external_filaments_still_work():
    """Regression test: GET /external/filament still works after sync changes."""
    # Ensure we have data by triggering a refresh first
    httpx.post(f"{URL}/api/v1/external/refresh", timeout=60)

    result = httpx.get(f"{URL}/api/v1/external/filament")
    result.raise_for_status()

    data = result.json()
    assert isinstance(data, list), "External filaments should be a JSON array"

    if data:
        # Verify structure of first item
        item = data[0]
        assert "id" in item
        assert "manufacturer" in item
        assert "name" in item
        assert "material" in item
        assert "density" in item
        assert "weight" in item
        assert "diameter" in item


def test_external_materials_still_work():
    """Regression test: GET /external/material still works after sync changes."""
    # Ensure we have data by triggering a refresh first
    httpx.post(f"{URL}/api/v1/external/refresh", timeout=60)

    result = httpx.get(f"{URL}/api/v1/external/material")
    result.raise_for_status()

    data = result.json()
    assert isinstance(data, list), "External materials should be a JSON array"

    if data:
        # Verify structure of first item
        item = data[0]
        assert "material" in item
        assert "density" in item


def test_external_concurrent_refresh():
    """Test that concurrent refresh requests are properly serialized or rejected."""

    def do_refresh() -> httpx.Response:
        return httpx.post(f"{URL}/api/v1/external/refresh", timeout=60)

    # Fire two refresh requests concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(do_refresh) for _ in range(2)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # At least one should succeed (200), and at most one should get 409
    status_codes = [r.status_code for r in results]
    assert 200 in status_codes, f"At least one refresh should succeed, got: {status_codes}"

    # The second one should either succeed (if it waited) or get 409 (if rejected)
    for code in status_codes:
        assert code in {200, 409}, f"Unexpected status code: {code}"


def test_external_status_idempotent():
    """Test that GET /external/status is read-only and does not trigger a sync."""
    # Get status twice with a small delay
    result1 = httpx.get(f"{URL}/api/v1/external/status")
    result1.raise_for_status()
    data1 = result1.json()

    time.sleep(0.5)

    result2 = httpx.get(f"{URL}/api/v1/external/status")
    result2.raise_for_status()
    data2 = result2.json()

    # last_sync timestamp should not change between reads (no sync was triggered)
    assert data1["last_sync"] == data2["last_sync"]
    assert data1["last_sync_success"] == data2["last_sync_success"]
