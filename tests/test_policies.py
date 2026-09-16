"""AccessListPolicy rule resolution, and the contact expectation set."""

from types import SimpleNamespace

import pytest

from aa_altcorp.alerts import expect, policies, taxonomy

CHAR = taxonomy.EntityType.CHARACTER
CORP = taxonomy.EntityType.CORPORATION
ALLIANCE = taxonomy.EntityType.ALLIANCE


def _policy(**overrides):
    defaults = {
        "enabled": True,
        "expect_positive_contacts": False,
        "minimum_standing": 0.1,
        "expect_states": [],
        "expect_groups": [],
        "expect_corporations": [],
        "expect_alliances": [],
        "expect_entities": [],
        "expect_mains_only": False,
        "expected_tier": CHAR,
        "alert_missing": True,
        "alert_unexpected": True,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _member(character_id, user_id, corporation_id=98000001, **extra):
    return {
        "character_id": character_id,
        "character_name": f"Pilot {character_id}",
        "user_id": user_id,
        "state_name": extra.pop("state_name", "Member"),
        "corporation_id": corporation_id,
        "alliance_id": extra.pop("alliance_id", None),
        "is_main": extra.pop("is_main", True),
    }


# -- expected_membership ----------------------------------------------------


def test_a_disabled_policy_expects_nobody(make_snapshot):
    resolved = policies.expected_membership(
        _policy(enabled=False, expect_positive_contacts=True),
        make_snapshot(),
        {(CORP, 98000001): 10.0},
    )

    assert not resolved


def test_no_policy_expects_nobody(make_snapshot):
    assert not policies.expected_membership(None, make_snapshot(), {(CORP, 1): 10.0})


def test_positive_contacts_land_at_their_own_tier(make_snapshot):
    blue = {(CORP, 98000001): 10.0, (CHAR, 95000001): 5.0, (ALLIANCE, 99001111): 7.5}

    resolved = policies.expected_membership(
        _policy(expect_positive_contacts=True), make_snapshot(), blue
    )

    assert 98000001 in resolved.corporations
    assert 95000001 in resolved.characters
    assert 99001111 in resolved.alliances
    assert "standing 10" in resolved.corporations[98000001]


def test_the_policy_standing_floor_is_applied(make_snapshot):
    blue = {(CORP, 1): 10.0, (CORP, 2): 1.0}

    resolved = policies.expected_membership(
        _policy(expect_positive_contacts=True, minimum_standing=5.0), make_snapshot(), blue
    )

    assert list(resolved.corporations) == [1]


def test_state_rules_resolve_to_characters(make_snapshot):
    snapshot = make_snapshot(
        characters=[_member(95000001, 1), _member(95000002, 2, state_name="Guest")],
        approved_user_ids=[1],
    )

    resolved = policies.expected_membership(_policy(expect_states=["Member"]), snapshot, {})

    assert list(resolved.characters) == [95000001]
    assert resolved.characters[95000001] == "state: Member"


def test_group_rules_resolve_to_characters(make_snapshot):
    snapshot = make_snapshot(
        characters=[_member(95000001, 1), _member(95000002, 2)],
        approved_user_ids=[1, 2],
        user_groups={1: {"Capitals"}},
    )

    resolved = policies.expected_membership(_policy(expect_groups=["Capitals"]), snapshot, {})

    assert list(resolved.characters) == [95000001]
    assert resolved.characters[95000001] == "group: Capitals"


def test_mains_only_excludes_alts(make_snapshot):
    snapshot = make_snapshot(
        characters=[_member(95000001, 1), _member(95000002, 1, is_main=False)],
        approved_user_ids=[1],
    )

    resolved = policies.expected_membership(
        _policy(expect_states=["Member"], expect_mains_only=True), snapshot, {}
    )

    assert list(resolved.characters) == [95000001]


def test_a_corporation_rule_expands_to_its_members(make_snapshot):
    snapshot = make_snapshot(
        characters=[_member(95000001, 1), _member(95000002, 2, corporation_id=98000002)],
        approved_user_ids=[1, 2],
    )

    resolved = policies.expected_membership(
        _policy(expect_corporations=[98000001], expected_tier=CHAR), snapshot, {}
    )

    assert list(resolved.characters) == [95000001]
    assert "member of corporation 98000001" in resolved.characters[95000001]


def test_a_corporation_rule_can_stay_at_its_own_tier(make_snapshot):
    resolved = policies.expected_membership(
        _policy(expect_corporations=[98000001], expected_tier=CORP), make_snapshot(), {}
    )

    assert list(resolved.corporations) == [98000001]
    assert resolved.characters == {}


def test_an_alliance_rule_expands_to_its_members(make_snapshot):
    snapshot = make_snapshot(
        characters=[_member(95000001, 1, alliance_id=99001111)], approved_user_ids=[1]
    )

    resolved = policies.expected_membership(
        _policy(expect_alliances=[99001111], expected_tier=CHAR), snapshot, {}
    )

    assert list(resolved.characters) == [95000001]


def test_explicit_entities_are_taken_verbatim(make_snapshot):
    resolved = policies.expected_membership(
        _policy(
            expect_entities=[
                {"entity_type": CORP, "entity_id": 98009999, "name": "Partner"},
                {"entity_type": CHAR, "entity_id": 95009999},
            ]
        ),
        make_snapshot(),
        {},
    )

    assert list(resolved.corporations) == [98009999]
    assert list(resolved.characters) == [95009999]


@pytest.mark.parametrize(
    "entry",
    [{"entity_id": 1}, {"entity_type": CORP}, {}, {"entity_type": "faction", "entity_id": 1}],
)
def test_malformed_explicit_entries_are_ignored(make_snapshot, entry):
    resolved = policies.expected_membership(_policy(expect_entities=[entry]), make_snapshot(), {})

    assert not resolved


def test_non_numeric_ids_in_a_rule_are_ignored(make_snapshot):
    resolved = policies.expected_membership(
        _policy(expect_corporations=["not-an-id"], expected_tier=CORP), make_snapshot(), {}
    )

    assert not resolved


def test_for_tier_is_empty_for_an_unknown_tier():
    assert policies.ExpectedAccess().for_tier("faction") == {}


# -- expected_contacts ------------------------------------------------------


def _settings(**overrides):
    defaults = {
        "standing_target_type": "alliance",
        "standing_target_id": 99005338,
        "expect_contact_characters": True,
        "expect_contact_corporations": True,
        "expect_contact_alliances": False,
        "expect_all_owned_characters": False,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def test_no_auth_means_no_expectations(make_snapshot):
    """Without association data nothing can be claimed to be expected."""
    from aa_altcorp.alerts.context import AuthSnapshot

    assert expect.expected_contacts(_settings(), AuthSnapshot.empty()) == {}


def test_only_valid_state_users_create_expectations(db, make_snapshot):
    snapshot = make_snapshot(
        characters=[_member(95000001, 1), _member(95000002, 2, corporation_id=98000002)],
        approved_user_ids=[1],
    )

    expected = expect.expected_contacts(_settings(), snapshot)

    assert (CHAR, 95000001) in expected
    assert (CHAR, 95000002) not in expected
    assert (CORP, 98000002) not in expected


def test_tier_toggles_switch_whole_tiers_off(db, make_snapshot):
    snapshot = make_snapshot(
        characters=[_member(95000001, 1, alliance_id=99001111)], approved_user_ids=[1]
    )

    characters_only = expect.expected_contacts(
        _settings(expect_contact_corporations=False), snapshot
    )
    assert [key[0] for key in characters_only] == [CHAR]

    with_alliances = expect.expected_contacts(_settings(expect_contact_alliances=True), snapshot)
    assert (ALLIANCE, 99001111) in with_alliances


def test_attached_alt_corporations_are_expected(db, make_snapshot):
    from django.contrib.auth.models import User

    from aa_altcorp.models import AltCorporation

    user = User.objects.create_user("pilot")
    AltCorporation.objects.create(
        user=user, corporation_id=98009999, corporation_name="Alt Holdings"
    )
    snapshot = make_snapshot(characters=[_member(95000001, user.pk)], approved_user_ids=[user.pk])

    expected = expect.expected_contacts(_settings(), snapshot)

    assert "Alt Holdings" in expected[(CORP, 98009999)]
