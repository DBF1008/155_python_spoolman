"""External database API."""

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from spoolman import externaldb
from spoolman.api.v1.models import ExternalSyncStatus
from spoolman.externaldb import ExternalFilament, ExternalMaterial, get_filaments_file, get_materials_file

router = APIRouter(
    prefix="/external",
    tags=["external"],
)


logger = logging.getLogger(__name__)


@router.get(
    "/filament",
    name="Get all external filaments",
    response_model_exclude_none=True,
    response_model=list[ExternalFilament],
)
async def filaments() -> FileResponse:
    """Get all external filaments."""
    return FileResponse(path=get_filaments_file(), media_type="application/json")


@router.get(
    "/material",
    name="Get all external materials",
    response_model_exclude_none=True,
    response_model=list[ExternalMaterial],
)
async def materials() -> FileResponse:
    """Get all external materials."""
    return FileResponse(path=get_materials_file(), media_type="application/json")


@router.get(
    "/status",
    name="Get external DB sync status",
)
async def sync_status() -> ExternalSyncStatus:
    """Get the status of the last external DB sync, including timing, counts, and any error."""
    status = externaldb.get_sync_status()
    return ExternalSyncStatus(**status.model_dump())


@router.post(
    "/refresh",
    name="Refresh external DB",
    response_model=ExternalSyncStatus,
    responses={
        400: {"description": "External DB URL is not configured."},
        409: {"description": "A sync is already in progress."},
    },
)
async def refresh() -> ExternalSyncStatus | JSONResponse:
    """Trigger a manual refresh of the external DB cache.

    Returns the sync status after the refresh completes.
    Returns 409 if a refresh is already running, or 400 if no external DB URL is configured.
    """
    url = externaldb.get_external_db_url()
    if not url.strip():
        return JSONResponse(
            status_code=400,
            content={"message": "External DB URL is not configured."},
        )

    try:
        status = await externaldb.refresh()
    except RuntimeError:
        return JSONResponse(
            status_code=409,
            content={"message": "A sync is already in progress."},
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"message": "External DB sync timed out."},
        )

    return ExternalSyncStatus(**status.model_dump())
