"""Stable keys for alert de-duplication and exemption matching.

Both sides of the suppression check are computed here so they cannot drift
apart.  Keys are readable composites rather than hashes: they show up in the
admin and in log lines, and debugging "why is this still alerting?" is much
easier when the key says what it is.

Both keys stay well inside the 190-character limit a MySQL ``utf8mb4`` index
imposes, even with the longest alert type and 19-digit EVE ids.
"""

from . import taxonomy

#: Placeholder for the ACL half of a contact facet, so the key keeps a fixed
#: shape and cannot collide with an ACL whose id happens to be empty.
NO_ACL = "-"

MAX_KEY_LENGTH = 190


def alert_dedup_key(alert_type, entity_type, entity_id):
    """Identify one alert: a condition applied to one entity."""
    return f"{alert_type}:{entity_type}:{entity_id}"


def facet_match_key(direction, entity_type, entity_id, facet_type, access_list_id=None):
    """Identify one suppressible facet.

    The direction is part of the key on purpose.  An exemption on a ``missing``
    alert says "it is fine that this entity is *not* blue"; on a ``present``
    alert it says the opposite.  Letting one suppress the other would silence a
    genuinely new problem.

    Conversely the alert *type* is deliberately absent: conditions 2 and 3 share
    the ``present`` direction, so an entity approved as a legitimate third-party
    blue stays approved if it later gains an invalid-state Auth owner.
    """
    acl = NO_ACL if access_list_id in (None, "") else access_list_id
    return f"{direction}:{entity_type}:{entity_id}:{facet_type}:{acl}"


def exemption_match_key(exemption):
    """Key for a stored exemption; assigned in ``Exemption.save``."""
    return facet_match_key(
        exemption.direction,
        exemption.entity_type,
        exemption.entity_id,
        exemption.facet_type,
        exemption.access_list_id,
    )


def candidate_facet_key(alert_type, entity_type, entity_id, facet_type, access_list_id=None):
    """Key a freshly detected facet would be suppressed by."""
    return facet_match_key(
        taxonomy.direction_for(alert_type),
        entity_type,
        entity_id,
        facet_type,
        access_list_id,
    )
