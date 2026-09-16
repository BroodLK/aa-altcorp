"""Auth-side effects of the Discord buttons.

Imports no Discord library on purpose: the whole action pipeline is plain
Django and can be unit tested without py-cord or a bot.

Nothing in this module touches EVE.  An exemption changes what Alliance Auth
alerts about; contacts and access lists are only ever edited in game.
"""

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from ..alerts import dedup, taxonomy
from ..models import Alert, AlertActionLog, AltCorpSettings, Exemption

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = "ac"
#: Discord caps custom_id at 100 characters. "ac:tb:" plus a pk is nowhere near
#: it, which is the point: the payload lives in the database, not the button.
CUSTOM_ID_MAX_LENGTH = 100

_RELATIVE_EXPIRY = re.compile(r"^(\d+)\s*([dhw])$", re.IGNORECASE)
_NEVER = {"never", "none", "permanent", "forever", ""}

EXPIRY_HELP = "1d, 3d, 7d, 14d, 30d, 90d, never, or a date as YYYY-MM-DD"


@dataclass
class Actor:
    """Who performed an action. Recorded on every audit row.

    ``user`` needs the annotation to be a dataclass field at all; it is typed
    loosely rather than as ``User`` so this module keeps no import of
    Alliance Auth's or Django's auth models at definition time.
    """

    discord_id: int | None = None
    discord_name: str = ""
    guild_id: int | None = None
    user: Any = None


# -- authorization ----------------------------------------------------------


def authorized_role_ids(settings=None):
    """Discord role IDs permitted to action alerts."""
    settings = settings or AltCorpSettings.current()
    role_ids = set()
    for entry in settings.discord_role_ids or []:
        try:
            role_ids.add(int(entry))
        except (TypeError, ValueError):
            logger.warning("Ignoring non-numeric Discord role id %r in settings", entry)
    return role_ids


def is_authorized(role_ids, settings=None):
    """True when the actor holds a configured role.

    Authorization is role-based only, as specified. Note the consequence: a
    role holder can create Auth-side exemptions without any Django permission.
    """
    allowed = authorized_role_ids(settings)
    if not allowed:
        return False
    try:
        held = {int(role_id) for role_id in role_ids}
    except (TypeError, ValueError):
        return False
    return bool(held & allowed)


def resolve_actor_user(discord_id):
    """Best-effort Discord -> Auth user, for the audit trail only.

    Never gates the action: authorization is the role check above.
    """
    if not discord_id:
        return None
    try:
        from aadiscordbot.utils.auth import get_auth_user
    except (ImportError, RuntimeError):
        return None
    try:
        return get_auth_user(int(discord_id))
    except Exception:  # noqa: BLE001 - NotAuthenticated and friends are bot-side
        logger.debug("No Auth user linked to Discord id %s", discord_id)
        return None


# -- custom_id codec --------------------------------------------------------


def encode_custom_id(alert_pk, action):
    code = taxonomy.ACTION_CODES[taxonomy.ActionType(action)]
    value = f"{CUSTOM_ID_PREFIX}:{code}:{int(alert_pk)}"
    if len(value) > CUSTOM_ID_MAX_LENGTH:  # pragma: no cover - unreachable in practice
        raise ValueError(f"custom_id too long: {value}")
    return value


def decode_custom_id(value):
    """Return ``(alert_pk, action)``, or ``None`` when this is not our button."""
    if not value or not value.startswith(f"{CUSTOM_ID_PREFIX}:"):
        return None
    try:
        _, code, raw_pk = value.split(":", 2)
        return int(raw_pk), taxonomy.CODE_TO_ACTION[code]
    except (KeyError, ValueError):
        logger.debug("Unrecognised alert custom_id %r", value)
        return None


# -- expiry parsing ---------------------------------------------------------


