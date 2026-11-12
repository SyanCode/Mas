"""Country of a visitor, for the traffic map. Only a country code is kept, never the address.

Mas reads the free *IP to Country Lite* database of DB-IP (CC BY 4.0, "IP Geolocation by DB-IP"),
downloaded once from the dashboard into the data folder and refreshed by the administrator when
wanted. Without the file the map is simply off: counting goes on without countries.
"""

from __future__ import annotations

import gzip
import ipaddress
import logging
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"
ATTRIBUTION = "IP Geolocation by DB-IP"
MAX_DOWNLOAD = 80 * 1024 * 1024


class GeoError(Exception):
    pass


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "mas"})
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:  # noqa: S310 - fixed https host
        size = 0
        while chunk := resp.read(1 << 20):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise GeoError("the database is larger than expected")
            out.write(chunk)


class Geo:
    def __init__(self, data_dir: str | Path, downloader: Callable[[str, Path], None] = _download) -> None:
        self.path = Path(data_dir) / "geo" / "dbip-country-lite.mmdb"
        self.downloader = downloader
        self._reader = None
        self._mtime = 0.0

    def available(self) -> bool:
        return self._open() is not None

    def _open(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._reader = None
            return None
        if self._reader is None or mtime != self._mtime:
            try:
                import maxminddb
                self._reader = maxminddb.open_database(str(self.path))
                self._mtime = mtime
            except Exception:  # noqa: BLE001 - missing library or damaged file: no map
                log.warning("country database not readable", exc_info=True)
                self._reader = None
        return self._reader

    def country(self, ip: str) -> str | None:
        """Two-letter country code of an address, None when unknown (private address, no database)."""
        reader = self._open()
        if reader is None:
            return None
        try:
            if not ipaddress.ip_address(ip).is_global:
                return None
            found = reader.get(ip)
        except (ValueError, Exception):  # noqa: BLE001
            return None
        code = ((found or {}).get("country") or {}).get("iso_code")
        return code if isinstance(code, str) and len(code) == 2 else None

    def install(self, today: date | None = None) -> dict:
        """Download the database of this month (or last month's when it is not out yet)."""
        today = today or date.today()
        months = [today.strftime("%Y-%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")]
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        last: Exception | None = None
        for month in months:
            with tempfile.TemporaryDirectory(dir=self.path.parent) as tmp:
                gz, out = Path(tmp) / "db.gz", Path(tmp) / "db.mmdb"
                try:
                    self.downloader(URL.format(month=month), gz)
                    with gzip.open(gz, "rb") as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                except Exception as exc:  # noqa: BLE001 - try the previous month
                    last = exc
                    continue
                os.replace(out, self.path)
                self._reader = None
                return {"month": month, "size": self.path.stat().st_size}
        raise GeoError(f"could not download the country database: {last}")
