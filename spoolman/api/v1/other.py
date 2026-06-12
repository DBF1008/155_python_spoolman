"""Filament related endpoints."""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, RootModel
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.api.v1.models import LocationOverview
from spoolman.database import filament, spool
from spoolman.database import setting as setting_db
from spoolman.database.database import get_db_session
from spoolman.exceptions import ItemNotFoundError
from spoolman.settings import parse_setting

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="",
    tags=["other"],
)

# ruff: noqa: D103


@router.get(
    "/material",
    name="Find materials",
    description="Get a list of all filament materials.",
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "A list of all filament materials.",
            "content": {
                "application/json": {
                    "example": [
                        "PLA",
                        "ABS",
                        "PETG",
                    ],
                },
            },
        },
    },
)
async def find_materials(
    *,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[str]:
    return await filament.find_materials(db=db)


@router.get(
    "/article-number",
    name="Find article numbers",
    description="Get a list of all article numbers.",
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "A list of all article numbers.",
            "content": {
                "application/json": {
                    "example": [
                        "123456",
                        "987654",
                    ],
                },
            },
        },
    },
)
async def find_article_numbers(
    *,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[str]:
    return await filament.find_article_numbers(db=db)


@router.get(
    "/lot-number",
    name="Find lot numbers",
    description="Get a list of all lot numbers.",
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "A list of all lot numbers.",
            "content": {
                "application/json": {
                    "example": [
                        "123456",
                        "987654",
                    ],
                },
            },
        },
    },
)
async def find_lot_numbers(
    *,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[str]:
    return await spool.find_lot_numbers(db=db)


@router.get(
    "/location",
    name="Find locations",
    description="Get a list of all spool locations.",
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "A list of all spool locations.",
            "content": {
                "application/json": {
                    "example": [
                        "Printer 1",
                        "Printer 2",
                        "Storage Shelf A",
                    ],
                },
            },
        },
    },
)
async def find_locations(
    *,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[str]:
    return await spool.find_locations(db=db)


@router.get(
    "/location/overview",
    name="Location overview",
    description=(
        "Get an overview of all locations, merging configured locations from settings "
        "with actual spool locations. Each location includes spool count, ordered spool IDs, "
        "and whether it is the empty/no-location bucket."
    ),
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "A list of location overviews.",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "name": "Shelf A",
                            "spool_count": 3,
                            "spool_ids": [5, 2, 8],
                            "is_empty_location": False,
                        },
                        {
                            "name": "",
                            "spool_count": 2,
                            "spool_ids": [1, 4],
                            "is_empty_location": True,
                        },
                    ],
                },
            },
        },
    },
)
async def find_locations_overview(
    *,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    allow_archived: Annotated[
        bool,
        Query(
            title="Allow Archived",
            description="Whether to include archived spools in the location overview.",
        ),
    ] = False,
) -> list[LocationOverview]:
    # 1. Get spool data grouped by location
    location_spools = await spool.find_locations_overview(db=db, allow_archived=allow_archived)

    # 2. Read configured locations from settings
    configured_locations: list[str] = []
    try:
        locations_def = parse_setting("locations")
        locations_setting = await setting_db.get(db, locations_def)
        configured_locations = json.loads(locations_setting.value)
    except (ItemNotFoundError, ValueError):
        pass  # Setting not set or invalid → no configured locations

    # 3. Read spool ordering from settings
    spool_orders: dict[str, list[int]] = {}
    try:
        orders_def = parse_setting("locations_spoolorders")
        orders_setting = await setting_db.get(db, orders_def)
        spool_orders = json.loads(orders_setting.value)
    except (ItemNotFoundError, ValueError):
        pass  # Setting not set or invalid → no custom ordering

    # 4. Merge: union of configured locations + actual locations
    all_location_names: list[str] = []
    seen: set[str] = set()

    # Configured locations first (preserves user-defined display order)
    for loc in configured_locations:
        if loc not in seen:
            all_location_names.append(loc)
            seen.add(loc)

    # Then any actual locations not in configured list (sorted for determinism)
    for loc in sorted(location_spools.keys()):
        if loc not in seen:
            all_location_names.append(loc)
            seen.add(loc)

    # 5. Build response
    result: list[LocationOverview] = []
    for name in all_location_names:
        spool_ids_in_loc = location_spools.get(name, [])
        spool_ids_set = set(spool_ids_in_loc)

        # Apply custom ordering if configured for this location
        ordered_ids = spool_orders.get(name, [])
        if ordered_ids:
            # Keep only IDs that actually exist in this location
            valid_ordered = [sid for sid in ordered_ids if sid in spool_ids_set]
            # Append any spools not in the order list (new spools added after ordering was set)
            ordered_set = set(valid_ordered)
            remaining = [sid for sid in spool_ids_in_loc if sid not in ordered_set]
            final_ids = valid_ordered + remaining
        else:
            # Default: already sorted by ID asc from the query
            final_ids = spool_ids_in_loc

        result.append(LocationOverview(
            name=name,
            spool_count=len(final_ids),
            spool_ids=final_ids,
            is_empty_location=(name == ""),
        ))

    return result


class RenameLocationBody(BaseModel):
    name: str = Field(description="The new name of the location.", min_length=1)


@router.patch(
    "/location/{location}",
    name="Rename location",
    description="Rename a spool location. All spools in this location will be moved to the new location.",
    response_model_exclude_none=True,
    response_model=RootModel[str],
)
async def rename_location(
    location: str,
    *,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    body: RenameLocationBody,
) -> str:
    logger.info("Renaming location %s to %s", location, body.name)
    await spool.rename_location(db=db, current_name=location, new_name=body.name)
    return body.name
