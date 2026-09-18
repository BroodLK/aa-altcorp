"""Audit and lookup services kept separate from HTTP and scheduled jobs."""

import json
import logging
from importlib import import_module
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.utils import timezone

from .models import (
    AltCharacter,
    AltCorporation,
    AltCorpReview,
    AltCorpSettings,
    CharacterAccessList,
    CharacterAccessToken,
)

logger = logging.getLogger(__name__)


ACL_OPERATIONS = (
    "GetCharactersAccessListsListing",
    "GetCharactersAccessListsDetail",
    "PostUniverseNames",
)

# ESI's access levels, most privileged first; anything unrecognized sorts after these.
ACCESS_ORDER = ("Admin", "Manager", "Allowed", "Blocked", "Unspecified")

# Membership group name paired with the id field ESI uses inside that group's entries.
MEMBERSHIP_GROUPS = (
    ("characters", "character_id"),
    ("corporations", "corporation_id"),
    ("alliances", "alliance_id"),
)

# /universe/names accepts at most 1000 ids per request.
NAME_CHUNK_SIZE = 1000

# Contact types this plugin has an association model for.
ASSOCIABLE_CONTACT_TYPES = ("character", "corporation")

# Delivery targets a Discord-style webhook, so nothing else is legitimate.
WEBHOOK_SCHEMES = frozenset({"http", "https"})

# Why the contacts tab has nothing to show, so the template can say which it is.
CONTACTS_NOT_INSTALLED = "not_installed"
CONTACTS_NO_TARGET = "no_target"
CONTACTS_NOT_SYNCED = "not_synced"

_ESI_PROVIDER = None


def _esi_provider():
    """Build the app-wide ESI client provider once; the spec load stays lazy."""
    global _ESI_PROVIDER
    if _ESI_PROVIDER is None:
        from esi.openapi_clients import ESIClientProvider

        from . import __version__

        _ESI_PROVIDER = ESIClientProvider(
            compatibility_date="2026-08-18",
            ua_appname="AAAltCorp",
            ua_version=__version__,
            operations=list(ACL_OPERATIONS),
        )
    return _ESI_PROVIDER


def _esi_error_types():
    """Failures that should log and skip rather than abort the whole audit."""
    types = [
        ImportError,
        KeyError,
        ObjectDoesNotExist,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ]
    try:
        from esi.exceptions import (
            ESIErrorLimitException,
            HTTPClientError,
            HTTPServerError,
        )
    except ImportError:
        pass
    else:
        types += [ESIErrorLimitException, HTTPClientError, HTTPServerError]
    return tuple(types)


def _esi_operation(client, operation_name):
    """Find a generated OpenAPI operation without assuming its tag name."""
    for tag_name, tag in client.api._operationindex._tags.items():
        if operation_name in tag._operations:
            return getattr(getattr(client, tag_name.replace(" ", "_")), operation_name)
    raise AttributeError(f"OpenAPI operation {operation_name!r} was not found")


def _field(entry, key):
    """Read a key from an ESI response entry, whether it is a mapping or a model."""
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def _membership_ids(memberships, skip_named=False):
    """Collect the EVE ids referenced by the given membership payloads.

    With skip_named, entries that already carry a name are left out, so a
    re-enrichment pass over fully named rows resolves nothing.
    """
    ids = set()
    for membership in memberships:
        for group, id_field in MEMBERSHIP_GROUPS:
            for entry in (membership or {}).get(group) or ():
                if skip_named and entry.get("name"):
                    continue
                entry_id = entry.get(id_field)
                if entry_id:
                    ids.add(int(entry_id))
    return ids


