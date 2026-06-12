"""Filament related endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, RootModel
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.api.v1.models import LocationOverviewEntry
from spoolman.database import filament, spool
from spoolman.database.database import get_db_session

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
    "/location-overview",
    name="Get location overview",
    description=(
        "Get a merged overview of all spool locations. This combines the configured locations "
        "(the 'locations' setting) with the locations spools are actually stored in, returned in display order. "
        "Each entry includes the number of non-archived spools in the location, those spools' IDs in display "
        "order (resolving the 'locations_spoolorders' setting), and whether it is the default location used "
        "for spools that have no location set."
    ),
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "A merged, ordered list of locations.",
            "content": {
                "application/json": {
                    "example": [
                        {"name": "", "is_default": True, "spool_count": 2, "spool_orders": [4, 6]},
                        {"name": "Printer 1", "is_default": False, "spool_count": 1, "spool_orders": [7]},
                        {"name": "Storage Shelf A", "is_default": False, "spool_count": 0, "spool_orders": []},
                    ],
                },
            },
        },
    },
)
async def get_location_overview(
    *,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[LocationOverviewEntry]:
    return await spool.get_location_overview(db=db)


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
