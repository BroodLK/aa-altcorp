"""Application views."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from esi.decorators import token_required
from esi.models import Token

from .models import (
    AltCharacter,
    AltCorporation,
    AltCorpSettings,
    CharacterAccessList,
    CharacterAccessToken,
)
from .services import (
    ASSOCIABLE_CONTACT_TYPES,
    CONTACTS_NOT_SYNCED,
    associate_contact,
    configured_contacts,
    contact_associations,
    contacts_diagnosis,
    local_entity_name,
    search_characters,
    search_local_corporations,
    search_main_characters,
)


def _posted_id(request, field):
    """Read a required numeric field from a POST body, or None when unusable.

    The templates post these through hidden inputs, so a missing or empty value
    means a malformed or hand-rolled request rather than ordinary user error --
    but a bare ``request.POST[field]`` would answer it with a 500.
    """
    try:
        return int(request.POST.get(field, ""))
    except (TypeError, ValueError):
        return None


@permission_required("aa_altcorp.basic_access")
def index(request):
    """Render the initial plugin page."""

    characters = (
        search_characters(request.GET.get("character", "")) if request.GET.get("character") else []
    )
    corporations = (
        search_local_corporations(request.GET.get("corporation", ""))
        if request.GET.get("corporation")
        else []
    )
    # review is a reverse OneToOneField, so it joins rather than needing a second query.
    relationships = list(AltCorporation.objects.select_related("user", "review"))
    try:
        from allianceauth.authentication.models import UserProfile
        main_names = dict(UserProfile.objects.filter(
            user_id__in=[relationship.user_id for relationship in relationships],
            main_character__isnull=False,
        ).values_list("user_id", "main_character__character_name"))
    except (ImportError, RuntimeError):
        main_names = {}
    for relationship in relationships:
        relationship.main_character_name = main_names.get(
            relationship.user_id, relationship.user.username
        )
    settings = AltCorpSettings.current()
    configuration_missing = []
    if not settings.approved_states:
        configuration_missing.append("approved states")
    if not settings.standing_target_id:
        configuration_missing.append("standing target")
    return render(
        request,
        "aa_altcorp/index.html",
        {
            "characters": characters,
            "corporations": corporations,
            "relationships": relationships,
            "configuration_missing": configuration_missing,
        },
    )


@permission_required("aa_altcorp.basic_access")
def contacts(request):
    settings = AltCorpSettings.current()
    contact_scope, contact_rows, contacts_reason = configured_contacts(settings)
    diagnosis = contacts_diagnosis(settings) if contacts_reason == CONTACTS_NOT_SYNCED else None
    contact_rows = [
        {
            "contact": contact,
            # Reading contact.contact_name would fall back to a per-row ESI
            # fetch for every entity not cached locally, so use only the
            # annotation configured_contacts already added in SQL. It is NULL,
            # not "", for an uncached entity, hence the "or".
            "name": getattr(contact, "contact_name_annotation", "")
            or f"{contact.contact_type} {contact.contact_id}",
            "associations": contact_associations(contact),
            "can_associate": contact.contact_type in ASSOCIABLE_CONTACT_TYPES,
        }
        for contact in contact_rows
    ]
    return render(
        request,
        "aa_altcorp/contacts.html",
        {
            "contact_scope": contact_scope,
            "contact_rows": contact_rows,
            "contacts_reason": contacts_reason,
            "contacts_diagnosis": diagnosis,
            "settings": settings,
            "configuration_missing": [
                label
                for label, value in (
                    ("approved states", settings.approved_states),
                    ("standing target", settings.standing_target_id),
                )
                if not value
            ],
        },
    )


@permission_required("aa_altcorp.manage_relationships")
def access_lists(request):
    """Display the latest ESI ACL snapshots for managed characters."""
    access_lists = CharacterAccessList.objects.order_by("character_id", "name")
    return render(request, "aa_altcorp/access_lists.html", {"access_lists": access_lists})


@login_required
@permission_required("aa_altcorp.manage_relationships")
@token_required(scopes=["esi-access.read_lists.v1"])
def add_character_token(request, token: Token):
    CharacterAccessToken.objects.update_or_create(
        character_id=token.character_id, defaults={"token_id": token.pk}
    )
    messages.success(request, "Character ACL access has been added.")
    return redirect("aa_altcorp:access_lists")


@permission_required("aa_altcorp.manage_relationships")
def main_character_search(request):
    """Feed the contact assignment dropdown with matching main characters."""
    return JsonResponse({"results": search_main_characters(request.GET.get("q", ""))})


@permission_required("aa_altcorp.manage_relationships")
@require_POST
def assign_contact(request):
    """Link an Auth account to a contact straight from the contacts tab."""
    # The dropdown clears user_id on every keystroke, so an empty value here is
    # a normal consequence of submitting without picking anyone.
    user_id = _posted_id(request, "user_id")
    contact_id = _posted_id(request, "contact_id")
    contact_type = request.POST.get("contact_type", "")
    if user_id is None or contact_id is None:
        messages.error(request, "Pick an account to associate before assigning.")
        return redirect("aa_altcorp:contacts")

    user = get_object_or_404(User, pk=user_id)
    # The name is resolved from the local cache, never taken from the request.
    association = associate_contact(user, contact_type, contact_id)
    if association is None:
        messages.error(request, "That contact type cannot be associated with an account.")
    else:
        messages.success(request, f"Associated {association} with {user.username}.")
    return redirect("aa_altcorp:contacts")


@permission_required("aa_altcorp.manage_relationships")
@require_POST
def attach(request):
    """Attach a corporation to an Auth account.

    The name is resolved from the posted id rather than read from the form. The
    dropdown offers several corporations but could only ever carry one name, so
    a posted name belonged to whichever option happened to be listed first.
    """
    user_id = _posted_id(request, "user_id")
    corporation_id = _posted_id(request, "corporation_id")
    if user_id is None or corporation_id is None:
        messages.error(request, "Pick an account and a corporation before attaching.")
        return redirect("aa_altcorp:index")

    user = get_object_or_404(User, pk=user_id)
    AltCorporation.objects.get_or_create(
        user=user,
        corporation_id=corporation_id,
        defaults={
            "corporation_name": local_entity_name("corporation", corporation_id)
            or str(corporation_id),
            "source": "auth",
        },
    )
    return redirect("aa_altcorp:index")


@permission_required("aa_altcorp.manage_relationships")
@require_POST
def detach(request, relationship_id):
    """Remove an attached corporation from an Auth account."""
    relationship = get_object_or_404(AltCorporation, pk=relationship_id)
    relationship.delete()
    return redirect("aa_altcorp:index")


@permission_required("aa_altcorp.manage_relationships")
@require_POST
def associate_character(request):
    """Explicitly associate a character with its Auth account."""
    user_id = _posted_id(request, "user_id")
    character_id = _posted_id(request, "character_id")
    if user_id is None or character_id is None:
        messages.error(request, "Pick an account and a character before associating.")
        return redirect("aa_altcorp:index")

    user = get_object_or_404(User, pk=user_id)
    AltCharacter.objects.get_or_create(
        user=user,
        character_id=character_id,
        defaults={
            "character_name": local_entity_name("character", character_id) or str(character_id)
        },
    )
    return redirect("aa_altcorp:index")
