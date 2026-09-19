"""Which entities Alliance Auth expects to hold contact standing.

Only feeds the ``missing`` direction.  The ``present`` direction needs no
expectation set: it iterates what actually has standing or access and asks who
owns it.

Scope spans all three tiers, so this is the loudest part of the engine on a
large install.  The four ``expect_*`` settings are the volume control, and the
configured standing target is excluded at its own tier -- an alliance is not a
contact of itself.
"""

from ..models import AltCharacter, AltCorporation
from . import taxonomy


def expected_contacts(settings, snapshot):
    """Return ``{(entity_type, entity_id): reason}`` for entities that should be blue."""
    if not snapshot.available:
        # Without Auth there is no association data, so nothing can be claimed
        # to be expected. Returning {} keeps the missing direction silent rather
        # than flagging the entire contact list.
        return {}

    expected = {}
    character_ids = _expected_character_ids(settings, snapshot)

    if settings.expect_contact_characters:
        for character_id in character_ids:
            facts = snapshot.characters.get(character_id)
            if facts and _is_in_standing_target(facts.alliance_id, settings):
                continue
            role = "main character" if facts and facts.is_main else "attached character"
            _add(expected, settings, taxonomy.EntityType.CHARACTER, character_id, role)

    if settings.expect_contact_corporations:
        for character_id in character_ids:
            facts = snapshot.characters.get(character_id)
            if facts and facts.corporation_id and not _is_in_standing_target(
                facts.alliance_id, settings
            ):
                _add(
                    expected,
                    settings,
                    taxonomy.EntityType.CORPORATION,
                    facts.corporation_id,
                    f"corporation of {facts.character_name or character_id}",
                )
        for corporation_id, corporation_name in _attached_corporations(snapshot):
            if _is_in_standing_target(snapshot.corp_alliance.get(int(corporation_id)), settings):
                continue
            _add(
                expected,
                settings,
                taxonomy.EntityType.CORPORATION,
                corporation_id,
                f"attached alt corporation {corporation_name}".strip(),
            )

    if settings.expect_contact_alliances:
        for character_id in character_ids:
            facts = snapshot.characters.get(character_id)
            if facts and facts.alliance_id:
                _add(
                    expected,
                    settings,
                    taxonomy.EntityType.ALLIANCE,
                    facts.alliance_id,
                    f"alliance of {facts.character_name or character_id}",
                )

    return expected


def _expected_character_ids(settings, snapshot):
    """Characters of valid-state users that the install cares about."""
    approved = snapshot.approved_user_ids
    if settings.expect_all_owned_characters:
        return {
            character_id
            for character_id, facts in snapshot.characters.items()
            if facts.user_id in approved
        }

    character_ids = {
        character_id
        for user_id, character_id in snapshot.main_character_of.items()
        if user_id in approved
    }
    character_ids.update(
        AltCharacter.objects.filter(user_id__in=approved).values_list("character_id", flat=True)
    )
    return character_ids


def _attached_corporations(snapshot):
    """Alt corporations explicitly attached by a valid-state user."""
    return AltCorporation.objects.filter(user_id__in=snapshot.approved_user_ids).values_list(
        "corporation_id", "corporation_name"
    )


def _add(expected, settings, entity_type, entity_id, reason):
    """Record an expectation unless the entity is the standing target itself."""
    entity_id = int(entity_id)
    if settings.standing_target_id and entity_id == int(settings.standing_target_id):
        if entity_type == settings.standing_target_type:
            return
    expected.setdefault((entity_type, entity_id), reason)


def _is_in_standing_target(alliance_id, settings):
    """Alliance standing covers its member characters and corporations."""
    return (
        settings.standing_target_type == taxonomy.EntityType.ALLIANCE
        and alliance_id is not None
        and settings.standing_target_id
        and int(alliance_id) == int(settings.standing_target_id)
    )