def _resolve_names(client, ids):
    """Resolve EVE ids to names in bulk; ids we cannot resolve are left out."""
    from esi.exceptions import HTTPNotModified

    pending = sorted(ids)
    if not pending:
        return {}
    operation = _esi_operation(client, "PostUniverseNames")
    names = {}
    for start in range(0, len(pending), NAME_CHUNK_SIZE):
        chunk = pending[start : start + NAME_CHUNK_SIZE]
        try:
            # django-esi hashes the cache key without the request body, so every
            # PostUniverseNames call collides on one entry; bypass it entirely.
            resolved = operation(body=chunk).result(
                use_etag=False, use_cache=False, store_cache=False
            )
        except (HTTPNotModified, *_esi_error_types()):
            # /universe/names rejects the whole batch when a single id is invalid.
            logger.warning("Unable to resolve %s EVE name(s), falling back to ids", len(chunk))
            continue
        for entry in resolved or ():
            entry_id = _field(entry, "id")
            entry_name = _field(entry, "name")
            if entry_id is not None and entry_name:
                names[int(entry_id)] = entry_name
    return names


def _access_sort_key(entry, id_field):
    """Order entries Admin -> Manager -> Allowed -> Blocked -> Unspecified, then by name."""
    access = entry.get("access")
    rank = ACCESS_ORDER.index(access) if access in ACCESS_ORDER else len(ACCESS_ORDER)
    return rank, entry.get("name") or "", entry.get(id_field) or 0


def _enrich_membership(membership, names):
    """Attach resolved names to a membership payload and sort it by access level."""
    membership = membership or {}
    enriched = {"allow_everyone": bool(membership.get("allow_everyone"))}
    for group, id_field in MEMBERSHIP_GROUPS:
        entries = []
        for entry in membership.get(group) or ():
            entry = dict(entry)
            name = names.get(entry.get(id_field))
            if name:
                entry["name"] = name
            entries.append(entry)
        entries.sort(key=lambda item, field=id_field: _access_sort_key(item, field))
        enriched[group] = entries
    return enriched


def _reenrich_stored_access_lists(client, character_id):
    """Re-apply names and ordering to unchanged rows, making no ACL requests."""
    rows = list(CharacterAccessList.objects.filter(character_id=character_id))
    if not rows:
        return 0
    names = _resolve_names(
        client, _membership_ids((row.membership for row in rows), skip_named=True)
    )
    for row in rows:
        membership = _enrich_membership(row.membership, names)
        if membership != row.membership:
            row.membership = membership
            row.save(update_fields=("membership",))
    return len(rows)


def search_characters(query):
    """Find characters and describe the Auth account/main relationship."""
    try:
        from allianceauth.authentication.models import CharacterOwnership
    except (ImportError, RuntimeError):
        return []

    matches = CharacterOwnership.objects.select_related(
        "character", "user", "user__profile", "user__profile__main_character"
    ).filter(character__character_name__icontains=query)[:25]
    results = []
    for ownership in matches:
        profile = getattr(ownership.user, "profile", None)
        main_character = getattr(profile, "main_character", None)
        if main_character is None:
            status = "No main character is configured for this account."
        elif main_character.pk == ownership.character.pk:
            status = "This is the main character associated with this account."
        else:
            status = f"Main character: {main_character.character_name}"
        results.append(
            {
                "character": ownership.character,
                "user": ownership.user,
                "status": status,
            }
        )
    return results


def search_users(query):
    """Backward-compatible account search for callers outside the page."""
    return User.objects.filter(Q(username__icontains=query) | Q(email__icontains=query)).order_by(
        "username"
    )[:25]


