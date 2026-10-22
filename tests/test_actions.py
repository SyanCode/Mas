"""Action allow-list, log sources and the CLI commands built on them."""

from __future__ import annotations

import pytest
import server
from typer.testing import CliRunner

from mas.actions import ActionError, available_actions, run_action
from mas.cli import app
from mas.host import FixtureHost
from mas.logs import demux_docker, get_logs
from mas.models import Manager, Service


@pytest.fixture
def host(tmp_path):
    return FixtureHost(server.build(tmp_path / "srv"))


@pytest.fixture
def services(host):
    from mas.correlator import scan

    return {s.id: s for s in scan(host).services}


def test_systemd_command(host, services):
    host._commands["systemctl *"] = {"returncode": 0}
    result = run_action(host, services["api.example.com"], "restart")
    assert result.ok and result.command == ["systemctl", "restart", "api.service"]


def test_pm2_runs_as_the_owner(host, services):
    host._commands["runuser -u alice -- * restart web"] = {"returncode": 0}
    result = run_action(host, services["app.example.com"], "restart")
    assert result.ok
    assert result.command[:4] == ["runuser", "-u", "alice", "--"]
    assert result.command[-2:] == ["restart", "web"]


def test_docker_uses_the_api(host, services):
    result = run_action(host, services["grafana.example.com"], "stop")
    assert result.ok
    assert host.executed[-1] == ["docker-api", "POST", f"/containers/{server.GRAFANA_ID}/stop"]


def test_php_fpm_and_static(host, services):
    host._commands["systemctl reload php8.2-fpm.service"] = {"returncode": 0}
    assert run_action(host, services["blog.example.com"], "reload").ok
    with pytest.raises(ActionError):
        run_action(host, services["blog.example.com"], "stop")  # would take every PHP site down
    assert available_actions(services["docs.example.com"]) == ["reload"]


def test_refuses_unknown_and_tampered_services(host, services):
    with pytest.raises(ActionError):
        run_action(host, services["unknown-python3"], "stop")
    evil = Service(id="x", name="x", manager=Manager.SYSTEMD, unit="ssh.service; rm -rf /")
    with pytest.raises(ActionError):
        run_action(host, evil, "restart")
    with pytest.raises(ActionError):
        run_action(host, services["api.example.com"], "kill")


def test_failed_command_is_reported(host, services):
    host._commands["systemctl restart api.service"] = {"returncode": 1, "stderr": "Job failed"}
    result = run_action(host, services["api.example.com"], "restart")
    assert not result.ok and "Job failed" in result.output


def test_logs_sources(host, services, tmp_path):
    host._commands["journalctl -u api.service *"] = {"stdout": None}
    (host.real("/var/log/nginx")).mkdir(parents=True)
    (host.real("/var/log/nginx/access.log")).write_text("\n".join(f"line {i}" for i in range(500)))
    (sources,) = get_logs(host, services["api.example.com"], 50)
    assert sources.name == "journalctl -u api.service"
    static = get_logs(host, services["docs.example.com"], 10)
    assert static[0].lines == [f"line {i}" for i in range(490, 500)]
    assert static[1].error  # error.log does not exist
    assert get_logs(host, services["unknown-python3"])[0].error


def test_demux_docker():
    frame = lambda stream, text: bytes([stream, 0, 0, 0]) + len(text).to_bytes(4, "big") + text
    assert demux_docker(frame(1, b"out\n") + frame(2, b"err\n")) == ["out", "err"]
    assert demux_docker(b"plain tty output\n") == ["plain tty output"]


def test_cli_user_and_history(host, tmp_path):
    runner = CliRunner()
    base = ["--fixture", str(host.fixture_dir), "--db", str(tmp_path / "cli.db")]
    r = runner.invoke(app, [*base, "user", "create", "admin", "--no-2fa"], input="short\nshort\nlong enough pw\nlong enough pw\n")
    assert r.exit_code == 0, r.output
    assert "at least 10" in r.output
    r = runner.invoke(app, [*base, "user", "list"])
    assert "admin" in r.output and "2FA off" in r.output
    r = runner.invoke(app, [*base, "user", "rename", "admin", "Syan"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, [*base, "user", "list"])
    assert r.output.startswith("syan ")  # "admin" after it is the role
    assert runner.invoke(app, [*base, "user", "rename", "syan", "9bad"]).exit_code != 0
    r = runner.invoke(app, [*base, "restart", "api.example.com"], input="n\n")
    assert r.exit_code != 0  # aborted at the confirmation
    r = runner.invoke(app, [*base, "history"], env={"COLUMNS": "200"})
    assert "user-create" in r.output


def test_cli_rejects_non_loopback_bind(host, tmp_path):
    r = CliRunner().invoke(app, ["--fixture", str(host.fixture_dir), "--db", str(tmp_path / "x.db"),
                                 "serve", "--host", "0.0.0.0"])
    assert r.exit_code == 1 and "loopback" in r.output


@pytest.mark.parametrize("error", [PermissionError(13, "Permission denied", "/var/lib/mas/mas.db"),
                                   __import__("sqlite3").OperationalError("unable to open database file")])
def test_permission_errors_ask_for_sudo(monkeypatch, capsys, error):
    from mas import cli

    def boom(**_):
        raise error

    monkeypatch.setattr(cli, "app", boom)
    monkeypatch.setattr(cli, "is_root", lambda: False)
    with pytest.raises(SystemExit) as exit_info:
        cli.run(["apps", "--all"])
    assert exit_info.value.code == 1
    assert "sudo mas apps --all" in capsys.readouterr().err
    monkeypatch.setattr(cli, "is_root", lambda: True)  # root: a real problem, not a missing sudo
    with pytest.raises(type(error)):
        cli.run(["apps"])
