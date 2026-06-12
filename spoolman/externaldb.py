"""Functions for syncing data from an external database of manufacturers, filaments, materials, etc."""

import asyncio
import datetime
import logging
import os
import time
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from urllib.parse import urljoin

import hishel
from pydantic import BaseModel, Field, RootModel
from scheduler.asyncio.scheduler import Scheduler

from spoolman import filecache
from spoolman.env import get_cache_dir

logger = logging.getLogger(__name__)


DEFAULT_EXTERNAL_DB_URL = "https://donkie.github.io/SpoolmanDB/"
DEFAULT_SYNC_INTERVAL = 3600

controller = hishel.Controller(allow_stale=True)
try:
    cache_path = get_cache_dir() / "hishel"
    cache_storage = hishel.AsyncFileStorage(base_path=cache_path)
except PermissionError:
    logger.warning(
        "Failed to setup disk-based cache due to permission error. Ensure the path %s is writable. "
        "Using in-memory cache instead as fallback.",
        str(cache_path.resolve()),
    )
    cache_storage = hishel.AsyncInMemoryStorage()


class SpoolType(Enum):
    PLASTIC = "plastic"
    CARDBOARD = "cardboard"
    METAL = "metal"


class Finish(Enum):
    MATTE = "matte"
    GLOSSY = "glossy"


class MultiColorDirection(Enum):
    COAXIAL = "coaxial"
    LONGITUDINAL = "longitudinal"


class Pattern(Enum):
    MARBLE = "marble"
    SPARKLE = "sparkle"


class ExternalFilament(BaseModel):
    id: str = Field(description="A unique ID for this filament.", examples=["polymaker_pla_polysonicblack_1000_175"])
    manufacturer: str = Field(description="Filament manufacturer.", examples=["Polymaker"])
    name: str = Field(description="Filament name.", examples=["Polysonic\u2122 Black"])
    material: str = Field(description="Filament material.", examples=["PLA"])
    density: float = Field(description="Density in g/cm3.", examples=[1.23])
    weight: float = Field(description="Net weight of a single spool.", examples=[1000])
    spool_weight: float | None = Field(default=None, description="Weight of an empty spool.", examples=[140])
    spool_type: SpoolType | None = Field(None, description="Type of spool.", examples=[SpoolType.PLASTIC])
    diameter: float = Field(description="Filament in mm.", examples=[1.75])
    color_hex: str | None = Field(
        default=None,
        description="Filament color code in hex format, for single-color filaments.",
        examples=["2c3232"],
    )
    color_hexes: list[str] | None = Field(
        default=None,
        description="For multi-color filaments. List of hex color codes in hex format.",
        examples=[["2c3232", "5f5f5f"]],
    )
    extruder_temp: int | None = Field(default=None, description="Extruder/nozzle temperature in °C.", examples=[210])
    bed_temp: int | None = Field(default=None, description="Bed temperature in °C.", examples=[50])
    finish: Finish | None = Field(default=None, description="Finish of the filament.", examples=[Finish.MATTE])
    multi_color_direction: MultiColorDirection | None = Field(
        default=None,
        description="Direction of multi-color filaments.",
        examples=[MultiColorDirection.COAXIAL],
    )
    pattern: Pattern | None = Field(default=None, description="Pattern of the filament.", examples=[Pattern.MARBLE])
    translucent: bool = Field(default=False, description="Whether the filament is translucent.")
    glow: bool = Field(default=False, description="Whether the filament is glow-in-the-dark.")


class ExternalFilamentsFile(RootModel):
    root: list[ExternalFilament]

    def __iter__(self) -> Iterator[ExternalFilament]:
        """Iterate over the filaments."""
        return iter(self.root)

    def __getitem__(self, index: int) -> ExternalFilament:
        """Get a specific filament by index."""
        return self.root[index]


class ExternalMaterial(BaseModel):
    material: str = Field(examples=["PLA"])
    density: float = Field(examples=[1.24])
    extruder_temp: int | None = Field(default=None, description="Extruder/nozzle temperature in °C.", examples=[210])
    bed_temp: int | None = Field(default=None, description="Bed temperature in °C.", examples=[50])


class ExternalMaterialsFile(RootModel):
    root: list[ExternalMaterial]

    def __iter__(self) -> Iterator[ExternalMaterial]:
        """Iterate over the materials."""
        return iter(self.root)

    def __getitem__(self, index: int) -> ExternalMaterial:
        """Get a specific material by index."""
        return self.root[index]


def get_external_db_url() -> str:
    """Get the external database URL from environment variables. Defaults to DEFAULT_EXTERNAL_DB_URL."""
    return os.getenv("EXTERNAL_DB_URL", DEFAULT_EXTERNAL_DB_URL)


def get_external_db_sync_interval() -> int:
    """Get the external database sync interval from environment variables. Defaults to DEFAULT_SYNC_INTERVAL."""
    return int(os.getenv("EXTERNAL_DB_SYNC_INTERVAL", DEFAULT_SYNC_INTERVAL))