def sync_character_access_lists(character_id):
    """Fetch and persist ESI ACLs through django-esi's OpenAPI client."""
    try:
        from esi.exceptions import HTTPNotModified
        from esi.models import Token

        stored = CharacterAccessToken.objects.filter(character_id=character_id).first()
        if stored is None:
            logger.debug("No ACL token stored for character %s, skipping", character_id)
            return 0
        access_token = Token.objects.get(pk=stored.token_id)
        logger.info(
            "Fetching ESI ACLs for character %s with token %s", character_id, access_token.pk
        )
        client = _esi_provider().client
        list_operation = _esi_operation(client, "GetCharactersAccessListsListing")
        detail_operation = _esi_operation(client, "GetCharactersAccessListsDetail")
        try:
            listing = list_operation(character_id=character_id, token=access_token).result()
        except HTTPNotModified:
            # A 304 means the set of ACLs is unchanged, not that it is empty, so
            # replay what is stored rather than returning early.
            if CharacterAccessList.objects.filter(character_id=character_id).exists():
                logger.debug("ACL listing unchanged for character %s", character_id)
                return _reenrich_stored_access_lists(client, character_id)
            # Nothing stored to replay, so the cached ETag would wedge this
            # character forever; ignore it once and refetch.
            logger.info(
                "ACL listing returned 304 with nothing stored for character %s, forcing a refresh",
                character_id,
            )
            listing = list_operation(character_id=character_id, token=access_token).result(
                force_refresh=True
            )
        access_list_ids = {entry.id for entry in listing.access_lists}
        CharacterAccessList.objects.filter(character_id=character_id).exclude(
            access_list_id__in=access_list_ids
        ).delete()
        cached = {
            row.access_list_id: row
            for row in CharacterAccessList.objects.filter(character_id=character_id)
        }
        fetched = {}
        for access_list_id in sorted(access_list_ids):
            try:
                details = detail_operation(
                    character_id=character_id,
                    access_list_id=access_list_id,
                    token=access_token,
                ).result()
            except HTTPNotModified:
                # ETags mean an unchanged ACL is never re-fetched, so replay what is
                # already stored to pick up names and ordering it was saved without.
                row = cached.get(access_list_id)
                if row is None:
                    logger.debug(
                        "ACL %s unchanged but not stored for character %s",
                        access_list_id,
                        character_id,
                    )
                    continue
                logger.debug("ACL %s unchanged for character %s", access_list_id, character_id)
                fetched[access_list_id] = {
                    "name": row.name,
                    "description": row.description,
                    "membership": row.membership,
                }
                continue
            fetched[access_list_id] = {
                "name": details.name,
                "description": details.description,
                "membership": details.membership.model_dump(mode="json"),
            }
        names = _resolve_names(client, _membership_ids(f["membership"] for f in fetched.values()))
        for access_list_id, found in fetched.items():
            CharacterAccessList.objects.update_or_create(
                character_id=character_id,
                access_list_id=access_list_id,
                defaults={
                    **found,
                    "membership": _enrich_membership(found["membership"], names),
                },
            )
        logger.info("Synchronized %s ACL(s) for character %s", len(fetched), character_id)
        return len(fetched)
    except _esi_error_types():
        logger.exception("Unable to synchronize ESI ACLs for character %s", character_id)
        return 0


def search_local_corporations(query):
    """Search corporations represented by Alliance Auth character ownership."""
    try:
        from allianceauth.authentication.models import CharacterOwnership
        from allianceauth.eveonline.models import EveCorporationInfo
    except ImportError:
        return []
    ids = CharacterOwnership.objects.filter(character__corporation_id__isnull=False).values(
        "character__corporation_id"
    )
    return EveCorporationInfo.objects.filter(
        corporation_id__in=ids, corporation_name__icontains=query
    ).distinct()[:25]


def configured_contacts(settings):
    """Return the scope, aa-contacts rows, and why the list is empty when it is."""
    try:
        # A missing INSTALLED_APPS entry raises RuntimeError, not ImportError.
        from aa_contacts.models import AllianceContact, CorporationContact
    except (ImportError, RuntimeError):
        return None, [], CONTACTS_NOT_INSTALLED
    if not settings.standing_target_id:
        return None, [], CONTACTS_NO_TARGET
    if settings.standing_target_type == "alliance":
        scope = "Alliance"
        contacts = AllianceContact.objects.filter(alliance__alliance_id=settings.standing_target_id)
    else:
        scope = "Corporation"
        contacts = CorporationContact.objects.filter(
            corporation__corporation_id=settings.standing_target_id
        )
    # with_contact_name() annotates the name in SQL. It only avoids the per-row
    # lookup for contacts whose Eve* row is cached locally: the annotation is NULL
    # otherwise, and contact_name treats that like "" and falls back to ESI.
    contacts = list(
        contacts.with_contact_name()
        .prefetch_related("labels")
        .order_by("-standing", "contact_name_annotation")
    )
    return scope, contacts, None if contacts else CONTACTS_NOT_SYNCED


