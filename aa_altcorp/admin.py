from django.contrib import admin
from django.db.models import Q
from django.utils import timezone

from .alerts import taxonomy
from .forms import AccessListPolicyForm, AltCorpSettingsForm
from .models import (
    AccessListPolicy,
    Alert,
    AlertActionLog,
    AlertFacet,
    AltCharacter,
    AltCorporation,
    AltCorpReview,
    AltCorpSettings,
    CharacterAccessList,
    CharacterAccessToken,
    Exemption,
)


@admin.register(AltCorporation)
class AltCorporationAdmin(admin.ModelAdmin):
    list_display = ("user", "corporation_name", "source", "last_checked_at")
    search_fields = ("user__username", "corporation_name")


@admin.register(AltCharacter)
class AltCharacterAdmin(admin.ModelAdmin):
    list_display = ("user", "character_name", "character_id")
    search_fields = ("user__username", "character_name", "character_id")


@admin.register(AltCorpReview)
class AltCorpReviewAdmin(admin.ModelAdmin):
    list_display = ("relationship", "approved", "member_state", "standing", "checked_at")
    list_filter = ("approved", "aa_contact_found")


@admin.register(AltCorpSettings)
class AltCorpSettingsAdmin(admin.ModelAdmin):
    form = AltCorpSettingsForm
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "enabled",
                    "approved_states",
                    "standing_target_type",
                    "standing_target_id",
                )
            },
        ),
        ("Notifications", {"fields": ("webhook_url", "notification_interval")}),
        (
            "Discord",
            {
                "fields": (
                    "alert_delivery",
                    "discord_channel_id",
                    "discord_guild_id",
                    "discord_role_ids",
                ),
                "description": (
                    "Holding one of these Discord roles grants Auth-side exemption powers "
                    "without any Django permission. Every action is still logged."
                ),
            },
        ),
        (
            "Alerts",
            {
                "fields": (
                    "alerts_enabled",
                    "minimum_blue_standing",
                    "treat_zero_standing_as_removed",
                    "alert_batch_size",
                    "renotify_interval_days",
                )
            },
        ),
        (
            "Entity scope",
            {
                "fields": (
                    "expect_contact_characters",
                    "expect_contact_corporations",
                    "expect_contact_alliances",
                    "expect_all_owned_characters",
                ),
                "description": (
                    "Which entities are expected to hold standing. Widening these widens "
                    "the first condition, and with it the alert volume."
                ),
            },
        ),
    )


@admin.register(CharacterAccessList)
class CharacterAccessListAdmin(admin.ModelAdmin):
    """Read-only: rows here are an ESI mirror, edits would be overwritten."""

    list_display = ("access_list_id", "name", "character_id", "synced_at")
    search_fields = ("access_list_id", "name", "character_id")
    readonly_fields = ("character_id", "access_list_id", "name", "description", "membership")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CharacterAccessToken)
class CharacterAccessTokenAdmin(admin.ModelAdmin):
    list_display = ("character_id", "token_id", "added_at")
    search_fields = ("character_id",)


class AlertFacetInline(admin.TabularInline):
    model = AlertFacet
    extra = 0
    can_delete = False
    fields = (
        "facet_type",
        "access_list_name",
        "access_list_id",
        "state",
        "reason_text",
        "suppressed_by",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "alert_type",
        "entity_type",
        "entity_name",
        "entity_id",
        "state",
        "first_seen_at",
        "last_seen_at",
    )
    list_filter = ("state", "alert_type", "entity_type")
    search_fields = ("entity_name", "entity_id", "summary", "dedup_key")
    date_hierarchy = "first_seen_at"
    list_select_related = ("user",)
    inlines = (AlertFacetInline,)
    actions = ("resolve_selected", "redeliver_selected")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields if field.name != "state"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Resolve selected alerts")
    def resolve_selected(self, request, queryset):
        updated = queryset.update(state=taxonomy.AlertState.RESOLVED, resolved_at=timezone.now())
        AlertFacet.objects.filter(alert__in=queryset).update(
            state=taxonomy.AlertState.RESOLVED, resolved_at=timezone.now()
        )
        self.message_user(request, f"Resolved {updated} alert(s).")

    @admin.action(description="Re-deliver selected alerts")
    def redeliver_selected(self, request, queryset):
        """Clear the delivery stamp so the next delivery task picks them up."""
        updated = queryset.update(notified_at=None)
        self.message_user(request, f"{updated} alert(s) queued for re-delivery.")


class ActiveExemptionFilter(admin.SimpleListFilter):
    """expires_at alone cannot express active/expired/revoked, so do it here."""

    title = "status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return (("active", "Active"), ("expired", "Expired"), ("revoked", "Revoked"))

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "active":
            return queryset.filter(revoked_at__isnull=True).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)
            )
        if self.value() == "expired":
            return queryset.filter(revoked_at__isnull=True, expires_at__lte=now)
        if self.value() == "revoked":
            return queryset.filter(revoked_at__isnull=False)
        return queryset


@admin.register(Exemption)
class ExemptionAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "direction",
        "entity_type",
        "entity_name",
        "entity_id",
        "access_list_name",
        "expires_at",
        "is_active",
        "created_by_discord_name",
        "created_at",
    )
    list_filter = (
        ActiveExemptionFilter,
        "kind",
        "direction",
        "facet_type",
        "entity_type",
        "source",
    )
    search_fields = ("entity_name", "entity_id", "reason", "match_key")
    readonly_fields = (
        "match_key",
        "created_at",
        "created_by_discord_id",
        "created_by_discord_name",
        "created_by_guild_id",
        "source_alert",
        "revoked_at",
        "revoked_by",
        "revoked_by_discord_id",
    )
    actions = ("revoke_selected",)

    @admin.display(boolean=True, description="Active")
    def is_active(self, obj):
        return obj.is_active

    @admin.action(description="Revoke selected exemptions")
    def revoke_selected(self, request, queryset):
        from .discord import actions as discord_actions

        revoked = discord_actions.revoke_exemptions(
            queryset.filter(revoked_at__isnull=True),
            reason="Revoked from Django admin",
            user=request.user,
        )
        self.message_user(request, f"Revoked {revoked} exemption(s).")


@admin.register(AlertActionLog)
class AlertActionLogAdmin(admin.ModelAdmin):
    """Fully read-only: this is the audit trail and must not be edited away."""

    list_display = (
        "performed_at",
        "action",
        "entity_type",
        "entity_name",
        "actor_discord_name",
        "expires_at",
    )
    list_filter = ("action", "entity_type")
    search_fields = ("entity_name", "entity_id", "reason", "actor_discord_name")
    date_hierarchy = "performed_at"

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccessListPolicy)
class AccessListPolicyAdmin(admin.ModelAdmin):
    form = AccessListPolicyForm
    list_display = (
        "access_list_id",
        "name",
        "enabled",
        "expect_positive_contacts",
        "minimum_standing",
        "alert_missing",
        "alert_unexpected",
        "updated_at",
    )
    list_filter = ("enabled", "expect_positive_contacts", "expected_tier")
    search_fields = ("access_list_id", "name", "notes")
