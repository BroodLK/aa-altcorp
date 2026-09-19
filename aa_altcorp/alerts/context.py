"""A read-only snapshot of Alliance Auth, taken once per scan.

The detector runs entirely against this dataclass rather than against the ORM.
That is what lets every detector test run under ``tests.settings``, where
``allianceauth`` is not installed: tests build a snapshot from plain dicts and
never touch :meth:`AuthSnapshot.from_auth`, which is the only part that imports
Alliance Auth.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CharacterFacts:
    """What the engine needs to know about one character."""

    character_id: int
    character_name: str = ""
    user_id: int | None = None
    state_name: str = ""
    corporation_id: int | None = None
    alliance_id: int | None = None
    is_main: bool = False


@dataclass
class AuthSnapshot:
    """Entity-to-Auth associations, resolved up front and reused all scan."""

    characters: dict[int, CharacterFacts] = field(default_factory=dict)
    #: corporation id -> the Auth user ids owning characters in it
    corporations: dict[int, set[int]] = field(default_factory=dict)
    #: alliance id -> the Auth user ids owning characters in it
    alliances: dict[int, set[int]] = field(default_factory=dict)
    approved_user_ids: set[int] = field(default_factory=set)
    main_character_of: dict[int, int] = field(default_factory=dict)
    user_states: dict[int, str] = field(default_factory=dict)
    user_groups: dict[int, set[str]] = field(default_factory=dict)
    #: corporation id -> alliance id, from the local EveCorporationInfo cache
    corp_alliance: dict[int, int | None] = field(default_factory=dict)
    #: (entity type, entity id) -> local EVE name
    entity_names: dict[tuple[str, int], str] = field(default_factory=dict)
    available: bool = True

    @classmethod
    def empty(cls):
        """A snapshot that knows nothing, used when Alliance Auth is absent."""
        return cls(available=False)

    # -- association lookups ------------------------------------------------

    def users_for(self, entity_type, entity_id):
        """Every Auth user id associated with an entity, at any tier."""
        if entity_type == "character":
            facts = self.characters.get(entity_id)
            return {facts.user_id} if facts and facts.user_id else set()
        if entity_type == "corporation":
            return set(self.corporations.get(entity_id, ()))
        if entity_type == "alliance":
            # Alliances are EVE entities, never Auth users.  Their membership
            # is useful for policy expansion, but must not imply a user link.
            return set()
        return set()

    def is_associated(self, entity_type, entity_id):
        return bool(self.users_for(entity_type, entity_id))

    def has_approved_user(self, entity_type, entity_id):
        """True when *any* owner is in an approved state.

        Deliberately permissive: a corporation with one lapsed member and nine
        current ones is still legitimately blue, and the stricter reading would
        alert on almost every corporation.
        """
        return bool(self.users_for(entity_type, entity_id) & self.approved_user_ids)

    def state_names_for(self, entity_type, entity_id):
        return sorted(
            {
                self.user_states.get(user_id, "")
                for user_id in self.users_for(entity_type, entity_id)
            }
        )

    # -- construction from Alliance Auth ------------------------------------

    @classmethod
    def from_auth(cls, settings):
        """Build a snapshot in three queries, or an empty one without Auth."""
        try:
            from allianceauth.authentication.models import CharacterOwnership
            from allianceauth.eveonline.models import (
                EveAllianceInfo,
                EveCharacter,
                EveCorporationInfo,
            )
        except (ImportError, RuntimeError):
            # A missing INSTALLED_APPS entry raises RuntimeError, not ImportError.
            logger.debug("Alliance Auth is unavailable; alert scan has no association data")
            return cls.empty()

        approved_states = {str(name) for name in (settings.approved_states or [])}
        snapshot = cls()

        ownerships = CharacterOwnership.objects.select_related(
            "character",
            "user",
            "user__profile",
            "user__profile__main_character",
            "user__profile__state",
        )
        for ownership in ownerships:
            character = ownership.character
            if character is None:
                continue
            profile = getattr(ownership.user, "profile", None)
            state = getattr(profile, "state", None)
            state_name = getattr(state, "name", "") or ""
            main = getattr(profile, "main_character", None)
            character_id = int(character.character_id)
            user_id = ownership.user_id

            corporation_id = _as_int(getattr(character, "corporation_id", None))
            alliance_id = _as_int(getattr(character, "alliance_id", None))
            snapshot.characters[character_id] = CharacterFacts(
                character_id=character_id,
                character_name=getattr(character, "character_name", "") or "",
                user_id=user_id,
                state_name=state_name,
                corporation_id=corporation_id,
                alliance_id=alliance_id,
                is_main=main is not None and main.pk == character.pk,
            )
            if corporation_id:
                snapshot.corporations.setdefault(corporation_id, set()).add(user_id)
            if alliance_id:
                snapshot.alliances.setdefault(alliance_id, set()).add(user_id)

            snapshot.user_states[user_id] = state_name
            # No approved_states configured means every state is acceptable,
            # matching how audit_relationship already reads the setting.
            if not approved_states or state_name in approved_states:
                snapshot.approved_user_ids.add(user_id)
            if main is not None:
                snapshot.main_character_of[user_id] = int(main.character_id)

        # AltCorporation is the plugin's durable relationship record.  It can
        # exist before Alliance Auth has a current CharacterOwnership row (or
        # after that row has gone stale), but it still identifies the Auth user
        # attached to the corporation.  Include it in the same association map
        # used by users_for() so alert displays agree with the relationships
        # page.
        from ..models import AltCorporation
        from ..models import AltCharacter

        for corporation_id, user_id in AltCorporation.objects.values_list(
            "corporation_id", "user_id"
        ):
            user_id = int(user_id)
            snapshot.corporations.setdefault(int(corporation_id), set()).add(user_id)
            # An AltCorporation row is the plugin's explicit approval of this
            # relationship.  Preserve that approval even when the user's
            # current CharacterOwnership data is unavailable.
            snapshot.approved_user_ids.add(user_id)

        # Apply the same relationship fallback for explicitly linked
        # characters.  Character alerts use snapshot.characters directly, so
        # without this merge an AltCharacter can appear on the relationships
        # page while the alert still reports no Auth user.
        for character_id, character_name, user_id in AltCharacter.objects.values_list(
            "character_id", "character_name", "user_id"
        ):
            character_id = int(character_id)
            user_id = int(user_id)
            if character_id not in snapshot.characters:
                snapshot.characters[character_id] = CharacterFacts(
                    character_id=character_id,
                    character_name=character_name or "",
                    user_id=user_id,
                )
            snapshot.approved_user_ids.add(user_id)

        snapshot.user_groups = _user_groups(set(snapshot.user_states))
        snapshot.corp_alliance = {
            int(corp_id): _as_int(alliance_id)
            for corp_id, alliance_id in EveCorporationInfo.objects.values_list(
                "corporation_id", "alliance__alliance_id"
            )
        }
        snapshot.entity_names.update(
            {
                ("character", int(entity_id)): name
                for entity_id, name in EveCharacter.objects.values_list(
                    "character_id", "character_name"
                )
                if name
            }
        )
        snapshot.entity_names.update(
            {
                ("corporation", int(entity_id)): name
                for entity_id, name in EveCorporationInfo.objects.values_list(
                    "corporation_id", "corporation_name"
                )
                if name
            }
        )
        snapshot.entity_names.update(
            {
                ("alliance", int(entity_id)): name
                for entity_id, name in EveAllianceInfo.objects.values_list(
                    "alliance_id", "alliance_name"
                )
                if name
            }
        )
        return snapshot


def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _user_groups(user_ids):
    """Group names per user, in one query."""
    if not user_ids:
        return {}
    from django.contrib.auth.models import User

    groups = {}
    rows = User.groups.through.objects.filter(user_id__in=user_ids).values_list(
        "user_id", "group__name"
    )
    for user_id, group_name in rows:
        groups.setdefault(user_id, set()).add(group_name)
    return groups
