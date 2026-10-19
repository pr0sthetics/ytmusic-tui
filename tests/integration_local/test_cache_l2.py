"""Cache da API pela camada L2 (T10-R4) — sem rede.

Efeito final na UI: busca repetida não bate no transporte e o resultado
cacheado renderiza igual; query nova volta à rede. O cache do singleton cai
no tmp do teste (fixture da camada) — nada toca o cache real do usuário.
"""

import pytest

import ytmusic_tui.api as api_module
from ytmusic_tui.tui.app import YTMusicTUI
from ytmusic_tui.tui.widgets import AlbumListView, SongListView


class _CountingTransport:
    """Cliente falso no transporte, contando as idas à \"rede\"."""

    def __init__(self) -> None:
        self.song_calls = 0
        self.album_calls = 0
        self.videos_calls = 0

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        if filter == "songs":
            self.song_calls += 1
            return [{"resultType": "song", "videoId": "v1", "title": "Música"}]
        if filter == "videos":
            self.videos_calls += 1
            return [{"resultType": "video", "videoId": "vid1", "title": "Clipe"}]
        self.album_calls += 1
        return [{"resultType": "album", "browseId": "b1", "title": "Álbum"}]


def _install_transport(monkeypatch, transport) -> None:
    api_module.ytmusic._use_client(transport)


@pytest.mark.asyncio
async def test_busca_repetida_usa_cache_e_nao_bate_na_rede(monkeypatch):
    transport = _CountingTransport()
    _install_transport(monkeypatch, transport)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        await screen.perform_search("coldplay")
        await pilot.pause()
        songs = screen.query_one("#search-songs", SongListView)
        albums = screen.query_one("#search-albums", AlbumListView)
        videos = screen.query_one("#search-videos", SongListView)
        assert [song.video_id for song in songs.songs] == ["v1"]
        assert [album.browse_id for album in albums.albums] == ["b1"]
        assert [video.video_id for video in videos.songs] == ["vid1"]
        assert (transport.song_calls, transport.album_calls, transport.videos_calls) == (1, 1, 1)

        # Mesma busca de novo: veio do cache, transporte intocado.
        await screen.perform_search("coldplay")
        await pilot.pause()
        songs = screen.query_one("#search-songs", SongListView)
        assert [song.video_id for song in songs.songs] == ["v1"]
        assert (transport.song_calls, transport.album_calls, transport.videos_calls) == (1, 1, 1)

        # Query diferente não acerta o cache da anterior.
        await screen.perform_search("radiohead")
        await pilot.pause()
        assert (transport.song_calls, transport.album_calls, transport.videos_calls) == (2, 2, 2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
