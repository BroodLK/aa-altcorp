"""Character linking integration for the ACL read scope."""

from charlink.app_imports.utils import AppImport, LoginImport
from charlink.utils import users_with_permissions
from django.db.models import Exists, OuterRef
from esi.models import Token

from .models import CharacterAccessToken

ACL_SCOPE = "esi-access.read_lists.v1"


def add_acl_character(request, token: Token):
    CharacterAccessToken.objects.update_or_create(
        character_id=token.character_id,
        defaults={"token_id": token.pk},
    )


def users_with_acl_permission():
    return users_with_permissions("aa_altcorp.manage_relationships", require_all=False)


app_import = AppImport(
    "aa_altcorp",
    [
        LoginImport(
            app_label="aa_altcorp",
            unique_id="acl",
            field_label="Add Character ACL Token",
            add_character=add_acl_character,
            scopes=[ACL_SCOPE],
            check_permissions=lambda user: user.has_perm("aa_altcorp.manage_relationships"),
            is_character_added=lambda char: CharacterAccessToken.objects.filter(
                character_id=char.character_id
            ).exists(),
            is_character_added_annotation=Exists(
                CharacterAccessToken.objects.filter(
                    character_id=OuterRef("character_id"),
                ),
            ),
            get_users_with_perms=users_with_acl_permission,
        )
    ],
)
