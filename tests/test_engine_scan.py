"""Reconciliation: idempotence, resolution, and the no-mass-resolve guard."""

from aa_altcorp.alerts import engine, taxonomy
from aa_altcorp.models import AccessListPolicy, Alert, AlertFacet

from .conftest import ContactRow

CORP = taxonomy.EntityType.CORPORATION


def test_scan_is_a_no_op_while_alerts_are_disabled(alert_settings, stub_aa_contacts):
    alert_settings.alerts_enabled = False
    alert_settings.save()
    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])

    result = engine.run_scan(alert_settings)

    assert result.ran is False
    assert Alert.objects.count() == 0


def test_scan_creates_one_alert_with_its_facets(
    alert_settings, stub_aa_contacts, acl_row, monkeypatch
):
    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    acl_row(access_list_id=7001, corporations=[{"corporation_id": 98009999, "access": "Allowed"}])
    AccessListPolicy.objects.create(access_list_id=7001)

    result = engine.run_scan(alert_settings)

    assert result.created == 1
    alert = Alert.objects.get()
    assert alert.entity_id == 98009999
    assert alert.state == taxonomy.AlertState.OPEN
    assert {f.facet_type for f in alert.facets.all()} == {
        taxonomy.FacetType.CONTACT,
        taxonomy.FacetType.ACL,
    }


def test_rescanning_an_unchanged_world_creates_nothing(alert_settings, stub_aa_contacts):
    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])

    engine.run_scan(alert_settings)
    before = list(Alert.objects.values_list("pk", "dedup_key"))
    second = engine.run_scan(alert_settings)

    assert second.created == 0
    assert second.updated == 1
    assert list(Alert.objects.values_list("pk", "dedup_key")) == before
    assert AlertFacet.objects.count() == 1


def test_a_disappearing_condition_resolves_the_alert(alert_settings, stub_aa_contacts):
    """A contact removed in game resolves its alert on the next scan.

    Two contacts, not one: an empty contact list is reported by
    configured_contacts as CONTACTS_NOT_SYNCED, which the detector treats as an
    unreadable source rather than as "the alliance has no contacts".
    """
    keep = ContactRow(98000042, CORP, 5.0, "Still Blue")
    stub = stub_aa_contacts(alliance_rows=[keep, ContactRow(98009999, CORP, 5.0, "Third Party")])
    engine.run_scan(alert_settings)
    assert Alert.objects.count() == 2

    # One contact is gone from EVE on the next sync.
    stub.AllianceContact.objects.rows = [keep]
    engine.run_scan(alert_settings)

    alert = Alert.objects.get(entity_id=98009999)
    assert alert.state == taxonomy.AlertState.RESOLVED
    assert alert.resolved_at is not None
    assert Alert.objects.get(entity_id=98000042).state == taxonomy.AlertState.OPEN


def test_an_empty_contact_list_is_treated_as_unreadable(alert_settings, stub_aa_contacts):
    """Guards the rule above: zero contacts must not resolve everything."""
    stub = stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    engine.run_scan(alert_settings)

    stub.AllianceContact.objects.rows = []
    result = engine.run_scan(alert_settings)

    assert result.contacts_available is False
    assert Alert.objects.get().state == taxonomy.AlertState.OPEN


def test_an_unavailable_source_never_mass_resolves(
    alert_settings, stub_aa_contacts, no_aa_contacts
):
    """One failed sync must not wipe the board.

    The alert is seeded while contacts are readable, then aa_contacts is made
    unimportable. The contact facet has to survive untouched.
    """
    from aa_altcorp.models import Alert as AlertModel

    alert = AlertModel.objects.create(
        alert_type=taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER,
        entity_type=CORP,
        entity_id=98009999,
        entity_name="Third Party",
    )
    AlertFacet.objects.create(alert=alert, facet_type=taxonomy.FacetType.CONTACT)
    alert.recompute_state()

    result = engine.run_scan(alert_settings)

    assert result.contacts_available is False
    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.OPEN


def test_an_active_exemption_suppresses_on_detection(alert_settings, stub_aa_contacts, actor):
    from aa_altcorp.discord import actions

    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    engine.run_scan(alert_settings)
    alert = Alert.objects.get()
    actions.apply_action(alert, "MARK_EXEMPT", reason="Approved logistics partner", actor=actor)

    result = engine.run_scan(alert_settings)

    alert.refresh_from_db()
    assert result.suppressed == 1
    assert alert.state == taxonomy.AlertState.SUPPRESSED


def test_an_expired_exemption_stops_suppressing(alert_settings, stub_aa_contacts, actor):
    from datetime import timedelta

    from django.utils import timezone

    from aa_altcorp.discord import actions
    from aa_altcorp.models import Exemption

    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    engine.run_scan(alert_settings)
    alert = Alert.objects.get()
    actions.apply_action(
        alert, "TEMPORARY_BLUE", reason="Trial blue", expiry_text="7d", actor=actor
    )
    engine.run_scan(alert_settings)
    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.SUPPRESSED

    # Expiry is a query predicate, so backdating is enough; no cleanup task runs.
    Exemption.objects.update(expires_at=timezone.now() - timedelta(days=1))
    engine.run_scan(alert_settings)

    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.OPEN


def test_a_revoked_exemption_stops_suppressing(alert_settings, stub_aa_contacts, actor):
    from aa_altcorp.discord import actions

    stub_aa_contacts(alliance_rows=[ContactRow(98009999, CORP, 5.0, "Third Party")])
    engine.run_scan(alert_settings)
    alert = Alert.objects.get()
    actions.apply_action(alert, "MARK_EXEMPT", reason="Approved", actor=actor)
    engine.run_scan(alert_settings)

    actions.remove_exemption(alert, reason="No longer approved", actor=actor)
    engine.run_scan(alert_settings)

    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.OPEN


def test_an_empty_acl_mirror_is_treated_as_unreadable(alert_settings, stub_aa_contacts):
    """The ACL half of the no-mass-resolve guard.

    An ACL facet is seeded while nothing is in the mirror. A sync that has never
    run, or whose tokens have all lapsed, must not be read as "every access list
    is now empty" -- that would resolve every open ACL facet at once. Contacts
    stay readable here on purpose: with both sources down the engine resolves
    nothing at all and the test would pass for the wrong reason.
    """
    stub_aa_contacts(alliance_rows=[ContactRow(98000002, CORP, 5.0, "Unrelated Corp")])
    alert = Alert.objects.create(
        alert_type=taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER,
        entity_type=CORP,
        entity_id=98009999,
        entity_name="Third Party",
    )
    AlertFacet.objects.create(
        alert=alert,
        facet_type=taxonomy.FacetType.ACL,
        access_list_id=7001,
        access_list_name="Staging",
    )
    alert.recompute_state()

    result = engine.run_scan(alert_settings)

    assert result.acls_available is False
    assert result.contacts_available is True
    assert result.skipped.get("acls_never_synced") == 1
    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.OPEN
    assert alert.facets.get().state == taxonomy.AlertState.OPEN
