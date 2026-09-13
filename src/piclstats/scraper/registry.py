"""Event registry — maps seasons to raceresult.com event IDs."""

SEASONS: dict[int, list[int]] = {
    2026: [
        410562,  # State Kick-Off - Playin' at Penn College
    ],
    2025: [
        355717,  # State #1 - Playin' at Penn College
        360950,  # State #2 - Grinnin' at Granite
        361172,  # Eastern Gold #1 - Bike Battle at Belmont
        361851,  # Eastern Blue #1 - Bike Battle at Belmont
        361853,  # Western #1 - Ascent at Alameda
        363783,  # Central #1 - Harvest Fields Frenzy
        363307,  # Western #2 - Wild Ride at Wainer
        364676,  # Eastern Blue #2 - Big Send at Blue Mountain
        364678,  # Eastern Gold #2 - Big Send at Blue Mountain
        364389,  # Central #2 - Pedal Fling at Oesterling
        366643,  # State Championships - Hershey Hustle
    ],
    2024: [
        302854,  # State #1 - Penn College
        307899,  # State #2 - Grinnin' at Granite
        309262,  # Central #1 - Pedal Fling Osterling
        309188,  # Western #1 - Boyce Big Ring
        310441,  # Central #2 - Cruisin' at Coleman
        309268,  # Eastern #1 - Flowin' at Fair Hill
        310422,  # Western #2 - Wild Ride at Wainer
        311397,  # Eastern #2 - Big Send at Blue
        312168,  # State Championships - Johnstown Showdown
        309937,  # Exhibition - Johnstown Short Track
    ],
    2023: [
        256112,  # Race #1 Boyce
        259674,  # Race #2 Granite
        261175,  # Race #3 Johnstown
        265527,  # Race #4 Blue Mountain
        266840,  # Race #5 Fair Hill
    ],
    2022: [
        213417,  # Race #1 Granite
        219111,  # Race #2 Johnstown
        219869,  # Race #3 Boyce
        220775,  # Race #4 Blue Mountain
        221776,  # Race #5 Fair Hill Rally
    ],
}

# Patterns to match when scanning config for the individual results list name.
# Tried in order; first match wins.
RESULT_LIST_PATTERNS: list[str] = [
    "Individual Results - ALL",
    "Indiv Race Results",
    "Individual Results - ALL - PICL",
    "Individual Results - PICL",
    "PICL - Individual Results",
    "Individual Race Results",
    "Indiv Results -",
]


def get_events(seasons: tuple[int, ...] | None = None) -> list[tuple[int, int, int]]:
    """Return (season, event_order, event_id) tuples for requested seasons."""
    result: list[tuple[int, int, int]] = []
    for season, ids in sorted(SEASONS.items()):
        if seasons and season not in seasons:
            continue
        for order, event_id in enumerate(ids, start=1):
            result.append((season, order, event_id))
    return result
