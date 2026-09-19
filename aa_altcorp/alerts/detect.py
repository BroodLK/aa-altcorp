"""Turn Alliance Auth, aa-contacts and the ACL mirror into candidate alerts.

One module rather than two detectors, because an alert is keyed on the *entity*
and spans both facets: the contact and ACL facts have to be joined per entity
before anything can be emitted.

Nothing here writes to the database or talks to EVE.
"""

import logging
from dataclasses import dataclass, field

from .. import services
from ..models import AccessListPolicy, CharacterAccessList
from . import policies, taxonomy

logger = logging.getLogger(__name__)

#: ESI access levels that actually grant access.  "Blocked" is a deny, and
#: "Unspecified" is neither -- listed but inert.
ALLOWING = frozenset({"Admin", "Manager", "Allowed"})

#: Membership group name -> (id field, entity type), mirroring services.MEMBERSHIP_GROUPS.
GROUP_TIERS = {
    "characters": ("character_id", taxonomy.EntityType.CHARACTER),
    "corporations": ("corporation_id", taxonomy.EntityType.CORPORATION),
    "alliances": ("alliance_id", taxonomy.EntityType.ALLIANCE),
}


@dataclass(frozen=True)
class CandidateFacet:
    facet_type: str
    access_list_id: int | None = None
    access_list_name: str = ""
    reason_text: str = ""
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateAlert:
    alert_type: str
    entity_type: str
    entity_id: int
    entity_name: str = ""
    user_id: int | None = None
    summary: str = ""
    detail: dict = field(default_factory=dict)
    facets: tuple = ()


@dataclass
class Availability:
    """Which data sources this scan actually managed to read.

    The engine refuses to resolve alerts belonging to an unavailable source, so
    one failed ESI sync cannot mass-resolve the whole board.
    """

    contacts: bool = True
    acls: bool = True
    skipped: dict = field(default_factory=dict)

    def skip(self, reason, count=1):
        self.skipped[reason] = self.skipped.get(reason, 0) + count


