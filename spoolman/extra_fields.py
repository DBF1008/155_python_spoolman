"""Custom/extra fields for spoolman entities."""

import contextlib
import json
import logging
from enum import Enum

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.database import filament as db_filament
from spoolman.database import setting as db_setting
from spoolman.database import spool as db_spool
from spoolman.database import vendor as db_vendor
from spoolman.exceptions import ItemNotFoundError
from spoolman.settings import parse_setting

logger = logging.getLogger(__name__)


class EntityType(Enum):
    vendor = "vendor"
    filament = "filament"
    spool = "spool"


class ExtraFieldType(Enum):
    text = "text"
    integer = "integer"
    integer_range = "integer_range"
    float = "float"
    float_range = "float_range"
    datetime = "datetime"
    boolean = "boolean"
    choice = "choice"


class ExtraFieldParameters(BaseModel):
    name: str = Field(description="Nice name", min_length=1, max_length=128)
    order: int = Field(0, description="Order of the field")
    unit: str | None = Field(None, description="Unit of the value", min_length=1, max_length=16)
    field_type: ExtraFieldType = Field(description="Type of the field")
    default_value: str | None = Field(None, description="Default value of the field")
    choices: list[str] | None = Field(
        None,
        description="Choices for the field, only for field type choice",
        min_length=1,
    )
    multi_choice: bool | None = Field(None, description="Whether multiple choices can be selected")


class ExtraField(ExtraFieldParameters):
    key: str = Field(description="Unique key", pattern="^[a-z0-9_]+$", min_length=1, max_length=64)
    entity_type: EntityType = Field(description="Entity type this field is for")


def validate_extra_field_value(field: ExtraFieldParameters, value: str) -> None:  # noqa: C901, PLR0912
    """Validate that the value has the correct type."""
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError("Value is not valid JSON.") from None

    if field.field_type == ExtraFieldType.text:
        if not isinstance(data, str):
            raise ValueError("Value is not a string.")
    elif field.field_type == ExtraFieldType.integer:
        if not isinstance(data, int):
            raise ValueError("Value is not an integer.")
    elif field.field_type == ExtraFieldType.integer_range:
        if not isinstance(data, list):
            raise ValueError("Value is not a list.")
        if len(data) != 2:  # noqa: PLR2004
            raise ValueError("Value list must have exactly two values.")
        if not all(isinstance(value, int) or value is None for value in data):
            raise ValueError("Value list must contain only integers or null.")
    elif field.field_type == ExtraFieldType.float:
        if not isinstance(data, (float, int)) or isinstance(data, bool):
            raise ValueError("Value is not a float.")
    elif field.field_type == ExtraFieldType.float_range:
        if not isinstance(data, list):
            raise ValueError("Value is not a list.")
        if len(data) != 2:  # noqa: PLR2004
            raise ValueError("Value list must have exactly two values.")
        if not all(
            (isinstance(value, (float, int)) or value is None) and not isinstance(value, bool) for value in data
        ):
            raise ValueError("Value list must contain only floats or null.")
    elif field.field_type == ExtraFieldType.datetime:
        if not isinstance(data, str):
            raise ValueError("Value is not a string.")
    elif field.field_type == ExtraFieldType.boolean:
        if not isinstance(data, bool):
            raise ValueError("Value is not a boolean.")
    elif field.field_type == ExtraFieldType.choice:
        if field.multi_choice:
            if not isinstance(data, list):
                raise ValueError("Value is not a list.")
            if not all(isinstance(value, str) for value in data):
                raise ValueError("Value list must contain only strings.")
            if field.choices is not None and not all(value in field.choices for value in data):
                raise ValueError("Value list contains invalid choices.")
        else:
            if not isinstance(data, str):
                raise ValueError("Value is not a string.")
            if field.choices is not None and data not in field.choices:
                raise ValueError("Value is not a valid choice.")
    else:
        raise ValueError(f"Unknown field type {field.field_type}.")


def validate_extra_field(field: ExtraFieldParameters) -> None:
    """Validate an extra field."""
    # Validate choices exist if field type is choice
    if field.field_type == ExtraFieldType.choice:
        if field.choices is None:
            raise ValueError("Choices must be set for field type choice.")
        if field.multi_choice is None:
            raise ValueError("Multi choice must be set for field type choice.")
    else:
        if field.choices is not None:
            raise ValueError("Choices must not be set for field type other than choice.")
        if field.multi_choice is not None:
            raise ValueError("Multi choice must not be set for field type other than choice.")

    # Validate default value data type
    if field.default_value is not None:
        try:
            validate_extra_field_value(field, field.default_value)
        except ValueError as e:
            raise ValueError(f"Default value is not valid: {e}") from None


