"""Actions, exemption matching, and partial suppression."""

from datetime import timedelta

import pytest
from django.utils import timezone

from aa_altcorp.alerts import dedup, taxonomy
from aa_altcorp.discord import actions
from aa_altcorp.models import AlertActionLog, Exemption

BOTH_FACETS = (
    (taxonomy.FacetType.CONTACT, None, ""),
    (taxonomy.FacetType.ACL, 7001, "Capital Staging"),
)


# -- available actions ------------------------------------------------------


def test_actions_follow_the_open_facets(make_alert):
    alert = make_alert(facets=BOTH_FACETS)

    assert actions.available_actions(alert) == (
        taxonomy.ActionType.MARK_EXEMPT,
        taxonomy.ActionType.TEMPORARY_BLUE,
        taxonomy.ActionType.TEMPORARY_ACL_EXEMPTION,
    )
    assert actions.action_label(alert, "MARK_EXEMPT") == "Mark Both Exempt"


def test_a_contact_only_alert_offers_no_acl_action(make_alert):
    alert = make_alert(facets=((taxonomy.FacetType.CONTACT, None, ""),))

    assert taxonomy.ActionType.TEMPORARY_ACL_EXEMPTION not in actions.available_actions(alert)
    assert actions.action_label(alert, "MARK_EXEMPT") == "Mark Exempt"


# -- partial suppression ----------------------------------------------------


def test_temporary_blue_narrows_a_two_facet_alert(make_alert, actor):
    """The whole point of facets: one action must not silence the other problem."""
    alert = make_alert(facets=BOTH_FACETS)

    created, log = actions.apply_action(
        alert, "TEMPORARY_BLUE", reason="Trial blue", expiry_text="7d", actor=actor
    )

    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.OPEN
    assert len(created) == 1
    assert created[0].facet_type == taxonomy.FacetType.CONTACT
    contact = alert.facets.get(facet_type=taxonomy.FacetType.CONTACT)
    acl = alert.facets.get(facet_type=taxonomy.FacetType.ACL)
    assert contact.state == taxonomy.AlertState.SUPPRESSED
    assert acl.state == taxonomy.AlertState.OPEN
    # The buttons narrow with it.
    assert actions.available_actions(alert) == (
        taxonomy.ActionType.MARK_EXEMPT,
        taxonomy.ActionType.TEMPORARY_ACL_EXEMPTION,
        taxonomy.ActionType.REMOVE_EXEMPTION,
    )
    assert log.action == taxonomy.ActionType.TEMPORARY_BLUE


def test_covering_the_last_facet_suppresses_the_alert(make_alert, actor):
    alert = make_alert(facets=BOTH_FACETS)
    actions.apply_action(alert, "TEMPORARY_BLUE", reason="Trial", expiry_text="7d", actor=actor)
    actions.apply_action(
        alert, "TEMPORARY_ACL_EXEMPTION", reason="Approved", expiry_text="30d", actor=actor
    )

    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.SUPPRESSED


def test_mark_exempt_covers_every_open_facet(make_alert, actor):
    alert = make_alert(facets=BOTH_FACETS)

    created, _ = actions.apply_action(alert, "MARK_EXEMPT", reason="Approved", actor=actor)

    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.SUPPRESSED
    assert {e.facet_type for e in created} == {
        taxonomy.FacetType.CONTACT,
        taxonomy.FacetType.ACL,
    }


def test_acl_exemptions_are_created_one_per_list(make_alert, actor):
    """Per-ACL rows behind a single click, so a new ACL is not covered later."""
    alert = make_alert(
        facets=(
            (taxonomy.FacetType.ACL, 7001, "Staging"),
            (taxonomy.FacetType.ACL, 7002, "Hangar"),
        )
    )

    created, _ = actions.apply_action(
        alert, "TEMPORARY_ACL_EXEMPTION", reason="Approved", expiry_text="30d", actor=actor
    )

    assert sorted(e.access_list_id for e in created) == [7001, 7002]


# -- the audit trail --------------------------------------------------------


def test_every_audit_field_is_recorded(make_alert, actor):
    alert = make_alert(facets=BOTH_FACETS)

    created, log = actions.apply_action(
        alert, "MARK_EXEMPT", reason="Third-party logistics", expiry_text="", actor=actor
    )

    assert log.actor_discord_id == actor.discord_id
    assert log.actor_discord_name == "SomeAdmin"
    assert log.actor_guild_id == 999
    assert log.entity_type == alert.entity_type
    assert log.entity_id == alert.entity_id
    assert log.alert_type == alert.alert_type
    assert log.reason == "Third-party logistics"
    assert log.expires_at is None
    assert log.performed_at is not None
    assert {f["access_list_id"] for f in log.facets_affected} == {None, 7001}
    assert set(log.exemptions.values_list("pk", flat=True)) == {e.pk for e in created}


def test_removing_an_exemption_logs_and_reopens(make_alert, actor):
    alert = make_alert(facets=BOTH_FACETS)
    actions.apply_action(alert, "MARK_EXEMPT", reason="Approved", actor=actor)

    log = actions.remove_exemption(alert, reason="Changed our mind", actor=actor)

    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.OPEN
    assert all(f.suppressed_by_id is None for f in alert.facets.all())
    assert log.action == taxonomy.ActionType.REMOVE_EXEMPTION
    # Rows are revoked, never deleted: the audit trail has to survive.
    assert Exemption.objects.count() == 2
    assert Exemption.objects.filter(revoked_at__isnull=False).count() == 2
    assert AlertActionLog.objects.count() == 2


