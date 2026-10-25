"""End-to-end tests: Mas against a real Docker, a real Postgres and a real mail server.

They run on a throwaway machine (the "E2E" GitHub Actions workflow, or a test VM) as root:

    sudo MAS_E2E=1 python -m pytest tests/e2e -v

Without MAS_E2E=1 every test is skipped. They refuse to run where Mas is installed: they create
containers named like the real ones (``supabase-db``) and bind ports such as 25 and 993.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from e2e_helpers import Env

E2E = os.environ.get("MAS_E2E") == "1"


def pytest_collection_modifyitems(config, items):
    reason = None
    if not E2E:
        reason = "end-to-end tests: set MAS_E2E=1 on a throwaway machine (see tests/e2e/conftest.py)"
    elif Path("/etc/mas/config.toml").exists() or Path("/var/lib/mas/mas.db").exists():
        reason = "Mas is installed on this machine: end-to-end tests only run on a throwaway one"
    elif hasattr(os, "geteuid") and os.geteuid() != 0:
        reason = "end-to-end tests need root (Docker, ports 25 and 993, /etc/letsencrypt)"
    elif shutil.which("docker") is None:
        reason = "Docker is not installed"
    if reason:
        for item in items:
            if "/e2e/" in str(item.fspath).replace("\\", "/"):
                item.add_marker(pytest.mark.skip(reason=reason))


def pytest_runtest_logreport(report):
    """On GitHub Actions, a failure becomes an annotation: its reason shows on the run's page."""
    if report.failed and os.environ.get("GITHUB_ACTIONS") == "true":
        text = str(report.longrepr)[-3500:]
        text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        title = report.nodeid.replace(":", " ").replace(",", " ")
        sys.__stdout__.write(f"\n::error title={title} ({report.when})::{text}\n")
        sys.__stdout__.flush()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)
