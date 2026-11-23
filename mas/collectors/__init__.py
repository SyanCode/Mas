"""Collectors: each one reads a single source and returns raw typed records.

A collector must never break the scan. When its source is missing it returns
an empty list and records a warning on the ``ScanContext``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from mas.config import Config
from mas.host import Host

log = logging.getLogger("mas.scan")
_logged: set[str] = set()  # a warning goes to the journal once per process, not at every scan


@dataclass
class ScanContext:
    host: Host
    config: Config = field(default_factory=Config)
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        if message not in _logged and len(_logged) < 500:
            _logged.add(message)
            log.warning(message)
        if message not in self.warnings:
            self.warnings.append(message)
