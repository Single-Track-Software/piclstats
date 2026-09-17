"""Guards on admin user actions (pure)."""

from piclstats.web.admin import user_action_block

ME = {"id": 1, "role": "admin", "is_active": True}
OTHER_ADMIN = {"id": 2, "role": "admin", "is_active": True}
MEMBER = {"id": 3, "role": "coach", "is_active": True}


def test_cannot_demote_or_deactivate_self():
    assert user_action_block("set_role", "coach", ME, ME, 2)
    assert user_action_block("deactivate", None, ME, ME, 2)


def test_self_promote_to_admin_is_harmless():
    assert user_action_block("set_role", "admin", ME, ME, 2) is None


def test_last_admin_is_protected():
    assert user_action_block("deactivate", None, OTHER_ADMIN, ME, 1)
    assert user_action_block("set_role", "picl", OTHER_ADMIN, ME, 1)
    assert user_action_block("deactivate", None, OTHER_ADMIN, ME, 2) is None


def test_members_are_unaffected():
    assert user_action_block("deactivate", None, MEMBER, ME, 1) is None
    assert user_action_block("set_role", "admin", MEMBER, ME, 1) is None
    assert user_action_block("activate", None, MEMBER, ME, 1) is None
    assert user_action_block("send_reset", None, MEMBER, ME, 1) is None