def evaluate(settings, snapshot):
    """Return ``(candidates, availability)`` for one scan."""
    availability = Availability()
    blue, contact_names = _blue_contacts(settings, availability)
    acl_access, acl_names, acl_rows = _acl_access(availability)
    # Queried once and used by both directions: alert_missing gates the expected
    # set below, alert_unexpected gates the present facets per entity.
    policies_by_acl = _enabled_policies()
    expected_contacts = _expected_contacts(settings, snapshot)
    expected_access = _expected_access(policies_by_acl, snapshot, blue, acl_rows, availability)

    entities = set(blue) | set(expected_contacts) | set(acl_access)
    for tier_map in expected_access.values():
        for entity_type, ids in tier_map.items():
            entities.update((entity_type, entity_id) for entity_id in ids)

    candidates = []
    for entity_type, entity_id in sorted(entities):
        candidate = _evaluate_entity(
            settings=settings,
            snapshot=snapshot,
            entity_type=entity_type,
            entity_id=entity_id,
            blue=blue,
            contact_names=contact_names,
            acl_access=acl_access,
            acl_names=acl_names,
            expected_contacts=expected_contacts,
            expected_access=expected_access,
            policies_by_acl=policies_by_acl,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates, availability


# -- fact gathering ---------------------------------------------------------


def _blue_contacts(settings, availability):
    """``{(entity_type, entity_id): standing}`` for everything currently blue."""
    scope, contacts, reason = services.configured_contacts(settings)
    if reason:
        # Every reason means the contact list cannot be trusted as ground truth,
        # so the engine must not resolve contact facets from it.  That includes
        # CONTACTS_NOT_SYNCED, which configured_contacts also returns for an
        # empty list: a configured standing target with zero contacts is far
        # more likely to be a sync that has not run than an alliance that
        # genuinely has no contacts, and guessing wrong would resolve the whole
        # board at once.
        availability.contacts = False
        availability.skip(f"contacts_{reason}")
        return {}, {}

    floor = settings.minimum_blue_standing
    blue, names = {}, {}
    for contact in contacts:
        entity_type = contact.contact_type
        if entity_type not in taxonomy.EntityType.values:
            # Factions have no Auth association, so no condition applies.
            continue
        key = (entity_type, int(contact.contact_id))
        standing = contact.standing or 0.0
        # Reading contact.contact_name would fall back to a per-row ESI fetch
        # for any entity not cached locally, so use only the annotation.
        names[key] = getattr(contact, "contact_name_annotation", "") or ""
        if standing == 0.0 and settings.treat_zero_standing_as_removed:
            continue
        if standing >= floor:
            blue[key] = standing
    return blue, names


def _acl_access(availability):
    """Who currently has access to each ACL, de-duplicated across viewers.

    The same ACL seen through three linked characters is three rows but one
    access list, so keep the newest and remember which characters saw it.
    """
    rows = {}
    membership_rows = {}
    seen_via = {}
    for row in CharacterAccessList.objects.all():
        seen_via.setdefault(row.access_list_id, []).append(row.character_id)
        membership_rows.setdefault(row.access_list_id, []).append(row)
        existing = rows.get(row.access_list_id)
        # synced_at is auto_now, so it is set in practice -- but a row loaded
        # from a fixture may not have it, and comparing against None raises.
        if existing is None or existing.synced_at is None:
            rows[row.access_list_id] = row
        elif row.synced_at is not None and row.synced_at > existing.synced_at:
            rows[row.access_list_id] = row

    access = {}
    names = {}
    prepared = {}
    for access_list_id, row in rows.items():
        names[access_list_id] = row.name or f"ACL {access_list_id}"
        memberships = [item.membership or {} for item in membership_rows[access_list_id]]
        allowed = set()
        for group, (id_field, entity_type) in GROUP_TIERS.items():
            for membership in memberships:
                for entry in membership.get(group) or ():
                    entity_id = entry.get(id_field)
                    if not entity_id:
                        continue
                    if entry.get("access") in ALLOWING:
                        allowed.add((entity_type, int(entity_id)))
        prepared[access_list_id] = {
            "allow_everyone": any(item.get("allow_everyone") for item in memberships),
            "allowed": allowed,
            "seen_via": sorted(set(seen_via.get(access_list_id, ()))),
        }
        for key in allowed:
            access.setdefault(key, set()).add(access_list_id)

    if not rows:
        # An empty mirror means the ACL side cannot be trusted as ground truth,
        # exactly as for contacts above: a sync that has never run, or whose
        # tokens have all lapsed, is far more likely than every access list in
        # the install genuinely being empty.  Without this the engine would
        # resolve every open ACL facet on the board in one scan.
        availability.acls = False
        availability.skip("acls_never_synced")
    return access, names, prepared


def _expected_contacts(settings, snapshot):
    from . import expect

    return expect.expected_contacts(settings, snapshot)


def _enabled_policies():
    """``{access_list_id: policy}`` for every enabled ACL policy.

    A *disabled* policy is deliberately absent, so it behaves exactly like no
    policy at all: no "missing access" claims, and "present but unjustified"
    alerts still fire.
    """
    return {
        policy.access_list_id: policy for policy in AccessListPolicy.objects.filter(enabled=True)
    }


def _expected_access(policies_by_acl, snapshot, blue, acl_rows, availability):
    """``{access_list_id: {entity_type: {entity_id: reason}}}`` for policied ACLs."""
    expected = {}
    for access_list_id, prepared in acl_rows.items():
        policy = policies_by_acl.get(access_list_id)
        if policy is None or not policy.alert_missing:
            continue
        if prepared["allow_everyone"]:
            # Everyone already has access; nothing can be missing from it.
            availability.skip("acl_allow_everyone")
            continue
        resolved = policies.expected_membership(policy, snapshot, blue)
        expected[access_list_id] = {
            taxonomy.EntityType.CHARACTER: resolved.characters,
            taxonomy.EntityType.CORPORATION: resolved.corporations,
            taxonomy.EntityType.ALLIANCE: resolved.alliances,
        }
    return expected


# -- per-entity classification ----------------------------------------------


def _evaluate_entity(
    *,
    settings,
    snapshot,
    entity_type,
    entity_id,
    blue,
    contact_names,
    acl_access,
    acl_names,
    expected_contacts,
    expected_access,
    policies_by_acl,
):
    key = (entity_type, entity_id)
    if _is_standing_target(settings, key):
        return None
    users = snapshot.users_for(entity_type, entity_id)
    approved = snapshot.has_approved_user(entity_type, entity_id)

    if approved:
        alert_type = taxonomy.AlertType.MISSING_FOR_VALID_USER
    elif users:
        alert_type = taxonomy.AlertType.PRESENT_FOR_INVALID_USER
    else:
        alert_type = taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER

    direction = taxonomy.direction_for(alert_type)
    if direction is taxonomy.Direction.MISSING:
        facets = _missing_facets(
            settings,
            snapshot,
            key,
            blue,
            acl_access,
            acl_names,
            expected_contacts,
            expected_access,
        )
    else:
        facets = _present_facets(key, blue, acl_access, acl_names, policies_by_acl)

    if not facets:
        return None

    entity_name = contact_names.get(key) or _snapshot_name(snapshot, entity_type, entity_id)
    user_id = _primary_user(snapshot, users, approved)
    detail = {
        "users": sorted(users),
        "states": snapshot.state_names_for(entity_type, entity_id),
        "approved": approved,
    }
    main_character = _main_character(snapshot, users)
    if main_character:
        detail["main_character_name"] = main_character.character_name
        detail["main_character_id"] = main_character.character_id
    if direction is taxonomy.Direction.MISSING:
        detail["expected_because"] = expected_contacts.get(key, "")
    else:
        detail["standing"] = blue.get(key)

    summary = _summary(alert_type, entity_type, entity_name, entity_id, facets)
    if (
        main_character
        and main_character.character_name
        and entity_type
        in (
            taxonomy.EntityType.CHARACTER,
            taxonomy.EntityType.CORPORATION,
        )
    ):
        prefix = (
            f"{main_character.character_name}'s character"
            if entity_type == taxonomy.EntityType.CHARACTER
            else f"{main_character.character_name}'s corporation"
        )
        summary = f"{prefix} {summary}"

    return CandidateAlert(
        alert_type=alert_type,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        user_id=user_id,
        summary=summary,
        detail=detail,
        facets=tuple(facets),
    )


def _is_standing_target(settings, key):
    entity_type, entity_id = key
    return bool(
        settings.standing_target_id
        and entity_type == settings.standing_target_type
        and int(entity_id) == int(settings.standing_target_id)
    )


def _missing_facets(
    settings, snapshot, key, blue, acl_access, acl_names, expected_contacts, expected_access
):
    entity_type, entity_id = key
    facets = []
    if key in expected_contacts and not _has_contact_coverage(snapshot, key, blue):
        facets.append(
            CandidateFacet(
                facet_type=taxonomy.FacetType.CONTACT,
                reason_text=(
                    f"Expected to be blue ({expected_contacts[key]}) but standing is below "
                    f"{settings.minimum_blue_standing:g}"
                ),
                detail={"expected_because": expected_contacts[key]},
            )
        )
    holding = acl_access.get(key, set())
    for access_list_id, tiers in expected_access.items():
        reason = tiers.get(entity_type, {}).get(entity_id)
        if reason is None or access_list_id in holding:
            continue
        if entity_type == taxonomy.EntityType.CHARACTER and _parent_has_access(
            snapshot, entity_id, access_list_id, acl_access
        ):
            continue
        facets.append(
            CandidateFacet(
                facet_type=taxonomy.FacetType.ACL,
                access_list_id=access_list_id,
                access_list_name=acl_names.get(access_list_id, ""),
                reason_text=f"Expected to have access ({reason}) but is not on the list",
                detail={"expected_because": reason},
            )
        )
    return facets


def _has_contact_coverage(snapshot, key, blue):
    """Standing on a corporation/alliance covers its member entities."""
    if key in blue:
        return True
    entity_type, entity_id = key
    if entity_type == taxonomy.EntityType.CHARACTER:
        facts = snapshot.characters.get(entity_id)
        if facts is None:
            return False
        return (taxonomy.EntityType.CORPORATION, facts.corporation_id) in blue or (
            taxonomy.EntityType.ALLIANCE,
            facts.alliance_id,
        ) in blue
    if entity_type == taxonomy.EntityType.CORPORATION:
        alliance_id = snapshot.corp_alliance.get(entity_id)
        return (taxonomy.EntityType.ALLIANCE, alliance_id) in blue
    return False


def _parent_has_access(snapshot, character_id, access_list_id, acl_access):
    """A corporation/alliance ACL entry grants its members access too."""
    facts = snapshot.characters.get(character_id)
    if facts is None:
        return False
    return bool(
        access_list_id
        in acl_access.get((taxonomy.EntityType.CORPORATION, facts.corporation_id), set())
        or access_list_id
        in acl_access.get((taxonomy.EntityType.ALLIANCE, facts.alliance_id), set())
    )


def _present_facets(key, blue, acl_access, acl_names, policies_by_acl):
    facets = []
    if key in blue:
        facets.append(
            CandidateFacet(
                facet_type=taxonomy.FacetType.CONTACT,
                reason_text=f"Holds standing {blue[key]:g}",
                detail={"standing": blue[key]},
            )
        )
    for access_list_id in sorted(acl_access.get(key, set())):
        policy = policies_by_acl.get(access_list_id)
        if policy is None:
            # ACL monitoring is opt-in: a synced list is not necessarily in use.
            continue
        if not policy.alert_unexpected:
            # The operator has said unexpected access on this list is not worth
            # alerting on.
            continue
        facets.append(
            CandidateFacet(
                facet_type=taxonomy.FacetType.ACL,
                access_list_id=access_list_id,
                access_list_name=acl_names.get(access_list_id, ""),
                reason_text="Has access to this list",
            )
        )
    return facets


def _primary_user(snapshot, users, approved):
    """The user to attribute the alert to; prefer an approved one for clarity."""
    if not users:
        return None
    if approved:
        candidates = sorted(users & snapshot.approved_user_ids)
    else:
        candidates = sorted(users)
    return candidates[0] if candidates else None


def _snapshot_name(snapshot, entity_type, entity_id):
    name = snapshot.entity_names.get((entity_type, entity_id))
    if name:
        return name
    if entity_type == taxonomy.EntityType.CHARACTER:
        facts = snapshot.characters.get(entity_id)
        if facts:
            return facts.character_name
    return ""


def _summary(alert_type, entity_type, entity_name, entity_id, facets):
    label = entity_name or f"{entity_type} {entity_id}"
    kinds = {facet.facet_type for facet in facets}
    if kinds == {taxonomy.FacetType.CONTACT}:
        what = "contact standings"
    elif kinds == {taxonomy.FacetType.ACL}:
        what = "ACL access"
    else:
        what = "contact standings and ACL access"
    if taxonomy.direction_for(alert_type) is taxonomy.Direction.MISSING:
        return f"{label} is missing {what}"
    return f"{label} has {what}"


def _main_character(snapshot, users):
    for user_id in sorted(users):
        character_id = snapshot.main_character_of.get(user_id)
        facts = snapshot.characters.get(character_id)
        if facts:
            return facts
    return None