def contacts_diagnosis(settings):
    """Explain a configured-but-empty contacts list: what exists vs what was asked for."""
    try:
        from aa_contacts.models import AllianceContact, CorporationContact
    except (ImportError, RuntimeError):
        return None
    if settings.standing_target_type == "alliance":
        model, owner_field = AllianceContact, "alliance__alliance_id"
    else:
        model, owner_field = CorporationContact, "corporation__corporation_id"
    owner_ids = sorted(set(model.objects.values_list(owner_field, flat=True)))
    return {
        "target_id": settings.standing_target_id,
        "total": model.objects.count(),
        "owner_ids": owner_ids[:10],
        "owner_count": len(owner_ids),
    }


def contact_associations(contact):
    """Return the main characters attached to a contact."""
    if contact.contact_type == "corporation":
        associations = AltCorporation.objects.filter(
            corporation_id=contact.contact_id
        ).select_related("user")
        fallback_field = "corporation_name"
    elif contact.contact_type == "character":
        associations = AltCharacter.objects.filter(
            character_id=contact.contact_id
        ).select_related("user")
        fallback_field = "character_name"
    else:
        return []

    try:
        from allianceauth.authentication.models import UserProfile

        main_names = dict(
            UserProfile.objects.filter(
                user_id__in=[association.user_id for association in associations],
                main_character__isnull=False,
            ).values_list("user_id", "main_character__character_name")
        )
    except (ImportError, RuntimeError):
        main_names = {}

    return [
        main_names.get(association.user_id)
        or getattr(association, fallback_field)
        for association in associations
    ]


def search_main_characters(query, limit=25):
    """Search Auth main characters for the contact assignment dropdown."""
    try:
        from allianceauth.authentication.models import UserProfile
    except (ImportError, RuntimeError):
        return []
    profiles = (
        UserProfile.objects.select_related("user", "main_character")
        .filter(main_character__isnull=False, main_character__character_name__icontains=query)
        .order_by("main_character__character_name")[:limit]
    )
    return [
        {
            "user_id": profile.user_id,
            "character_name": profile.main_character.character_name,
            "corporation_name": profile.main_character.corporation_name,
        }
        for profile in profiles
    ]


def local_entity_name(entity_type, entity_id):
    """Resolve an EVE id to a name from Alliance Auth's local cache only.

    Never contacts ESI: an entity that is not cached returns "" so the caller
    can fall back to something cheap.  aa-contacts' ``contact_name`` property
    would fetch per row instead, which is why neither the contacts page nor the
    detector reads it.
    """
    try:
        from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
    except (ImportError, RuntimeError):
        return ""
    if entity_type == "character":
        queryset = EveCharacter.objects.filter(character_id=entity_id)
        field = "character_name"
    elif entity_type == "corporation":
        queryset = EveCorporationInfo.objects.filter(corporation_id=entity_id)
        field = "corporation_name"
    else:
        return ""
    return queryset.values_list(field, flat=True).first() or ""


def associate_contact(user, contact_type, contact_id, contact_name=""):
    """Link an Auth user to a character or corporation contact.

    ``contact_name`` is a fallback, not a source of truth.  Left empty, the name
    is resolved from Alliance Auth's local cache, so a caller handling an HTTP
    request never has to trust a client-supplied name.
    """
    if contact_type not in ASSOCIABLE_CONTACT_TYPES:
        return None
    name = contact_name or local_entity_name(contact_type, contact_id)
    if contact_type == "character":
        return AltCharacter.objects.get_or_create(
            user=user, character_id=contact_id, defaults={"character_name": name}
        )[0]
    return AltCorporation.objects.get_or_create(
        user=user,
        corporation_id=contact_id,
        defaults={"corporation_name": name, "source": "auth"},
    )[0]


