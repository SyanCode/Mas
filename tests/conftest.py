from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

import server  # noqa: E402  (tests/fixtures/server.py)

from mas.collectors import ScanContext  # noqa: E402
from mas.correlator import scan  # noqa: E402
from mas.host import FixtureHost  # noqa: E402


@pytest.fixture(scope="session")
def server_dir(tmp_path_factory) -> Path:
    return server.build(tmp_path_factory.mktemp("server"))


@pytest.fixture
def host(server_dir) -> FixtureHost:
    return FixtureHost(server_dir)


@pytest.fixture
def ctx(host) -> ScanContext:
    return ScanContext(host)


@pytest.fixture(scope="session")
def result(server_dir):
    return scan(FixtureHost(server_dir))


@pytest.fixture(scope="session")
def services(result):
    return {s.id: s for s in result.services}
