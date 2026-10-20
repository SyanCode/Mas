"""Cached scan result shared by the API and the background scanner."""

from __future__ import annotations

import logging
import threading

from mas.config import Config
from mas.correlator import scan
from mas.db import Database
from mas.host import Host
from mas.models import ScanResult, Service, ServiceType

log = logging.getLogger(__name__)


def apply_meta(result: ScanResult, db: Database) -> None:
    meta = db.all_meta()
    for svc in result.services:
        m = meta.get(svc.id)
        if m is None:
            continue
        svc.display_name, svc.notes = m["display_name"], m["notes"]


def run_scan(host: Host, config: Config, db: Database) -> ScanResult:
    overrides = {}
    for sid, m in db.all_meta().items():
        if m["type_override"]:
            try:
                overrides[sid] = ServiceType(m["type_override"])
            except ValueError:
                pass
    result = scan(host, config, type_overrides=overrides)
    apply_meta(result, db)
    return result


class Scanner:
    def __init__(self, host: Host, config: Config, db: Database) -> None:
        self.host, self.config, self.db = host, config, db
        self._result: ScanResult | None = None
        self._lock = threading.Lock()

    def refresh(self) -> ScanResult:
        with self._lock:
            self._result = run_scan(self.host, self.config, self.db)
            return self._result

    def result(self) -> ScanResult:
        return self._result or self.refresh()

    def get(self, service_id: str) -> Service | None:
        return next((s for s in self.result().services if s.id == service_id), None)

    def reapply_meta(self) -> None:
        if self._result is not None:
            apply_meta(self._result, self.db)
