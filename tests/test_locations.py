"""Unit tests for the pure location-overview merge logic in ``spoolman.locations``.

These tests have no third-party dependencies (the module is stdlib-only), so they can be
run directly with ``pytest tests/test_locations.py`` without the full app stack.
"""

from spoolman.locations import DEFAULT_LOCATION, LocationOverview, build_location_overview


def test_empty_everything():
    """No configured locations and no spools yields an empty overview."""
    assert build_location_overview([], {}, []) == []


def test_default_location_first_only_when_populated():
    """The default empty location appears first, and only when it holds spools."""
    overview = build_location_overview(
        configured_locations=["Shelf A"],
        locations_spoolorders={},
        spools=[(1, None), (2, "Shelf A"), (3, None)],
    )
    assert overview == [
        LocationOverview(name=DEFAULT_LOCATION, is_default=True, spool_count=2, spool_orders=[1, 3]),
        LocationOverview(name="Shelf A", is_default=False, spool_count=1, spool_orders=[2]),
    ]


def test_no_default_location_when_no_unlocated_spools():
    """With every spool located, there is no default empty entry."""
    overview = build_location_overview(
        configured_locations=[],
        locations_spoolorders={},
        spools=[(1, "A"), (2, "A")],
    )
    assert overview == [
        LocationOverview(name="A", is_default=False, spool_count=2, spool_orders=[1, 2]),
    ]
    assert all(not entry.is_default for entry in overview)


def test_configured_location_with_zero_spools_is_kept():
    """A configured location with no spools is still returned, with count 0."""
    overview = build_location_overview(
        configured_locations=["Printer 1", "Printer 2"],
        locations_spoolorders={},
        spools=[(1, "Printer 1")],
    )
    assert overview == [
        LocationOverview(name="Printer 1", is_default=False, spool_count=1, spool_orders=[1]),
        LocationOverview(name="Printer 2", is_default=False, spool_count=0, spool_orders=[]),
    ]


def test_unconfigured_locations_appended_in_ascending_id_first_appearance():
    """Locations found only on spools come after configured ones, ordered by first id."""
    overview = build_location_overview(
        configured_locations=[],
        locations_spoolorders={},
        spools=[(1, "Zebra"), (2, "Apple"), (3, "Zebra")],
    )
    # "Zebra" first appears at id 1, "Apple" at id 2 -> Zebra before Apple (not alphabetical).
    assert [entry.name for entry in overview] == ["Zebra", "Apple"]
    assert overview == [
        LocationOverview(name="Zebra", is_default=False, spool_count=2, spool_orders=[1, 3]),
        LocationOverview(name="Apple", is_default=False, spool_count=1, spool_orders=[2]),
    ]


def test_per_location_order_honors_spoolorders_with_unlisted_last():
    """Configured spool order wins; unlisted ids fall to the end in ascending-id order."""
    overview = build_location_overview(
        configured_locations=["Box"],
        locations_spoolorders={"Box": [3, 1]},
        spools=[(1, "Box"), (2, "Box"), (3, "Box"), (4, "Box")],
    )
    assert overview == [
        LocationOverview(name="Box", is_default=False, spool_count=4, spool_orders=[3, 1, 2, 4]),
    ]


def test_stale_ids_in_spoolorders_are_ignored():
    """Ids in the configured order that are not present are simply skipped."""
    overview = build_location_overview(
        configured_locations=["Shelf A"],
        locations_spoolorders={"Shelf A": [10, 5]},  # neither id is present
        spools=[(1, "Shelf A"), (2, "Shelf A")],
    )
    assert overview == [
        LocationOverview(name="Shelf A", is_default=False, spool_count=2, spool_orders=[1, 2]),
    ]


def test_full_merge_scenario():
    """A combined scenario exercising every ordering rule at once."""
    overview = build_location_overview(
        configured_locations=["Printer 1", "Printer 2", "Shelf A"],
        locations_spoolorders={"Printer 1": [3, 1], "Shelf A": [10]},
        spools=[
            (1, "Printer 1"),
            (2, "Storage"),  # unconfigured
            (3, "Printer 1"),
            (4, None),  # default empty
            (5, "Printer 2"),
            (6, None),  # default empty
            (7, "Storage"),  # unconfigured (already seen at id 2)
        ],
    )
    assert overview == [
        LocationOverview(name=DEFAULT_LOCATION, is_default=True, spool_count=2, spool_orders=[4, 6]),
        LocationOverview(name="Printer 1", is_default=False, spool_count=2, spool_orders=[3, 1]),
        LocationOverview(name="Printer 2", is_default=False, spool_count=1, spool_orders=[5]),
        LocationOverview(name="Shelf A", is_default=False, spool_count=0, spool_orders=[]),
        LocationOverview(name="Storage", is_default=False, spool_count=2, spool_orders=[2, 7]),
    ]
    # Invariant: spool_count always equals the number of ordered ids.
    for entry in overview:
        assert entry.spool_count == len(entry.spool_orders)


def test_robust_against_malformed_settings():
    """Non-string configured entries and malformed spool orders are tolerated."""
    overview = build_location_overview(
        configured_locations=["Good", 5, None, "", "Good"],  # type: ignore[list-item]
        locations_spoolorders={"Good": ["x", 2, True, 1]},  # type: ignore[dict-item]
        spools=[(1, "Good"), (2, "Good"), (3, "Good")],
    )
    # Only "Good" is a valid configured location; no spurious entries from 5/None/""/dup.
    # In the order list, "x" and True are ignored, leaving {2: pos1, 1: pos3}; id 3 is
    # unlisted and falls to the end -> [2, 1, 3].
    assert overview == [
        LocationOverview(name="Good", is_default=False, spool_count=3, spool_orders=[2, 1, 3]),
    ]


def test_robust_against_wrong_top_level_types():
    """Wrong top-level setting types fall back to no configuration without crashing."""
    overview = build_location_overview(
        configured_locations=None,  # type: ignore[arg-type]
        locations_spoolorders=None,  # type: ignore[arg-type]
        spools=[(1, None), (2, "A")],
    )
    assert overview == [
        LocationOverview(name=DEFAULT_LOCATION, is_default=True, spool_count=1, spool_orders=[1]),
        LocationOverview(name="A", is_default=False, spool_count=1, spool_orders=[2]),
    ]
