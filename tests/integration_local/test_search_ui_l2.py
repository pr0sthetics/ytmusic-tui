"""Busca na UI pela camada L2 (T08 · §10.1) — ``run_test``, sem rede.

O cliente da API é substituído NO TRANSPORTE (``client.search``), não no
``search_catalog``: a validação nova (``InvalidResponseError``) executa de
verdade no e2e — a mensagem clara chega à tela e o app segue responsivo.
"""

import pytest
from textual.widgets import Static, TabbedContent

import ytmusic_tui.api as api_module
from ytmusic_tui.tui.app import YTMusicTUI


class _Transport:
    """Cliente falso no nível do transporte — nenhuma rede envolvida."""

    def __init__(self, search_impl):
        self._search_impl = search_impl

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        return self._search_impl(query, filter)


def _install_transport(monkeypatch, search_impl) -> None:
    """Substitui o cliente do singleton pelo transporte controlado (A14)."""
    api_module.ytmusic._use_client(_Transport(search_impl))


@pytest.mark.asyncio
async def test_malformed_transport_response_shows_error_without_crash(monkeypatch):
    """Formato inesperado do transporte → mensagem clara, UI viva (§10.1)."""

    def _search(query, filter):
        return {"videoId": "x"} if filter == "songs" else []

    _install_transport(monkeypatch, _search)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        await screen.perform_search("coldplay")
        await pilot.pause()

        message = screen.query_one("#search-message", Static)
        assert message.display is True
        rendered = str(message.render())
        assert "Erro na busca" in rendered
        assert "esperado lista, recebido dict" in rendered

        # UI segue responsiva depois do erro: trocar de aba não levanta.
        screen.query_one("#main-tabs", TabbedContent).active = "tab-downloads"
        await pilot.pause()
        assert screen.query_one("#main-tabs", TabbedContent).active == "tab-downloads"


@pytest.mark.asyncio
async def test_search_offline_shows_error_and_ui_stays_responsive(monkeypatch):
    """Sem internet: erro compreensível na busca e UI responsiva (§10.1).

    O transporte levanta um erro realista de resolução de nome — o mesmo
    que a pilha real produziria offline. O bloqueio de rede da subárvore
    cobre o caminho de qualquer tentativa real: a conexão morreria no guard
    antes de sair da máquina.
    """

    def _offline(query, filter):
        raise ConnectionError("Temporary failure in name resolution")

    _install_transport(monkeypatch, _offline)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        await screen.perform_search("coldplay")
        await pilot.pause()

        message = screen.query_one("#search-message", Static)
        assert message.display is True
        rendered = str(message.render())
        assert "Erro na busca" in rendered
        assert "name resolution" in rendered

        # Responsiva: navegação continua funcionando depois do erro.
        tabs = screen.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-downloads"
        await pilot.pause()
        tabs.active = "tab-search"
        await pilot.pause()
        assert tabs.active == "tab-search"
