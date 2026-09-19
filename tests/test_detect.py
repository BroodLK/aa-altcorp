"""The 3x2 classification matrix, plus the facet-building rules."""

import pytest

from aa_altcorp.alerts import detect, taxonomy
from aa_altcorp.models import AccessListPolicy

from .conftest import ContactRow

CORP = taxonomy.EntityType.CORPORATION
CHAR = taxonomy.EntityType.CHARACTER
ALLIANCE = taxonomy.EntityType.ALLIANCE


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


# -- the classification matrix ---------------------------------------------


def test_valid_user_with_standing_produces_no_alert(
    alert_settings, stub_aa_contacts, make_snapshot
):
    stub_aa_contacts(alliance_rows=[ContactRow(98000001, CORP, 10.0, "Blue Corp")])
    snapshot = make_snapshot(characters=[_member(95000001, 1)], approved_user_ids=[1])

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    assert [c for c in candidates if c.entity_id == 98000001] == []


def test_valid_user_missing_standing_is_condition_one(
    alert_settings, stub_aa_contacts, make_snapshot
):
    stub_aa_contacts(alliance_rows=[])
    snapshot = make_snapshot(characters=[_member(95000001, 1)], approved_user_ids=[1])

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    character = next(c for c in candidates if c.entity_type == CHAR)
    assert character.alert_type == taxonomy.AlertType.MISSING_FOR_VALID_USER
    assert [f.facet_type for f in character.facets] == [taxonomy.FacetType.CONTACT]
    assert "missing contact standings" in character.summary


def test_invalid_user_with_standing_is_condition_two(
    alert_settings, stub_aa_contacts, make_snapshot
):
    stub_aa_contacts(alliance_rows=[ContactRow(98000001, CORP, 10.0, "Lapsed Corp")])
    # The owner exists but is not in an approved state.
    snapshot = make_snapshot(
        characters=[_member(95000001, 1, state_name="Guest")], approved_user_ids=[]
    )

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    corp = next(c for c in candidates if c.entity_id == 98000001)
    assert corp.alert_type == taxonomy.AlertType.PRESENT_FOR_INVALID_USER
    assert corp.detail["states"] == ["Guest"]


def test_unassociated_entity_with_standing_is_condition_three(
    alert_settings, stub_aa_contacts, make_snapshot
):
    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    snapshot = make_snapshot()

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    corp = next(c for c in candidates if c.entity_id == 98009999)
    assert corp.alert_type == taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER
    assert corp.detail["users"] == []


def test_invalid_user_without_standing_produces_no_alert(
    alert_settings, stub_aa_contacts, make_snapshot
):
    """They should not have standing anyway, so its absence is not a problem."""
    stub_aa_contacts(alliance_rows=[])
    snapshot = make_snapshot(
        characters=[_member(95000001, 1, state_name="Guest")], approved_user_ids=[]
    )

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    assert candidates == []


def test_unknown_entity_without_standing_produces_no_alert(
    alert_settings, stub_aa_contacts, make_snapshot
):
    stub_aa_contacts(alliance_rows=[])

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    assert candidates == []


# -- contact facts ----------------------------------------------------------


def test_a_corporation_alert_covers_both_facets(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    acl_row(corporations=[{"corporation_id": 98009999, "access": "Allowed"}])
    AccessListPolicy.objects.create(access_list_id=7001)

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    corp = next(c for c in candidates if c.entity_id == 98009999)
    assert {f.facet_type for f in corp.facets} == {
        taxonomy.FacetType.CONTACT,
        taxonomy.FacetType.ACL,
    }
    assert "contact standings and ACL access" in corp.summary


@pytest.mark.parametrize("treat_as_removed", [True, False])
def test_zero_standing_is_configurable(
    alert_settings, stub_aa_contacts, make_snapshot, treat_as_removed
):
    """aa-contacts writes 0.0 for contacts it could not delete, so 0.0 is ambiguous."""
    alert_settings.treat_zero_standing_as_removed = treat_as_removed
    alert_settings.save()
    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 0.0, "Zeroed")])

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    # 0.0 is below the 0.1 floor either way, so it never counts as blue.
    assert candidates == []


def test_factions_are_ignored(alert_settings, stub_aa_contacts, make_snapshot):
    """Factions have no Auth association, so none of the three conditions apply."""
    stub_aa_contacts(alliance_rows=[ContactRow(500001, "faction", 10.0, "Caldari State")])

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    assert candidates == []


def test_missing_contacts_marks_the_source_unavailable(
    alert_settings, no_aa_contacts, make_snapshot
):
    candidates, availability = detect.evaluate(alert_settings, make_snapshot())

    assert candidates == []
    assert availability.contacts is False


# -- ACL facts --------------------------------------------------------------


