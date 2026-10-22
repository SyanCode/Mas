from __future__ import annotations

from dataclasses import asdict

import pytest

from mas.router import PortMapping, RouterError, restore, switch_to


def mapping(port: int, client: str = "192.168.1.10") -> PortMapping:
    return PortMapping("", port, "TCP", port, client, True, f"service-{port}")


class FakeGateway:
    def __init__(self, mappings: list[PortMapping], fail_target: str | None = None) -> None:
        self.entries = {(m.remote_host, m.external_port, m.protocol): m for m in mappings}
        self.fail_target = fail_target

    def mappings(self) -> list[PortMapping]:
        return list(self.entries.values())

    def mappings_to(self, address: str) -> list[PortMapping]:
        return [m for m in self.mappings() if m.internal_client == address]

    def set_mapping(self, item: PortMapping, target: str, current_address: str | None = None) -> None:
        if target == self.fail_target:
            raise RouterError("simulated router failure")
        key = (item.remote_host, item.external_port, item.protocol)
        self.entries[key] = PortMapping(item.remote_host, item.external_port, item.protocol,
                                        item.internal_port, target, item.enabled,
                                        item.description, item.lease_duration)


def test_switch_saves_snapshot_before_moving_only_matching_rules() -> None:
    old, new = "192.168.1.10", "192.168.1.20"
    gateway = FakeGateway([mapping(80), mapping(443), mapping(2222, "192.168.1.30")])
    saved: list[list[dict]] = []

    snapshot = switch_to(gateway, old, new, before_switch=saved.append)

    assert [m.external_port for m in gateway.mappings_to(new)] == [80, 443]
    assert [m.external_port for m in gateway.mappings_to("192.168.1.30")] == [2222]
    assert snapshot == saved[0] == [asdict(mapping(80)), asdict(mapping(443))]


def test_switch_rolls_back_after_a_later_rule_fails() -> None:
    old, new = "192.168.1.10", "192.168.1.20"
    gateway = FakeGateway([mapping(80), mapping(443)], fail_target=new)

    with pytest.raises(RouterError, match="attempted rollback"):
        switch_to(gateway, old, new)

    assert [m.external_port for m in gateway.mappings_to(old)] == [80, 443]


def test_restore_returns_saved_rules_to_old_address() -> None:
    old, new = "192.168.1.10", "192.168.1.20"
    saved = [asdict(mapping(80)), asdict(mapping(443))]
    gateway = FakeGateway([mapping(80, new), mapping(443, new), mapping(8080, "192.168.1.30")])

    count = restore(gateway, saved, new, old)

    assert count == 2
    assert [m.external_port for m in gateway.mappings_to(old)] == [80, 443]
    assert [m.external_port for m in gateway.mappings_to("192.168.1.30")] == [8080]


def test_restore_refuses_mapping_changed_by_operator() -> None:
    old, new = "192.168.1.10", "192.168.1.20"
    saved = [asdict(mapping(80))]
    gateway = FakeGateway([mapping(80, "192.168.1.99")])

    with pytest.raises(RouterError, match="snapshot kept"):
        restore(gateway, saved, new, old)

    assert gateway.mappings_to("192.168.1.99")