def parse_expiry(text, now=None):
    """Parse the modal's free-text expiry into a datetime, or ``None`` for never.

    Discord modals cannot contain buttons, so the spec's expiry button row is a
    parsed text field instead; the offered values become the placeholder.
    """
    now = now or timezone.now()
    value = (text or "").strip().lower()
    if value in _NEVER:
        return None
    match = _RELATIVE_EXPIRY.match(value)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        if amount <= 0:
            raise ValueError(f"Expiry must be in the future. Try {EXPIRY_HELP}.")
        delta = {
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return now + delta
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"Could not read {text!r} as an expiry. Try {EXPIRY_HELP}.")
    if parsed <= now:
        raise ValueError(f"{text!r} is in the past. Try {EXPIRY_HELP}.")
    return parsed


def _parse_date(value):
    from django.utils.dateparse import parse_date, parse_datetime

    parsed = parse_datetime(value)
    if parsed is None:
        date = parse_date(value)
        if date is None:
            return None
        from datetime import datetime, time

        parsed = datetime.combine(date, time.max)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_default_timezone())
    return parsed


# -- available actions ------------------------------------------------------


def open_facet_types(alert):
    return set(
        alert.facets.filter(state=taxonomy.AlertState.OPEN).values_list("facet_type", flat=True)
    )


def available_actions(alert):
    """Which buttons this alert should show right now.

    Facet-driven, so exempting one facet removes only that button and the
    message narrows to the problem that is left.
    """
    open_types = open_facet_types(alert)
    actions = []
    if open_types:
        actions.append(taxonomy.ActionType.MARK_EXEMPT)
    if taxonomy.FacetType.CONTACT in open_types:
        actions.append(taxonomy.ActionType.TEMPORARY_BLUE)
    if taxonomy.FacetType.ACL in open_types:
        actions.append(taxonomy.ActionType.TEMPORARY_ACL_EXEMPTION)
    if alert.facets.filter(state=taxonomy.AlertState.SUPPRESSED).exists():
        actions.append(taxonomy.ActionType.REMOVE_EXEMPTION)
    return tuple(actions)


def action_label(alert, action):
    """ "Mark Both Exempt" only when both facet kinds are actually open."""
    action = taxonomy.ActionType(action)
    if action is taxonomy.ActionType.MARK_EXEMPT:
        if len(open_facet_types(alert)) > 1:
            return "Mark Both Exempt"
        return "Mark Exempt"
    return action.label


# -- the transaction --------------------------------------------------------


@transaction.atomic
def apply_action(alert, action, reason, expiry_text=None, actor=None):
    """Create exemptions for the targeted facets and log the action.

    Returns ``(exemptions, log)``.
    """
    action = taxonomy.ActionType(action)
    actor = actor or Actor()
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A reason is required.")
    if action not in available_actions(alert):
        # Guards against a stale button on an old message.
        raise ValueError("That action is no longer available for this alert.")
    if action is taxonomy.ActionType.REMOVE_EXEMPTION:
        return [], remove_exemption(alert, reason, actor)

    expires_at = parse_expiry(expiry_text)
    if expires_at is None and action in taxonomy.EXPIRY_REQUIRED:
        raise ValueError(f"{action.label} needs an expiry. Try {EXPIRY_HELP}.")

    wanted = taxonomy.facets_for_action(action)
    facets = list(alert.facets.filter(state=taxonomy.AlertState.OPEN, facet_type__in=wanted))
    if not facets:
        raise ValueError("There is nothing open for that action to cover.")

    direction = alert.direction
    created = []
    for facet in facets:
        exemption = create_exemption(
            kind=taxonomy.KIND_FOR_ACTION[action],
            direction=direction,
            alert=alert,
            facet=facet,
            reason=reason,
            expires_at=expires_at,
            actor=actor,
        )
        facet.state = taxonomy.AlertState.SUPPRESSED
        facet.suppressed_by = exemption
        facet.save(update_fields=("state", "suppressed_by", "last_seen_at"))
        created.append(exemption)

    log = _log_action(
        action=action,
        alert=alert,
        facets=facets,
        reason=reason,
        expires_at=expires_at,
        actor=actor,
    )
    log.exemptions.set(created)
    alert.recompute_state()
    return created, log


