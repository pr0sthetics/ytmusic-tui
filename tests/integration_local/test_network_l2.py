"""Resiliência de rede na busca pela camada L2 (T10-R3) — sem rede real.

O transporte é falso e controlado: cada teste diz qual exceção crua o
``search`` levanta (as mesmas do ``requests``/``ytmusicapi``) e a UI prova o
efeito final — mensagem classificada na tela, retry com teto e painel vivo.
O sleep do retry é neutralizado pela fixture autouse do conftest da camada
(o backoff é testado em unidade, com relógio fake, no L1).
"""

import pytest
import requests
from textual.widgets import Static, TabbedContent
from ytmusicapi.exceptions import YTMusicServerError

import ytmusic_tui.api as api_module
from ytmusic_tui.tui.app import YTMusicTUI
from ytmusic_tui.tui.widgets import AlbumListView, SongListView


class _Transport:
    """Cliente falso no nível do transporte — nenhuma rede envolvida."""

    def __init__(self, songs_impl, albums_impl=None):
        self._songs_impl = songs_impl
        self._albums_impl = albums_impl if albums_impl is not None else (lambda: [])
        self.song_calls = 0
        self.album_calls = 0

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        if filter == "songs":
            self.song_calls += 1
            return self._songs_impl()
        self.album_calls += 1
        return self._albums_impl()


def _install_transport(monkeypatch, transport) -> None:
    """Substitui o cliente do singleton pelo transporte controlado (A14)."""
    api_module.ytmusic._use_client(transport)


@pytest.mark.asyncio
async def test_falha_de_rede_retenta_ate_o_teto_e_avisa(monkeypatch):
    """ConnectionError → 3 tentativas → mensagem de rede com contexto na tela."""

    def _songs():
        raise requests.exceptions.ConnectionError("caiu a conexão")

    transport = _Transport(_songs)
    _install_transport(monkeypatch, transport)

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
        assert "falha de rede" in rendered
        assert 'busca "coldplay"' in rendered
        assert transport.song_calls == 3  # teto da política (T10-R3)

        # UI segue responsiva depois da desistência.
        tabs = screen.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-downloads"
        await pilot.pause()
        assert tabs.active == "tab-downloads"


@pytest.mark.asyncio
async def test_timeout_de_leitura_vira_erro_compreensivel(monkeypatch):
    """ReadTimeout → mensagem específica de tempo esgotado (não "caiu a rede")."""

    def _songs():
        raise requests.exceptions.ReadTimeout("servidor não respondeu")

    transport = _Transport(_songs)
    _install_transport(monkeypatch, transport)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        await screen.perform_search("coldplay")
        await pilot.pause()

        rendered = str(screen.query_one("#search-message", Static).render())
        assert "Erro na busca" in rendered
        assert "tempo esgotado (leitura)" in rendered
        assert transport.song_calls == 3


@pytest.mark.asyncio
async def test_rate_limit_429_re_tenta_e_avisa(monkeypatch):
    """HTTP 429 é transitório (re-tenta) e a mensagem fala em limite."""

    def _songs():
        raise YTMusicServerError("Server returned HTTP 429: Too Many Requests.\ncalma lá")

    transport = _Transport(_songs)
    _install_transport(monkeypatch, transport)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        await screen.perform_search("coldplay")
        await pilot.pause()

        rendered = str(screen.query_one("#search-message", Static).render())
        assert "limite de requisições" in rendered
        assert transport.song_calls == 3


@pytest.mark.asyncio
async def test_recusa_definitiva_nao_re_tenta(monkeypatch):
    """HTTP 403 não melhora com insistência: 1 tentativa e mensagem clara."""

    def _songs():
        raise YTMusicServerError("Server returned HTTP 403: Forbidden.")

    transport = _Transport(_songs)
    _install_transport(monkeypatch, transport)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        await screen.perform_search("coldplay")
        await pilot.pause()

        rendered = str(screen.query_one("#search-message", Static).render())
        assert "recusou a requisição" in rendered
        assert transport.song_calls == 1  # não é retryable


@pytest.mark.asyncio
async def test_transitorio_recupera_na_segunda_tentativa(monkeypatch):
    """Uma falha transitória não chega ao usuário: a 2ª tentativa entrega."""
    state = {"fail": True}

    def _songs():
        if state["fail"]:
            state["fail"] = False
            raise requests.exceptions.ConnectionError("quase caiu")
        return [{"resultType": "song", "videoId": "v1", "title": "Música ok"}]

    def _albums():
        return [{"resultType": "album", "browseId": "b1", "title": "Álbum ok"}]

    transport = _Transport(_songs, _albums)
    _install_transport(monkeypatch, transport)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        await screen.perform_search("coldplay")
        await pilot.pause()

        # Resultado renderizado de verdade (modelos internos do T10-R1).
        songs = screen.query_one("#search-songs", SongListView)
        albums = screen.query_one("#search-albums", AlbumListView)
        assert [song.video_id for song in songs.songs] == ["v1"]
        assert [album.browse_id for album in albums.albums] == ["b1"]
        assert transport.song_calls == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
