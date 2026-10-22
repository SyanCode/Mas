"""The logo of `mas --version` and of the installer."""

from typer.testing import CliRunner

from mas import __version__
from mas.banner import ART, render, version_from_source
from mas.cli import app


def test_plain_and_colored():
    plain = render("1.2.3", color=False)
    assert ART.splitlines()[3] in plain and "v1.2.3 · Your server, mapped and deployed." in plain
    assert "\033[38;2;" in render("1.2.3") and render("1.2.3", subtitle="updated").count("updated") == 1


def test_version_stays_plain_for_scripts():
    assert CliRunner().invoke(app, ["--version"]).output.strip() == f"mas {__version__}"
    assert version_from_source() == __version__
