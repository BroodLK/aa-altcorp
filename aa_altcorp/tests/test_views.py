"""The HTTP layer, exercised against a real Alliance Auth project.

These only run under ``testauth.settings``: the templates extend
``allianceauth/base-bs5.html`` and the URLs are mounted by Alliance Auth from
the ``url_hook`` in :mod:`aa_altcorp.auth_hooks`, so neither resolves without
Auth installed.  ``setUpClass`` skips the module rather than failing when it is
run under ``tests.settings``.

Alliance Auth wraps every hooked view in ``main_character_required``, so each
user here needs a main character or the response is a redirect to the dashboard
rather than the page under test.
"""

from unittest import SkipTest
from unittest.mock import patch

from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from aa_altcorp.models import AltCharacter, AltCorporation


def _require_allianceauth():
    """Return AuthUtils, or skip the module when Auth is not installed.

    Under ``tests.settings`` importing Auth's models raises RuntimeError -- the
    app is on the path but absent from INSTALLED_APPS -- which is exactly the
    signal that this module has nothing to test there.
    """
    try:
        from allianceauth.tests.auth_utils import AuthUtils
    except (ImportError, RuntimeError) as error:
        raise SkipTest("Alliance Auth is not installed") from error
    try:
        reverse("aa_altcorp:index")
    except NoReverseMatch as error:  # pragma: no cover
        raise SkipTest("aa_altcorp URLs are not mounted") from error
    return AuthUtils


