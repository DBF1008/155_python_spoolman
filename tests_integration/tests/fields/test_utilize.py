"""Integration tests for the custom extra fields system."""

import json

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


def test_create_vendor_auto_populates_extra_field_defaults():
    """Test that creating a vendor without extra fields auto-populates defaults."""
    # Create a field definition with a default value
    result = httpx.post(
        f"{URL}/api/v1/field/vendor/mydefaultfield",
        json={
            "name": "My default field",
            "field_type": "text",
            "default_value": json.dumps("Default Value"),
        },
    )
    assert_httpx_success(result)

    # Create vendor WITHOUT providing any extra fields
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={
            "name": "Vendor With Defaults",
        },
    )
    assert_httpx_success(result)

    # Verify the default was applied
    result = httpx.get(f"{URL}/api/v1/vendor/{result.json()['id']}")
    assert_httpx_success(result)
    vendor_obj = result.json()
    assert vendor_obj["extra"] == {"mydefaultfield": '"Default Value"'}

    # Clean up
    httpx.delete(f"{URL}/api/v1/field/vendor/mydefaultfield").raise_for_status()
    httpx.delete(f"{URL}/api/v1/vendor/{vendor_obj['id']}").raise_for_status()


def test_create_vendor_manual_value_overrides_default():
    """Test that explicitly provided extra field values take priority over defaults."""
    result = httpx.post(
        f"{URL}/api/v1/field/vendor/myoverridefield",
        json={
            "name": "My override field",
            "field_type": "text",
            "default_value": json.dumps("Default"),
        },
    )
    assert_httpx_success(result)

    # Create vendor WITH an explicit value for the field that has a default
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={
            "name": "Vendor Override",
            "extra": {
                "myoverridefield": '"Custom Value"',
            },
        },
    )
    assert_httpx_success(result)

    # Verify the manual value was used, NOT the default
    result = httpx.get(f"{URL}/api/v1/vendor/{result.json()['id']}")
    assert_httpx_success(result)
    vendor_obj = result.json()
    assert vendor_obj["extra"] == {"myoverridefield": '"Custom Value"'}

    # Clean up
    httpx.delete(f"{URL}/api/v1/field/vendor/myoverridefield").raise_for_status()
    httpx.delete(f"{URL}/api/v1/vendor/{vendor_obj['id']}").raise_for_status()


def test_create_vendor_partial_extra_fills_remaining_defaults():
    """Test that providing some extra fields still fills defaults for the rest."""
    # Create two field definitions with defaults
    result = httpx.post(
        f"{URL}/api/v1/field/vendor/field_a",
        json={
            "name": "Field A",
            "field_type": "text",
            "default_value": json.dumps("Default A"),
        },
    )
    assert_httpx_success(result)

    result = httpx.post(
        f"{URL}/api/v1/field/vendor/field_b",
        json={
            "name": "Field B",
            "field_type": "text",
            "default_value": json.dumps("Default B"),
        },
    )
    assert_httpx_success(result)

    # Create vendor with only field_a provided explicitly
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={
            "name": "Partial Vendor",
            "extra": {
                "field_a": '"Manual A"',
            },
        },
    )
    assert_httpx_success(result)

    # Verify: field_a has manual value, field_b has default
    result = httpx.get(f"{URL}/api/v1/vendor/{result.json()['id']}")
    assert_httpx_success(result)
    vendor_obj = result.json()
    assert vendor_obj["extra"]["field_a"] == '"Manual A"'
    assert vendor_obj["extra"]["field_b"] == '"Default B"'

    # Clean up
    httpx.delete(f"{URL}/api/v1/field/vendor/field_a").raise_for_status()
    httpx.delete(f"{URL}/api/v1/field/vendor/field_b").raise_for_status()
    httpx.delete(f"{URL}/api/v1/vendor/{vendor_obj['id']}").raise_for_status()


def test_create_filament_auto_populates_extra_field_defaults():
    """Test that creating a filament without extra fields auto-populates defaults."""
    result = httpx.post(
        f"{URL}/api/v1/field/filament/fildefault",
        json={
            "name": "Filament default field",
            "field_type": "text",
            "default_value": json.dumps("Fil Default"),
        },
    )
    assert_httpx_success(result)

    # Need a vendor for the filament
    vendor_result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={"name": "Temp Vendor"},
    )
    assert_httpx_success(vendor_result)
    vendor_id = vendor_result.json()["id"]

    # Create filament WITHOUT extra fields
    result = httpx.post(
        f"{URL}/api/v1/filament",
        json={
            "density": 1.24,
            "diameter": 1.75,
            "vendor_id": vendor_id,
        },
    )
    assert_httpx_success(result)

    # Verify default was applied
    result = httpx.get(f"{URL}/api/v1/filament/{result.json()['id']}")
    assert_httpx_success(result)
    fil = result.json()
    assert fil["extra"] == {"fildefault": '"Fil Default"'}

    # Clean up
    httpx.delete(f"{URL}/api/v1/field/filament/fildefault").raise_for_status()
    httpx.delete(f"{URL}/api/v1/filament/{fil['id']}").raise_for_status()
    httpx.delete(f"{URL}/api/v1/vendor/{vendor_id}").raise_for_status()


def test_create_spool_auto_populates_extra_field_defaults():
    """Test that creating a spool without extra fields auto-populates defaults."""
    result = httpx.post(
        f"{URL}/api/v1/field/spool/spooldefault",
        json={
            "name": "Spool default field",
            "field_type": "text",
            "default_value": json.dumps("Spool Default"),
        },
    )
    assert_httpx_success(result)

    # Need a vendor and filament for the spool
    vendor_result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={"name": "Temp Vendor"},
    )
    assert_httpx_success(vendor_result)
    vendor_id = vendor_result.json()["id"]

    fil_result = httpx.post(
        f"{URL}/api/v1/filament",
        json={
            "density": 1.24,
            "diameter": 1.75,
            "vendor_id": vendor_id,
        },
    )
    assert_httpx_success(fil_result)
    filament_id = fil_result.json()["id"]

    # Create spool WITHOUT extra fields
    result = httpx.post(
        f"{URL}/api/v1/spool",
        json={
            "filament_id": filament_id,
        },
    )
    assert_httpx_success(result)

    # Verify default was applied
    result = httpx.get(f"{URL}/api/v1/spool/{result.json()['id']}")
    assert_httpx_success(result)
    spool_obj = result.json()
    assert spool_obj["extra"] == {"spooldefault": '"Spool Default"'}

    # Clean up
    httpx.delete(f"{URL}/api/v1/field/spool/spooldefault").raise_for_status()
    httpx.delete(f"{URL}/api/v1/spool/{spool_obj['id']}").raise_for_status()
    httpx.delete(f"{URL}/api/v1/filament/{filament_id}").raise_for_status()
    httpx.delete(f"{URL}/api/v1/vendor/{vendor_id}").raise_for_status()


def test_create_entity_no_defaults_no_extra_in_response():
    """Test that when no field definitions exist, response has no extra key."""
    result = httpx.post(
        f"{URL}/api/v1/vendor",
        json={"name": "Plain Vendor"},
    )
    assert_httpx_success(result)

    result = httpx.get(f"{URL}/api/v1/vendor/{result.json()['id']}")
    assert_httpx_success(result)
    vendor_obj = result.json()
    # No extra fields defined, so extra should be absent or empty
    assert "extra" not in vendor_obj or vendor_obj["extra"] == {}

    httpx.delete(f"{URL}/api/v1/vendor/{vendor_obj['id']}").raise_for_status()
