"""Reconcile detected candidates against stored alerts and exemptions."""

import logging
import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import Alert, AlertFacet, AltCorpSettings, Exemption
from . import dedup, detect, taxonomy
from .context import AuthSnapshot

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    scan_id: uuid.UUID | None = None
    ran: bool = False
    created: int = 0
    updated: int = 0
    suppressed: int = 0
    resolved: int = 0
    contacts_available: bool = True
    acls_available: bool = True
    skipped: dict = field(default_factory=dict)


def active_exemptions(match_keys):
    """Exemptions currently suppressing any of ``match_keys``.

    Expiry is a query predicate rather than a cleanup task: the spec requires
    expired exemptions to stop suppressing *automatically*, which a predicate
    guarantees and a periodic task only approximates -- it would be silently
    wrong for as long as celery beat is down.  The expired row also stays
    intact for the audit trail.
    """
    if not match_keys:
        return {}
    now = timezone.now()
    rows = (
        Exemption.objects.filter(match_key__in=list(match_keys), revoked_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .order_by("created_at")
    )
    return {row.match_key: row for row in rows}


def run_scan(settings=None):
    """Detect, suppress, and reconcile. Returns a :class:`ScanResult`."""
    settings = settings or AltCorpSettings.current()
    result = ScanResult()
    if not settings.alerts_enabled:
        logger.debug("Alert scan skipped: alerts are disabled in settings")
        return result

    result.ran = True
    result.scan_id = uuid.uuid4()
    snapshot = AuthSnapshot.from_auth(settings)
    candidates, availability = detect.evaluate(settings, snapshot)
    result.contacts_available = availability.contacts
    result.acls_available = availability.acls
    result.skipped = dict(availability.skipped)

    match_keys = {
        dedup.candidate_facet_key(
            candidate.alert_type,
            candidate.entity_type,
            candidate.entity_id,
            facet.facet_type,
            facet.access_list_id,
        )
        for candidate in candidates
        for facet in candidate.facets
    }
    exemptions = active_exemptions(match_keys)

    with transaction.atomic():
        for candidate in candidates:
            _reconcile(candidate, exemptions, result)
        result.resolved = _resolve_stragglers(result, availability)
    return result


def _reconcile(candidate, exemptions, result):
    alert, created = Alert.objects.update_or_create(
        dedup_key=dedup.alert_dedup_key(
            candidate.alert_type, candidate.entity_type, candidate.entity_id
        ),
        defaults={
            "alert_type": candidate.alert_type,
            "entity_type": candidate.entity_type,
            "entity_id": candidate.entity_id,
            "entity_name": candidate.entity_name,
            "user_id": candidate.user_id,
            "summary": candidate.summary,
            "detail": candidate.detail,
            "scan_id": result.scan_id,
        },
    )
    if created:
        result.created += 1
    else:
        result.updated += 1

    seen = []
    for facet in candidate.facets:
        key = dedup.candidate_facet_key(
            candidate.alert_type,
            candidate.entity_type,
            candidate.entity_id,
            facet.facet_type,
            facet.access_list_id,
        )
        exemption = exemptions.get(key)
        row, _ = AlertFacet.objects.update_or_create(
            alert=alert,
            facet_type=facet.facet_type,
            access_list_id=facet.access_list_id,
            defaults={
                "access_list_name": facet.access_list_name,
                "reason_text": facet.reason_text,
                "detail": facet.detail,
                "state": (
                    taxonomy.AlertState.SUPPRESSED if exemption else taxonomy.AlertState.OPEN
                ),
                "suppressed_by": exemption,
                "resolved_at": None,
            },
        )
        if exemption:
            result.suppressed += 1
        seen.append(row.pk)

    # A facet the entity no longer trips is resolved, not deleted, so the
    # Discord message can still be narrowed and the history survives.
    alert.facets.exclude(pk__in=seen).exclude(state=taxonomy.AlertState.RESOLVED).update(
        state=taxonomy.AlertState.RESOLVED, resolved_at=timezone.now()
    )
    alert.recompute_state()


def _resolve_stragglers(result, availability):
    """Resolve alerts this scan did not see -- within covered facet kinds only.

    Skipping an unavailable source is a hard safety rule: without it, a single
    failed ESI sync or an uninstalled aa-contacts would mass-resolve every open
    alert and destroy the operator's board.
    """
    covered = []
    if availability.contacts:
        covered.append(taxonomy.FacetType.CONTACT)
    if availability.acls:
        covered.append(taxonomy.FacetType.ACL)
    if not covered:
        logger.warning("No alert source was readable this scan; resolving nothing")
        return 0

    stale = AlertFacet.objects.filter(
        facet_type__in=covered,
        state__in=(taxonomy.AlertState.OPEN, taxonomy.AlertState.SUPPRESSED),
    ).exclude(alert__scan_id=result.scan_id)
    alert_ids = set(stale.values_list("alert_id", flat=True))
    resolved = stale.update(state=taxonomy.AlertState.RESOLVED, resolved_at=timezone.now())

    for alert in Alert.objects.filter(pk__in=alert_ids):
        # discord_message_id is left intact so the message can still be edited.
        alert.recompute_state()
    return resolved
