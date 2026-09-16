"""Admin forms for settings backed by Alliance Auth and aa-contacts data."""

from django import forms

from .models import AccessListPolicy, AltCorpSettings, CharacterAccessList


def _keep_current(choices, value, label=None):
    """Add ``value`` to ``choices`` when the live source no longer offers it.

    Choices here come from Alliance Auth and aa-contacts, so a value that was
    valid when it was saved can vanish: a renamed state, contacts not synced
    yet, or aa-contacts uninstalled entirely.  Without this the whole settings
    page fails validation and cannot be saved at all -- not even to correct an
    unrelated field.
    """
    if value in (None, ""):
        return choices
    if any(str(value) == str(existing) for existing, _ in choices):
        return choices
    return [(value, label or f"{value} (no longer listed)"), *choices]


class AltCorpSettingsForm(forms.ModelForm):
    approved_states = forms.MultipleChoiceField(required=False)
    standing_target_id = forms.ChoiceField(required=False)

    class Meta:
        model = AltCorpSettings
        # Explicit tuple: a field missing here is silently dropped on save.
        # tests/test_admin_forms.py asserts this covers every editable field.
        fields = (
            "enabled",
            "approved_states",
            "standing_target_type",
            "standing_target_id",
            "notification_interval",
            "webhook_url",
            "discord_channel_id",
            "discord_guild_id",
            "discord_role_ids",
            "alert_delivery",
            "alerts_enabled",
            "minimum_blue_standing",
            "treat_zero_standing_as_removed",
            "alert_batch_size",
            "renotify_interval_days",
            "expect_contact_characters",
            "expect_contact_corporations",
            "expect_contact_alliances",
            "expect_all_owned_characters",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            from allianceauth.authentication.models import State

            state_choices = [
                (state.name, state.name) for state in State.objects.order_by("priority")
            ]
        except (ImportError, RuntimeError):
            state_choices = [("Member", "Member")]
        for stored in self.instance.approved_states or []:
            state_choices = _keep_current(state_choices, stored)
        self.fields["approved_states"].choices = state_choices
        try:
            from aa_contacts.models import AllianceContact, CorporationContact

            target_type = self.data.get("standing_target_type", self.instance.standing_target_type)
            model = AllianceContact if target_type == "alliance" else CorporationContact
            entity_field = "alliance" if target_type == "alliance" else "corporation"
            # Offer only entities aa-contacts actually holds contacts for, keyed by
            # EVE id rather than local row pk so every reader agrees on the value.
            owners = set(
                model.objects.values_list(
                    f"{entity_field}__{entity_field}_id",
                    f"{entity_field}__{entity_field}_name",
                )
            )
            target_choices = sorted(owners, key=lambda owner: owner[1] or "")
        except (ImportError, RuntimeError):
            target_choices = []
        self.fields["standing_target_id"].choices = _keep_current(
            target_choices, self.instance.standing_target_id
        )
        self.fields["approved_states"].initial = self.instance.approved_states

    def clean_standing_target_id(self):
        # ChoiceField hands back "" rather than None, which a BigIntegerField
        # cannot store: saving one would raise ValueError on int("").
        return self.cleaned_data["standing_target_id"] or None

    def clean_discord_role_ids(self):
        """Coerce the JSON field to a list of ints so the bot can compare directly."""
        value = self.cleaned_data.get("discord_role_ids") or []
        if isinstance(value, (str, int)):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise forms.ValidationError("Enter a list of Discord role IDs.")
        role_ids = []
        for entry in value:
            try:
                role_ids.append(int(entry))
            except (TypeError, ValueError) as error:
                raise forms.ValidationError(
                    f"{entry!r} is not a Discord role ID. Role IDs are numeric."
                ) from error
        return role_ids

    def clean_notification_interval(self):
        value = self.cleaned_data["notification_interval"].strip()
        if len(value.split()) != 5:
            raise forms.ValidationError("Enter exactly five cron fields.")
        allowed = set("0123456789*/,-")
        if any(set(field) - allowed for field in value.split()):
            raise forms.ValidationError(
                "Cron fields may contain numbers, *, /, commas, and hyphens."
            )
        return value


class AccessListPolicyForm(forms.ModelForm):
    """Pick the ACL from what has actually been synced, rather than typing an id."""

    access_list_id = forms.ChoiceField(required=True, label="Access list")

    class Meta:
        model = AccessListPolicy
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        synced = sorted(
            set(CharacterAccessList.objects.values_list("access_list_id", "name")),
            key=lambda row: (row[1] or "", row[0]),
        )
        choices = [(acl_id, name or f"ACL {acl_id}") for acl_id, name in synced]
        current = self.instance.access_list_id
        if current and all(current != acl_id for acl_id, _ in choices):
            # Keep an existing policy editable even if its ACL is no longer synced.
            choices.insert(0, (current, self.instance.name or f"ACL {current}"))
        self.fields["access_list_id"].choices = choices

    def clean_access_list_id(self):
        return int(self.cleaned_data["access_list_id"])
