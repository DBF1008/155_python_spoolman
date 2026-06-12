"""Unit tests for the shared extra-fields implementation.

These run without docker (in-memory SQLite) and exercise the core functions in ``spoolman.extra_fields``
directly. They are the decisive, fast proof that both entry points share one implementation: the cache is
invalidated, orphaned per-entity values are cleared, and validation/invariants are enforced regardless of
how the change arrives.

Run from the project root with::

    python -m pytest tests/ -q
"""

import json
from collections.abc import AsyncGenerator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from spoolman import extra_fields as ef
from spoolman.database import models
from spoolman.database import setting as db_setting
from spoolman.extra_fields import (
    EntityType,
    ExtraField,
    add_or_update_extra_field,
    apply_extra_fields_setting,
    delete_extra_field,
    entity_type_for_setting_key,
    get_extra_fields,
    replace_extra_fields,
    reset_extra_fields,
    setting_key_for_entity_type,
)
from spoolman.settings import parse_setting


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated in-memory SQLite session with the schema created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,  # keep a single connection so the in-memory DB is shared
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Reset the process-global field cache around every test."""
    ef.extra_field_cache.clear()
    yield
    ef.extra_field_cache.clear()


def make_field(
    key: str,
    *,
    entity_type: EntityType = EntityType.spool,
    field_type: str = "text",
    **kwargs: object,
) -> ExtraField:
    """Build an ExtraField for the given key."""
    return ExtraField.model_validate(
        {"key": key, "name": key, "field_type": field_type, "entity_type": entity_type.value, **kwargs},
    )


async def _spool_field_count(db: AsyncSession, key: str) -> int:
    return await db.scalar(
        select(func.count()).select_from(models.SpoolField).where(models.SpoolField.key == key),
    )


def test_setting_key_roundtrip() -> None:
    """The setting-key helpers map both ways and ignore unrelated keys."""
    for entity_type in EntityType:
        key = setting_key_for_entity_type(entity_type)
        assert entity_type_for_setting_key(key) is entity_type
    assert entity_type_for_setting_key("currency") is None
    assert entity_type_for_setting_key("extra_fields_unknown") is None


@pytest.mark.asyncio
async def test_apply_setting_invalidates_cache(db: AsyncSession) -> None:
    """Writing the bulk array via the setting path refreshes the cache (the needs-a-restart bug)."""
    # Seed via the field path so the cache holds "foo".
    await add_or_update_extra_field(db, EntityType.spool, make_field("foo"))
    assert [f.key for f in await get_extra_fields(db, EntityType.spool)] == ["foo"]
    assert EntityType.spool in ef.extra_field_cache

    # Overwrite via the setting bulk path with a different set.
    await apply_extra_fields_setting(
        db,
        EntityType.spool,
        json.dumps([{"key": "bar", "name": "Bar", "field_type": "text"}]),
    )

    # get_extra_fields reflects the new set immediately -- no restart, cache was refreshed.
    assert [f.key for f in await get_extra_fields(db, EntityType.spool)] == ["bar"]
    assert ef.extra_field_cache[EntityType.spool][0].key == "bar"


@pytest.mark.asyncio
async def test_reset_clears_cache_and_orphan_values(db: AsyncSession) -> None:
    """Resetting via the setting path clears the cache and the stored per-entity values."""
    await add_or_update_extra_field(db, EntityType.spool, make_field("foo"))

    # Simulate an entity carrying a value for the field.
    db.add(models.SpoolField(spool_id=1, key="foo", value=json.dumps("x")))
    await db.flush()
    assert await _spool_field_count(db, "foo") == 1

    await reset_extra_fields(db, EntityType.spool)

    assert await get_extra_fields(db, EntityType.spool) == []
    assert ef.extra_field_cache[EntityType.spool] == []
    assert await _spool_field_count(db, "foo") == 0


@pytest.mark.asyncio
async def test_delete_via_reconciler_clears_orphan_values(db: AsyncSession) -> None:
    """Removing a single field through the field path clears that field's stored values."""
    await add_or_update_extra_field(db, EntityType.spool, make_field("keep"))
    await add_or_update_extra_field(db, EntityType.spool, make_field("drop"))
    db.add(models.SpoolField(spool_id=1, key="drop", value=json.dumps("x")))
    db.add(models.SpoolField(spool_id=1, key="keep", value=json.dumps("y")))
    await db.flush()

    await delete_extra_field(db, EntityType.spool, "drop")

    assert {f.key for f in await get_extra_fields(db, EntityType.spool)} == {"keep"}
    assert await _spool_field_count(db, "drop") == 0
    assert await _spool_field_count(db, "keep") == 1  # untouched