def create_exemption(*, kind, direction, alert, facet, reason, expires_at, actor):
    """Insert one exemption, revoking any active duplicate first.

    Stands in for a partial unique index on ``match_key``: MySQL ignores the
    condition on one, and Alliance Auth is overwhelmingly MySQL.
    """
    match_key = dedup.facet_match_key(
        direction, alert.entity_type, alert.entity_id, facet.facet_type, facet.access_list_id
    )
    now = timezone.now()
    Exemption.objects.filter(match_key=match_key, revoked_at__isnull=True).update(
        revoked_at=now, revoked_reason="Superseded by a newer exemption"
    )
    return Exemption.objects.create(
        kind=kind,
        direction=direction,
        facet_type=facet.facet_type,
        entity_type=alert.entity_type,
        entity_id=alert.entity_id,
        entity_name=alert.entity_name,
        access_list_id=facet.access_list_id,
        access_list_name=facet.access_list_name,
        reason=reason,
        expires_at=expires_at,
        created_by=actor.user,
        created_by_discord_id=actor.discord_id,
        created_by_discord_name=actor.discord_name,
        created_by_guild_id=actor.guild_id,
        source=(
            taxonomy.ExemptionSource.DISCORD if actor.discord_id else taxonomy.ExemptionSource.ADMIN
        ),
        source_alert=alert,
    )


@transaction.atomic
def remove_exemption(alert, reason, actor=None):
    """Revoke this alert's exemptions and reopen its facets.

    Normal evaluation resumes immediately: the facets go back to open now, and
    the next scan re-derives them from live data.
    """
    actor = actor or Actor()
    reason = (reason or "").strip() or "Exemption removed"
    facets = list(alert.facets.filter(state=taxonomy.AlertState.SUPPRESSED))
    exemption_ids = [facet.suppressed_by_id for facet in facets if facet.suppressed_by_id]

    revoke_exemptions(
        Exemption.objects.filter(pk__in=exemption_ids, revoked_at__isnull=True),
        reason=reason,
        user=actor.user,
        discord_id=actor.discord_id,
    )
    for facet in facets:
        facet.state = taxonomy.AlertState.OPEN
        facet.suppressed_by = None
        facet.save(update_fields=("state", "suppressed_by", "last_seen_at"))

    log = _log_action(
        action=taxonomy.ActionType.REMOVE_EXEMPTION,
        alert=alert,
        facets=facets,
        reason=reason,
        expires_at=None,
        actor=actor,
    )
    log.exemptions.set(exemption_ids)
    alert.recompute_state()
    return log


def revoke_exemptions(queryset, reason, user=None, discord_id=None):
    """Mark exemptions revoked. Rows are kept: the audit trail is not deleted."""
    return queryset.update(
        revoked_at=timezone.now(),
        revoked_by=user if user is not None and user.is_authenticated else None,
        revoked_by_discord_id=discord_id,
        revoked_reason=reason,
    )


def _log_action(*, action, alert, facets, reason, expires_at, actor):
    return AlertActionLog.objects.create(
        action=action,
        alert=alert,
        alert_type=alert.alert_type,
        entity_type=alert.entity_type,
        entity_id=alert.entity_id,
        entity_name=alert.entity_name,
        facets_affected=[
            {
                "facet_type": facet.facet_type,
                "access_list_id": facet.access_list_id,
                "access_list_name": facet.access_list_name,
            }
            for facet in facets
        ],
        reason=reason,
        expires_at=expires_at,
        actor_user=actor.user,
        actor_discord_id=actor.discord_id,
        actor_discord_name=actor.discord_name,
        actor_guild_id=actor.guild_id,
    )


def alert_for(alert_pk):
    return Alert.objects.filter(pk=alert_pk).prefetch_related("facets").first()
