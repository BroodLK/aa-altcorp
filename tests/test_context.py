"""AuthSnapshot association lookups and the no-Alliance-Auth fallback."""

from types import SimpleNamespace

from aa_altcorp.alerts.context import AuthSnapshot, CharacterFacts, _as_int


def test_from_auth_degrades_without_alliance_auth():
    """Under both test settings modules allianceauth is not importable."""
    snapshot = AuthSnapshot.from_auth(SimpleNamespace(approved_states=["Member"]))

    assert snapshot.available is False
    assert snapshot.characters == {}
    assert snapshot.approved_user_ids == set()


def test_users_for_resolves_each_tier(make_snapshot):
    snapshot = make_snapshot(
        characters=[
            {
                "character_id": 95000001,
                "character_name": "Pilot",
                "user_id": 7,
                "state_name": "Member",
                "corporation_id": 98000001,
                "alliance_id": 99001111,
                "is_main": True,
            }
        ],
        approved_user_ids=[7],
    )

    assert snapshot.users_for("character", 95000001) == {7}
    assert snapshot.users_for("corporation", 98000001) == {7}
    assert snapshot.users_for("alliance", 99001111) == {7}
    assert snapshot.users_for("faction", 500001) == set()
    assert snapshot.users_for("character", 99999999) == set()


def test_has_approved_user_is_true_if_any_owner_qualifies():
    """A corporation with one lapsed member among nine current ones is still fine."""
    snapshot = AuthSnapshot()
    snapshot.corporations[98000001] = {1, 2}
    snapshot.approved_user_ids = {2}

    assert snapshot.has_approved_user("corporation", 98000001) is True
    assert snapshot.is_associated("corporation", 98000001) is True

    snapshot.approved_user_ids = set()
    assert snapshot.has_approved_user("corporation", 98000001) is False


def test_state_names_are_reported_for_the_alert_detail():
    snapshot = AuthSnapshot()
    snapshot.corporations[98000001] = {1, 2}
    snapshot.user_states = {1: "Guest", 2: "Member"}

    assert snapshot.state_names_for("corporation", 98000001) == ["Guest", "Member"]


def test_a_character_without_an_owner_has_no_users():
    snapshot = AuthSnapshot()
    snapshot.characters[95000001] = CharacterFacts(character_id=95000001)

    assert snapshot.users_for("character", 95000001) == set()


def test_as_int_tolerates_junk():
    assert _as_int("42") == 42
    assert _as_int(None) is None
    assert _as_int("not-a-number") is None