# -- matching semantics -----------------------------------------------------


def test_match_key_separates_the_two_directions():
    """An exemption for "should not be blue" must not silence "should be blue"."""
    missing = dedup.candidate_facet_key(
        taxonomy.AlertType.MISSING_FOR_VALID_USER, "corporation", 1, "contact", None
    )
    present = dedup.candidate_facet_key(
        taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER, "corporation", 1, "contact", None
    )
    assert missing != present


def test_conditions_two_and_three_share_a_match_key():
    """Both mean "should not have it", so one decision covers the other."""
    invalid_user = dedup.candidate_facet_key(
        taxonomy.AlertType.PRESENT_FOR_INVALID_USER, "corporation", 1, "contact", None
    )
    no_user = dedup.candidate_facet_key(
        taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER, "corporation", 1, "contact", None
    )
    assert invalid_user == no_user


def test_match_keys_fit_the_index():
    key = dedup.facet_match_key("present", "corporation", 9_999_999_999_999_999, "acl", 12345678)
    assert len(key) <= dedup.MAX_KEY_LENGTH


def test_a_new_exemption_revokes_the_active_duplicate(make_alert, actor):
    """Stands in for a partial unique index, which MySQL silently ignores."""
    alert = make_alert(facets=((taxonomy.FacetType.CONTACT, None, ""),))
    first, _ = actions.apply_action(alert, "MARK_EXEMPT", reason="First", actor=actor)
    actions.remove_exemption(alert, reason="reopen", actor=actor)
    second, _ = actions.apply_action(alert, "MARK_EXEMPT", reason="Second", actor=actor)

    key = first[0].match_key
    active = Exemption.objects.filter(match_key=key, revoked_at__isnull=True)
    assert list(active) == [second[0]]


def test_an_expired_exemption_is_not_returned_as_active(make_alert, actor):
    from aa_altcorp.alerts import engine

    alert = make_alert(facets=((taxonomy.FacetType.CONTACT, None, ""),))
    created, _ = actions.apply_action(
        alert, "TEMPORARY_BLUE", reason="Trial", expiry_text="1d", actor=actor
    )
    key = created[0].match_key
    assert engine.active_exemptions([key])

    Exemption.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert engine.active_exemptions([key]) == {}


# -- validation -------------------------------------------------------------


def test_a_reason_is_required(make_alert, actor):
    alert = make_alert(facets=BOTH_FACETS)

    with pytest.raises(ValueError, match="reason is required"):
        actions.apply_action(alert, "MARK_EXEMPT", reason="   ", actor=actor)
    assert Exemption.objects.count() == 0


def test_temporary_actions_require_an_expiry(make_alert, actor):
    alert = make_alert(facets=BOTH_FACETS)

    with pytest.raises(ValueError, match="needs an expiry"):
        actions.apply_action(
            alert, "TEMPORARY_BLUE", reason="Trial", expiry_text="never", actor=actor
        )


def test_a_stale_button_cannot_apply_an_unavailable_action(make_alert, actor):
    alert = make_alert(facets=((taxonomy.FacetType.CONTACT, None, ""),))

    with pytest.raises(ValueError, match="no longer available"):
        actions.apply_action(
            alert, "TEMPORARY_ACL_EXEMPTION", reason="x", expiry_text="7d", actor=actor
        )


@pytest.mark.parametrize(
    ("text", "days"),
    [("1d", 1), ("3d", 3), ("7 d", 7), ("14D", 14), ("30d", 30), ("2w", 14), ("12h", 0)],
)
def test_parse_expiry_accepts_the_offered_shorthands(text, days):
    now = timezone.now()
    parsed = actions.parse_expiry(text, now=now)
    assert parsed > now
    if days:
        assert (parsed - now).days == days


@pytest.mark.parametrize("text", ["", "never", "none", None, "  "])
def test_parse_expiry_treats_never_as_no_expiry(text):
    assert actions.parse_expiry(text) is None


def test_parse_expiry_accepts_an_iso_date():
    future = (timezone.now() + timedelta(days=5)).date().isoformat()
    assert actions.parse_expiry(future) > timezone.now()


@pytest.mark.parametrize("text", ["soonish", "0d", "-3d", "2020-01-01", "tomorrow"])
def test_parse_expiry_rejects_nonsense(text):
    with pytest.raises(ValueError):
        actions.parse_expiry(text)


# -- authorization ----------------------------------------------------------


def test_is_authorized_requires_a_configured_role(alert_settings):
    assert actions.is_authorized([4242], alert_settings) is True
    assert actions.is_authorized(["4242"], alert_settings) is True
    assert actions.is_authorized([1], alert_settings) is False
    assert actions.is_authorized([], alert_settings) is False


def test_no_configured_roles_denies_everyone(alert_settings):
    """Fail closed: an unconfigured install must not hand out exemption powers."""
    alert_settings.discord_role_ids = []
    alert_settings.save()

    assert actions.is_authorized([4242], alert_settings) is False


def test_custom_id_round_trips_and_stays_short():
    for action in taxonomy.ActionType:
        value = actions.encode_custom_id(987654321, action)
        assert len(value) <= actions.CUSTOM_ID_MAX_LENGTH
        assert actions.decode_custom_id(value) == (987654321, action)


def test_decode_ignores_other_buttons():
    assert actions.decode_custom_id("someone-elses-button") is None
    assert actions.decode_custom_id("ac:zz:1") is None
    assert actions.decode_custom_id("") is None
