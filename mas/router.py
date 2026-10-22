"""Standards based LAN router port forwarding (UPnP IGD).

This is the general integration path for ISP boxes that expose UPnP IGD,
including Freebox models with UPnP enabled. It only changes existing rules
whose LAN target exactly matches the old server address.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass


class RouterError(Exception):
    pass


SSDP_ADDRESS = ("239.255.255.250", 1900)
SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")
SSDP_REQUEST = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                "MAN: \"ssdp:discover\"\r\nMX: 2\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n")
SERVICES = ("WANIPConnection:2", "WANIPConnection:1", "WANPPPConnection:1")

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        return None


def _validate_local_http_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username:
        raise RouterError("router URLs must use local HTTP addresses")
    try:
        addresses = {ipaddress.ip_address(item[4][0].split("%", 1)[0])
                     for item in socket.getaddrinfo(parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        raise RouterError("could not resolve the router address") from None
    if not addresses or any((not ip.is_private and ip not in SHARED_ADDRESS_SPACE)
                            or ip.is_loopback or ip.is_link_local for ip in addresses):
        raise RouterError("router URL points outside the private network")


def _open_local(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: float = 8):
    _validate_local_http_url(url)
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    opener = urllib.request.build_opener(_NoRedirect())
    return opener.open(request, timeout=timeout)


@dataclass
class PortMapping:
    remote_host: str
    external_port: int
    protocol: str
    internal_port: int
    internal_client: str
    enabled: bool
    description: str
    lease_duration: int = 0


def discover_gateway(timeout: float = 3.0) -> "UpnpGateway":
    """Find a UPnP IGD on the local network using SSDP discovery."""
    locations = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.settimeout(timeout)
            sock.sendto(SSDP_REQUEST.encode(), SSDP_ADDRESS)
            while True:
                try:
                    response, _ = sock.recvfrom(8192)
                except socket.timeout:
                    break
                for line in response.decode("latin-1", "replace").splitlines():
                    key, sep, value = line.partition(":")
                    if sep and key.lower() == "location":
                        locations.add(value.strip())
    except OSError as exc:
        raise RouterError(f"UPnP discovery failed: {exc}") from None
    for location in sorted(locations):
        try:
            gateway = UpnpGateway.from_description(location)
            return gateway
        except RouterError:
            continue
    raise RouterError("no UPnP IGD router found; enable UPnP on the box or configure port forwarding manually")


class UpnpGateway:
    def __init__(self, control_url: str, service_type: str) -> None:
        self.control_url, self.service_type = control_url, service_type

    @classmethod
    def from_description(cls, location: str) -> "UpnpGateway":
        try:
            _validate_local_http_url(location)
            with _open_local(location, timeout=4) as response:
                root = ET.fromstring(response.read(1024 * 1024))
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError, RouterError):
            raise RouterError("could not read router description") from None
        base = root.findtext("{*}URLBase") or location
        for service in root.findall(".//{*}service"):
            service_type = service.findtext("{*}serviceType") or ""
            if any(service_type.endswith(name) for name in SERVICES):
                control = service.findtext("{*}controlURL")
                if control:
                    control_url = urllib.parse.urljoin(base, control)
                    _validate_local_http_url(control_url)
                    return cls(control_url, service_type)
        raise RouterError("router does not expose WANIPConnection or WANPPPConnection")

    def _action(self, name: str, args: dict[str, str]) -> dict[str, str]:
        soap = "http://schemas.xmlsoap.org/soap/envelope/"
        envelope = ET.Element(f"{{{soap}}}Envelope", {f"{{{soap}}}encodingStyle":
                              "http://schemas.xmlsoap.org/soap/encoding/"})
        body_node = ET.SubElement(envelope, f"{{{soap}}}Body")
        body = ET.SubElement(body_node, f"{{{self.service_type}}}{name}")
        for key, value in args.items():
            ET.SubElement(body, key).text = str(value)
        payload = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
        headers = {"Content-Type": 'text/xml; charset="utf-8"',
                   "SOAPAction": f'"{self.service_type}#{name}"'}
        try:
            with _open_local(self.control_url, data=payload, headers=headers, timeout=8) as response:
                root = ET.fromstring(response.read(256 * 1024))
        except urllib.error.HTTPError as exc:
            try:
                root = ET.fromstring(exc.read(256 * 1024))
                code = root.findtext(".//{*}errorCode")
                desc = root.findtext(".//{*}errorDescription") or exc.reason
            except ET.ParseError:
                code, desc = None, exc.reason
            raise RouterError(f"router {name} failed ({code or exc.code}): {desc}") from None
        except (OSError, ET.ParseError, http.client.HTTPException) as exc:
            raise RouterError(f"router {name} failed: {exc}") from None
        return {child.tag.split("}")[-1]: child.text or "" for child in root.iter() if child is not root}

    def mappings(self) -> list[PortMapping]:
        out = []
        for index in range(513):
            try:
                item = self._action("GetGenericPortMappingEntry", {"NewPortMappingIndex": str(index)})
            except RouterError as exc:
                if "713" in str(exc):  # SpecifiedArrayIndexInvalid: end of table
                    return out
                raise
            try:
                mapping = PortMapping(
                    remote_host=item["NewRemoteHost"], external_port=int(item["NewExternalPort"]),
                    protocol=item["NewProtocol"].upper(), internal_port=int(item["NewInternalPort"]),
                    internal_client=item["NewInternalClient"], enabled=item["NewEnabled"].lower() == "1",
                    description=item["NewPortMappingDescription"],
                    lease_duration=int(item.get("NewLeaseDuration", "0")),
                )
            except (KeyError, ValueError):
                continue
            out.append(mapping)
        raise RouterError("router returned more than 512 mappings; refusing an incomplete switch")

    def mappings_to(self, address: str) -> list[PortMapping]:
        return [mapping for mapping in self.mappings() if mapping.internal_client == address]

    def external_address(self) -> str:
        return self._action("GetExternalIPAddress", {}).get("NewExternalIPAddress", "")

    def set_mapping(self, mapping: PortMapping, target: str, current_address: str | None = None) -> None:
        args = {
            "NewRemoteHost": mapping.remote_host,
            "NewExternalPort": str(mapping.external_port),
            "NewProtocol": mapping.protocol,
            "NewInternalPort": str(mapping.internal_port),
            "NewInternalClient": target,
            "NewEnabled": "1" if mapping.enabled else "0",
            "NewPortMappingDescription": mapping.description[:64],
            "NewLeaseDuration": str(mapping.lease_duration),
        }
        # UPnP IGD rejects AddPortMapping when the same WAN tuple belongs to a
        # different LAN client (error 718). Replace the existing rule, and put
        # it back if the replacement rule is refused.
        self._action("DeletePortMapping", {key: args[key] for key in
                                            ("NewRemoteHost", "NewExternalPort", "NewProtocol")})
        try:
            self._action("AddPortMapping", args)
        except RouterError as exc:
            args["NewInternalClient"] = current_address or mapping.internal_client
            try:
                self._action("AddPortMapping", args)
            except RouterError as rollback:
                raise RouterError(f"new mapping failed ({exc}); original rule rollback also failed ({rollback})")
            raise


def switch_to(gateway: UpnpGateway, old_address: str, new_address: str,
              before_switch: Callable[[list[dict]], None] | None = None) -> list[dict]:
    """Move all existing matching UPnP mappings and return a rollback snapshot."""
    old_address, new_address = old_address.strip(), new_address.strip()
    if not old_address or not new_address or old_address == new_address:
        raise RouterError("source and replacement LAN addresses must be different and non-empty")
    mappings = gateway.mappings_to(old_address)
    snapshot = [asdict(mapping) for mapping in mappings]
    if before_switch:
        before_switch(snapshot)
    changed: list[PortMapping] = []
    try:
        for mapping in mappings:
            gateway.set_mapping(mapping, new_address)
            changed.append(mapping)
    except RouterError as exc:
        # Restore any earlier rules so a partial switch does not strand traffic.
        for mapping in reversed(changed):
            try:
                gateway.set_mapping(mapping, old_address, current_address=new_address)
            except RouterError:
                pass
        raise RouterError(f"router switch stopped; attempted rollback: {exc}") from None
    return snapshot


def restore(gateway: UpnpGateway, snapshot: list[dict], replacement_address: str, old_address: str) -> int:
    """Restore only snapshot rules still pointing to the replacement address."""
    current = {(m.remote_host, m.external_port, m.protocol): m for m in gateway.mappings()}
    to_restore: list[PortMapping] = []
    already_restored = 0
    for raw in snapshot:
        mapping = PortMapping(**raw)
        key = (mapping.remote_host, mapping.external_port, mapping.protocol)
        live = current.get(key)
        if live is None:
            raise RouterError(f"saved mapping {mapping.protocol}/{mapping.external_port} is missing; snapshot kept")
        if live.internal_client == old_address:
            already_restored += 1
        elif live.internal_client == replacement_address:
            to_restore.append(mapping)
        else:
            raise RouterError(f"saved mapping {mapping.protocol}/{mapping.external_port} now targets "
                              f"{live.internal_client}; snapshot kept")
    restored = 0
    changed: list[PortMapping] = []
    try:
        for mapping in to_restore:
            gateway.set_mapping(mapping, old_address, current_address=replacement_address)
            changed.append(mapping)
            restored += 1
    except RouterError as exc:
        for mapping in reversed(changed):
            try:
                gateway.set_mapping(mapping, replacement_address, current_address=old_address)
            except RouterError:
                pass
        raise RouterError(f"restore stopped; attempted rollback: {exc}") from None
    return restored + already_restored