def validate_extra_field_dict(all_fields: list[ExtraField], fields_input: dict[str, str]) -> None:
    """Validate a dict of extra fields."""
    all_field_lookup = {field.key: field for field in all_fields}
    for key, value in fields_input.items():
        if key not in all_field_lookup:
            raise ValueError(f"Unknown extra field {key}.")
        field = all_field_lookup[key]
        try:
            validate_extra_field_value(field, value)
        except ValueError as e:
            raise ValueError(f"Invalid extra field for key {key}: {e!s}") from None


EXTRA_FIELD_SETTING_PREFIX = "extra_fields_"

extra_field_cache: dict[EntityType, list[ExtraField]] = {}


def setting_key_for_entity_type(entity_type: EntityType) -> str:
    """Return the setting key that stores the extra fields for an entity type."""
    return f"{EXTRA_FIELD_SETTING_PREFIX}{entity_type.name}"


def entity_type_for_setting_key(key: str) -> EntityType | None:
    """Return the entity type a setting key stores extra fields for, or None if it isn't such a key."""
    if not key.startswith(EXTRA_FIELD_SETTING_PREFIX):
        return None
    try:
        return EntityType(key[len(EXTRA_FIELD_SETTING_PREFIX) :])
    except ValueError:
        return None


async def get_extra_fields(db: AsyncSession, entity_type: EntityType) -> list[ExtraField]:
    """Get all extra fields for a specific entity type."""
    if entity_type in extra_field_cache:
        return extra_field_cache[entity_type]

    setting_def = parse_setting(setting_key_for_entity_type(entity_type))
    try:
        setting = await db_setting.get(db, setting_def)
        setting_value = setting.value
    except ItemNotFoundError:
        setting_value = setting_def.default

    setting_array = json.loads(setting_value)
    if not isinstance(setting_array, list):
        logger.warning("Setting %s is not a list, using default.", setting_def.key)
        setting_array = []

    fields = [ExtraField.parse_obj(obj) for obj in setting_array]
    extra_field_cache[entity_type] = fields
    return fields


def _ensure_compatible_change(existing_field: ExtraField, new_field: ExtraField) -> None:
    """Verify that changing an existing field would not break already-stored entity data."""
    if existing_field.field_type != new_field.field_type:
        raise ValueError("Field type cannot be changed.")
    if new_field.field_type == ExtraFieldType.choice:
        # Can't change multi choice since that would break existing data
        if existing_field.multi_choice != new_field.multi_choice:
            raise ValueError("Multi choice cannot be changed.")

        # Verify that we have only added new choices, not removed any
        if (
            existing_field.choices is not None
            and new_field.choices is not None
            and not all(choice in new_field.choices for choice in existing_field.choices)
        ):
            raise ValueError("Cannot remove existing choices.")


async def _clear_entity_field_values(db: AsyncSession, entity_type: EntityType, key: str) -> None:
    """Delete the stored value of an extra field from every entity of the given type."""
    if entity_type == EntityType.vendor:
        await db_vendor.clear_extra_field(db, key)
    elif entity_type == EntityType.filament:
        await db_filament.clear_extra_field(db, key)
    elif entity_type == EntityType.spool:
        await db_spool.clear_extra_field(db, key)
    else:
        raise ValueError(f"Unknown entity type {entity_type.name}.")


async def replace_extra_fields(
    db: AsyncSession,
    entity_type: EntityType,
    new_fields: list[ExtraField],
    *,
    validate_keys: set[str] | None = None,
) -> list[ExtraField]:
    """Replace the full set of extra fields for an entity type.

    This is the single source of truth used both by the dedicated field endpoints and by direct writes
    through the generic setting endpoint. It validates the new set, persists it (which also emits the
    setting websocket event), refreshes the in-memory cache, and clears orphaned values for any fields
    that were removed.

    Args:
        db: The database session.
        entity_type: The entity type the fields belong to.
        new_fields: The complete new list of extra fields.
        validate_keys: If given, only structurally validate fields whose key is in this set. The
            single-field callers use this so an unrelated, possibly pre-existing invalid field does not
            block the operation. If None, every field is structurally validated.

    Returns:
        The new list of extra fields.

    """
    existing_fields = await get_extra_fields(db, entity_type)
    existing_by_key = {field.key: field for field in existing_fields}

    seen: set[str] = set()
    for field in new_fields:
        if field.key in seen:
            raise ValueError(f"Duplicate extra field {field.key}.")
        seen.add(field.key)
        if validate_keys is None or field.key in validate_keys:
            validate_extra_field(field)
        existing_field = existing_by_key.get(field.key)
        if existing_field is not None:
            _ensure_compatible_change(existing_field, field)

    setting_def = parse_setting(setting_key_for_entity_type(entity_type))
    await db_setting.update(db=db, definition=setting_def, value=json.dumps(jsonable_encoder(new_fields)))

    # Update cache
    extra_field_cache[entity_type] = new_fields

    # Delete the stored values of any fields that were removed
    for removed_key in set(existing_by_key) - seen:
        await _clear_entity_field_values(db, entity_type, removed_key)

    return new_fields


