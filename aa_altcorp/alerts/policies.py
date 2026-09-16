"""Resolve an AccessListPolicy into the set of entities expected on one ACL.

Policies are opt-in per ACL.  Without one, the engine has no basis to claim an
entity *should* have access, so the whole "missing access" family is skipped --
see :func:`aa_altcorp.alerts.detect.evaluate`.
"""

from dataclasses import dataclass, field

from . import taxonomy


@dataclass
class ExpectedAccess:
    """Entity id -> the human reason it is expected, per tier."""

    characters: dict[int, str] = field(default_factory=dict)
    corporations: dict[int, str] = field(default_factory=dict)
    alliances: dict[int, str] = field(default_factory=dict)

    def for_tier(self, entity_type):
        return {
            taxonomy.EntityType.CHARACTER: self.characters,
            taxonomy.EntityType.CORPORATION: self.corporations,
            taxonomy.EntityType.ALLIANCE: self.alliances,
        }.get(entity_type, {})

    def __bool__(self):
        return bool(self.characters or self.corporations or self.alliances)


def expected_membership(policy, snapshot, blue_contacts):
    """Build the expected-access set for one ACL.

    ``blue_contacts`` maps ``(entity_type, entity_id) -> standing`` and comes
    from the contact side of the scan, so the "any positive contact" baseline
    costs no extra queries.
    """
    expected = ExpectedAccess()
    if policy is None or not policy.enabled:
        return expected

    if policy.expect_positive_contacts:
        for (entity_type, entity_id), standing in blue_contacts.items():
            if standing < policy.minimum_standing:
                continue
            target = expected.for_tier(entity_type)
            if target is not None:
                target.setdefault(entity_id, f"blue contact (standing {standing:g})")

    wanted_states = {str(name) for name in (policy.expect_states or [])}
    wanted_groups = {str(name) for name in (policy.expect_groups or [])}
    if wanted_states or wanted_groups:
        for character_id, facts in snapshot.characters.items():
            if policy.expect_mains_only and not facts.is_main:
                continue
            if facts.user_id is None:
                continue
            if facts.state_name in wanted_states:
                expected.characters.setdefault(character_id, f"state: {facts.state_name}")
                continue
            matched = wanted_groups & snapshot.user_groups.get(facts.user_id, set())
            if matched:
                expected.characters.setdefault(character_id, f"group: {sorted(matched)[0]}")

    _expand_tier(
        expected,
        snapshot,
        policy,
        ids=policy.expect_corporations or [],
        entity_type=taxonomy.EntityType.CORPORATION,
        members=snapshot.corporations,
        label="corporation",
    )
    _expand_tier(
        expected,
        snapshot,
        policy,
        ids=policy.expect_alliances or [],
        entity_type=taxonomy.EntityType.ALLIANCE,
        members=snapshot.alliances,
        label="alliance",
    )

    for entry in policy.expect_entities or []:
        entity_type = entry.get("entity_type")
        entity_id = entry.get("entity_id")
        if not entity_type or entity_id is None:
            continue
        target = expected.for_tier(entity_type)
        if target is not None:
            target.setdefault(int(entity_id), "explicitly listed on the policy")

    return expected


def _expand_tier(expected, snapshot, policy, ids, entity_type, members, label):
    """Add a corporation or alliance rule, expanding to characters when asked."""
    for raw_id in ids:
        try:
            entity_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if policy.expected_tier == taxonomy.EntityType.CHARACTER:
            # Expand to the Auth-known members, so the ACL is checked at the
            # tier it actually lists people at.
            user_ids = members.get(entity_id, set())
            for character_id, facts in snapshot.characters.items():
                if facts.user_id not in user_ids:
                    continue
                if policy.expect_mains_only and not facts.is_main:
                    continue
                expected.characters.setdefault(character_id, f"member of {label} {entity_id}")
        else:
            expected.for_tier(entity_type).setdefault(entity_id, f"{label} listed on the policy")
