"""Scheduled work: ACL synchronisation, alert scanning, and delivery."""

import logging
from random import randint

from celery import shared_task

from .models import AltCharacter, AltCorporation, AltCorpSettings, CharacterAccessToken
from .services import (
    _esi_error_types,
    audit_relationship,
    notify_review,
    sync_character_access_lists,
)

logger = logging.getLogger(__name__)

#: Spread per-character ESI calls so a large install does not burst the error limit.
TASK_JITTER = 300


def _once_options(key=None):
    """QueueOnce options, but only when celery_once is actually configured.

    celery_once ships with Alliance Auth, yet neither test settings module sets
    CELERY_ONCE.  An unconditional ``base=QueueOnce`` would therefore break both
    suites at import time.
    """
    try:
        from celery_once import QueueOnce
        from django.conf import settings as django_settings
    except ImportError:
        return {}
    if not getattr(django_settings, "CELERY_ONCE", None):
        return {}
    options = {"base": QueueOnce, "once": {"graceful": True}}
    if key:
        options["once"]["keys"] = list(key)
    return options


def _tracked_character_ids():
    """Characters whose ACLs are worth fetching."""
    character_ids = set(CharacterAccessToken.objects.values_list("character_id", flat=True))
    try:
        from allianceauth.authentication.models import CharacterOwnership

        user_ids = set(AltCorporation.objects.values_list("user_id", flat=True))
        user_ids.update(AltCharacter.objects.values_list("user_id", flat=True))
        character_ids.update(
            CharacterOwnership.objects.filter(user_id__in=user_ids)
            .filter(user__profile__main_character_id__isnull=False)
            .values_list("user__profile__main_character_id", flat=True)
        )
    except (ImportError, RuntimeError):
        pass
    return character_ids


@shared_task(
    name="aa_altcorp.tasks.audit_alt_corporations",
    ignore_result=True,
)
def audit_alt_corporations():
    """Audit every attachment; deployments can schedule this hourly or daily.

    Name and return value are unchanged: existing installs have a PeriodicTask
    pointing here.  The alert scan is chained on only when it is enabled.
    """
    logger.info("Starting Alt Corp audit and ACL synchronization")
    checked = 0
    character_ids = _tracked_character_ids()
    for character_id in character_ids:
        synced = sync_character_access_lists(character_id)
        logger.info("ACL sync result for character %s: %s ACL(s)", character_id, synced)
    for relationship in AltCorporation.objects.select_related("user"):
        try:
            notify_review(audit_relationship(relationship))
        except _esi_error_types():
            logger.exception("Unable to audit relationship %s", relationship.pk)
        checked += 1
    logger.info(
        "Finished Alt Corp audit and ACL synchronization: %s characters, %s relationships",
        len(character_ids),
        checked,
    )
    if AltCorpSettings.current().alerts_enabled:
        run_alert_scan.apply_async(countdown=5)
    return checked


@shared_task(name="aa_altcorp.tasks.sync_all_access_lists", ignore_result=True)
def sync_all_access_lists():
    """Fan the ACL sync out, one subtask per character, with jitter.

    An opt-in alternative to the serial sync inside ``audit_alt_corporations``,
    for installs large enough that one task cannot get through every character
    in time.  Deliberately not chained from the audit: the fan-out returns as
    soon as the subtasks are queued, so a scan started behind it would read a
    half-synced mirror and resolve live ACL facets as stragglers.  Schedule this
    on its own, and let the alert scan run on its own cron.
    """
    character_ids = _tracked_character_ids()
    for character_id in character_ids:
        sync_character_acl.apply_async(
            args=[character_id],
            countdown=randint(0, TASK_JITTER),  # noqa: S311
        )
    return len(character_ids)


@shared_task(
    name="aa_altcorp.tasks.sync_character_acl",
    ignore_result=True,
    **_once_options(key=["character_id"]),
)
def sync_character_acl(character_id):
    """Sync one character's ACLs. Guarded so a fan-out survives one failure."""
    try:
        return sync_character_access_lists(character_id)
    except _esi_error_types():
        logger.exception("Unable to synchronize ACLs for character %s", character_id)
        return 0


@shared_task(name="aa_altcorp.tasks.run_alert_scan", ignore_result=True, **_once_options())
def run_alert_scan():
    """Detect, suppress, reconcile, then hand the new alerts to delivery."""
    from .alerts import engine

    settings = AltCorpSettings.current()
    result = engine.run_scan(settings)
    if not result.ran:
        logger.debug("Alert scan skipped: alerts are disabled")
        return 0
    logger.info(
        "Alert scan %s: %s created, %s updated, %s suppressed, %s resolved (skipped: %s)",
        result.scan_id,
        result.created,
        result.updated,
        result.suppressed,
        result.resolved,
        result.skipped or "none",
    )
    deliver_pending_alerts.apply_async(countdown=5)
    return result.created


@shared_task(name="aa_altcorp.tasks.deliver_pending_alerts", ignore_result=True)
def deliver_pending_alerts(limit=None):
    """Deliver a batch of open alerts. One failure never aborts the batch."""
    from .discord import delivery

    settings = AltCorpSettings.current()
    alerts = list(delivery.pending_alerts(settings, limit=limit))
    if not alerts:
        return {}
    counts = delivery.deliver_alerts(alerts, settings)
    logger.info("Delivered alerts: %s", counts)
    return counts


@shared_task(name="aa_altcorp.tasks.refresh_alert_messages", ignore_result=True)
def refresh_alert_messages():
    """Re-render posted messages whose alert has narrowed or resolved.

    Covers the cases with no interaction to edit through: a scan changed the
    alert, or an admin revoked an exemption in Django admin.
    """
    from .alerts import taxonomy
    from .discord import delivery
    from .models import Alert

    stale = Alert.objects.filter(
        discord_message_id__isnull=False,
        state__in=(taxonomy.AlertState.SUPPRESSED, taxonomy.AlertState.RESOLVED),
    )
    refreshed = 0
    for alert in stale:
        if delivery.request_message_refresh(alert):
            refreshed += 1
    return refreshed
