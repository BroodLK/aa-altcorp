import importlib
import sys
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth.models import User
from esi.exceptions import HTTPClientError, HTTPNotModified
from esi.models import Token

from aa_altcorp import services
from aa_altcorp.models import (
    AltCharacter,
    AltCorporation,
    AltCorpSettings,
    CharacterAccessList,
    CharacterAccessToken,
)

CHARACTER_ID = 212620763


def test_app_is_installed():
    config = apps.get_app_config("aa_altcorp")
    assert config.verbose_name == "AA Alt Corp v0.0.9"


class _Membership:
    """Stands in for the pydantic membership model returned by django-esi."""

    def __init__(self, data):
        self._data = data

    def model_dump(self, mode=None):
        return self._data


class _Operation:
    """Mimics esi.openapi_clients.EsiOperation: call stores kwargs, result() replays."""

    def __init__(self, responder):
        self._responder = responder
        self._kwargs = {}
        self.calls = []
        self.result_kwargs = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        self._kwargs = kwargs
        return self

    def result(self, **extra):
        self.result_kwargs.append(extra)
        value = self._responder(self._kwargs)
        if isinstance(value, Exception):
            raise value
        return value


def _fake_client(listing, detail, names=None):
    """Build a stand-in for esi.openapi_clients.ESIClient with the tags we use."""
    acl_operations = {
        "GetCharactersAccessListsListing": _Operation(listing),
        "GetCharactersAccessListsDetail": _Operation(detail),
    }
    universe_operations = {"PostUniverseNames": _Operation(names or (lambda _kwargs: []))}
    return SimpleNamespace(
        api=SimpleNamespace(
            _operationindex=SimpleNamespace(
                _tags={
                    "Access List": SimpleNamespace(_operations=acl_operations),
                    "Universe": SimpleNamespace(_operations=universe_operations),
                }
            )
        ),
        Access_List=SimpleNamespace(**acl_operations),
        Universe=SimpleNamespace(**universe_operations),
    )


MEMBERSHIP = {
    "allow_everyone": False,
    "alliances": [],
    "characters": [{"character_id": 42, "access": "Admin"}],
    "corporations": [],
}


def _detail(access_list_id):
    return SimpleNamespace(
        id=access_list_id,
        name=f"ACL {access_list_id}",
        description="managed",
        membership=_Membership(MEMBERSHIP),
    )


def _listing(*ids):
    return SimpleNamespace(access_lists=[SimpleNamespace(id=i) for i in ids])


@pytest.fixture
def linked_character(db):
    """A character with a stored ACL token, as the admin flow would create."""
    user = User.objects.create_user("acl-owner")
    token = Token.objects.create(
        user=user,
        character_id=CHARACTER_ID,
        character_name="ACL Owner",
        access_token="x",
        refresh_token="y",
        token_type="Character",
        character_owner_hash="hash",
    )
    CharacterAccessToken.objects.create(character_id=CHARACTER_ID, token_id=token.pk)
    return token


@pytest.fixture
def install_client(monkeypatch):
    """Swap the cached provider for one handing out a fake client."""

    def _install(listing, detail, names=None):
        client = _fake_client(listing, detail, names)
        monkeypatch.setattr(services, "_ESI_PROVIDER", SimpleNamespace(client=client))
        return client

    return _install


def test_esi_operation_resolves_through_the_tag_index():
    client = _fake_client(lambda _kwargs: _listing(1), lambda kwargs: _detail(1))
    operation = services._esi_operation(client, "GetCharactersAccessListsListing")
    assert operation is client.Access_List.GetCharactersAccessListsListing


def test_esi_operation_reports_unknown_operations():
    client = _fake_client(lambda _kwargs: _listing(), lambda kwargs: _detail(1))
    with pytest.raises(AttributeError, match="GetNothing"):
        services._esi_operation(client, "GetNothing")


