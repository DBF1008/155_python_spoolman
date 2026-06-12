"""Integration tests for parity between the two extra-field entry points.

Extra fields can be modified either through the dedicated ``/field`` endpoints or by writing the
``extra_fields_*`` settings directly through the generic ``/setting`` endpoint. These tests verify that
the second path now behaves identically to the first: changes are reflected immediately (no restart),
orphaned per-entity values are cleared on removal, and the same validation/invariants are enforced.

The ``/setting`` body is a raw string, so (matching the existing setting tests) the value is posted as a
JSON-encoded string, e.g. ``json=json.dumps(fields_list)``; an empty body (``json=""``) resets the setting.
"""

import json
from typing import Any

import httpx

from ..conftest import URL, assert_httpx_code, assert_httpx_success, assert_lists_compatible


def test_setting_delete_all_reflected_in_field_get_without_restart():
    """Removing all fields via the setting API must be visible to the field API immediately.

    This is the core regression: the setting path previously never invalidated the in-process field
    cache, so a removal only took effect after a server restart.
    """
    # Create a field through the dedicated field API (this populates the in-process cache).
    result = httpx.post(
        f"{URL}/api/v1/field/spool/cachefield",
        json={"name": "Cache field", "field_type": "text", "default_value": json.dumps("Hello World")},
    )
    assert_httpx_success(result)

    # It is present (and now definitely cached).
    result = httpx.get(f"{URL}/api/v1/field/spool")
    assert_httpx_success(result)
    assert any(field["key"] == "cachefield" for field in result.json())

    # Remove all spool fields through the generic setting API.
    result = httpx.post(f"{URL}/api/v1/setting/extra_fields_spool", json=json.dumps([]))
    assert_httpx_success(result)

    # The field API must reflect the removal immediately, without a restart.
    result = httpx.get(f"{URL}/api/v1/field/spool")
    assert_httpx_success(result)
    assert result.json() == []


def test_setting_edit_reflected_in_field_get():
    """Editing a field through the setting API is reflected immediately by the field API."""
    result = httpx.post(
        f"{URL}/api/v1/field/spool/editfield",
        json={"name": "Original name", "field_type": "text"},
    )
    assert_httpx_success(result)

    # Edit the field's display name via a bulk write through the setting API.
    edited = {"key": "editfield", "name": "Renamed", "field_type": "text"}
    result = httpx.post(f"{URL}/api/v1/setting/extra_fields_spool", json=json.dumps([edited]))
    assert_httpx_success(result)

    result = httpx.get(f"{URL}/api/v1/field/spool")
    assert_httpx_success(result)
    fields = result.json()
    assert len(fields) == 1
    assert fields[0]["key"] == "editfield"
    assert fields[0]["name"] == "Renamed"

    # Clean up
    httpx.post(f"{URL}/api/v1/setting/extra_fields_spool", json="").raise_for_status()


def test_setting_delete_clears_orphan_entity_values(random_filament: dict[str, Any]):
    """Removing a field via the setting API clears its stored values from entities (parity with /field)."""
    # Define a spool field.
    result = httpx.post(
        f"{URL}/api/v1/field/spool/orphanfield",
        json={"name": "Orphan field", "field_type": "text"},
    )
    assert_httpx_success(result)

    # Create a spool carrying a value for that field.
    result = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": random_filament["id"],
            "used_weight": 0,
            "extra": {"orphanfield": json.dumps("some value")},
        },
    )
    assert_httpx_success(result)
    spool_id = result.json()["id"]

    # Sanity: the value is present on the spool.
    result = httpx.get(f"{URL}/api/v1/spool/{spool_id}")
    assert_httpx_success(result)
    assert result.json()["extra"].get("orphanfield") == json.dumps("some value")

    # Remove all spool fields via the setting API (reset to default).
    result = httpx.post(f"{URL}/api/v1/setting/extra_fields_spool", json="")
    assert_httpx_success(result)

    # The orphaned value must be gone from the spool.
    result = httpx.get(f"{URL}/api/v1/spool/{spool_id}")
    assert_httpx_success(result)
    assert "orphanfield" not in result.json()["extra"]

    # Clean up the spool (the field was already removed by the reset above).
    httpx.delete(f"{URL}/api/v1/spool/{spool_id}").raise_for_status()


def test_setting_rejects_invalid_field_definition():
    """A structurally invalid field written via the setting API is rejected, just like via /field."""
    # A choice field without choices is invalid.
    invalid = {"key": "badchoice", "name": "Bad choice", "field_type": "choice"}
    result = httpx.post(f"{URL}/api/v1/setting/extra_fields_spool", json=json.dumps([invalid]))
    assert_httpx_code(result, 400)

    # Nothing should have been persisted.
    result = httpx.get(f"{URL}/api/v1/field/spool")
    assert_httpx_success(result)
    assert all(field["key"] != "badchoice" for field in result.json())


def test_setting_rejects_incompatible_type_change():
    """Changing a field's type via the setting API is rejected (would break existing data)."""
    result = httpx.post(
        f"{URL}/api/v1/field/spool/typedfield",
        json={"name": "Typed field", "field_type": "text"},
    )
    assert_httpx_success(result)

    # Attempt to change the type from text to integer through the setting API.
    changed = {"key": "typedfield", "name": "Typed field", "field_type": "integer"}
    result = httpx.post(f"{URL}/api/v1/setting/extra_fields_spool", json=json.dumps([changed]))
    assert_httpx_code(result, 400)

    # The field must be unchanged (still text).
    result = httpx.get(f"{URL}/api/v1/field/spool")
    assert_httpx_success(result)
    fields = {field["key"]: field for field in result.json()}
    assert fields["typedfield"]["field_type"] == "text"

    # Clean up
    httpx.post(f"{URL}/api/v1/setting/extra_fields_spool", json="").raise_for_status()


def test_setting_write_visible_via_field_api():
    """A field written through the setting API is readable through the dedicated field API."""
    field = {
        "key": "sharedfield",
        "name": "Shared field",
        "field_type": "text",
        "default_value": json.dumps("x"),
    }
    result = httpx.post(f"{URL}/api/v1/setting/extra_fields_filament", json=json.dumps([field]))
    assert_httpx_success(result)
    assert result.json()["is_set"] is True

    result = httpx.get(f"{URL}/api/v1/field/filament")
    assert_httpx_success(result)
    assert_lists_compatible(
        result.json(),
        [{"key": "sharedfield", "name": "Shared field", "field_type": "text", "entity_type": "filament"}],
        sort_key="key",
    )

    # Clean up
    httpx.post(f"{URL}/api/v1/setting/extra_fields_filament", json="").raise_for_status()
