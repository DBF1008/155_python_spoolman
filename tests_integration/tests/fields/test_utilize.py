"""Integration tests for the custom extra fields system."""

import json
from typing import Any

import httpx

from ..conftest import URL, assert_httpx_code, assert_httpx_success


def test_add_vendor_with_extra_field():
    """Test adding a vendor with a custom field."""
    result = httpx.post(
        f"{URL}/api/v1/field/vendor/mytextfield",
        json={
            "name": "My text field",
            "field_type": "text",
            "default_value": json.dumps("Hello World"),
        },
    )
    assert_httpx_success(result)

    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={
            "name": "My Vendor",
            "extra": {
                "mytextfield": '"My Value"',
            },
        },
    )
    assert_httpx_success(result)

    # Verify
    result = httpx.get(f"{URL}/api/v1/vendor/{result.json()['id']}")
    assert_httpx_success(result)
    vendor = result.json()
    assert vendor["name"] == "My Vendor"
    assert vendor["extra"] == {"mytextfield": '"My Value"'}

    # Clean up
    result = httpx.delete(f"{URL}/api/v1/field/vendor/mytextfield")
    assert_httpx_success(result)

    result = httpx.delete(f"{URL}/api/v1/vendor/{vendor['id']}")
    assert_httpx_success(result)


def test_add_vendor_with_invalid_extra_field():
    """Test adding a vendor with an invalid custom field."""
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={
            "name": "My Vendor",
            "extra": {
                "somefield": 42,
            },
        },
    )
    assert_httpx_code(result, 422)


def test_add_vendor_with_extra_field_then_delete():
    """Test adding a vendor with an extra field, then delete the field.

    Vendor GET response should then not contain the extra field.
    """
    result = httpx.post(
        f"{URL}/api/v1/field/vendor/mytextfield",
        json={
            "name": "My text field",
            "field_type": "text",
            "default_value": json.dumps("Hello World"),
        },
    )
    assert_httpx_success(result)

    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={
            "name": "My Vendor",
            "extra": {
                "mytextfield": '"My Value"',
            },
        },
    )
    assert_httpx_success(result)

    # Verify
    result = httpx.get(f"{URL}/api/v1/vendor/{result.json()['id']}")
    assert_httpx_success(result)
    vendor = result.json()
    assert vendor["name"] == "My Vendor"
    assert vendor["extra"] == {"mytextfield": '"My Value"'}

    # Remove field
    result = httpx.delete(f"{URL}/api/v1/field/vendor/mytextfield")
    assert_httpx_success(result)

    # Verify
    result = httpx.get(f"{URL}/api/v1/vendor/{vendor['id']}")
    assert_httpx_success(result)
    vendor = result.json()
    assert vendor["name"] == "My Vendor"
    assert "extra" not in vendor or vendor["extra"] == {}

    result = httpx.delete(f"{URL}/api/v1/vendor/{vendor['id']}")
    assert_httpx_success(result)


def test_update_existing_vendor_with_new_extra_field():
    """Test updating an existing vendor with a new extra field."""
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={
            "name": "My Vendor",
        },
    )
    assert_httpx_success(result)
    vendor_id = result.json()["id"]

    result = httpx.post(
        f"{URL}/api/v1/field/vendor/mytextfield",
        json={
            "name": "My text field",
            "field_type": "text",
            "default_value": json.dumps("Hello World"),
        },
    )
    assert_httpx_success(result)

    result = httpx.patch(
        f"{URL}/api/v1/vendor/{vendor_id}",
        json={
            "extra": {
                "mytextfield": '"My Value"',
            },
        },
    )
    assert_httpx_success(result)

    # Verify
    result = httpx.get(f"{URL}/api/v1/vendor/{vendor_id}")
    assert_httpx_success(result)
    vendor = result.json()
    assert vendor["name"] == "My Vendor"
    assert vendor["extra"] == {"mytextfield": '"My Value"'}

    # Clean up
    result = httpx.delete(f"{URL}/api/v1/field/vendor/mytextfield")
    assert_httpx_success(result)

    result = httpx.delete(f"{URL}/api/v1/vendor/{vendor_id}")
    assert_httpx_success(result)


