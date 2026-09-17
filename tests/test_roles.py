"""Role ranking (pure)."""

from piclstats.web.auth import DEFAULT_ROLE, ROLES, landing_for, role_allows


def test_ranking():
    assert ROLES == ("coach", "picl", "admin")
    assert role_allows("coach", "coach") and not role_allows("coach", "picl")
    assert (
        role_allows("picl", "coach")
        and role_allows("picl", "picl")
        and not role_allows("picl", "admin")
    )
    assert all(role_allows("admin", r) for r in ROLES)


def test_unknown_or_legacy_roles_allow_nothing():
    assert not role_allows("member", "coach")
    assert not role_allows(None, "coach")
    assert DEFAULT_ROLE == "coach"


def test_landing_is_the_first_usable_page():
    assert landing_for("coach") == "/"
    assert landing_for("picl") == "/staging"
    assert landing_for("admin") == "/staging"
    assert landing_for(None) == "/"
