"""Unit tests for the extra-fields default-population logic.

These are fast, server-less tests for ``populate_with_defaults`` — the helper that the
create endpoints use to auto-fill default values for extra fields the caller did not provide.
The end-to-end behaviour against a running server is covered separately in
``tests_integration/tests/fields/test_utilize.py``.
"""

import json

from spoolman.extra_fields import (
    EntityType,
    ExtraField,
    ExtraFieldType,
    populate_with_defaults,
)


def _text_field(key: str, default: str | None) -> ExtraField:
    """Build a text extra field, optionally with a JSON-encoded default value."""
    return ExtraField(
        key=key,
        entity_type=EntityType.spool,
        name=key,
        field_type=ExtraFieldType.text,
        default_value=None if default is None else json.dumps(default),
    )


def test_default_is_filled_when_missing():
    """A field with a default is auto-filled when the caller omitted it."""
    fields = [_text_field("color", "red")]
    existing: dict[str, str] = {}

    populate_with_defaults(fields, existing)

    assert existing == {"color": '"red"'}


def test_manual_value_takes_precedence():
    """An explicitly-provided value is never overwritten by the default."""
    fields = [_text_field("color", "red")]
    existing = {"color": '"blue"'}

    populate_with_defaults(fields, existing)

    assert existing == {"color": '"blue"'}, "manual value must win over the default"


def test_field_without_default_is_not_added():
    """A field that has no configured default is left absent, not invented."""
    fields = [_text_field("notes", None)]
    existing: dict[str, str] = {}

    populate_with_defaults(fields, existing)

    assert existing == {}


def test_deleted_field_is_not_repopulated():
    """A field absent from the field list (e.g. deleted) is never re-added.

    This preserves the field-deletion semantic: once a field is removed it should not
    reappear on newly created objects, even if other defaulted fields still exist.
    """
    fields = [_text_field("color", "red")]  # "old_field" is no longer defined
    existing: dict[str, str] = {}

    populate_with_defaults(fields, existing)

    assert "old_field" not in existing
    assert existing == {"color": '"red"'}


def test_mixed_fields_only_fill_missing_defaults():
    """With several fields, fill only the defaulted-and-missing ones; keep the rest."""
    fields = [
        _text_field("color", "red"),  # has default, missing -> filled
        _text_field("size", "large"),  # has default, but provided -> kept
        _text_field("notes", None),  # no default -> untouched
    ]
    existing = {"size": '"small"'}

    populate_with_defaults(fields, existing)

    assert existing == {"size": '"small"', "color": '"red"'}


def test_returns_none_and_mutates_in_place():
    """The helper mutates the dict in place and returns None (matches its callers)."""
    fields = [_text_field("color", "red")]
    existing: dict[str, str] = {}

    result = populate_with_defaults(fields, existing)

    assert result is None
    assert existing == {"color": '"red"'}


def test_empty_field_list_is_a_noop():
    """No configured fields means nothing is added."""
    existing: dict[str, str] = {}

    populate_with_defaults([], existing)

    assert existing == {}