class ViewTestCase(TestCase):
    """Shared setup: a member with a main character and the plugin permissions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AuthUtils = _require_allianceauth()

    def setUp(self):
        self.user = self.AuthUtils.create_member("pilot")
        self.character = self.AuthUtils.add_main_character_2(
            self.user,
            name="Pilot One",
            character_id=95000001,
            corp_id=98000001,
            corp_name="Example Corp",
            corp_ticker="EXMP",
        )
        self.user = self.AuthUtils.add_permission_to_user_by_name(
            "aa_altcorp.basic_access", self.user
        )
        self.user = self.AuthUtils.add_permission_to_user_by_name(
            "aa_altcorp.manage_relationships", self.user
        )
        self.client.force_login(self.user)


class PageAccessTests(ViewTestCase):
    def test_every_page_renders(self):
        """Proves the templates resolve the Auth base template and render."""
        for name in ("index", "contacts", "access_lists"):
            with self.subTest(page=name):
                response = self.client.get(reverse(f"aa_altcorp:{name}"))
                self.assertEqual(response.status_code, 200)

    def test_pages_are_gated_on_permissions(self):
        stranger = self.AuthUtils.create_member("stranger")
        self.AuthUtils.add_main_character_2(
            stranger, name="Stranger", character_id=95000002, corp_id=98000002
        )
        self.client.force_login(stranger)

        response = self.client.get(reverse("aa_altcorp:index"))

        self.assertEqual(response.status_code, 302)

    def test_mutating_views_reject_get(self):
        for name in ("attach", "assign_contact", "associate_character"):
            with self.subTest(view=name):
                response = self.client.get(reverse(f"aa_altcorp:{name}"))
                self.assertEqual(response.status_code, 405)


class AttachTests(ViewTestCase):
    """The corporation name must come from the posted id, not from the form.

    The index page offers a select of several corporations but could only ever
    carry one hidden name, so a posted name belonged to whichever option
    happened to be listed first. Picking any other one stored a mismatched name.
    """

    def setUp(self):
        super().setUp()
        from allianceauth.eveonline.models import EveCorporationInfo

        self.other = EveCorporationInfo.objects.create(
            corporation_id=98009999,
            corporation_name="Second Corp",
            corporation_ticker="SEC",
            member_count=12,
        )

    def test_the_name_is_resolved_from_the_posted_id(self):
        response = self.client.post(
            reverse("aa_altcorp:attach"),
            {"user_id": self.user.pk, "corporation_id": self.other.corporation_id},
        )

        self.assertEqual(response.status_code, 302)
        attached = AltCorporation.objects.get(corporation_id=98009999)
        self.assertEqual(attached.corporation_name, "Second Corp")

    def test_a_posted_name_is_ignored(self):
        """Belt and braces: even if a client sends one, it must not be stored."""
        self.client.post(
            reverse("aa_altcorp:attach"),
            {
                "user_id": self.user.pk,
                "corporation_id": self.other.corporation_id,
                "corporation_name": "Attacker Supplied Corp",
            },
        )

        attached = AltCorporation.objects.get(corporation_id=98009999)
        self.assertEqual(attached.corporation_name, "Second Corp")

    def test_an_unknown_corporation_falls_back_to_its_id(self):
        self.client.post(
            reverse("aa_altcorp:attach"),
            {"user_id": self.user.pk, "corporation_id": 98001234},
        )

        attached = AltCorporation.objects.get(corporation_id=98001234)
        self.assertEqual(attached.corporation_name, "98001234")

    def test_a_malformed_post_does_not_500(self):
        response = self.client.post(reverse("aa_altcorp:attach"), {"user_id": ""})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AltCorporation.objects.exists())


class AssociateCharacterTests(ViewTestCase):
    def test_the_name_is_resolved_from_the_posted_id(self):
        self.client.post(
            reverse("aa_altcorp:associate_character"),
            {"user_id": self.user.pk, "character_id": 95000001},
        )

        associated = AltCharacter.objects.get(character_id=95000001)
        self.assertEqual(associated.character_name, "Pilot One")

    def test_a_malformed_post_does_not_500(self):
        response = self.client.post(
            reverse("aa_altcorp:associate_character"), {"character_id": "nonsense"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AltCharacter.objects.exists())


class ContactsPageTests(ViewTestCase):
    """The contacts page must not resolve names through ESI.

    aa-contacts' ``contact_name`` property falls back to
    ``create_corporation()`` -- a live ESI call -- for any entity not cached
    locally. The page renders one row per contact, so reading it there was one
    request per uncached row, inside the response cycle.
    """

    def _configure_contacts(self):
        from aa_contacts.models import AllianceContact
        from allianceauth.eveonline.models import EveAllianceInfo

        from aa_altcorp.models import AltCorpSettings

        alliance = EveAllianceInfo.objects.create(
            alliance_id=99005338,
            alliance_name="Test Alliance",
            alliance_ticker="TEST",
            executor_corp_id=98000001,
        )
        settings = AltCorpSettings.current()
        settings.standing_target_type = "alliance"
        settings.standing_target_id = alliance.alliance_id
        settings.save()
        # Deliberately not backed by a local EveCorporationInfo row, so the
        # name annotation comes back NULL and the ESI fallback would fire.
        AllianceContact.objects.create(
            alliance=alliance, contact_id=98007777, contact_type="corporation", standing=10.0
        )
        return alliance

    def test_rendering_makes_no_esi_call_for_an_uncached_contact(self):
        from allianceauth.eveonline.models import EveCorporationInfo

        self._configure_contacts()

        with patch.object(EveCorporationInfo.objects, "create_corporation") as create_corporation:
            response = self.client.get(reverse("aa_altcorp:contacts"))

        self.assertEqual(response.status_code, 200)
        create_corporation.assert_not_called()
        # With no local row the annotation is NULL, so the page must show a
        # placeholder rather than the literal string "None".
        self.assertContains(response, "corporation 98007777")
        self.assertNotContains(response, ">None<")

    def test_a_locally_cached_contact_shows_its_real_name(self):
        from allianceauth.eveonline.models import EveCorporationInfo

        alliance = self._configure_contacts()
        EveCorporationInfo.objects.create(
            corporation_id=98007777,
            corporation_name="Cached Corp",
            corporation_ticker="CACH",
            member_count=3,
        )
        self.assertTrue(alliance.contacts.exists())

        response = self.client.get(reverse("aa_altcorp:contacts"))

        self.assertContains(response, "Cached Corp")