def test_blocked_and_unspecified_are_not_access(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    stub_aa_contacts(alliance_rows=[])
    acl_row(
        characters=[
            {"character_id": 95000002, "access": "Blocked"},
            {"character_id": 95000003, "access": "Unspecified"},
        ]
    )

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    assert candidates == []


def test_one_acl_seen_through_two_characters_yields_one_facet(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    stub_aa_contacts(alliance_rows=[])
    entry = [{"corporation_id": 98009999, "access": "Allowed"}]
    acl_row(access_list_id=7001, character_id=95000001, corporations=entry)
    acl_row(access_list_id=7001, character_id=95000002, corporations=entry)
    AccessListPolicy.objects.create(access_list_id=7001)

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    corp = next(c for c in candidates if c.entity_id == 98009999)
    assert len(corp.facets) == 1
    assert corp.facets[0].access_list_id == 7001


def test_no_policy_means_no_missing_access_alerts(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    """Without a policy the app has no basis to claim anyone should be on an ACL."""
    stub_aa_contacts(alliance_rows=[ContactRow(98000001, CORP, 10.0, "Blue Corp")])
    acl_row(characters=[])
    snapshot = make_snapshot(characters=[_member(95000001, 1)], approved_user_ids=[1])

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    assert all(
        facet.facet_type != taxonomy.FacetType.ACL
        for candidate in candidates
        for facet in candidate.facets
    )


def test_policy_enables_missing_access_alerts(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    stub_aa_contacts(alliance_rows=[ContactRow(98000001, CORP, 10.0, "Blue Corp")])
    acl_row(access_list_id=7001, characters=[])
    AccessListPolicy.objects.create(
        access_list_id=7001, name="Capital Staging", expect_positive_contacts=True
    )
    snapshot = make_snapshot(characters=[_member(95000001, 1)], approved_user_ids=[1])

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    corp = next(c for c in candidates if c.entity_id == 98000001)
    assert corp.alert_type == taxonomy.AlertType.MISSING_FOR_VALID_USER
    acl_facets = [f for f in corp.facets if f.facet_type == taxonomy.FacetType.ACL]
    assert [f.access_list_id for f in acl_facets] == [7001]
    assert "blue contact" in acl_facets[0].reason_text


def test_allow_everyone_suppresses_the_missing_family(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    stub_aa_contacts(alliance_rows=[ContactRow(98000001, CORP, 10.0, "Blue Corp")])
    acl_row(access_list_id=7001, allow_everyone=True)
    AccessListPolicy.objects.create(access_list_id=7001, expect_positive_contacts=True)
    snapshot = make_snapshot(characters=[_member(95000001, 1)], approved_user_ids=[1])

    candidates, availability = detect.evaluate(alert_settings, snapshot)

    assert availability.skipped.get("acl_allow_everyone") == 1
    assert all(
        facet.facet_type != taxonomy.FacetType.ACL
        for candidate in candidates
        for facet in candidate.facets
    )


def test_present_acl_access_alerts_without_any_policy(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    """Unconfigured ACLs are outside the opt-in monitoring scope."""
    stub_aa_contacts(alliance_rows=[])
    acl_row(
        access_list_id=7001,
        name="Staging",
        characters=[{"character_id": 95009999, "access": "Admin"}],
    )

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    assert candidates == []


# -- entity scope -----------------------------------------------------------


def test_standing_target_is_never_expected_to_be_its_own_contact(
    alert_settings, stub_aa_contacts, make_snapshot
):
    alert_settings.expect_contact_alliances = True
    alert_settings.save()
    stub_aa_contacts(alliance_rows=[])
    snapshot = make_snapshot(
        characters=[_member(95000001, 1, alliance_id=99005338)], approved_user_ids=[1]
    )

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    assert all(c.entity_type != ALLIANCE for c in candidates)


def test_alliance_tier_is_off_by_default(alert_settings, stub_aa_contacts, make_snapshot):
    stub_aa_contacts(alliance_rows=[])
    snapshot = make_snapshot(
        characters=[_member(95000001, 1, alliance_id=99001111)], approved_user_ids=[1]
    )

    candidates, _ = detect.evaluate(alert_settings, snapshot)

    assert all(c.entity_type != ALLIANCE for c in candidates)


def test_expect_all_owned_characters_widens_the_scope(
    alert_settings, stub_aa_contacts, make_snapshot
):
    stub_aa_contacts(alliance_rows=[])
    alt = _member(95000002, 1, is_main=False)
    snapshot = make_snapshot(characters=[_member(95000001, 1), alt], approved_user_ids=[1])

    narrow, _ = detect.evaluate(alert_settings, snapshot)
    assert 95000002 not in {c.entity_id for c in narrow}

    alert_settings.expect_all_owned_characters = True
    alert_settings.save()
    wide, _ = detect.evaluate(alert_settings, snapshot)
    assert 95000002 in {c.entity_id for c in wide}


def test_alert_unexpected_false_suppresses_present_acl_facets(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    """The operator has said unjustified access on this list is not worth alerting."""
    stub_aa_contacts(alliance_rows=[])
    acl_row(
        access_list_id=7001,
        name="Staging",
        characters=[{"character_id": 95009999, "access": "Admin"}],
    )
    AccessListPolicy.objects.create(
        access_list_id=7001,
        alert_unexpected=False,
        alert_missing=False,
        expect_positive_contacts=False,
    )

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    # The ACL facet was this entity's only problem, so the alert disappears.
    assert candidates == []


def test_a_disabled_policy_still_reports_present_acl_access(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    """A disabled policy must behave exactly like no policy, not like a silencer."""
    stub_aa_contacts(alliance_rows=[])
    acl_row(
        access_list_id=7001,
        name="Staging",
        characters=[{"character_id": 95009999, "access": "Admin"}],
    )
    AccessListPolicy.objects.create(access_list_id=7001, enabled=False, alert_unexpected=False)

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    assert candidates == []


def test_alert_unexpected_false_narrows_a_dual_facet_alert(
    alert_settings, stub_aa_contacts, make_snapshot, acl_row
):
    """Exempting the ACL axis must leave the contact axis alerting, and reword."""
    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    acl_row(
        access_list_id=7001,
        name="Staging",
        corporations=[{"corporation_id": 98009999, "access": "Allowed"}],
    )
    AccessListPolicy.objects.create(
        access_list_id=7001,
        alert_unexpected=False,
        alert_missing=False,
        expect_positive_contacts=False,
    )

    candidates, _ = detect.evaluate(alert_settings, make_snapshot())

    corp = next(c for c in candidates if c.entity_id == 98009999)
    assert [f.facet_type for f in corp.facets] == [taxonomy.FacetType.CONTACT]
    assert corp.summary == "Third Party has contact standings"
