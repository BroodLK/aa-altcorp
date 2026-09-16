"""Shared fixtures for the alert engine tests.

Under ``tests.settings`` neither ``allianceauth``, ``aa_contacts``, ``discord``
nor ``aadiscordbot`` is importable, so everything that touches them is either
stubbed in ``sys.modules`` or fed a hand-built dataclass.
"""

import sys
from types import SimpleNamespace

import pytest

from aa_altcorp.alerts import taxonomy
from aa_altcorp.alerts.context import AuthSnapshot, CharacterFacts


class ContactRow(SimpleNamespace):
    """A row shaped like aa_contacts' Contact, as configured_contacts yields it."""

    def __init__(self, contact_id, contact_type="corporation", standing=5.0, name=""):
        super().__init__(
            contact_id=contact_id,
            contact_type=contact_type,
            standing=standing,
            contact_name_annotation=name,
            notes="",
        )


class ContactQuerysetStub:
    """Supports the chain configured_contacts() actually uses."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def with_contact_name(self):
        return self

    def prefetch_related(self, *args):
        return self

    def order_by(self, *args):
        return self

    def values_list(self, *fields, flat=False):
        return []

    def count(self):
        return len(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


@pytest.fixture
def stub_aa_contacts(monkeypatch):
    """Install a fake ``aa_contacts.models`` returning the given contact rows."""

    def install(alliance_rows=(), corporation_rows=()):
        module = SimpleNamespace(
            AllianceContact=SimpleNamespace(objects=ContactQuerysetStub(alliance_rows)),
            CorporationContact=SimpleNamespace(objects=ContactQuerysetStub(corporation_rows)),
        )
        monkeypatch.setitem(sys.modules, "aa_contacts", SimpleNamespace(models=module))
        monkeypatch.setitem(sys.modules, "aa_contacts.models", module)
        return module

    return install


@pytest.fixture
def no_aa_contacts(monkeypatch):
    """Make importing aa_contacts fail, as on an install without it."""
    monkeypatch.setitem(sys.modules, "aa_contacts", None)
    monkeypatch.setitem(sys.modules, "aa_contacts.models", None)


@pytest.fixture
def make_snapshot():
    """Build an AuthSnapshot from plain dicts.

    This is the whole reason AuthSnapshot has an ordinary constructor separate
    from from_auth(): detectors stay testable without Alliance Auth.
    """

    def build(characters=(), approved_user_ids=(), user_groups=None, corp_alliance=None):
        snapshot = AuthSnapshot(available=True)
        for spec in characters:
            facts = CharacterFacts(**spec)
            snapshot.characters[facts.character_id] = facts
            if facts.user_id is not None:
                snapshot.user_states[facts.user_id] = facts.state_name
                if facts.corporation_id:
                    snapshot.corporations.setdefault(facts.corporation_id, set()).add(facts.user_id)
                if facts.alliance_id:
                    snapshot.alliances.setdefault(facts.alliance_id, set()).add(facts.user_id)
                if facts.is_main:
                    snapshot.main_character_of[facts.user_id] = facts.character_id
        snapshot.approved_user_ids = set(approved_user_ids)
        snapshot.user_groups = dict(user_groups or {})
        snapshot.corp_alliance = dict(corp_alliance or {})
        return snapshot

    return build


@pytest.fixture
def alert_settings(db):
    """A saved settings singleton with the alert engine switched on."""
    from aa_altcorp.models import AltCorpSettings

    settings = AltCorpSettings.current()
    settings.alerts_enabled = True
    settings.standing_target_type = "alliance"
    settings.standing_target_id = 99005338
    settings.approved_states = ["Member"]
    settings.minimum_blue_standing = 0.1
    settings.discord_role_ids = [4242]
    settings.save()
    return settings


@pytest.fixture
def make_alert(db):
    """Create an Alert plus its facets directly, bypassing a scan."""
    from aa_altcorp.models import Alert, AlertFacet

    def build(
        alert_type=taxonomy.AlertType.PRESENT_WITHOUT_AUTH_USER,
        entity_type=taxonomy.EntityType.CORPORATION,
        entity_id=98000001,
        entity_name="Example Corp",
        facets=(("contact", None, ""),),
    ):
        alert = Alert.objects.create(
            alert_type=alert_type,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
        )
        for facet_type, access_list_id, access_list_name in facets:
            AlertFacet.objects.create(
                alert=alert,
                facet_type=facet_type,
                access_list_id=access_list_id,
                access_list_name=access_list_name,
            )
        alert.recompute_state()
        return alert

    return build


@pytest.fixture
def actor():
    from aa_altcorp.discord.actions import Actor

    return Actor(discord_id=123456789, discord_name="SomeAdmin", guild_id=999)


@pytest.fixture
def acl_row(db):
    """Create a CharacterAccessList row with a hand-written membership payload."""
    from aa_altcorp.models import CharacterAccessList

    def build(access_list_id=7001, character_id=95000001, name="Capital Staging", **groups):
        membership = {
            "allow_everyone": groups.pop("allow_everyone", False),
            "characters": groups.pop("characters", []),
            "corporations": groups.pop("corporations", []),
            "alliances": groups.pop("alliances", []),
        }
        return CharacterAccessList.objects.create(
            character_id=character_id,
            access_list_id=access_list_id,
            name=name,
            membership=membership,
        )

    return build


class _FakeItem:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeContainer:
    """Records add_item() calls so tests can inspect the rendered components."""

    def __init__(self, *args, **kwargs):
        self.children = []
        self.init_kwargs = kwargs

    def add_item(self, item):
        self.children.append(item)


@pytest.fixture
def stub_discord(monkeypatch):
    """A py-cord stand-in good enough to import and exercise views/modals."""
    ui = SimpleNamespace(
        View=type("View", (_FakeContainer,), {}),
        Modal=type("Modal", (_FakeContainer,), {}),
        Button=_FakeItem,
        InputText=_FakeItem,
    )
    module = SimpleNamespace(
        ui=ui,
        ButtonStyle=SimpleNamespace(
            primary="primary", secondary="secondary", success="success", danger="danger"
        ),
        InputTextStyle=SimpleNamespace(short="short", long="long"),
        Embed=SimpleNamespace(from_dict=lambda data: data),
        NotFound=type("NotFound", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "discord", module)
    monkeypatch.setitem(sys.modules, "discord.ui", ui)
    for name in ("aa_altcorp.discord.views", "aa_altcorp.discord.modals"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return module


@pytest.fixture
def stub_aadiscordbot(monkeypatch):
    """Record what would have been queued, without a broker."""
    calls = []

    def apply_async(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

    tasks = SimpleNamespace(run_task_function=SimpleNamespace(apply_async=apply_async))
    monkeypatch.setitem(sys.modules, "aadiscordbot", SimpleNamespace(tasks=tasks))
    monkeypatch.setitem(sys.modules, "aadiscordbot.tasks", tasks)
    return calls


@pytest.fixture
def no_aadiscordbot(monkeypatch):
    monkeypatch.setitem(sys.modules, "aadiscordbot", None)
    monkeypatch.setitem(sys.modules, "aadiscordbot.tasks", None)


@pytest.fixture
def bot_db(transactional_db):
    """Database access for coroutines.

    Django's connection registry is an ``asgiref.local.Local``, which is
    coroutine-aware: ORM work inside ``asyncio.run`` resolves to a different
    connection than the test body's. The default ``db`` fixture wraps the test
    in an uncommitted transaction that connection cannot see, so every query
    fails with "database table is locked". ``transactional_db`` commits, which
    lets the real threaded ``dbsafe.run_db`` be exercised as it runs in
    production. pytest-django gives it precedence when both are requested.
    """
    return transactional_db