def test_sync_persists_access_lists(linked_character, install_client):
    client = install_client(
        lambda _kwargs: _listing(7, 9), lambda kwargs: _detail(kwargs["access_list_id"])
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 2

    rows = CharacterAccessList.objects.filter(character_id=CHARACTER_ID).order_by("access_list_id")
    assert [row.access_list_id for row in rows] == [7, 9]
    assert rows[0].name == "ACL 7"
    assert rows[0].description == "managed"
    assert rows[0].membership == MEMBERSHIP
    listing_call = client.Access_List.GetCharactersAccessListsListing.calls[0]
    assert listing_call == {"character_id": CHARACTER_ID, "token": linked_character}


def test_sync_prunes_access_lists_that_disappeared(linked_character, install_client):
    CharacterAccessList.objects.create(character_id=CHARACTER_ID, access_list_id=99)
    install_client(lambda _kwargs: _listing(7), lambda kwargs: _detail(kwargs["access_list_id"]))

    assert services.sync_character_access_lists(CHARACTER_ID) == 1
    assert list(
        CharacterAccessList.objects.filter(character_id=CHARACTER_ID).values_list(
            "access_list_id", flat=True
        )
    ) == [7]


def test_sync_keeps_existing_row_when_detail_is_not_modified(linked_character, install_client):
    CharacterAccessList.objects.create(
        character_id=CHARACTER_ID, access_list_id=7, name="cached", membership=MEMBERSHIP
    )
    install_client(
        lambda _kwargs: _listing(7),
        lambda _kwargs: HTTPNotModified(status_code=304, headers={}),
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1
    row = CharacterAccessList.objects.get(character_id=CHARACTER_ID, access_list_id=7)
    assert row.name == "cached"


def test_sync_does_not_prune_when_listing_is_not_modified(linked_character, install_client):
    CharacterAccessList.objects.create(character_id=CHARACTER_ID, access_list_id=7, name="cached")
    install_client(
        lambda _kwargs: HTTPNotModified(status_code=304, headers={}),
        lambda kwargs: _detail(kwargs["access_list_id"]),
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1
    assert CharacterAccessList.objects.filter(character_id=CHARACTER_ID).count() == 1


def test_sync_skips_characters_without_a_stored_token(db, monkeypatch):
    def fail():
        raise AssertionError("the provider must not be built without a token")

    monkeypatch.setattr(services, "_esi_provider", fail)
    assert services.sync_character_access_lists(CHARACTER_ID) == 0


def test_sync_reports_client_errors_as_zero(linked_character, install_client):
    install_client(
        lambda _kwargs: HTTPClientError(status_code=403, headers={}, data=None),
        lambda kwargs: _detail(kwargs["access_list_id"]),
    )
    assert services.sync_character_access_lists(CHARACTER_ID) == 0


MIXED_MEMBERSHIP = {
    "allow_everyone": False,
    "alliances": [],
    "corporations": [],
    "characters": [
        {"character_id": 4, "access": "Blocked"},
        {"character_id": 1, "access": "Allowed"},
        {"character_id": 2, "access": "Admin"},
        {"character_id": 5, "access": "Unspecified"},
        {"character_id": 3, "access": "Manager"},
    ],
}

EVE_NAMES = {1: "Allowed Pilot", 2: "Admin Pilot", 3: "Manager Pilot", 4: "Blocked Pilot"}


def _names(kwargs):
    """Mimic PostUniverseNames: resolve the ids it knows, drop the rest."""
    return [
        SimpleNamespace(id=i, name=EVE_NAMES[i], category="character")
        for i in kwargs["body"]
        if i in EVE_NAMES
    ]


def _access_of(membership, group="characters"):
    return [entry["access"] for entry in membership[group]]


def test_enrich_membership_orders_admin_first_and_unspecified_last():
    membership = services._enrich_membership(MIXED_MEMBERSHIP, {})
    assert _access_of(membership) == ["Admin", "Manager", "Allowed", "Blocked", "Unspecified"]
    assert membership["allow_everyone"] is False
    assert membership["alliances"] == []


def test_enrich_membership_sorts_unrecognized_access_last():
    characters = [{"character_id": 9, "access": "Overlord"}, *MIXED_MEMBERSHIP["characters"]]
    membership = services._enrich_membership({"characters": characters}, {})
    assert _access_of(membership)[-1] == "Overlord"


def test_enrich_membership_breaks_ties_by_name():
    membership = services._enrich_membership(
        {
            "characters": [
                {"character_id": 1, "access": "Admin"},
                {"character_id": 2, "access": "Admin"},
            ]
        },
        {1: "Zeta", 2: "Alpha"},
    )
    assert [entry["name"] for entry in membership["characters"]] == ["Alpha", "Zeta"]


def test_enrich_membership_leaves_unresolved_ids_unnamed():
    membership = services._enrich_membership(MIXED_MEMBERSHIP, EVE_NAMES)
    by_id = {entry["character_id"]: entry for entry in membership["characters"]}
    assert by_id[2]["name"] == "Admin Pilot"
    assert "name" not in by_id[5]


def test_enrich_membership_tolerates_missing_groups():
    assert services._enrich_membership({}, {}) == {
        "allow_everyone": False,
        "characters": [],
        "corporations": [],
        "alliances": [],
    }


def test_sync_stores_resolved_names_in_access_order(linked_character, install_client):
    client = install_client(
        lambda _kwargs: _listing(7),
        lambda _kwargs: SimpleNamespace(
            id=7, name="ACL 7", description="managed", membership=_Membership(MIXED_MEMBERSHIP)
        ),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1

    membership = CharacterAccessList.objects.get(
        character_id=CHARACTER_ID, access_list_id=7
    ).membership
    assert [entry.get("name", entry["character_id"]) for entry in membership["characters"]] == [
        "Admin Pilot",
        "Manager Pilot",
        "Allowed Pilot",
        "Blocked Pilot",
        5,
    ]
    assert client.Universe.PostUniverseNames.calls == [{"body": [1, 2, 3, 4, 5]}]


def test_sync_resolves_names_once_for_all_access_lists(linked_character, install_client):
    client = install_client(
        lambda _kwargs: _listing(7, 9),
        lambda kwargs: SimpleNamespace(
            id=kwargs["access_list_id"],
            name=f"ACL {kwargs['access_list_id']}",
            description="",
            membership=_Membership(MIXED_MEMBERSHIP),
        ),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 2
    assert len(client.Universe.PostUniverseNames.calls) == 1


def test_sync_skips_name_resolution_when_there_are_no_ids(linked_character, install_client):
    client = install_client(
        lambda _kwargs: _listing(7),
        lambda _kwargs: SimpleNamespace(
            id=7, name="ACL 7", description="", membership=_Membership({"allow_everyone": True})
        ),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1
    assert client.Universe.PostUniverseNames.calls == []


def test_sync_reenriches_stored_membership_when_detail_is_not_modified(
    linked_character, install_client
):
    CharacterAccessList.objects.create(
        character_id=CHARACTER_ID,
        access_list_id=7,
        name="cached",
        description="from before the upgrade",
        membership=MIXED_MEMBERSHIP,
    )
    install_client(
        lambda _kwargs: _listing(7),
        lambda _kwargs: HTTPNotModified(status_code=304, headers={}),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1

    row = CharacterAccessList.objects.get(character_id=CHARACTER_ID, access_list_id=7)
    assert row.name == "cached"
    assert row.description == "from before the upgrade"
    assert _access_of(row.membership) == [
        "Admin",
        "Manager",
        "Allowed",
        "Blocked",
        "Unspecified",
    ]
    assert row.membership["characters"][0]["name"] == "Admin Pilot"


def test_sync_falls_back_to_ids_when_name_resolution_is_rejected(linked_character, install_client):
    install_client(
        lambda _kwargs: _listing(7),
        lambda _kwargs: SimpleNamespace(
            id=7, name="ACL 7", description="", membership=_Membership(MIXED_MEMBERSHIP)
        ),
        lambda _kwargs: HTTPClientError(status_code=404, headers={}, data=None),
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1

    membership = CharacterAccessList.objects.get(
        character_id=CHARACTER_ID, access_list_id=7
    ).membership
    assert _access_of(membership) == ["Admin", "Manager", "Allowed", "Blocked", "Unspecified"]
    assert not any("name" in entry for entry in membership["characters"])


def test_resolve_names_reads_mapping_and_model_entries():
    client = _fake_client(
        lambda _kwargs: _listing(),
        lambda _kwargs: None,
        lambda _kwargs: [{"id": 1, "name": "From Dict"}, SimpleNamespace(id=2, name="From Model")],
    )
    assert services._resolve_names(client, {1, 2}) == {1: "From Dict", 2: "From Model"}


def test_resolve_names_chunks_large_batches(monkeypatch):
    monkeypatch.setattr(services, "NAME_CHUNK_SIZE", 2)
    client = _fake_client(lambda _kwargs: _listing(), lambda _kwargs: None, _names)
    assert services._resolve_names(client, {1, 2, 3, 4}) == EVE_NAMES
    assert [call["body"] for call in client.Universe.PostUniverseNames.calls] == [[1, 2], [3, 4]]


def test_sync_ignores_a_304_detail_with_no_stored_row(linked_character, install_client):
    install_client(
        lambda _kwargs: _listing(7),
        lambda _kwargs: HTTPNotModified(status_code=304, headers={}),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 0
    assert not CharacterAccessList.objects.filter(character_id=CHARACTER_ID).exists()


class _Recorder:
    """Records ORM chaining so tests can assert which lookups were used."""

    def __init__(self, rows=(), owner_ids=()):
        self.rows = list(rows)
        self.owner_ids = list(owner_ids)
        self.filters = []
        self.q_args = []
        self.calls = []
        self.value_lists = []

    def filter(self, *args, **kwargs):
        self.filters.append(kwargs)
        self.q_args.append(args)
        return self

    def with_contact_name(self):
        self.calls.append("with_contact_name")
        return self

    def prefetch_related(self, *args):
        self.calls.append(("prefetch_related", args))
        return self

    def order_by(self, *args):
        self.calls.append(("order_by", args))
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def values_list(self, *fields, flat=False):
        self.value_lists.append((fields, flat))
        return list(self.owner_ids)

    def count(self):
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)


def _stub_aa_contacts(
    monkeypatch,
    alliance_rows=(),
    corporation_rows=(),
    alliance_owner_ids=(),
    corporation_owner_ids=(),
):
    """Stand in for aa_contacts.models, which cannot be imported under these settings."""
    alliance = _Recorder(alliance_rows, alliance_owner_ids)
    corporation = _Recorder(corporation_rows, corporation_owner_ids)
    module = SimpleNamespace(
        AllianceContact=SimpleNamespace(objects=alliance),
        CorporationContact=SimpleNamespace(objects=corporation),
    )
    monkeypatch.setitem(sys.modules, "aa_contacts", SimpleNamespace(models=module))
    monkeypatch.setitem(sys.modules, "aa_contacts.models", module)
    return alliance, corporation


def _target(target_type="alliance", target_id=99005338):
    return SimpleNamespace(standing_target_type=target_type, standing_target_id=target_id)


def test_configured_contacts_matches_alliances_on_the_eve_id(monkeypatch):
    alliance, _ = _stub_aa_contacts(monkeypatch, alliance_rows=[SimpleNamespace(standing=5.0)])

    scope, contacts, reason = services.configured_contacts(_target())

    assert (scope, reason) == ("Alliance", None)
    assert len(contacts) == 1
    # The FK traversal reads EveAllianceInfo.alliance_id; a bare alliance_id would
    # read the local auto-pk instead and never match.
    assert alliance.filters == [{"alliance__alliance_id": 99005338}]


def test_configured_contacts_matches_corporations_on_the_eve_id(monkeypatch):
    _, corporation = _stub_aa_contacts(monkeypatch, corporation_rows=[SimpleNamespace(standing=0)])

    scope, contacts, reason = services.configured_contacts(_target("corporation", 98000001))

    assert (scope, reason) == ("Corporation", None)
    assert len(contacts) == 1
    assert corporation.filters == [{"corporation__corporation_id": 98000001}]


def test_configured_contacts_annotates_names_and_orders_by_standing(monkeypatch):
    alliance, _ = _stub_aa_contacts(monkeypatch, alliance_rows=[SimpleNamespace()])

    services.configured_contacts(_target())

    # Without with_contact_name() every contact_name read falls back to a per-row
    # lookup that hits ESI on a miss.
    assert "with_contact_name" in alliance.calls
    assert ("order_by", ("-standing", "contact_name_annotation")) in alliance.calls


def test_configured_contacts_reports_when_aa_contacts_is_unavailable():
    # aa_contacts is installed but absent from INSTALLED_APPS, so importing its
    # models raises RuntimeError rather than ImportError.
    assert services.configured_contacts(_target()) == (
        None,
        [],
        services.CONTACTS_NOT_INSTALLED,
    )


def test_configured_contacts_reports_a_missing_standing_target(monkeypatch):
    _stub_aa_contacts(monkeypatch)

    assert services.configured_contacts(_target(target_id=None)) == (
        None,
        [],
        services.CONTACTS_NO_TARGET,
    )


def test_configured_contacts_reports_contacts_that_were_never_synced(monkeypatch):
    _stub_aa_contacts(monkeypatch)

    assert services.configured_contacts(_target()) == (
        "Alliance",
        [],
        services.CONTACTS_NOT_SYNCED,
    )


def test_aa_contact_for_matches_alliances_on_the_eve_id(monkeypatch):
    row = SimpleNamespace(standing=10.0)
    alliance, _ = _stub_aa_contacts(monkeypatch, alliance_rows=[row])

    assert services.aa_contact_for(98000001, _target()) is row
    assert alliance.filters[0] == {"alliance__alliance_id": 99005338}
    assert "alliance_id" not in alliance.filters[0]


def test_aa_contact_for_matches_corporations_on_the_eve_id(monkeypatch):
    row = SimpleNamespace(standing=-5.0)
    _, corporation = _stub_aa_contacts(monkeypatch, corporation_rows=[row])

    assert services.aa_contact_for(98000001, _target("corporation", 98000002)) is row
    assert corporation.filters[0] == {"corporation__corporation_id": 98000002}


def test_aa_contact_for_survives_aa_contacts_being_unavailable():
    assert services.aa_contact_for(98000001, _target()) is None


def test_settings_form_offers_only_entities_with_contacts(monkeypatch):
    from aa_altcorp.forms import AltCorpSettingsForm

    alliance, _ = _stub_aa_contacts(
        monkeypatch,
        alliance_owner_ids=[(99005338, "Second Alliance"), (99000001, "First Alliance")],
    )

    form = AltCorpSettingsForm()

    # EVE ids rather than local pks, ordered by the name the admin actually reads.
    assert form.fields["standing_target_id"].choices == [
        (99000001, "First Alliance"),
        (99005338, "Second Alliance"),
    ]
    assert alliance.value_lists == [(("alliance__alliance_id", "alliance__alliance_name"), False)]


def test_settings_form_offers_corporation_owners_for_corporation_targets(monkeypatch):
    from aa_altcorp.forms import AltCorpSettingsForm

    _, corporation = _stub_aa_contacts(monkeypatch, corporation_owner_ids=[(98000001, "Test Corp")])

    form = AltCorpSettingsForm(data={"standing_target_type": "corporation"})

    assert form.fields["standing_target_id"].choices == [(98000001, "Test Corp")]
    assert corporation.value_lists == [
        (("corporation__corporation_id", "corporation__corporation_name"), False)
    ]


def test_settings_form_offers_nothing_when_no_contacts_are_stored(monkeypatch):
    from aa_altcorp.forms import AltCorpSettingsForm

    _stub_aa_contacts(monkeypatch)

    # An unusable target must not be selectable at all.
    assert AltCorpSettingsForm().fields["standing_target_id"].choices == []


def _settings_post(**overrides):
    """A complete settings POST. Fields with model defaults are still required
    on a bound ModelForm, so every non-blank field has to appear here."""
    return {
        "standing_target_type": "alliance",
        "notification_interval": "0 0 * * *",
        "alert_delivery": "auto",
        "minimum_blue_standing": "0.1",
        "alert_batch_size": "10",
        "renotify_interval_days": "0",
        **overrides,
    }


def test_settings_form_accepts_an_offered_eve_id(monkeypatch):
    from aa_altcorp.forms import AltCorpSettingsForm

    _stub_aa_contacts(monkeypatch, alliance_owner_ids=[(99005338, "Test Alliance")])

    form = AltCorpSettingsForm(data=_settings_post(standing_target_id="99005338"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["standing_target_id"] == "99005338"


def test_settings_form_clears_the_standing_target_to_none(monkeypatch):
    from aa_altcorp.forms import AltCorpSettingsForm

    _stub_aa_contacts(monkeypatch, alliance_owner_ids=[(99005338, "Test Alliance")])

    form = AltCorpSettingsForm(data=_settings_post(standing_target_id=""))

    assert form.is_valid(), form.errors
    # "" reaches a BigIntegerField and raises ValueError on int("") when saved.
    assert form.cleaned_data["standing_target_id"] is None


def test_settings_form_degrades_when_aa_contacts_is_unavailable():
    from aa_altcorp.forms import AltCorpSettingsForm

    assert AltCorpSettingsForm().fields["standing_target_id"].choices == []


FULLY_NAMED = {**EVE_NAMES, 5: "Unspecified Pilot"}


def test_membership_ids_can_skip_entries_that_already_have_a_name():
    membership = services._enrich_membership(MIXED_MEMBERSHIP, EVE_NAMES)

    assert services._membership_ids([membership]) == {1, 2, 3, 4, 5}
    assert services._membership_ids([membership], skip_named=True) == {5}


def test_resolve_names_bypasses_the_esi_response_cache():
    client = _fake_client(lambda _kwargs: _listing(), lambda _kwargs: None, _names)

    services._resolve_names(client, {1})

    # django-esi hashes the cache key without the request body, so every
    # PostUniverseNames call shares one entry regardless of the ids asked for.
    assert client.Universe.PostUniverseNames.result_kwargs == [
        {"use_etag": False, "use_cache": False, "store_cache": False}
    ]


def test_sync_reenriches_stored_rows_when_the_listing_is_not_modified(
    linked_character, install_client
):
    CharacterAccessList.objects.create(
        character_id=CHARACTER_ID,
        access_list_id=7,
        name="cached",
        membership=MIXED_MEMBERSHIP,
    )
    client = install_client(
        lambda _kwargs: HTTPNotModified(status_code=304, headers={}),
        lambda kwargs: _detail(kwargs["access_list_id"]),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1

    row = CharacterAccessList.objects.get(character_id=CHARACTER_ID, access_list_id=7)
    assert _access_of(row.membership) == [
        "Admin",
        "Manager",
        "Allowed",
        "Blocked",
        "Unspecified",
    ]
    assert row.membership["characters"][0]["name"] == "Admin Pilot"
    # The stored ids are enough, so replaying must not spend ACL requests.
    assert client.Access_List.GetCharactersAccessListsDetail.calls == []


def test_sync_forces_a_refresh_when_a_304_listing_has_nothing_stored(
    linked_character, install_client
):
    responses = [HTTPNotModified(status_code=304, headers={}), _listing(7)]
    client = install_client(
        lambda _kwargs: responses.pop(0),
        lambda kwargs: _detail(kwargs["access_list_id"]),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1

    # Without the retry the cached ETag would wedge this character forever.
    assert client.Access_List.GetCharactersAccessListsListing.result_kwargs == [
        {},
        {"force_refresh": True},
    ]
    assert CharacterAccessList.objects.filter(character_id=CHARACTER_ID).count() == 1


def test_sync_resolves_no_names_when_stored_rows_are_already_named(
    linked_character, install_client
):
    named = services._enrich_membership(MIXED_MEMBERSHIP, FULLY_NAMED)
    CharacterAccessList.objects.create(
        character_id=CHARACTER_ID, access_list_id=7, name="cached", membership=named
    )
    client = install_client(
        lambda _kwargs: HTTPNotModified(status_code=304, headers={}),
        lambda kwargs: _detail(kwargs["access_list_id"]),
        _names,
    )

    assert services.sync_character_access_lists(CHARACTER_ID) == 1

    # Steady state: nothing left to resolve, so no request and no write.
    assert client.Universe.PostUniverseNames.calls == []
    assert CharacterAccessList.objects.get(character_id=CHARACTER_ID).membership == named


def test_reenrich_is_a_noop_without_stored_rows(db):
    client = _fake_client(lambda _kwargs: _listing(), lambda _kwargs: None, _names)

    assert services._reenrich_stored_access_lists(client, CHARACTER_ID) == 0
    assert client.Universe.PostUniverseNames.calls == []


def test_contacts_diagnosis_reports_the_alliances_that_do_have_contacts(monkeypatch):
    alliance, _ = _stub_aa_contacts(
        monkeypatch,
        alliance_rows=[SimpleNamespace()] * 250,
        alliance_owner_ids=[99005338, 99005338, 99000001],
    )

    diagnosis = services.contacts_diagnosis(_target(target_id=2))

    assert diagnosis == {
        "target_id": 2,
        "total": 250,
        "owner_ids": [99000001, 99005338],
        "owner_count": 2,
    }
    assert alliance.value_lists == [(("alliance__alliance_id",), True)]


def test_contacts_diagnosis_reports_an_entirely_empty_table(monkeypatch):
    _stub_aa_contacts(monkeypatch)

    diagnosis = services.contacts_diagnosis(_target())

    assert diagnosis["total"] == 0
    assert diagnosis["owner_ids"] == []


def test_contacts_diagnosis_uses_the_corporation_model_for_corporation_targets(monkeypatch):
    _, corporation = _stub_aa_contacts(
        monkeypatch,
        corporation_rows=[SimpleNamespace()],
        corporation_owner_ids=[98000001],
    )

    diagnosis = services.contacts_diagnosis(_target("corporation", 98000002))

    assert diagnosis["owner_ids"] == [98000001]
    assert corporation.value_lists == [(("corporation__corporation_id",), True)]


def test_contacts_diagnosis_caps_the_listed_owners(monkeypatch):
    _stub_aa_contacts(
        monkeypatch,
        alliance_rows=[SimpleNamespace()],
        alliance_owner_ids=list(range(99000000, 99000015)),
    )

    diagnosis = services.contacts_diagnosis(_target())

    assert len(diagnosis["owner_ids"]) == 10
    assert diagnosis["owner_count"] == 15


def test_contacts_diagnosis_is_none_when_aa_contacts_is_unavailable():
    # Same guard as configured_contacts: the import raises RuntimeError here.
    assert services.contacts_diagnosis(_target()) is None


MIGRATION_0003 = "aa_altcorp.migrations.0003_standing_target_eve_id"


class _FakeEveQuery:
    def __init__(self, rows):
        self.rows = rows

    def exists(self):
        return bool(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeEveManager:
    """Minimal stand-in for an EveAllianceInfo/EveCorporationInfo manager."""

    def __init__(self, rows):
        self.rows = rows

    def filter(self, **kwargs):
        if "pk" in kwargs:
            return _FakeEveQuery([row for row in self.rows if row.pk == int(kwargs["pk"])])
        ((field, value),) = kwargs.items()
        return _FakeEveQuery([row for row in self.rows if getattr(row, field) == value])


class _FakeApps:
    """Stands in for the migration-state app registry."""

    def __init__(self, **eve_rows):
        self.eve_rows = eve_rows

    def get_model(self, app_label, model_name):
        if app_label == "aa_altcorp":
            return AltCorpSettings
        if app_label == "eveonline" and model_name in self.eve_rows:
            return SimpleNamespace(objects=_FakeEveManager(self.eve_rows[model_name]))
        msg = f"No installed app with label {app_label!r}"
        raise LookupError(msg)


def _migration_apps(alliances=(), corporations=()):
    return _FakeApps(
        EveAllianceInfo=list(alliances),
        EveCorporationInfo=list(corporations),
    )


def _alliance(pk, alliance_id):
    return SimpleNamespace(pk=pk, alliance_id=alliance_id)


def _run_0003(apps):
    importlib.import_module(MIGRATION_0003).forwards(apps, None)
    config = AltCorpSettings.current()
    config.refresh_from_db()
    return config.standing_target_id


def test_standing_target_id_documents_its_id_space():
    field = AltCorpSettings._meta.get_field("standing_target_id")

    # The ambiguity of a bare BigIntegerField is what let two readers diverge.
    assert "EVE alliance or corporation ID" in field.help_text
    assert "not the local Alliance Auth row ID" in field.help_text


def test_migration_converts_a_stale_alliance_pk_to_the_eve_id(db):
    config = AltCorpSettings.current()
    config.standing_target_type = "alliance"
    config.standing_target_id = 1
    config.save()

    assert _run_0003(_migration_apps(alliances=[_alliance(1, 99009902)])) == 99009902


def test_migration_leaves_a_correct_eve_id_alone(db):
    config = AltCorpSettings.current()
    config.standing_target_type = "alliance"
    config.standing_target_id = 99009902
    config.save()

    # Idempotent: re-running migrate must not convert an already-good value, even
    # when that value also happens to match some other row's pk.
    apps = _migration_apps(alliances=[_alliance(99009902, 99009902), _alliance(1, 99009902)])
    assert _run_0003(apps) == 99009902


def test_migration_leaves_an_unrecognized_value_alone(db):
    config = AltCorpSettings.current()
    config.standing_target_type = "alliance"
    config.standing_target_id = 12345
    config.save()

    assert _run_0003(_migration_apps(alliances=[_alliance(1, 99009902)])) == 12345


def test_migration_converts_corporation_targets_on_the_corporation_model(db):
    config = AltCorpSettings.current()
    config.standing_target_type = "corporation"
    config.standing_target_id = 3
    config.save()

    apps = _migration_apps(
        corporations=[SimpleNamespace(pk=3, corporation_id=98000001)],
    )
    assert _run_0003(apps) == 98000001


def test_migration_ignores_settings_without_a_target(db):
    config = AltCorpSettings.current()
    config.standing_target_id = None
    config.save()

    # Returns before touching eveonline at all, which is what keeps a fresh
    # install safe when eveonline's own migrations have not run yet.
    assert _run_0003(_FakeApps()) is None


def test_migration_is_a_noop_without_the_eveonline_app(db):
    config = AltCorpSettings.current()
    config.standing_target_type = "alliance"
    config.standing_target_id = 1
    config.save()

    # eveonline is absent from these settings, so the LookupError guard applies.
    assert _run_0003(apps) == 1


class _ProfileRecorder:
    """Stand-in for UserProfile.objects, recording how mains were queried."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.selected = None
        self.filters = None
        self.ordering = None
        self.slice = None

    def select_related(self, *fields):
        self.selected = fields
        return self

    def filter(self, **kwargs):
        self.filters = kwargs
        return self

    def order_by(self, *fields):
        self.ordering = fields
        return self

    def __getitem__(self, item):
        self.slice = item
        return self.rows[item]


def _profile(user_id, character_name, corporation_name="Test Corp"):
    return SimpleNamespace(
        user_id=user_id,
        main_character=SimpleNamespace(
            character_name=character_name, corporation_name=corporation_name
        ),
    )


def _stub_allianceauth_profiles(monkeypatch, rows=()):
    recorder = _ProfileRecorder(rows)
    module = SimpleNamespace(UserProfile=SimpleNamespace(objects=recorder))
    monkeypatch.setitem(sys.modules, "allianceauth", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "allianceauth.authentication", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "allianceauth.authentication.models", module)
    return recorder


def test_search_main_characters_queries_mains_by_name(monkeypatch):
    recorder = _stub_allianceauth_profiles(
        monkeypatch, [_profile(7, "Bio Brute", "Brute Industries")]
    )

    assert services.search_main_characters("bio") == [
        {
            "user_id": 7,
            "character_name": "Bio Brute",
            "corporation_name": "Brute Industries",
        }
    ]
    assert recorder.filters == {
        "main_character__isnull": False,
        "main_character__character_name__icontains": "bio",
    }
    assert recorder.ordering == ("main_character__character_name",)
    assert recorder.selected == ("user", "main_character")


def test_search_main_characters_honours_the_limit(monkeypatch):
    recorder = _stub_allianceauth_profiles(monkeypatch, [_profile(1, "A"), _profile(2, "B")])

    services.search_main_characters("", limit=5)

    assert recorder.slice == slice(None, 5)


def test_search_main_characters_without_allianceauth():
    # allianceauth is installed but absent from INSTALLED_APPS, so importing its
    # models raises RuntimeError rather than ImportError.
    assert services.search_main_characters("bio") == []


def test_associate_contact_links_a_character(db):
    user = User.objects.create_user("assignee")

    association = services.associate_contact(user, "character", 212620763, "Bio Brute")

    assert association.character_name == "Bio Brute"
    assert AltCharacter.objects.filter(user=user, character_id=212620763).exists()


def test_associate_contact_links_a_corporation(db):
    user = User.objects.create_user("assignee")

    association = services.associate_contact(user, "corporation", 98000001, "Brute Industries")

    assert association.corporation_name == "Brute Industries"
    # Recorded as a manual link, matching what the index page's attach form stores.
    assert association.source == "auth"


def test_associate_contact_is_idempotent(db):
    user = User.objects.create_user("assignee")

    first = services.associate_contact(user, "character", 212620763, "Bio Brute")
    second = services.associate_contact(user, "character", 212620763, "Renamed")

    assert first.pk == second.pk
    assert AltCharacter.objects.filter(user=user).count() == 1


def test_associate_contact_declines_unsupported_types(db):
    user = User.objects.create_user("assignee")

    for contact_type in ("alliance", "faction", "nonsense"):
        assert services.associate_contact(user, contact_type, 99009902, "Some Alliance") is None
    assert not AltCharacter.objects.exists()
    assert not AltCorporation.objects.exists()


def test_associable_contact_types_agree_with_contact_associations(db):
    user = User.objects.create_user("assignee")

    # These two functions branch on the same contact types in different places;
    # pin them together so they cannot drift apart.
    for contact_type in services.ASSOCIABLE_CONTACT_TYPES:
        contact = SimpleNamespace(contact_type=contact_type, contact_id=1)
        assert services.contact_associations(contact) == []
        assert services.associate_contact(user, contact_type, 1, "Name") is not None
        assert services.contact_associations(contact) == ["Name"]

    for contact_type in ("alliance", "faction"):
        contact = SimpleNamespace(contact_type=contact_type, contact_id=1)
        assert services.contact_associations(contact) == []
        assert services.associate_contact(user, contact_type, 1, "Name") is None
