"""Pure helpers for building the merged location overview.

This module deliberately depends only on the standard library so the merge/ordering
logic can be unit-tested without a database or web framework.

It reproduces the location view that the web client assembles in
``client/src/pages/locations/components/locationContainer.tsx`` from the ``/spool`` list
and the ``locations`` / ``locations_spoolorders`` settings, so that callers no longer
have to stitch those three sources together themselves.
"""

from dataclasses import dataclass

# The default "empty" location. Spools with no location (``location is None``) belong
# here. Matches ``EMPTYLOC`` in the web client.
DEFAULT_LOCATION = ""


@dataclass
class LocationOverview:
    """A single location in the merged overview."""

    name: str
    is_default: bool
    spool_count: int
    spool_orders: list[int]


def _resolve_spool_order(spool_ids: list[int], configured_order: object) -> list[int]:
    """Order the spool ids present in a location.

    Spools whose id appears in ``configured_order`` come first, in that order; the
    remaining spools follow in ascending-id order. This mirrors the ``indexOf``-based
    comparator used by the web client, where ids not found in the configured order are
    pushed to the end.

    ``configured_order`` is the (untrusted) value of ``locations_spoolorders[name]``, so
    non-list values and non-integer entries are ignored.
    """
    index_by_id: dict[int, int] = {}
    sentinel = 0
    if isinstance(configured_order, list):
        # Any value strictly greater than every real position works as the "not found"
        # sentinel; the number of entries is always greater than the largest position.
        sentinel = len(configured_order)
        for position, raw_id in enumerate(configured_order):
            # Only honor integer ids (excluding bool), keeping the first occurrence like
            # JavaScript's Array.indexOf.
            if isinstance(raw_id, int) and not isinstance(raw_id, bool) and raw_id not in index_by_id:
                index_by_id[raw_id] = position

    # ``spool_ids`` is already ascending, so sorting by (configured position, id) keeps
    # the unordered ids in ascending-id order after the configured ones.
    return sorted(spool_ids, key=lambda spool_id: (index_by_id.get(spool_id, sentinel), spool_id))


def _ordered_location_names(
    grouped: dict[str, list[int]],
    first_seen: list[str],
    configured_locations: list[str],
) -> list[str]:
    """Return the location names in display order.

    Order: the default empty location first (only when populated), then the configured
    locations in setting order (de-duplicated, skipping blanks/non-strings), then any
    remaining location present on spools, in first-appearance order.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    # 1. Default empty location first, only when it actually holds spools.
    if DEFAULT_LOCATION in grouped:
        ordered.append(DEFAULT_LOCATION)
        seen.add(DEFAULT_LOCATION)

    # 2. Configured locations, in setting order (deduped, skipping blanks/non-strings).
    if isinstance(configured_locations, list):
        for raw_name in configured_locations:
            if isinstance(raw_name, str) and raw_name != DEFAULT_LOCATION and raw_name not in seen:
                ordered.append(raw_name)
                seen.add(raw_name)

    # 3. Locations present on spools but not configured, in first-appearance order.
    for name in first_seen:
        if name not in seen:
            ordered.append(name)
            seen.add(name)

    return ordered


def build_location_overview(
    configured_locations: list[str],
    locations_spoolorders: dict[str, list[int]],
    spools: list[tuple[int, str | None]],
) -> list[LocationOverview]:
    """Merge configured locations with the real spool distribution.

    Args:
        configured_locations: The (untrusted) value of the ``locations`` setting.
        locations_spoolorders: The (untrusted) value of the ``locations_spoolorders``
            setting, mapping a location name to an ordered list of spool ids.
        spools: ``(id, location)`` of every non-archived spool, in ascending-id order.

    Returns:
        The locations in display order. Each entry carries its non-archived spool count,
        the resolved display order of those spools' ids, and whether it is the default
        empty location. The order is:

        1. the default empty location, but only when it currently holds spools;
        2. every configured location, in setting order, even if it has no spools;
        3. any remaining location that exists on spools but is not configured, in
           ascending-id first-appearance order.

    """
    if not isinstance(locations_spoolorders, dict):
        locations_spoolorders = {}

    # Group non-archived spools by location, preserving the ascending-id order within
    # each group and recording the order in which non-default locations first appear.
    grouped: dict[str, list[int]] = {}
    first_seen: list[str] = []
    for spool_id, location in spools:
        name = location if location is not None else DEFAULT_LOCATION
        bucket = grouped.get(name)
        if bucket is None:
            bucket = []
            grouped[name] = bucket
            if name != DEFAULT_LOCATION:
                first_seen.append(name)
        bucket.append(spool_id)

    ordered_names = _ordered_location_names(grouped, first_seen, configured_locations)

    return [
        LocationOverview(
            name=name,
            is_default=name == DEFAULT_LOCATION,
            spool_count=len(grouped.get(name, [])),
            spool_orders=_resolve_spool_order(grouped.get(name, []), locations_spoolorders.get(name)),
        )
        for name in ordered_names
    ]