def aa_contact_for(corporation_id, settings):
    """Return the configured aa-contacts row, if aa-contacts is installed."""
    try:
        module = import_module("aa_contacts.models")
        model = (
            module.AllianceContact
            if settings.standing_target_type == "alliance"
            else module.CorporationContact
        )
        target_field = (
            "alliance__alliance_id"
            if settings.standing_target_type == "alliance"
            else "corporation__corporation_id"
        )
        return (
            model.objects.filter(**{target_field: settings.standing_target_id})
            .filter(Q(contact_id=corporation_id, contact_type="corporation"))
            .first()
        )
    except (ImportError, RuntimeError, AttributeError):
        return None


def _counts_as_blue(standing, config):
    """Whether a contact standing clears the configured blue threshold.

    Mirrors ``aa_altcorp.alerts.detect._blue_contacts``: aa-contacts forces
    standing to 0.0 rather than deleting a contact that carries notes or server
    links, so 0.0 is ambiguous and ``treat_zero_standing_as_removed`` decides
    which way to read it.
    """
    if standing is None:
        return False
    if standing == 0.0 and config.treat_zero_standing_as_removed:
        return False
    return standing >= config.minimum_blue_standing


def audit_relationship(relationship):
    """Evaluate member state and configured aa-contacts standing."""
    config = AltCorpSettings.current()
    review, _ = AltCorpReview.objects.get_or_create(relationship=relationship)
    state = getattr(getattr(relationship.user, "profile", None), "state", "")
    state_name = getattr(state, "name", state)
    contact = (
        aa_contact_for(relationship.corporation_id, config) if config.standing_target_id else None
    )
    state_ok = not config.approved_states or state_name in config.approved_states
    # Uses the same standing rules as the alert engine, so the legacy review and
    # the alert board cannot disagree about one corporation. They previously did:
    # this accepted any standing >= 0, which called a 0.0 standing approved while
    # treat_zero_standing_as_removed had the engine reading it as removed.
    standing = getattr(contact, "standing", None)
    contact_ok = contact is not None and _counts_as_blue(standing, config)
    review.member_state = state_name or "unknown"
    review.aa_contact_found = contact is not None
    review.standing = standing
    review.approved = state_ok and contact_ok
    review.reason = (
        "approved"
        if review.approved
        else (
            f"member state={state_name or 'unknown'}, "
            f"contact standing={standing if contact is not None else 'missing'}"
        )
    )
    review.checked_at = timezone.now()
    review.save()
    relationship.last_checked_at = review.checked_at
    relationship.save(update_fields=("last_checked_at",))
    return review


def post_webhook(url, payload):
    """POST a JSON payload to a Discord-style webhook; never raise on failure.

    Delivery is best effort.  A webhook that is down must not abort the caller's
    loop, so every transport error is logged and reported as a failed send.
    """
    if not url:
        return False
    # urlopen honours file:// and other local schemes, so pin it to HTTP(S)
    # rather than trusting whatever ended up in the settings row.
    if urlsplit(url).scheme not in WEBHOOK_SCHEMES:
        logger.error("Refusing to deliver a webhook to a non-HTTP(S) URL")
        return False
    try:
        body = json.dumps(payload).encode()
    except (TypeError, ValueError):
        logger.exception("Unable to serialize webhook payload")
        return False
    # S310: the scheme is pinned to HTTP(S) above, which is what it asks for.
    request = Request(url, data=body, headers={"Content-Type": "application/json"})  # noqa: S310
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310
            response.read()
    except (HTTPError, URLError, OSError, ValueError):
        logger.warning("Webhook delivery failed", exc_info=True)
        return False
    return True


def notify_review(review):
    """Send a compact webhook alert when an unapproved result is due."""
    config = AltCorpSettings.current()
    if review.approved or not config.enabled or not config.webhook_url:
        return False
    if not review.notification_due(config.notification_interval):
        return False
    sent = post_webhook(
        config.webhook_url,
        {
            "content": (
                f"Alt corporation review failed for {review.relationship.user.username}: "
                f"{review.relationship.corporation_name} ({review.reason})"
            )
        },
    )
    if not sent:
        return False
    now = timezone.now()
    review.first_notified_at = review.first_notified_at or now
    review.last_notified_at = now
    review.save(update_fields=("first_notified_at", "last_notified_at"))
    return True
