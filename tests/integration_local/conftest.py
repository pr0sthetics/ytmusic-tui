"""Fixtures da camada L2 (T08 · §10) — rede bloqueada e falsos reais.

A subárvore herda as fixtures globais (banco isolado etc.) e acrescenta:

- bloqueio de rede autouse: nenhum teste desta camada fala com a internet;
- sombreamento do fixture global ``_ffmpeg_on_path`` (aqui o PATH é real);
- estado global do app isolado (``download_dir`` em tmp) para os run_test.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

import ytmusic_tui.api as api_module
import ytmusic_tui.download as download_mod
from tests.integration_local._fakes import install_settings, install_yt_dlp
from ytmusic_tui.config import settings as global_settings
from ytmusic_tui.download.store import DownloadTaskStore
from ytmusic_tui.library.database import DatabaseManager


class NetworkBlockedError(RuntimeError):
    """Tentativa de conexão real na camada L2 — falha de propósito."""


@pytest.fixture(autouse=True)
def _ffmpeg_on_path():
    """Sombreia a fixture global (T06): na L2 o PATH é de verdade.

    ``tests/conftest.py`` monkeypatcha ``_which_ffmpeg`` para a suíte toda
    (independência do host); a L2 precisa do seam REAL: ``shutil.which``
    contra o PATH controlado pelo teste (binário falso executável ou ausência
    total). Auto-prova: se o sombreamento falhasse, o teste "ffmpeg ausente"
    encontraria ``/usr/bin/ffmpeg`` pela fixture global e quebraria.
    """
    yield


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Qualquer conexão real nesta subárvore falha com mensagem clara."""

    def _blocked(*args, **kwargs):
        raise NetworkBlockedError(
            "rede bloqueada na camada L2 (tests/integration_local): "
            "este teste não fala com a internet — use falsos locais"
        )

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture(autouse=True)
def _instant_retry_sleep(monkeypatch):
    """Retry sem espera real na L2 (o sleep é injetável, T10-R3).

    As buscas desta camada falham de propósito; sem isto o backoff do retry
    (0,5s + 1,0s por tentativa extra) atrasaria a suíte sem testar nada.
    """
    monkeypatch.setattr(api_module.ytmusic, "_sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def _l2_isolated_app_state(tmp_path, monkeypatch):
    """Isola estado global que o ``run_test`` toca de verdade.

    A reconciliação do ``on_mount`` varre ``settings.download_dir``: apontar
    para tmp garante que o app em teste nunca lê o disco do usuário (o
    espelho de tarefas já usa o ``db_manager`` isolado do conftest global).
    """
    download_dir = tmp_path / "app-downloads"
    download_dir.mkdir()
    monkeypatch.setattr(global_settings, "download_dir", download_dir)
    # Cache da API no tmp do teste (T10-R4): a busca pode acertar cache entre
    # chamadas — nunca entre testes, e nunca o cache real do usuário.
    monkeypatch.setattr(global_settings, "cache_dir", tmp_path / "api-cache")
    monkeypatch.setattr(api_module.ytmusic, "_cache", None)


@pytest.fixture(autouse=True)
def _reset_api_clients():
    """A14: transporte injetado na L2 não vaza para o próximo teste/módulo."""
    yield
    api_module.ytmusic.reset()


@pytest.fixture
def make_l2_env(tmp_path, monkeypatch):
    """Fábrica de ambientes L2: settings/stub/store + manager reais.

    ``mode=None`` não instala stub de yt-dlp — o teste quer o import real
    (caso "ffmpeg ausente": yt-dlp importável, ffmpeg fora do PATH).
    """
    managers: list[download_mod.DownloadManager] = []
    databases: list[DatabaseManager] = []

    def _make(mode: str | None = "ok") -> SimpleNamespace:
        download_dir = tmp_path / "library"
        download_dir.mkdir(parents=True, exist_ok=True)
        install_settings(monkeypatch, download_dir)
        if mode is not None:
            install_yt_dlp(monkeypatch, mode)
        db_dir = tmp_path / "db"
        db_dir.mkdir(exist_ok=True)
        db = DatabaseManager(db_dir / "tasks.db")
        db.migrate()
        databases.append(db)
        store = DownloadTaskStore(db=db)
        manager = download_mod.DownloadManager(store=store)
        managers.append(manager)
        return SimpleNamespace(manager=manager, store=store, download_dir=download_dir, db=db)

    yield _make
    for manager in managers:
        manager.shutdown()
    for db in databases:
        db.close()
