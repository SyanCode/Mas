"""Copy of the backups of Mas outside the server: any S3-compatible storage (AWS, Scaleway, OVH, Backblaze,
MinIO…), over HTTPS, with requests signed here (AWS Signature V4, no SDK). The secret key is kept encrypted
and never sent back to the dashboard. Without this a backup shares the fate of the server's disk.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from mas.db import Database
from mas.envcrypt import EnvError
from mas.notify import Notifier

log = logging.getLogger(__name__)

SETTING = "selfbackup:remote"
MAX_UPLOAD = 256 * 1024 * 1024
REGION_RE = __import__("re").compile(r"^[a-z0-9-]{2,30}$")
BUCKET_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class OffsiteError(Exception):
    pass


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def sign_v4(method: str, url: str, headers: dict[str, str], payload_hash: str, access_key: str, secret_key: str,
            region: str, now: datetime, service: str = "s3") -> str:
    """The ``Authorization`` header of a request. ``headers`` are the ones to sign (``host`` included)."""
    parts = urlsplit(url)
    names = sorted(h.lower() for h in headers)
    lowered = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    canonical = "\n".join([
        method, parts.path or "/",
        "&".join(sorted(parts.query.split("&"))) if parts.query else "",
        "".join(f"{n}:{lowered[n]}\n" for n in names), ";".join(names), payload_hash])
    day, stamp = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, "
            f"Signature={signature}")


class Offsite:
    def __init__(self, db: Database, notifier: Notifier) -> None:
        self.db, self.notifier = db, notifier
        self.opener = urllib.request.urlopen  # replaced in tests

    # -- settings ---------------------------------------------------------------------------------------

    def settings(self, reveal: bool = False) -> dict:
        raw = json.loads(self.db.get_setting(SETTING) or "null") or {}
        out = {"endpoint": raw.get("endpoint", ""), "region": raw.get("region", "us-east-1"), "bucket": raw.get("bucket", ""),
               "prefix": raw.get("prefix", "mas/"), "access_key": raw.get("access_key", ""),
               "has_secret": bool(raw.get("secret_sealed")), "enabled": bool(raw.get("enabled"))}
        if reveal:
            out["secret_key"] = self.notifier._open(raw.get("secret_sealed", "")) if raw.get("secret_sealed") else ""
        return out

    def configure(self, body: dict) -> dict:
        raw = json.loads(self.db.get_setting(SETTING) or "null") or {}
        endpoint = str(body.get("endpoint", "")).strip().rstrip("/")
        region, bucket = str(body.get("region", "")).strip(), str(body.get("bucket", "")).strip()
        prefix = str(body.get("prefix", "")).strip().lstrip("/")
        access = str(body.get("access_key", "")).strip()
        if body.get("enabled") or endpoint:
            parts = urlsplit(endpoint)
            if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.query:
                raise OffsiteError("endpoint: an https:// address with no path (https://s3.fr-par.scw.cloud)")
            if not REGION_RE.fullmatch(region):
                raise OffsiteError("region: letters, digits and dashes (us-east-1, fr-par…)")
            if not BUCKET_RE.fullmatch(bucket):
                raise OffsiteError("bucket: 3 to 63 lowercase letters, digits, dots and dashes")
            if not access or len(access) > 128:
                raise OffsiteError("access key: required")
        if prefix and (len(prefix) > 100 or ".." in prefix or any(c in prefix for c in "\n\r?#\\")):
            raise OffsiteError("prefix: a folder name such as mas/")
        sealed = raw.get("secret_sealed", "")
        if body.get("secret_key"):
            sealed = self.notifier._seal(str(body["secret_key"]))
        if body.get("enabled") and not sealed:
            raise OffsiteError("secret key: required")
        self.db.set_setting(SETTING, json.dumps({
            "enabled": bool(body.get("enabled")), "endpoint": endpoint, "region": region, "bucket": bucket,
            "prefix": prefix, "access_key": access, "secret_sealed": sealed}))
        return self.settings()

    # -- sending ---------------------------------------------------------------------------------------------

    def put(self, name: str, data: bytes, cfg: dict | None = None, now: datetime | None = None) -> str:
        cfg = cfg or self.settings(reveal=True)
        if not (cfg["endpoint"] and cfg["bucket"] and cfg["access_key"] and cfg.get("secret_key")):
            raise OffsiteError("the remote storage is not set up")
        if len(data) > MAX_UPLOAD:
            raise OffsiteError("the archive is too large to send")
        now = now or datetime.now(timezone.utc)
        key = f"{cfg['prefix']}{name}"
        url = f"{cfg['endpoint']}/{cfg['bucket']}/{quote(key, safe='/~')}"
        payload = hashlib.sha256(data).hexdigest()
        headers = {"host": urlsplit(url).netloc, "x-amz-content-sha256": payload, "x-amz-date": now.strftime("%Y%m%dT%H%M%SZ")}
        auth = sign_v4("PUT", url, headers, payload, cfg["access_key"], cfg["secret_key"], cfg["region"], now)
        req = urllib.request.Request(url, data=data, method="PUT", headers={
            "x-amz-content-sha256": payload, "x-amz-date": headers["x-amz-date"], "Authorization": auth,
            "Content-Type": "application/octet-stream", "User-Agent": "mas"})
        try:
            with self.opener(req, timeout=300) as resp:  # noqa: S310 - https only, set by an administrator
                if resp.status >= 300:
                    raise OffsiteError(f"the storage answered {resp.status}")
        except urllib.error.HTTPError as exc:
            raise OffsiteError(f"the storage answered {exc.code}") from None
        except OSError as exc:
            raise OffsiteError(f"cannot reach the storage: {exc}") from None
        return key

    def upload(self, archive: Path) -> str | None:
        """Send a backup if the copy is on. Never raises: the local backup is what counts."""
        cfg = self.settings(reveal=True)
        if not cfg["enabled"]:
            return None
        try:
            key = self.put(archive.name, archive.read_bytes(), cfg)
        except (OffsiteError, EnvError, OSError) as exc:
            self.db.audit("offsite", "mas-backup-offsite", False, detail=f"{archive.name}: {exc}"[:300])
            log.warning("offsite copy of %s failed: %s", archive.name, exc)
            return None
        self.db.audit("offsite", "mas-backup-offsite", True, detail=f"{archive.name} → {cfg['bucket']}/{key}")
        return key

    def test(self) -> str:
        return self.put("mas-test.txt", b"Mas can write here.\n")
