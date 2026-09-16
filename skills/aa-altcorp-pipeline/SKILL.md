---
name: aa-altcorp-pipeline
description: Run and diagnose the aa-altcorp Alliance Auth plugin validation pipeline, including pre-commit, pytest, and tox. Use when the user asks to run the project's pipelines, checks, or CI preparation.
metadata:
  short-description: Run aa-altcorp Alliance Auth checks
---

# aa-altcorp Alliance Auth plugin pipeline

Run the repository checks in a clean, repeatable order and report the first failure with its command and useful output. Work from the repository root.

On Windows PowerShell, use the repository helper. It validates the plugin through its Alliance Auth test project; it does not build a standalone application:

```powershell
.\scripts\run_pipeline.ps1
```

On Ubuntu or another Unix shell, run:

```bash
source .venv/bin/activate
pre-commit run --all-files
pytest
tox -e py312
```

If `.venv` does not exist, tell the user to install the project first with `python3.12 -m venv .venv` and `python -m pip install -e '.[dev]'`. Do not silently use a different virtual environment.

The checks are local equivalents of `.github/workflows/automated-checks.yml`. They validate repository hygiene, Python style, Django tests, and the Alliance Auth tox test runner. If a command fails, stop there, summarize the actionable error, and fix it only when the user asked for fixes. If all checks pass, report that the branch is ready to commit or push.

Do not run `git push`, create a pull request, merge branches, or change GitHub settings as part of this skill. Those are separate user-authorized actions.
