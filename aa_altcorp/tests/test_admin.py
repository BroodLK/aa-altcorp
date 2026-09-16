"""Admin registration, read-only audit, and the settings-form field guard."""

from django.contrib import admin
from django.test import TestCase

from aa_altcorp.admin import AlertActionLogAdmin
from aa_altcorp.forms import AccessListPolicyForm, AltCorpSettingsForm
from aa_altcorp.models import (
    AccessListPolicy,
    Alert,
    AlertActionLog,
    AltCharacter,
    AltCorporation,
    AltCorpReview,
    AltCorpSettings,
    CharacterAccessList,
    CharacterAccessToken,
    Exemption,
)


class AdminRegistrationTests(TestCase):
    def test_every_model_is_registered(self):
        for model in (
            AccessListPolicy,
            Alert,
            AlertActionLog,
            AltCharacter,
            AltCorporation,
            AltCorpReview,
            AltCorpSettings,
            CharacterAccessList,
            CharacterAccessToken,
            Exemption,
        ):
            self.assertIn(model, admin.site._registry, f"{model.__name__} is not registered")

    def test_the_audit_log_is_read_only(self):
        """It is the audit trail; it must not be editable away."""
        log_admin = AlertActionLogAdmin(AlertActionLog, admin.site)
        self.assertFalse(log_admin.has_add_permission(None))
        self.assertFalse(log_admin.has_change_permission(None))
        self.assertFalse(log_admin.has_delete_permission(None))
        readonly = set(log_admin.get_readonly_fields(None))
        self.assertEqual(readonly, {f.name for f in AlertActionLog._meta.fields})


class SettingsFormTests(TestCase):
    def test_the_field_tuple_covers_every_editable_setting(self):
        """A field missing from Meta.fields is silently dropped on save.

        That bug is invisible until someone's configuration stops persisting,
        so pin it here rather than trusting review.
        """
        auto_or_readonly = {"id", "updated_at"}
        expected = {
            field.name
            for field in AltCorpSettings._meta.fields
            if field.name not in auto_or_readonly and field.editable
        }
        self.assertEqual(
            expected - set(AltCorpSettingsForm.Meta.fields),
            set(),
            "New AltCorpSettings fields must be added to AltCorpSettingsForm.Meta.fields",
        )

    def test_discord_role_ids_are_coerced_to_integers(self):
        form = AltCorpSettingsForm(data=_settings_payload(discord_role_ids='["123", 456]'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["discord_role_ids"], [123, 456])

    def test_non_numeric_role_ids_are_rejected(self):
        form = AltCorpSettingsForm(data=_settings_payload(discord_role_ids='["not-an-id"]'))
        self.assertFalse(form.is_valid())
        self.assertIn("discord_role_ids", form.errors)


class AccessListPolicyFormTests(TestCase):
    def test_choices_come_from_synced_access_lists(self):
        CharacterAccessList.objects.create(
            character_id=95000001, access_list_id=7001, name="Capital Staging"
        )
        form = AccessListPolicyForm()
        self.assertEqual(form.fields["access_list_id"].choices, [(7001, "Capital Staging")])

    def test_it_degrades_when_nothing_is_synced_yet(self):
        self.assertEqual(AccessListPolicyForm().fields["access_list_id"].choices, [])

    def test_an_existing_policy_stays_editable_after_its_acl_vanishes(self):
        policy = AccessListPolicy.objects.create(access_list_id=7001, name="Gone")
        choices = AccessListPolicyForm(instance=policy).fields["access_list_id"].choices
        self.assertIn((7001, "Gone"), choices)


def _settings_payload(**overrides):
    return {
        "standing_target_type": "alliance",
        "notification_interval": "0 0 * * *",
        "alert_delivery": "auto",
        "minimum_blue_standing": "0.1",
        "alert_batch_size": "10",
        "renotify_interval_days": "0",
        **overrides,
    }
