"""Unit tests for rider merge/alias logic (DB stubbed).

Merging is destructive to how a rider's history reads: pick the wrong
canonical and a kid's race count splits across two profiles, or two different
kids with the same name get fused into one. These cover the selection rules
rather than the SQL itself.
"""

from __future__ import annotations

from piclstats.db import merge


class _Result:
    def __init__(self, rows, rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Records what was executed; returns canned rows."""

    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.statements = []
        self.commits = 0

    def execute(self, stmt, params=None):
        self.statements.append((stmt, params))
        return _Result(self.rows, self.rowcount)

    def commit(self):
        self.commits += 1


def _alias_writes(session):
    """(rider_id, canonical_id, match_method) for each alias insert recorded."""
    writes = []
    for stmt, _params in session.statements:
        compiled = getattr(stmt, "compile", None)
        if compiled is None:
            continue
        p = compiled().params
        if "rider_id" in p and "canonical_id" in p:
            writes.append((p["rider_id"], p["canonical_id"], p.get("match_method")))
    return writes


# ── Candidate grouping ───────────────────────────────────────────────

# (name, rider_id, team, races) — the query orders by name, races DESC, id.
_DUPES = [
    ("Alex Smith", 10, "Ridgeview", 12),
    ("Alex Smith", 11, "Northgate", 3),
    ("Sam Jones", 20, "Eastfield", 8),
    ("Sam Jones", 21, "Eastfield", 5),
    ("Sam Jones", 22, "Westhill", 1),
]


def test_groups_duplicate_names():
    groups = merge.find_auto_merge_candidates(_FakeSession(_DUPES))
    assert [g["name"] for g in groups] == ["Alex Smith", "Sam Jones"]
    assert groups[0]["rider_ids"] == [10, 11]
    assert groups[1]["rider_ids"] == [20, 21, 22]


def test_group_carries_teams_and_race_counts():
    groups = merge.find_auto_merge_candidates(_FakeSession(_DUPES))
    assert groups[0]["teams"] == ["Ridgeview", "Northgate"]
    assert groups[0]["race_counts"] == [12, 3]


def test_single_rider_names_are_not_candidates():
    rows = [("Solo Rider", 30, "Ridgeview", 6)]
    assert merge.find_auto_merge_candidates(_FakeSession(rows)) == []


def test_no_duplicates_at_all():
    assert merge.find_auto_merge_candidates(_FakeSession([])) == []


# ── Auto merge ───────────────────────────────────────────────────────


def test_canonical_is_the_rider_with_most_races():
    session = _FakeSession(_DUPES)
    count = merge.auto_merge(session)
    assert count == 3  # 1 alias for Alex, 2 for Sam
    assert _alias_writes(session) == [
        (11, 10, "auto_name"),
        (21, 20, "auto_name"),
        (22, 20, "auto_name"),
    ]


def test_auto_merge_commits_once():
    session = _FakeSession(_DUPES)
    merge.auto_merge(session)
    assert session.commits == 1


def test_dry_run_writes_nothing():
    session = _FakeSession(_DUPES)
    count = merge.auto_merge(session, dry_run=True)
    assert count == 0
    assert _alias_writes(session) == []
    assert session.commits == 0


def test_auto_merge_with_no_candidates_is_a_no_op():
    session = _FakeSession([])
    assert merge.auto_merge(session) == 0
    assert _alias_writes(session) == []


# ── Manual merge ─────────────────────────────────────────────────────


def test_manual_merge_records_each_alias():
    session = _FakeSession()
    assert merge.manual_merge(session, canonical_id=10, alias_ids=[11, 12]) == 2
    assert _alias_writes(session) == [(11, 10, "manual"), (12, 10, "manual")]
    assert session.commits == 1


def test_manual_merge_skips_aliasing_a_rider_to_itself():
    # Would otherwise create a self-referencing alias row and orphan the rider.
    session = _FakeSession()
    assert merge.manual_merge(session, canonical_id=10, alias_ids=[10, 11]) == 1
    assert _alias_writes(session) == [(11, 10, "manual")]


def test_manual_merge_with_empty_alias_list():
    session = _FakeSession()
    assert merge.manual_merge(session, canonical_id=10, alias_ids=[]) == 0


# ── Unmerge and resolution ───────────────────────────────────────────


def test_unmerge_reports_whether_a_row_went():
    assert merge.unmerge(_FakeSession(rowcount=1), rider_id=11) is True
    assert merge.unmerge(_FakeSession(rowcount=0), rider_id=99) is False


def test_unmerge_commits():
    session = _FakeSession(rowcount=1)
    merge.unmerge(session, rider_id=11)
    assert session.commits == 1


def test_canonical_id_resolves_through_an_alias():
    assert merge.get_canonical_id(_FakeSession([(10,)]), rider_id=11) == 10


def test_canonical_id_of_an_unaliased_rider_is_itself():
    assert merge.get_canonical_id(_FakeSession([]), rider_id=42) == 42


def test_merge_stats_shape():
    session = _FakeSession([(5, 2, 3, 400)])
    assert merge.merge_stats(session) == {
        "aliases": 5,
        "canonical_groups": 2,
        "remaining_dupes": 3,
        "total_riders": 400,
    }