async def add_or_update_extra_field(db: AsyncSession, entity_type: EntityType, extra_field: ExtraField) -> None:
    """Add or update an extra field for a specific entity type."""
    extra_fields = await get_extra_fields(db, entity_type)
    new_fields = [field for field in extra_fields if field.key != extra_field.key]
    new_fields.append(extra_field)

    await replace_extra_fields(db, entity_type, new_fields, validate_keys={extra_field.key})

    logger.info("Added/updated extra field %s for entity type %s.", extra_field.key, entity_type.name)


async def delete_extra_field(db: AsyncSession, entity_type: EntityType, key: str) -> None:
    """Delete an extra field for a specific entity type."""
    extra_fields = await get_extra_fields(db, entity_type)

    # Check if the field exists
    if not any(field.key == key for field in extra_fields):
        raise ItemNotFoundError(f"Extra field with key {key} does not exist.")

    new_fields = [field for field in extra_fields if field.key != key]

    # Nothing to structurally validate on delete; replace_extra_fields clears the removed field's values.
    await replace_extra_fields(db, entity_type, new_fields, validate_keys=set())

    logger.info("Deleted extra field %s for entity type %s.", key, entity_type.name)


async def apply_extra_fields_setting(db: AsyncSession, entity_type: EntityType, value: str) -> None:
    """Apply a full extra-fields array supplied through the generic setting endpoint.

    Routes the raw setting value through the same shared implementation as the field endpoints so that
    validation, cache invalidation and orphaned-value cleanup all behave identically across both entry
    points. Raises ValueError on any invalid input.
    """
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError("Value is not valid JSON.") from None
    if not isinstance(raw, list):
        raise ValueError("Extra fields must be an array.")  # noqa: TRY004

    new_fields: list[ExtraField] = []
    for index, obj in enumerate(raw):
        if not isinstance(obj, dict):
            raise ValueError(f"Extra field at index {index} must be an object.")  # noqa: TRY004
        # Force the entity type to match the setting key, mirroring the field endpoint.
        data = {**obj, "entity_type": entity_type.value}
        try:
            new_fields.append(ExtraField.model_validate(data))
        except ValidationError as e:
            raise ValueError(f"Invalid extra field at index {index}: {e}") from None

    await replace_extra_fields(db, entity_type, new_fields)


async def reset_extra_fields(db: AsyncSession, entity_type: EntityType) -> None:
    """Remove all extra fields for an entity type (mirrors un-setting the setting).

    Deletes the underlying setting (emitting the DELETED event and leaving it unset), refreshes the cache
    and clears the stored values of all previously-defined fields from every entity. Idempotent: if the
    setting was never set it still ensures the cache and entity values are cleared.
    """
    existing_fields = await get_extra_fields(db, entity_type)

    setting_def = parse_setting(setting_key_for_entity_type(entity_type))
    # Already-unset is fine; we still clear the values and cache so the state stays consistent.
    with contextlib.suppress(ItemNotFoundError):
        await db_setting.delete(db=db, definition=setting_def)

    # Update cache to the default (empty) value.
    extra_field_cache[entity_type] = []

    for field in existing_fields:
        await _clear_entity_field_values(db, entity_type, field.key)

    logger.info("Reset all extra fields for entity type %s.", entity_type.name)


async def populate_with_defaults(db: AsyncSession, entity_type: EntityType, existing: dict[str, str]) -> None:
    """Populate the given list of extra fields with defaults."""
    extra_fields = await get_extra_fields(db, entity_type)
    for extra_field in extra_fields:
        if extra_field.default_value is None:
            continue
        if extra_field.key in existing:
            continue
        existing[extra_field.key] = extra_field.default_value
