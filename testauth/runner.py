"""Test runner that drops Alliance Auth's deployment-environment checks.

Alliance Auth registers checks that probe the live Redis, MySQL and MariaDB
servers for their versions (``allianceauth.checks.system_package_redis`` and
friends).  Against this project's in-memory SQLite and fakeredis backends they
do not merely report a warning -- they raise, because each one only catches
``InvalidVersion`` and not the ``ResponseError`` or ``ConnectionError`` a
non-server backend produces.  They also say nothing whatsoever about the plugin
under test.

Only those functions are removed.  Every other check still runs, which is the
point: the model, admin, URL and django-esi security checks are exactly the ones
worth having on a plugin.
"""

from django.test.runner import DiscoverRunner


class SkipEnvironmentChecksRunner(DiscoverRunner):
    def run_checks(self, databases=None):
        from django.core.checks.registry import registry

        original = set(registry.registered_checks)
        registry.registered_checks = {
            check for check in original if getattr(check, "__module__", "") != "allianceauth.checks"
        }
        try:
            super().run_checks(databases=databases)
        finally:
            registry.registered_checks = original
