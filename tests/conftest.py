from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_app_data(monkeypatch, tmp_path_factory):
    """Nenhum teste lê ou grava na pasta de dados real (histórico, crates, config)."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("APPDATA", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    return home
