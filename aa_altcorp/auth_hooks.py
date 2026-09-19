"""Alliance Auth navigation and URL hooks."""

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook
from django.utils.translation import gettext_lazy as _

from aa_altcorp import urls


class AaAltcorpMenuItem(MenuItemHook):
    """Show the app menu item only to users with app access."""

    def __init__(self):
        super().__init__(
            _("Alt Corps"), "fas fa-users fa-fw", "aa_altcorp:index", navactive=["aa_altcorp:"]
        )

    def render(self, request):
        if request.user.has_perm("aa_altcorp.basic_access"):
            return super().render(request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return AaAltcorpMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "aa_altcorp", r"^aa-altcorp/")


@hooks.register("discord_cogs_hook")
def register_cogs() -> list[str]:
    """Expose the alert action cog to allianceauth-discordbot.

    Returning a dotted path keeps the import lazy: the cog is only loaded by
    the bot process, which is the only place py-cord is guaranteed present.
    AuthBot wraps each load_extension in try/except, so a missing dependency
    cannot take the bot down.
    """
    return ["aa_altcorp.cogs.altcorp_alerts"]
