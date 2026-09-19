"""Guards the rule that keeps this app installable and testable.

``allianceauth``, ``aa_contacts``, ``aadiscordbot`` and ``discord`` are all
optional at runtime.  Importing one at *module* level breaks any install that
lacks it, and breaks both test settings modules, which have none of them.
Every use must therefore sit inside a function, behind a guard.

Checked statically with ``ast`` rather than by re-importing: importing a Django
models module twice re-registers its models and warns, and the invariant is a
property of the source, not of a particular import order.

``auth_hooks`` and the four bot-side modules are exempt -- only Alliance Auth
itself and the bot process ever import those.
"""

import ast
import pathlib

import pytest

FORBIDDEN = {"allianceauth", "aa_contacts", "aadiscordbot", "discord"}

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "aa_altcorp"

#: Loaded by Alliance Auth or by the bot, so a hard dependency is fine there.
EXEMPT = {
    "auth_hooks.py",
    "discord/views.py",
    "discord/modals.py",
    "discord/bot_functions.py",
    "cogs/altcorp_alerts.py",
}


def _source_files():
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        if relative.startswith(("migrations/", "tests/")):
            continue
        if relative in EXEMPT:
            continue
        yield relative, path


def _module_level_imports(tree):
    """Import statements at column 0, i.e. executed at import time."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module


@pytest.mark.parametrize("relative,path", list(_source_files()), ids=lambda v: str(v))
def test_optional_dependencies_are_never_imported_at_module_level(relative, path):
    tree = ast.parse(path.read_text(encoding="utf-8"))

    offenders = [name for name in _module_level_imports(tree) if name.split(".")[0] in FORBIDDEN]

    assert offenders == [], (
        f"{relative} imports {offenders} at module level. "
        "Move it inside the function that needs it, behind "
        "try/except (ImportError, RuntimeError)."
    )


def test_the_exempt_list_still_matches_reality():
    """A renamed bot-side module must not silently lose its exemption."""
    for relative in EXEMPT:
        assert (PACKAGE_ROOT / relative).exists(), f"{relative} no longer exists"


def test_guarded_imports_catch_runtime_error():
    """A missing INSTALLED_APPS entry raises RuntimeError, not ImportError.

    Catching only ImportError is the classic way to get this wrong, so assert
    the existing call sites keep both.
    """
    source = (PACKAGE_ROOT / "services.py").read_text(encoding="utf-8")
    assert "(ImportError, RuntimeError)" in source