def test_create_vendor_autofills_default_extra_field():
    """Creating a vendor without specifying a defaulted extra field auto-fills the default."""
    default_value = json.dumps("Default Vendor Note")

    result = httpx.post(
        f"{URL}/api/v1/field/vendor/auto_default",
        json={
            "name": "Auto default field",
            "field_type": "text",
            "default_value": default_value,
        },
    )
    assert_httpx_success(result)

    # Create a vendor WITHOUT passing the extra field at all.
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={"name": "My Vendor"},
    )
    assert_httpx_success(result)
    created = result.json()

    # The create response itself should already contain the auto-filled default.
    assert created["extra"] == {"auto_default": default_value}

    # ...and it should be persisted.
    result = httpx.get(f"{URL}/api/v1/vendor/{created['id']}")
    assert_httpx_success(result)
    assert result.json()["extra"] == {"auto_default": default_value}

    # Clean up
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/field/vendor/auto_default"))
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/vendor/{created['id']}"))


def test_create_vendor_manual_extra_field_overrides_default():
    """An explicitly provided extra field value takes precedence over the configured default."""
    default_value = json.dumps("Default Vendor Note")
    manual_value = json.dumps("Manually Set")

    result = httpx.post(
        f"{URL}/api/v1/field/vendor/auto_override",
        json={
            "name": "Auto override field",
            "field_type": "text",
            "default_value": default_value,
        },
    )
    assert_httpx_success(result)

    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={"name": "My Vendor", "extra": {"auto_override": manual_value}},
    )
    assert_httpx_success(result)
    created = result.json()

    assert created["extra"] == {"auto_override": manual_value}

    # Clean up
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/field/vendor/auto_override"))
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/vendor/{created['id']}"))


def test_create_vendor_field_without_default_not_autofilled():
    """A field with no configured default is not invented when creating an object."""
    result = httpx.post(
        f"{URL}/api/v1/field/vendor/auto_nodefault",
        json={
            "name": "Auto no-default field",
            "field_type": "text",
        },
    )
    assert_httpx_success(result)

    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={"name": "My Vendor"},
    )
    assert_httpx_success(result)
    created = result.json()

    assert "auto_nodefault" not in created["extra"]
    assert created["extra"] == {}

    # Clean up
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/field/vendor/auto_nodefault"))
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/vendor/{created['id']}"))


def test_create_vendor_invalid_extra_field_still_rejected():
    """Invalid explicitly provided values are still rejected even when defaults are configured."""
    result = httpx.post(
        f"{URL}/api/v1/field/vendor/auto_invalid",
        json={
            "name": "Auto invalid field",
            "field_type": "text",
            "default_value": json.dumps("Default"),
        },
    )
    assert_httpx_success(result)

    # "123" is valid JSON but decodes to an int, not a string -> must be rejected, not defaulted.
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={"name": "My Vendor", "extra": {"auto_invalid": "123"}},
    )
    assert_httpx_code(result, 400)

    # Clean up
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/field/vendor/auto_invalid"))


def test_create_filament_autofills_default_extra_field():
    """Creating a filament without specifying a defaulted extra field auto-fills the default."""
    default_value = json.dumps("Default Filament Note")

    result = httpx.post(
        f"{URL}/api/v1/field/filament/auto_default",
        json={
            "name": "Auto default field",
            "field_type": "text",
            "default_value": default_value,
        },
    )
    assert_httpx_success(result)

    result = httpx.post(
        f"{URL}/api/v1/filament",
        json={"density": 1.25, "diameter": 1.75},
    )
    assert_httpx_success(result)
    created = result.json()

    assert created["extra"] == {"auto_default": default_value}

    # Clean up
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/field/filament/auto_default"))
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/filament/{created['id']}"))


def test_create_spool_autofills_default_extra_field(random_filament: dict[str, Any]):
    """Creating a spool without specifying a defaulted extra field auto-fills the default."""
    default_value = json.dumps("Default Spool Note")

    result = httpx.post(
        f"{URL}/api/v1/field/spool/auto_default",
        json={
            "name": "Auto default field",
            "field_type": "text",
            "default_value": default_value,
        },
    )
    assert_httpx_success(result)

    result = httpx.post(
        f"{URL}/api/v1/spool",
        json={"filament_id": random_filament["id"]},
    )
    assert_httpx_success(result)
    created = result.json()

    assert created["extra"] == {"auto_default": default_value}

    # Clean up
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/field/spool/auto_default"))
    assert_httpx_success(httpx.delete(f"{URL}/api/v1/spool/{created['id']}"))
