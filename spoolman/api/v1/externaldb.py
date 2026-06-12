"""External database API."""

import datetime
import logging

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from spoolman.api.v1.models import SpoolmanDateTime
from spoolman.externaldb import (
    ExternalFilament,
    ExternalMaterial,
    SyncStatus,
    get_external_db_sync_interval,
    get_filaments_file,
    get_materials_file,
    get_sync_status,
    refresh_now,
)

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


class ExternalDbSyncStatus(BaseModel):
    """Status of the external database synchronization, including basic cache statistics."""

    is_syncing: bool = Field(description="Whether a sync is currently in progress.")
    success: bool | None = Field(
        default=None,
        description="Whether the most recent sync attempt succeeded. Null if no sync has run yet since startup.",
    )
    error: str | None = Field(
        default=None,
        description="Error message from the most recent sync attempt, if it failed.",
    )
    last_attempt: SpoolmanDateTime | None = Field(
        default=None,
        description="When the most recent sync was attempted. UTC Timezone.",
    )
    last_success: SpoolmanDateTime | None = Field(
        default=None,
        description="When the most recent successful sync finished. UTC Timezone.",
    )
    source_url: str | None = Field(
        default=None,
        description="The external database URL used for the most recent sync attempt.",
        examples=["https://donkie.github.io/SpoolmanDB/"],
    )
    filament_count: int | None = Field(
        default=None,
        description="Number of filaments stored by the last successful sync.",
        examples=[1423],
    )
    material_count: int | None = Field(
        default=None,
        description="Number of materials stored by the last successful sync.",
        examples=[41],
    )
    sync_interval: int = Field(
        description="Configured interval in seconds between automatic syncs. 0 means periodic sync is disabled.",
        examples=[3600],
    )
    seconds_since_success: float | None = Field(
        default=None,
        description="Seconds elapsed since the last successful sync. Null if no sync has succeeded yet.",
        examples=[120.5],
    )
    is_stale: bool = Field(
        description=(
            "Whether the local cache is considered stale: true if no sync has succeeded yet, or if the "
            "last successful sync is older than the configured sync interval."
        ),
    )
    filaments_cached: bool = Field(description="Whether a locally cached filaments file is currently available.")
    materials_cached: bool = Field(description="Whether a locally cached materials file is currently available.")

    @classmethod
    def from_status(cls, status: SyncStatus) -> "ExternalDbSyncStatus":
        """Build the API status model from the internal sync status, computing freshness fields."""
        sync_interval = get_external_db_sync_interval()

        seconds_since_success: float | None = None
        if status.last_success is not None:
            now = datetime.datetime.now(datetime.timezone.utc)
            seconds_since_success = (now - status.last_success).total_seconds()

        is_stale = status.last_success is None or (
            sync_interval > 0 and seconds_since_success is not None and seconds_since_success > sync_interval
        )

        return cls(
            is_syncing=status.is_syncing,
            success=status.success,
            error=status.error,
            last_attempt=status.last_attempt,
            last_success=status.last_success,
            source_url=status.source_url,
            filament_count=status.filament_count,
            material_count=status.material_count,
            sync_interval=sync_interval,
            seconds_since_success=seconds_since_success,
            is_stale=is_stale,
            filaments_cached=get_filaments_file().exists(),
            materials_cached=get_materials_file().exists(),
        )


@router.get(
    "/status",
    name="Get external database sync status",
    description=(
        "Get the status of the external database synchronization, including when it last synced, basic "
        "statistics, and whether the local cache is considered stale. Does not trigger a sync."
    ),
    response_model=ExternalDbSyncStatus,
)
async def status() -> ExternalDbSyncStatus:
    """Return the current external database sync status."""
    return ExternalDbSyncStatus.from_status(get_sync_status())


@router.post(
    "/refresh",
    name="Refresh the external database",
    description=(
        "Trigger an immediate refresh of the external database cache and return the resulting sync status. "
        "If the refresh fails, the previously cached data is kept and served by the read-only endpoints; "
        "inspect the 'success' and 'error' fields to detect failures."
    ),
    response_model=ExternalDbSyncStatus,
)
async def refresh() -> ExternalDbSyncStatus:
    """Trigger an external database refresh and return the resulting sync status."""
    return ExternalDbSyncStatus.from_status(await refresh_now())