class SyncStatus(BaseModel):
    """Status of the last external DB sync operation."""

    last_sync: datetime.datetime | None = Field(
        default=None,
        description="Timestamp of the last completed sync attempt (success or failure).",
    )
    last_sync_success: bool | None = Field(
        default=None,
        description="Whether the last sync attempt succeeded. None if no sync has been attempted.",
    )
    last_sync_duration_seconds: float | None = Field(
        default=None,
        description="Duration of the last sync attempt in seconds.",
    )
    last_sync_filament_count: int | None = Field(
        default=None,
        description="Number of filaments synced in the last successful sync.",
    )
    last_sync_material_count: int | None = Field(
        default=None,
        description="Number of materials synced in the last successful sync.",
    )
    external_db_url: str = Field(
        default="",
        description="The configured external DB URL.",
    )
    last_error: str | None = Field(
        default=None,
        description="Error message from the last failed sync attempt. None if the last sync succeeded.",
    )
    is_syncing: bool = Field(
        default=False,
        description="Whether a sync operation is currently in progress.",
    )


# Module-level sync state
_sync_status = SyncStatus(external_db_url=get_external_db_url())
_sync_lock_instance: asyncio.Lock | None = None


def _get_sync_lock() -> asyncio.Lock:
    """Lazily create the asyncio lock to avoid event loop issues."""
    global _sync_lock_instance  # noqa: PLW0603
    if _sync_lock_instance is None:
        _sync_lock_instance = asyncio.Lock()
    return _sync_lock_instance


def get_sync_status() -> SyncStatus:
    """Return a copy of the current sync status."""
    return _sync_status.model_copy()


async def _download_file(url: str) -> bytes:
    """Download a file from a URL and return the contents as a string.

    Uses a file-based cache.
    """
    async with hishel.AsyncCacheClient(storage=cache_storage, controller=controller) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.read()


def _parse_filaments_from_bytes(data: bytes) -> ExternalFilamentsFile:
    """Parse a bytes string into a list of ExternalFilament objects."""
    return ExternalFilamentsFile.parse_raw(data)


def _parse_materials_from_bytes(data: bytes) -> ExternalMaterialsFile:
    """Parse a bytes string into a list of ExternalMaterial objects."""
    return ExternalMaterialsFile.parse_raw(data)


def _write_to_local_cache(filename: str, data: bytes) -> None:
    """Write data to the local cache."""
    filecache.update_file(filename, data)


def get_filaments_file() -> Path:
    """Get the path to the filaments file."""
    return filecache.get_file("filaments.json")


def get_materials_file() -> Path:
    """Get the path to the materials file."""
    return filecache.get_file("materials.json")


async def _sync() -> None:
    """Download and parse external DB files, writing them to the local cache.

    On success, the local cache files are updated atomically.
    On failure, the old cache files are left untouched and the error is recorded in the sync status.
    """
    global _sync_status  # noqa: PLW0603

    url = get_external_db_url()
    _sync_status = _sync_status.model_copy(update={"external_db_url": url, "is_syncing": True})
    start_time = time.monotonic()

    try:
        logger.info("Syncing external DB from %s", url)

        filaments = _parse_filaments_from_bytes(await _download_file(urljoin(url, "filaments.json")))
        materials = _parse_materials_from_bytes(await _download_file(urljoin(url, "materials.json")))

        # Only write to cache after both files are successfully downloaded and parsed.
        # This ensures that a failure mid-sync does not leave the cache in a partially-updated state.
        _write_to_local_cache("filaments.json", filaments.json().encode())
        _write_to_local_cache("materials.json", materials.json().encode())

        duration = time.monotonic() - start_time
        _sync_status = SyncStatus(
            last_sync=datetime.datetime.now(datetime.timezone.utc),
            last_sync_success=True,
            last_sync_duration_seconds=round(duration, 3),
            last_sync_filament_count=len(filaments.root),
            last_sync_material_count=len(materials.root),
            external_db_url=url,
            last_error=None,
            is_syncing=False,
        )

        logger.info(
            "External DB synced. Filaments: %d, Materials: %d (%.2fs)",
            len(filaments.root),
            len(materials.root),
            duration,
        )
    except Exception as exc:
        duration = time.monotonic() - start_time
        error_message = str(exc)
        _sync_status = _sync_status.model_copy(
            update={
                "last_sync": datetime.datetime.now(datetime.timezone.utc),
                "last_sync_success": False,
                "last_sync_duration_seconds": round(duration, 3),
                "last_error": error_message,
                "is_syncing": False,
            }
        )
        logger.exception("Failed to sync external DB: %s", error_message)


async def refresh(timeout: float = 30.0) -> SyncStatus:
    """Manually trigger an external DB sync.

    Args:
        timeout: Maximum time in seconds to wait for the sync to complete.

    Returns:
        The sync status after the refresh completes.

    Raises:
        RuntimeError: If a sync is already in progress.
        asyncio.TimeoutError: If the sync does not complete within the timeout.

    """
    lock = _get_sync_lock()
    if lock.locked():
        raise RuntimeError("A sync is already in progress.")

    async with lock:
        await asyncio.wait_for(_sync(), timeout=timeout)

    return get_sync_status()


def schedule_tasks(scheduler: Scheduler) -> None:
    """Schedule tasks to be executed by the provided scheduler.

    Args:
        scheduler: The scheduler to use for scheduling tasks.

    """
    if len(get_external_db_url().strip()) == 0:
        logger.info("External DB URL is empty. Skipping sync.")
        return

    logger.info("Scheduling external DB sync.")

    # Run once on startup
    scheduler.once(datetime.timedelta(seconds=0), _sync)  # type: ignore[arg-type]

    sync_interval = get_external_db_sync_interval()
    if sync_interval > 0:
        scheduler.cyclic(datetime.timedelta(seconds=DEFAULT_SYNC_INTERVAL), _sync)  # type: ignore[arg-type]
    else:
        logger.info("Sync interval is 0, skipping periodic sync of external db.")
