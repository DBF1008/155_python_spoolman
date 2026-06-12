"""A file-based cache system for reading/writing files."""

from pathlib import Path

from spoolman.env import get_cache_dir


def get_file(name: str) -> Path:
    """Get the path to a file in the cache dir."""
    return get_cache_dir() / name


def update_file(name: str, data: bytes) -> None:
    """Update a file if it differs from the given data.

    Uses a temporary file + atomic rename to prevent readers from seeing partial data.
    """
    path = get_file(name)
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def get_file_contents(name: str) -> bytes:
    """Get the contents of a file."""
    path = get_file(name)
    return path.read_bytes()
