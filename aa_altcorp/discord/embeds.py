"""Discord message bodies, built as plain dicts.

No Discord import: ``send_channel_message_by_discord_id`` wants
``embed.to_dict()`` anyway, and building the dict directly keeps this module
importable on an install with no bot -- and testable without py-cord.

Every value is clamped to Discord's documented limits.  An ACL with a very
long name, a 2000-character reason, or an entity sitting on forty access lists
would otherwise produce a silent HTTP 400 at send time.
"""

from ..alerts import taxonomy

TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FIELD_LIMIT = 25
FOOTER_LIMIT = 2048

COLOUR_OPEN = 0xC0392B
COLOUR_RESOLVED = 0x27AE60

#: Reserve a slot for the "and N more" line when facets overflow.
_MAX_FACET_FIELDS = FIELD_LIMIT - 4


def _clip(text, limit):
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _field(name, value, inline=False):
    return {
        "name": _clip(name, FIELD_NAME_LIMIT),
        "value": _clip(value or "—", FIELD_VALUE_LIMIT),
        "inline": inline,
    }


def entity_label(alert):
    name = alert.entity_name or f"{alert.entity_type} {alert.entity_id}"
    return f"{name} ({alert.entity_type} {alert.entity_id})"


def entity_image_url(alert):
    """Return the EVE image endpoint appropriate for the alert entity."""
    paths = {
        taxonomy.EntityType.CHARACTER: "characters/{}/portrait",
        taxonomy.EntityType.CORPORATION: "corporations/{}/logo",
        taxonomy.EntityType.ALLIANCE: "alliances/{}/logo",
    }
    path = paths.get(alert.entity_type)
    if not path:
        return ""
    return f"https://images.evetech.net/{path.format(int(alert.entity_id))}?size=128"


def open_facets(alert):
    return [f for f in alert.facets.all() if f.state == taxonomy.AlertState.OPEN]


def alert_embed(alert):
    """The alert as it stands right now, listing only unresolved facets."""
    facets = open_facets(alert)
    label = entity_label(alert)
    main_name = alert.detail.get("main_character_name")
    if main_name and alert.entity_type == taxonomy.EntityType.CHARACTER:
        label = f"Main's character: {label}"
    elif main_name and alert.entity_type == taxonomy.EntityType.CORPORATION:
        label = f"Main's corporation: {label}"
    fields = []

    users = alert.detail.get("users") or []
    states = [s for s in (alert.detail.get("states") or []) if s]
    if users and states:
        fields.append(_field("Auth User", f"State: {', '.join(states)}", True))
    elif users:
        fields.append(_field("Auth User", "State: unknown", True))
    else:
        fields.append(_field("Auth User", "none", True))

    for facet in facets[:_MAX_FACET_FIELDS]:
        fields.append(_field(_facet_name(facet), facet.reason_text))
    if len(facets) > _MAX_FACET_FIELDS:
        fields.append(_field("More", f"and {len(facets) - _MAX_FACET_FIELDS} further access lists"))

    suppressed = [f for f in alert.facets.all() if f.state == taxonomy.AlertState.SUPPRESSED]
    if suppressed:
        fields.append(
            _field(
                "Already exempted",
                ", ".join(_facet_name(facet) for facet in suppressed),
            )
        )

    embed = {
        "title": _clip(alert.summary or label, TITLE_LIMIT),
        "description": _clip(_alert_description(alert), DESCRIPTION_LIMIT),
        "color": COLOUR_OPEN,
        "fields": fields[:FIELD_LIMIT],
        "footer": {
            "text": _clip(
                "Informational only. This app never changes EVE contacts or access lists.",
                FOOTER_LIMIT,
            )
        },
    }
    image_url = entity_image_url(alert)
    if image_url:
        embed["thumbnail"] = {"url": image_url}
    return embed


def resolved_embed(alert, log):
    """The "RESOLVED IN AUTH" block, shown once every facet is covered."""
    covered = ", ".join(
        _facet_label(
            entry["facet_type"], entry.get("access_list_name"), entry.get("access_list_id")
        )
        for entry in (log.facets_affected or [])
    )
    expires = log.expires_at.strftime("%Y-%m-%d %H:%M UTC") if log.expires_at else "Never"
    actor = log.actor_discord_name or (
        str(log.actor_discord_id) if log.actor_discord_id else "unknown"
    )
    embed = {
        "title": "RESOLVED IN AUTH",
        "description": _clip(
            "The Auth-side alert is suppressed. Nothing was changed in EVE.",
            DESCRIPTION_LIMIT,
        ),
        "color": COLOUR_RESOLVED,
        "fields": [
            _field("Action", log.get_action_display(), True),
            _field("Entity", entity_label(alert), True),
            _field("Covers", covered or "—"),
            _field("Reason", log.reason),
            _field("Expires", expires, True),
            _field("Actioned by", actor, True),
        ],
    }
    image_url = entity_image_url(alert)
    if image_url:
        embed["thumbnail"] = {"url": image_url}
    return embed


def resolved_alert_embed(alert):
    """Render a condition that cleared during a later scan."""
    embed = {
        "title": _clip(f"Resolved: {entity_label(alert)}", TITLE_LIMIT),
        "description": _clip(
            "This alert condition is no longer present. The alert was cleared automatically.",
            DESCRIPTION_LIMIT,
        ),
        "color": COLOUR_RESOLVED,
        "fields": [_field("Entity", entity_label(alert))],
        "footer": {
            "text": _clip(
                "Informational only. This app never changes EVE contacts or access lists.",
                FOOTER_LIMIT,
            )
        },
    }
    image_url = entity_image_url(alert)
    if image_url:
        embed["thumbnail"] = {"url": image_url}
    return embed


def alert_summary_line(alert):
    """One-line webhook body for installs without the bot."""
    facets = open_facets(alert)
    detail = "; ".join(f"{_facet_name(f)}: {f.reason_text}" for f in facets)
    return _clip(
        f"[aa-altcorp] {alert.summary or entity_label(alert)} - {detail}"
        "\nActions require the allianceauth-discordbot integration.",
        2000,
    )


def _facet_name(facet):
    return _facet_label(facet.facet_type, facet.access_list_name, facet.access_list_id)


def _alert_description(alert):
    kinds = {facet.facet_type for facet in open_facets(alert)}
    if kinds == {taxonomy.FacetType.CONTACT}:
        return "The entity is missing required contact standings."
    if kinds == {taxonomy.FacetType.ACL}:
        return "The entity is missing required ACL access."
    if taxonomy.FacetType.CONTACT in kinds and taxonomy.FacetType.ACL in kinds:
        return "The entity is missing required contact standings and ACL access."
    return taxonomy.ALERT_LABELS.get(alert.alert_type, "")


def _facet_label(facet_type, access_list_name, access_list_id):
    if facet_type == taxonomy.FacetType.ACL:
        return f"ACL {access_list_name or access_list_id}"
    return "Contact standing"


def _states_suffix(states):
    return f" (state: {', '.join(states)})" if states else ""
