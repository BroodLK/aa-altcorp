"""Model-level invariants, run against a real database by the AA test runner."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from aa_altcorp.alerts import taxonomy
from aa_altcorp.models import AccessListPolicy, Alert, AlertFacet, Exemption


class AlertModelTests(TestCase):
    def setUp(self):
        self.alert = Alert.objects.create(
            alert_type=taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER,
            entity_type=taxonomy.EntityType.CORPORATION,
            entity_id=98000001,
            entity_name="Example Corp",
        )

    def test_dedup_key_is_derived_on_save(self):
        self.assertEqual(self.alert.dedup_key, "present_without_auth_user:corporation:98000001")

    def test_duplicate_dedup_keys_are_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Alert.objects.create(
                alert_type=taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER,
                entity_type=taxonomy.EntityType.CORPORATION,
                entity_id=98000001,
            )

    def test_a_facet_cannot_be_duplicated_within_an_alert(self):
        AlertFacet.objects.create(
            alert=self.alert, facet_type=taxonomy.FacetType.ACL, access_list_id=7001
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AlertFacet.objects.create(
                alert=self.alert, facet_type=taxonomy.FacetType.ACL, access_list_id=7001
            )

    def test_state_is_derived_from_the_facets(self):
        contact = AlertFacet.objects.create(alert=self.alert, facet_type=taxonomy.FacetType.CONTACT)
        acl = AlertFacet.objects.create(
            alert=self.alert, facet_type=taxonomy.FacetType.ACL, access_list_id=7001
        )
        self.assertEqual(self.alert.recompute_state(), taxonomy.AlertState.OPEN)

        # One suppressed facet must not silence the other problem.
        contact.state = taxonomy.AlertState.SUPPRESSED
        contact.save()
        self.assertEqual(self.alert.recompute_state(), taxonomy.AlertState.OPEN)

        acl.state = taxonomy.AlertState.SUPPRESSED
        acl.save()
        self.assertEqual(self.alert.recompute_state(), taxonomy.AlertState.SUPPRESSED)

        AlertFacet.objects.filter(alert=self.alert).update(state=taxonomy.AlertState.RESOLVED)
        self.assertEqual(self.alert.recompute_state(), taxonomy.AlertState.RESOLVED)
        self.assertIsNotNone(self.alert.resolved_at)

    def test_an_alert_with_no_facets_is_resolved(self):
        self.assertEqual(self.alert.recompute_state(), taxonomy.AlertState.RESOLVED)


class ExemptionModelTests(TestCase):
    def _exemption(self, **overrides):
        defaults = {
            "kind": taxonomy.ExemptionKind.MARK_EXEMPT,
            "direction": taxonomy.Direction.PRESENT,
            "facet_type": taxonomy.FacetType.CONTACT,
            "entity_type": taxonomy.EntityType.CORPORATION,
            "entity_id": 98000001,
            "reason": "Approved",
        }
        return Exemption.objects.create(**{**defaults, **overrides})

    def test_match_key_is_computed_on_save(self):
        exemption = self._exemption()
        self.assertEqual(exemption.match_key, "present:corporation:98000001:contact:-")

    def test_direction_separates_the_keys(self):
        present = self._exemption()
        missing = self._exemption(direction=taxonomy.Direction.MISSING)
        self.assertNotEqual(present.match_key, missing.match_key)

    def test_an_acl_exemption_requires_an_access_list(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._exemption(facet_type=taxonomy.FacetType.ACL, access_list_id=None)

    def test_is_active_reflects_expiry_and_revocation(self):
        from datetime import timedelta

        from django.utils import timezone

        self.assertTrue(self._exemption().is_active)
        self.assertFalse(self._exemption(expires_at=timezone.now() - timedelta(days=1)).is_active)
        self.assertFalse(self._exemption(revoked_at=timezone.now()).is_active)


class PermissionTests(TestCase):
    def test_the_new_permissions_exist_after_migrate(self):
        """Proves migration 0004's CreateModel General actually took effect."""
        from django.contrib.auth.models import Permission

        codenames = set(
            Permission.objects.filter(
                content_type__app_label="aa_altcorp",
                codename__in=("view_alerts", "manage_alerts", "basic_access"),
            ).values_list("codename", flat=True)
        )
        self.assertEqual(codenames, {"view_alerts", "manage_alerts", "basic_access"})


class AccessListPolicyTests(TestCase):
    def test_one_policy_per_access_list(self):
        AccessListPolicy.objects.create(access_list_id=7001)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AccessListPolicy.objects.create(access_list_id=7001)

    def test_it_falls_back_to_the_id_for_display(self):
        self.assertEqual(str(AccessListPolicy.objects.create(access_list_id=7001)), "ACL 7001")
