from netcoop import __version__
from netcoop.cli import main


def test_package_imports():
    assert __version__ == "0.1.0"


def test_cli_runs():
    assert main([]) == 0
