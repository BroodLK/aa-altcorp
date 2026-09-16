"""Vocabulary shared by the alert engine, the admin, and the Discord layer.

Kept free of model imports on purpose: ``aa_altcorp.models`` imports this module
for its field choices, so importing anything from ``aa_altcorp.models`` here would
be circular.  Only ``django.db.models.TextChoices`` is used, which is safe.
"""

from django.db import models


class AlertType(models.TextChoices):
    """The three conditions the engine reports."""

    MISSING_FOR_VALID_USER = "missing_for_valid_user", "Missing for valid user"
    PRESENT_FOR_INVALID_USER = "present_for_invalid_user", "Present for invalid user"
    PRESENT_WITHOUT_AUTH_USER = "present_without_auth_user", "Present without Auth user"


class Direction(models.TextChoices):
    """Whether the entity has too little access/standing, or too much."""

    MISSING = "missing", "Missing"
    PRESENT = "present", "Present"


class FacetType(models.TextChoices):
    """The two axes an alert can fire on."""

    CONTACT = "contact", "Contact standing"
    ACL = "acl", "Access list"


class EntityType(models.TextChoices):
    CHARACTER = "character", "Character"
    CORPORATION = "corporation", "Corporation"
    ALLIANCE = "alliance", "Alliance"


class ActionType(models.TextChoices):
    """Auth-side actions offered in Discord.  None of these touch EVE."""

    MARK_EXEMPT = "MARK_EXEMPT", "Mark exempt"
    TEMPORARY_BLUE = "TEMPORARY_BLUE", "Temporary blue"
    TEMPORARY_ACL_EXEMPTION = "TEMPORARY_ACL_EXEMPTION", "Temporary ACL exemption"
    REMOVE_EXEMPTION = "REMOVE_EXEMPTION", "Remove exemption"


class ExemptionKind(models.TextChoices):
    """Mirrors the three creating actions; REMOVE_EXEMPTION creates no row."""

    MARK_EXEMPT = "mark_exempt", "Mark exempt"
    TEMPORARY_BLUE = "temporary_blue", "Temporary blue"
    TEMPORARY_ACL_EXEMPTION = "temporary_acl_exemption", "Temporary ACL exemption"


class AlertState(models.TextChoices):
    OPEN = "open", "Open"
    SUPPRESSED = "suppressed", "Suppressed"
    RESOLVED = "resolved", "Resolved"


class ExemptionSource(models.TextChoices):
    DISCORD = "discord", "Discord"
    ADMIN = "admin", "Django admin"


class DeliveryChannel(models.TextChoices):
    BOT = "bot", "Discord bot"
    WEBHOOK = "webhook", "Webhook"


class AlertDelivery(models.TextChoices):
    """How ``AltCorpSettings`` wants alerts delivered."""

    AUTO = "auto", "Automatic (bot when available, otherwise webhook)"
    BOT = "bot", "Discord bot only"
    WEBHOOK = "webhook", "Webhook only"
    NONE = "none", "Do not deliver"


#: Which direction each condition describes.  Conditions 2 and 3 differ only in
#: *why* the entity should not have access, so they share a direction -- and
#: therefore share exemptions.
DIRECTION_FOR_ALERT = {
    AlertType.MISSING_FOR_VALID_USER: Direction.MISSING,
    AlertType.PRESENT_FOR_INVALID_USER: Direction.PRESENT,
    AlertType.PRESENT_WITHOUT_AUTH_USER: Direction.PRESENT,
}

#: Which facets each action suppresses.  REMOVE_EXEMPTION is absent because it
#: revokes rather than creates.
FACET_FOR_ACTION = {
    ActionType.TEMPORARY_BLUE: (FacetType.CONTACT,),
    ActionType.TEMPORARY_ACL_EXEMPTION: (FacetType.ACL,),
    ActionType.MARK_EXEMPT: (FacetType.CONTACT, FacetType.ACL),
}

#: The exemption a given action creates.
KIND_FOR_ACTION = {
    ActionType.MARK_EXEMPT: ExemptionKind.MARK_EXEMPT,
    ActionType.TEMPORARY_BLUE: ExemptionKind.TEMPORARY_BLUE,
    ActionType.TEMPORARY_ACL_EXEMPTION: ExemptionKind.TEMPORARY_ACL_EXEMPTION,
}

#: Actions whose modal must produce an expiry.  MARK_EXEMPT may be permanent.
EXPIRY_REQUIRED = frozenset({ActionType.TEMPORARY_BLUE, ActionType.TEMPORARY_ACL_EXEMPTION})

#: Short codes used inside Discord ``custom_id`` values, which are capped at 100
#: characters.  Two characters each keeps the whole id well under the limit.
ACTION_CODES = {
    ActionType.MARK_EXEMPT: "me",
    ActionType.TEMPORARY_BLUE: "tb",
    ActionType.TEMPORARY_ACL_EXEMPTION: "ta",
    ActionType.REMOVE_EXEMPTION: "rx",
}
CODE_TO_ACTION = {code: action for action, code in ACTION_CODES.items()}

ALERT_LABELS = {
    AlertType.MISSING_FOR_VALID_USER: (
        "User is in a valid state, but the entity is missing required "
        "contact standing and/or ACL access"
    ),
    AlertType.PRESENT_FOR_INVALID_USER: (
        "User is not in a valid state, but the entity still has contact standing and/or ACL access"
    ),
    AlertType.PRESENT_WITHOUT_AUTH_USER: (
        "Entity has contact standing and/or ACL access, but is not associated with any Auth user"
    ),
}


def direction_for(alert_type):
    """Return the direction of an alert type, tolerating a plain string."""
    return DIRECTION_FOR_ALERT[AlertType(alert_type)]


def facets_for_action(action):
    """Return the facet types an action suppresses; empty for REMOVE_EXEMPTION."""
    return FACET_FOR_ACTION.get(ActionType(action), ())