@pytest.mark.asyncio
async def test_apply_setting_validates_field_definitions(db: AsyncSession) -> None:
    """The setting path enforces per-field validation (parity with the field path)."""
    with pytest.raises(ValueError, match=r"[Cc]hoices"):
        await apply_extra_fields_setting(
            db,
            EntityType.spool,
            json.dumps([{"key": "badchoice", "name": "Bad", "field_type": "choice"}]),  # choice w/o choices
        )
    # Nothing persisted.
    assert await get_extra_fields(db, EntityType.spool) == []


@pytest.mark.asyncio
async def test_apply_setting_rejects_duplicate_keys(db: AsyncSession) -> None:
    """Duplicate keys within a bulk write are rejected."""
    with pytest.raises(ValueError, match=r"[Dd]uplicate"):
        await apply_extra_fields_setting(
            db,
            EntityType.spool,
            json.dumps(
                [
                    {"key": "dup", "name": "A", "field_type": "text"},
                    {"key": "dup", "name": "B", "field_type": "text"},
                ],
            ),
        )


@pytest.mark.asyncio
async def test_setting_rejects_incompatible_type_change(db: AsyncSession) -> None:
    """An incompatible field-type change through the setting path is rejected."""
    await add_or_update_extra_field(db, EntityType.spool, make_field("f", field_type="text"))
    with pytest.raises(ValueError, match=r"[Ff]ield type"):
        await apply_extra_fields_setting(
            db,
            EntityType.spool,
            json.dumps([{"key": "f", "name": "f", "field_type": "integer"}]),
        )


@pytest.mark.asyncio
async def test_apply_setting_bad_json_and_non_list(db: AsyncSession) -> None:
    """Malformed JSON or a non-array body is rejected with a ValueError."""
    with pytest.raises(ValueError, match="JSON"):
        await apply_extra_fields_setting(db, EntityType.spool, "{not json")
    with pytest.raises(ValueError, match="array"):
        await apply_extra_fields_setting(db, EntityType.spool, json.dumps({"not": "a list"}))


@pytest.mark.asyncio
async def test_delete_not_blocked_by_invalid_sibling(db: AsyncSession) -> None:
    """Deleting a field must not fail just because an unrelated stored field is structurally invalid.

    Databases edited through the old, unvalidated setting path may contain such entries.
    """
    valid = make_field("keep", field_type="text")
    # Structurally invalid per validate_extra_field (choice without choices) but accepted by the model,
    # as the old setting path could have stored.
    invalid_sibling = ExtraField.model_validate(
        {"key": "badchoice", "name": "Bad", "field_type": "choice", "entity_type": "spool"},
    )
    ef.extra_field_cache[EntityType.spool] = [valid, invalid_sibling]

    # Must not raise despite the invalid sibling.
    await delete_extra_field(db, EntityType.spool, "keep")

    remaining = {f.key for f in await get_extra_fields(db, EntityType.spool)}
    assert remaining == {"badchoice"}


@pytest.mark.asyncio
async def test_field_and_setting_paths_converge(db: AsyncSession) -> None:
    """The field path and the setting path produce an identical stored field set."""
    await add_or_update_extra_field(
        db,
        EntityType.spool,
        make_field("k", field_type="text", default_value=json.dumps("d")),
    )
    via_field = [f.model_dump() for f in await get_extra_fields(db, EntityType.spool)]

    await reset_extra_fields(db, EntityType.spool)
    await apply_extra_fields_setting(
        db,
        EntityType.spool,
        json.dumps([{"key": "k", "name": "k", "field_type": "text", "default_value": json.dumps("d")}]),
    )
    via_setting = [f.model_dump() for f in await get_extra_fields(db, EntityType.spool)]

    assert via_field == via_setting


@pytest.mark.asyncio
async def test_replace_persists_setting_row(db: AsyncSession) -> None:
    """replace_extra_fields persists through the setting layer so the value survives a cache clear."""
    await replace_extra_fields(db, EntityType.filament, [make_field("p", entity_type=EntityType.filament)])

    # Drop the cache: the next read must come from the persisted setting row, not memory.
    ef.extra_field_cache.clear()
    stored = await db_setting.get(db, parse_setting(setting_key_for_entity_type(EntityType.filament)))
    assert json.loads(stored.value)[0]["key"] == "p"
    assert [f.key for f in await get_extra_fields(db, EntityType.filament)] == ["p"]
