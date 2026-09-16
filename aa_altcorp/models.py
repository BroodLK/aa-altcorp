"""Persistent records for alt corporation reviews.

Entity ids are deliberately stored as integers instead of foreign keys.  This
keeps the records resilient when EVE SDE rows are refreshed and lets the app
run in the small standalone test project as well as inside Alliance Auth.
"""

from datetime import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone

from .alerts import dedup, taxonomy


def default_approved_states():
    return ["Member"]


class General(models.Model):
    """Meta model providing the app permissions."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Has the ability to view this page"),
            ("manage_relationships", "Has the ability to add relationships"),
            ("view_alerts", "Has the ability to view contact and ACL alerts"),
            ("manage_alerts", "Has the ability to manage alert exemptions and ACL policies"),
        )


class AltCorporation(models.Model):
    """A corporation attached to an authenticated user's main character."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alt_corporations"
    )
    corporation_id = models.BigIntegerField()
    corporation_name = models.CharField(max_length=255)
    source = models.CharField(max_length=20, default="esi")
    attached_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "corporation_id"), name="unique_user_alt_corp")
        ]
        ordering = ("corporation_name",)

    def __str__(self):
        return self.corporation_name or str(self.corporation_id)


class AltCharacter(models.Model):
    """A character explicitly associated with an Auth account."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alt_characters"
    )
    character_id = models.BigIntegerField()
    character_name = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "character_id"), name="unique_user_alt_character"
            )
        ]
        ordering = ("character_name",)

    def __str__(self):
        return self.character_name or str(self.character_id)


class CharacterAccessList(models.Model):
    """Latest ESI access lists returned for an associated character."""

    character_id = models.BigIntegerField()
    access_list_id = models.BigIntegerField()
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    membership = models.JSONField(default=dict)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("character_id", "access_list_id"), name="unique_character_access_list"
            )
        ]


class CharacterAccessToken(models.Model):
    """Reference to the encrypted django-esi token used for ACL access."""

    character_id = models.BigIntegerField(unique=True)
    token_id = models.BigIntegerField(unique=True)
    added_at = models.DateTimeField(auto_now_add=True)


class AltCorpSettings(models.Model):
    """Singleton configuration editable from Django admin."""

    approved_states = models.JSONField(
        default=default_approved_states,
        help_text="Alliance Auth member states accepted by the audit",
    )
    standing_target_type = models.CharField(
        max_length=12,
        choices=(("alliance", "Alliance"), ("corporation", "Corporation")),
        default="alliance",
    )
    standing_target_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="EVE alliance or corporation ID, not the local Alliance Auth row ID",
    )
    notification_interval = models.CharField(max_length=100, default="0 0 * * *")
    webhook_url = models.URLField(
        blank=True,
        help_text="Fallback delivery. Webhooks cannot carry buttons; the bot is needed for those.",
    )
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- Discord -----------------------------------------------------------
    discord_channel_id = models.BigIntegerField(
        null=True, blank=True, help_text="Channel the alert engine posts to via the bot"
    )
    discord_guild_id = models.BigIntegerField(
        null=True, blank=True, help_text="Guild the alert channel lives in, used for logging"
    )
    discord_role_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Discord role IDs allowed to action alerts. Holding one of these grants "
            "Auth-side exemption powers without any Django permission."
        ),
    )
    alert_delivery = models.CharField(
        max_length=8,
        choices=taxonomy.AlertDelivery.choices,
        default=taxonomy.AlertDelivery.AUTO,
    )

    # --- Alert engine ------------------------------------------------------
    alerts_enabled = models.BooleanField(
        default=False, help_text="Off by default so upgrading does not start alerting unannounced"
    )
    minimum_blue_standing = models.FloatField(
        default=0.1, help_text="Standing at or above which a contact counts as blue"
    )
    treat_zero_standing_as_removed = models.BooleanField(
        default=True,
        help_text=(
            "aa-contacts forces standing to 0.0 instead of deleting contacts that carry notes "
            "or server links, so 0.0 is ambiguous. Enabled means 0.0 counts as no standing."
        ),
    )
    alert_batch_size = models.PositiveSmallIntegerField(
        default=10, help_text="Alerts delivered per batch, to stay inside Discord rate limits"
    )
    renotify_interval_days = models.PositiveSmallIntegerField(
        default=0, help_text="Re-deliver still-open alerts after this many days; 0 notifies once"
    )

    # --- Entity scope for the "should have" direction ----------------------
    expect_contact_characters = models.BooleanField(default=True)
    expect_contact_corporations = models.BooleanField(default=True)
    expect_contact_alliances = models.BooleanField(
        default=False,
        help_text=(
            "Alliance-level blue is usually a deliberate diplomatic act, "
            "not a per-member consequence"
        ),
    )
    expect_all_owned_characters = models.BooleanField(
        default=False,
        help_text=(
            "Off: only main characters and explicitly attached alts are expected to hold "
            "standing. On: every owned character of a valid-state user is."
        ),
    )

    class Meta:
        verbose_name = "Alt Corp settings"

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]


class AltCorpReview(models.Model):
    """Latest audit result and notification cooldown for one attachment."""

    relationship = models.OneToOneField(
        AltCorporation, on_delete=models.CASCADE, related_name="review"
    )
    approved = models.BooleanField(null=True)
    member_state = models.CharField(max_length=100, blank=True)
    standing = models.FloatField(null=True, blank=True)
    aa_contact_found = models.BooleanField(null=True)
    reason = models.TextField(blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    first_notified_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)

    def notification_due(self, interval):
        if self.last_notified_at is None:
            return True
        if hasattr(interval, "total_seconds"):
            return self.last_notified_at + interval <= timezone.now()
        try:
            from croniter import croniter

            # datetime.datetime rather than timezone.datetime: the latter is an
            # incidental re-export from django.utils.timezone, not part of its API.
            next_run = croniter(interval, self.last_notified_at).get_next(datetime)
            return next_run <= timezone.now()
        except (ImportError, ValueError):
            return False


class AccessListPolicy(models.Model):
    """Who Alliance Auth thinks should be on one ESI access list.

    Opt-in, per ACL.  An ACL with no enabled policy contributes no "missing
    access" alerts at all -- without a policy the app has no basis to claim an
    entity should be on it.  "Present but unjustified" alerts need no policy,
    because they are true regardless of what access was intended.
    """

    access_list_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)

    expect_positive_contacts = models.BooleanField(
        default=True, help_text="Every blue contact is expected to have access to this ACL"
    )
    minimum_standing = models.FloatField(
        default=0.1, help_text="Standing floor used by expect_positive_contacts"
    )
    expect_states = models.JSONField(
        default=list, blank=True, help_text="Alliance Auth state names expected to have access"
    )
    expect_groups = models.JSONField(
        default=list, blank=True, help_text="Alliance Auth group names expected to have access"
    )
    expect_corporations = models.JSONField(
        default=list, blank=True, help_text="EVE corporation IDs expected to have access"
    )
    expect_alliances = models.JSONField(
        default=list, blank=True, help_text="EVE alliance IDs expected to have access"
    )
    expect_entities = models.JSONField(
        default=list,
        blank=True,
        help_text="Explicit entries, each with entity_type, entity_id and an optional name",
    )
    expect_mains_only = models.BooleanField(
        default=False, help_text="Restrict state and group rules to main characters"
    )
    expected_tier = models.CharField(
        max_length=12,
        choices=taxonomy.EntityType.choices,
        default=taxonomy.EntityType.CHARACTER,
        help_text="Tier that corporation and alliance rules expand to",
    )

    alert_missing = models.BooleanField(
        default=True, help_text="Alert when an expected entity is absent from this ACL"
    )
    alert_unexpected = models.BooleanField(
        default=True, help_text="Alert when an unexpected entity has access to this ACL"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Access list policy"
        verbose_name_plural = "Access list policies"
        ordering = ("name", "access_list_id")

    def __str__(self):
        return self.name or f"ACL {self.access_list_id}"


class Exemption(models.Model):
    """A standing decision by an admin about one entity and one facet.

    Models the underlying situation rather than "alert 1234 was dismissed", so
    a later scan understands why it should stay quiet instead of recreating the
    same alert.  Never touches EVE; this is Alliance Auth state only.
    """

    kind = models.CharField(max_length=24, choices=taxonomy.ExemptionKind.choices)
    direction = models.CharField(max_length=8, choices=taxonomy.Direction.choices)
    facet_type = models.CharField(max_length=8, choices=taxonomy.FacetType.choices)

    entity_type = models.CharField(max_length=12, choices=taxonomy.EntityType.choices)
    entity_id = models.BigIntegerField()
    entity_name = models.CharField(max_length=255, blank=True)
    access_list_id = models.BigIntegerField(null=True, blank=True)
    access_list_name = models.CharField(max_length=255, blank=True)

    reason = models.TextField(help_text="Required. Explains the decision to whoever reads it later")
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Empty means the exemption never expires"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by_discord_id = models.BigIntegerField(null=True, blank=True)
    created_by_discord_name = models.CharField(max_length=190, blank=True)
    created_by_guild_id = models.BigIntegerField(null=True, blank=True)
    source = models.CharField(
        max_length=12,
        choices=taxonomy.ExemptionSource.choices,
        default=taxonomy.ExemptionSource.DISCORD,
    )
    source_alert = models.ForeignKey(
        "Alert", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    revoked_by_discord_id = models.BigIntegerField(null=True, blank=True)
    revoked_reason = models.TextField(blank=True)

    match_key = models.CharField(max_length=dedup.MAX_KEY_LENGTH, db_index=True, editable=False)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("match_key", "revoked_at"), name="altcorp_exempt_match_idx"),
            models.Index(fields=("kind", "expires_at"), name="altcorp_exempt_kind_idx"),
        ]
        constraints = [
            # An ACL exemption without an ACL id would match every list at once.
            models.CheckConstraint(
                condition=(
                    models.Q(facet_type=taxonomy.FacetType.ACL, access_list_id__isnull=False)
                    | ~models.Q(facet_type=taxonomy.FacetType.ACL)
                ),
                name="altcorp_acl_exemption_needs_acl",
            )
        ]
        # No conditional unique index on match_key: MySQL ignores the condition
        # and Alliance Auth is overwhelmingly MySQL. create_exemption() revokes
        # any active duplicate inside a transaction instead.

    def __str__(self):
        return f"{self.get_kind_display()} - {self.entity_name or self.entity_id}"

    def save(self, *args, **kwargs):
        self.match_key = dedup.exemption_match_key(self)
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        """For display only. Filtering uses a query predicate, never this."""
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class Alert(models.Model):
    """One condition affecting one entity, spanning one or more facets."""

    alert_type = models.CharField(max_length=32, choices=taxonomy.AlertType.choices)
    entity_type = models.CharField(max_length=12, choices=taxonomy.EntityType.choices)
    entity_id = models.BigIntegerField()
    entity_name = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="altcorp_alerts",
    )

    state = models.CharField(
        max_length=12,
        choices=taxonomy.AlertState.choices,
        default=taxonomy.AlertState.OPEN,
        db_index=True,
    )
    summary = models.CharField(max_length=500, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    dedup_key = models.CharField(max_length=dedup.MAX_KEY_LENGTH, unique=True, editable=False)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    scan_id = models.UUIDField(null=True, blank=True)

    discord_channel_id = models.BigIntegerField(null=True, blank=True)
    discord_message_id = models.BigIntegerField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    delivery = models.CharField(max_length=12, blank=True, choices=taxonomy.DeliveryChannel.choices)

    class Meta:
        ordering = ("-last_seen_at",)
        indexes = [
            models.Index(fields=("state", "alert_type"), name="altcorp_alert_state_idx"),
            models.Index(fields=("entity_type", "entity_id"), name="altcorp_alert_entity_idx"),
            models.Index(fields=("state", "notified_at"), name="altcorp_alert_notify_idx"),
        ]

    def __str__(self):
        return f"{self.get_alert_type_display()} - {self.entity_name or self.entity_id}"

    def save(self, *args, **kwargs):
        self.dedup_key = dedup.alert_dedup_key(self.alert_type, self.entity_type, self.entity_id)
        super().save(*args, **kwargs)

    @property
    def direction(self):
        return taxonomy.direction_for(self.alert_type)

    def recompute_state(self, save=True):
        """Derive the alert state from its facets.

        Open beats suppressed beats resolved, so exempting one facet narrows the
        alert rather than silencing the other.
        """
        states = set(self.facets.values_list("state", flat=True))
        if taxonomy.AlertState.OPEN in states:
            state = taxonomy.AlertState.OPEN
        elif taxonomy.AlertState.SUPPRESSED in states:
            state = taxonomy.AlertState.SUPPRESSED
        else:
            state = taxonomy.AlertState.RESOLVED
        if state == taxonomy.AlertState.RESOLVED:
            resolved_at = self.resolved_at or timezone.now()
        else:
            resolved_at = None
        changed = state != self.state or resolved_at != self.resolved_at
        self.state = state
        self.resolved_at = resolved_at
        if save and changed:
            super().save(update_fields=("state", "resolved_at", "last_seen_at"))
        return state


class AlertFacet(models.Model):
    """One suppressible axis of an alert: contact standing, or a single ACL."""

    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="facets")
    facet_type = models.CharField(max_length=8, choices=taxonomy.FacetType.choices)
    access_list_id = models.BigIntegerField(null=True, blank=True)
    access_list_name = models.CharField(max_length=255, blank=True)

    state = models.CharField(
        max_length=12, choices=taxonomy.AlertState.choices, default=taxonomy.AlertState.OPEN
    )
    suppressed_by = models.ForeignKey(
        Exemption,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suppressed_facets",
    )
    reason_text = models.CharField(max_length=500, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("facet_type", "access_list_name", "access_list_id")
        constraints = [
            models.UniqueConstraint(
                fields=("alert", "facet_type", "access_list_id"),
                name="altcorp_unique_alert_facet",
            )
        ]

    def __str__(self):
        if self.facet_type == taxonomy.FacetType.ACL:
            return f"ACL {self.access_list_name or self.access_list_id}"
        return "Contact standing"


class AlertActionLog(models.Model):
    """Immutable audit trail. One row per action, including removals.

    Every identifying field is snapshotted rather than only referenced, so the
    history survives deletion of the alert or the exemptions it created.
    """

    action = models.CharField(max_length=32, choices=taxonomy.ActionType.choices)
    alert = models.ForeignKey(
        Alert, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    exemptions = models.ManyToManyField(Exemption, blank=True, related_name="action_logs")

    alert_type = models.CharField(max_length=32, blank=True)
    entity_type = models.CharField(max_length=12)
    entity_id = models.BigIntegerField()
    entity_name = models.CharField(max_length=255, blank=True)
    facets_affected = models.JSONField(
        default=list, blank=True, help_text="Snapshot of the facets this action covered"
    )

    reason = models.TextField()
    expires_at = models.DateTimeField(null=True, blank=True)

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_discord_id = models.BigIntegerField(null=True, blank=True)
    actor_discord_name = models.CharField(max_length=190, blank=True)
    actor_guild_id = models.BigIntegerField(null=True, blank=True)

    performed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Alert action log"
        verbose_name_plural = "Alert action log"
        ordering = ("-performed_at",)

    def __str__(self):
        return f"{self.get_action_display()} - {self.entity_name or self.entity_id}"
