"""The taxonomy is the contract between the detector, the admin, and Discord."""

import pytest

from aa_altcorp.alerts import taxonomy as tx


def test_every_alert_type_has_a_direction():
    assert set(tx.DIRECTION_FOR_ALERT) == set(tx.AlertType)


def test_conditions_two_and_three_share_the_present_direction():
    """They differ only in *why* the entity should not have access, so an
    exemption created for one must keep suppressing if the other takes over."""
    assert tx.direction_for(tx.AlertType.PRESENT_FOR_INVALID_USER) is tx.Direction.PRESENT
    assert tx.direction_for(tx.AlertType.PRESENT_WITHOUT_AUTH_USER) is tx.Direction.PRESENT
    assert tx.direction_for(tx.AlertType.MISSING_FOR_VALID_USER) is tx.Direction.MISSING


def test_direction_for_accepts_a_plain_string():
    assert tx.direction_for("missing_for_valid_user") is tx.Direction.MISSING


def test_every_creating_action_maps_to_facets_and_a_kind():
    creating = set(tx.ActionType) - {tx.ActionType.REMOVE_EXEMPTION}
    assert set(tx.FACET_FOR_ACTION) == creating
    assert set(tx.KIND_FOR_ACTION) == creating


def test_remove_exemption_creates_nothing():
    assert tx.facets_for_action(tx.ActionType.REMOVE_EXEMPTION) == ()
    assert tx.ActionType.REMOVE_EXEMPTION not in tx.KIND_FOR_ACTION


@pytest.mark.parametrize(
    ("action", "facets"),
    [
        (tx.ActionType.TEMPORARY_BLUE, (tx.FacetType.CONTACT,)),
        (tx.ActionType.TEMPORARY_ACL_EXEMPTION, (tx.FacetType.ACL,)),
        (tx.ActionType.MARK_EXEMPT, (tx.FacetType.CONTACT, tx.FacetType.ACL)),
    ],
)
def test_action_facet_mapping(action, facets):
    assert tx.facets_for_action(action) == facets


def test_only_the_temporary_actions_require_an_expiry():
    assert tx.EXPIRY_REQUIRED == {
        tx.ActionType.TEMPORARY_BLUE,
        tx.ActionType.TEMPORARY_ACL_EXEMPTION,
    }


def test_action_codes_are_unique_and_short():
    assert set(tx.ACTION_CODES) == set(tx.ActionType)
    assert len(set(tx.ACTION_CODES.values())) == len(tx.ActionType)
    assert all(len(code) == 2 for code in tx.ACTION_CODES.values())
    assert tx.CODE_TO_ACTION == {c: a for a, c in tx.ACTION_CODES.items()}


def test_every_alert_type_has_a_human_label():
    assert set(tx.ALERT_LABELS) == set(tx.AlertType)
    assert all(tx.ALERT_LABELS[t].strip() for t in tx.AlertType)
