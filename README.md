# AA Alt Corp

`aa-altcorp` is an Alliance Auth plugin for tracking alt-corporation relationships and auditing whether member-related characters, corporations, and alliances have the expected contact standings and access-list permissions.

It reads Alliance Auth and `aa-contacts` data, compares that data with configured policy, and reports differences for human review. It does not modify EVE contacts or access lists.

## What it does

- Records corporations and characters associated with Alliance Auth users.
- Audits contact standings and access-list access for valid members.
- Detects blue contacts and ACL entries without a matching Auth relationship.
- Supports policies for Auth states, groups, positive contacts, named entities, and individual access lists.
- Creates separate alert facets for contact and ACL problems.
- Supports temporary exemptions with expiry, revocation, and an audit trail.
- Delivers alerts through `allianceauth-discordbot` or a plain webhook.
- Keeps the older corporation review flow available for existing installations.

The alert engine is informational. It never writes to EVE contacts or EVE access lists; changes remain manual administrator actions.

## Requirements

The plugin supports Python 3.10 through 3.13 and requires:

```text
Alliance Auth >= 5.3, < 6
aa-contacts >= 1.0.1, < 2
django-esi >= 9, < 10
croniter >= 6, < 7
django-celery-beat >= 2.7, < 3
```

## Installation

Activate the virtual environment used by Alliance Auth and install the package:

```bash
pip install allianceauth-altcorp
```

Add the application to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "aa_altcorp",
]
```

Run migrations and collect static files:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

The app configuration is `aa_altcorp.apps.AaAltcorpConfig`.

## Configuration

Open **Django Admin → Alt Corp settings** after installation. Configure the standing target, valid Auth states, expected entity scope, alert delivery, notification interval, and Discord details there.

Alerting defaults to disabled so an upgrade does not immediately send notifications. Configure the expected state and policies first, then enable alerts.

An access list produces a missing-access alert only when it has an **Access list policy** in Django Admin. Present-but-unjustified entries can still produce alerts without a policy.

## Scheduling

Create the periodic audit, alert scan, and Discord refresh tasks with:

```bash
python manage.py schedule_altcorp_tasks --scan-cron "0 * * * *"
```

The command is idempotent. `--hours` changes the audit interval, and `--remove` removes the tasks it created. On larger installations, schedule `aa_altcorp.tasks.sync_all_access_lists` separately to fan ACL synchronization out across characters.

## Discord alerts

Discord integration is provided by [allianceauth-discordbot](https://github.com/Solar-Helix-Independent-Transport/allianceauth-discordbot). Go to that repository for installation, configuration, and bot worker instructions.

## Permissions

| Permission | Purpose |
| --- | --- |
| `aa_altcorp.basic_access` | View plugin pages |
| `aa_altcorp.manage_relationships` | Attach corporations and characters |
| `aa_altcorp.view_alerts` | Reserved for future use |
| `aa_altcorp.manage_alerts` | Reserved for future use |

Alerts, exemptions, and access-list policies are managed through Django Admin permissions. The reserved alert permissions do not independently grant admin access. Discord authorization follows the configured roles.

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pre-commit run --all-files
pytest
tox -e py312
```

On Windows, run the complete local pipeline with:

```powershell
.\scripts\run_pipeline.ps1
```

GitHub Actions runs pre-commit, the Auth-absent pytest suite on Python 3.10–3.13, and the Alliance Auth integration suite on Python 3.12. See [RELEASING.md](RELEASING.md) for the first push, development workflow, and releases.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).
