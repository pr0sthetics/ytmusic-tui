"""Testes do wrapper da API (fronteira tipada do T10-R1).

O transporte é sempre falso (sem rede): prova-se aqui a forma da saída
(modelos internos, nunca dict cru do ytmusicapi), a validação de forma do
T08 (lista/dict, mensagens preservadas), a regra de fronteira — nenhum
módulo acima de ``api/`` importa ytmusicapi (teste de fonte, A5) — e a URL
de watch única no módulo-folha ``urls`` (teste de fonte, T11-R1).
"""

import re
import threading
import time
from pathlib import Path

import pytest

import ytmusic_tui
import ytmusic_tui.api as api_module
from ytmusic_tui.api import SearchResult, YTMusicAPI
from ytmusic_tui.api.models import Album, AlbumDetail, Track
from ytmusic_tui.errors import InvalidResponseError
from ytmusic_tui.urls import watch_url


class _FakeYTMusic:
    """Substituto de YTMusic que não toca a rede."""

    def __init__(self, *args, **kwargs):
        self.searches = []
        self.watch_calls = []

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        self.searches.append(filter)
        result_type = {
            "songs": "song",
            "artists": "artist",
            "albums": "album",
            "playlists": "playlist",
            "videos": "video",
        }.get(filter, "song")
        item = {"resultType": result_type, "title": f"{filter}-{query}"}
        if result_type == "song":
            item["videoId"] = f"vid-{query}"
        elif result_type == "video":
            item["videoId"] = f"vidv-{query}"
        elif result_type == "album":
            item["browseId"] = f"MPREb-{query}"
        return [item]

    def get_album(self, browse_id):
        return {
            "browseId": browse_id,
            "title": "Álbum teste",
            "artists": [{"name": "Artista"}],
            "year": "2020",
            "trackCount": 1,
            "tracks": [
                {"videoId": "t1", "title": "Faixa", "artists": [{"name": "Artista"}]},
            ],
        }

    def get_watch_playlist(self, videoId=None, radio=False, limit=25):
        self.watch_calls.append((videoId, radio, limit))
        return {
            "tracks": [
                {
                    "videoId": "radio-1",
                    "title": "Parecida",
                    "length": "3:00",
                    "artists": [{"name": "Artista"}],
                    "thumbnail": [{"url": "http://x/radio.jpg"}],
                }
            ]
        }


@pytest.fixture
def fake_ytmusic(monkeypatch):
    monkeypatch.setattr(api_module, "YTMusic", _FakeYTMusic)


def test_initialize_anonymous_returns_true(fake_ytmusic):
    """Sem credenciais, o cliente é criado anônimo e inicializado."""
    api = YTMusicAPI()
    assert api.initialize() is True
    assert isinstance(api._require_client(), _FakeYTMusic)

    # reentrância: já inicializado
    assert api.initialize() is True

    api.reset()
    assert api._client is None


def test_client_requires_initialize(monkeypatch):
    """Se a inicialização falhar, acessar o cliente deve levantar erro claro."""
    monkeypatch.setattr(
        api_module,
        "YTMusic",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    api = YTMusicAPI()
    assert api.initialize() is False

    with pytest.raises(RuntimeError):
        api._require_client()


def test_initialize_failure_returns_false(monkeypatch):
    """Se YTMusic() falhar, initialize() deve retornar False, não quebrar."""
    monkeypatch.setattr(
        api_module,
        "YTMusic",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    api = YTMusicAPI()
    assert api.initialize() is False
    assert api._client is None


def test_search_catalog_parallel_songs_videos_albums_typed(fake_ytmusic):
    """search_catalog devolve modelos internos (dict cru não sai da api/)."""
    api = YTMusicAPI()
    assert api.initialize() is True

    result = api.search_catalog("coldplay", songs_limit=5, albums_limit=3)

    assert isinstance(result, SearchResult)
    assert len(result.tracks) == 1
    track = result.tracks[0]
    assert isinstance(track, Track)
    assert track.video_id == "vid-coldplay"
    assert track.title == "songs-coldplay"
    # Ausente é explícito: o fake não manda artistas/duração.
    assert track.artists == ()
    assert track.duration_seconds is None

    assert len(result.albums) == 1
    album = result.albums[0]
    assert isinstance(album, Album)
    assert album.browse_id == "MPREb-coldplay"
    assert album.title == "albums-coldplay"

    # F10: vídeos chegam tipados como Track (tocam/baixam pelo pipeline de áudio).
    assert len(result.videos) == 1
    video = result.videos[0]
    assert isinstance(video, Track)
    assert video.video_id == "vidv-coldplay"
    assert video.title == "videos-coldplay"

    # A14: cada worker de busca tem o próprio cliente; o conjunto de filtros
    # consultados é a união dos clientes das threads.
    searches = sorted(search for client in api._clients for search in client.searches)
    assert searches == ["albums", "songs", "videos"]


def test_get_album_returns_typed_detail(fake_ytmusic):
    """get_album devolve AlbumDetail com faixas tipadas (T10-R1)."""
    api = YTMusicAPI()
    api.initialize()

    details = api.get_album("MPREb-1")

    assert isinstance(details, AlbumDetail)
    assert details.browse_id == "MPREb-1"
    assert details.title == "Álbum teste"
    assert details.track_count == 1
    assert [t.video_id for t in details.tracks] == ["t1"]


def test_get_radio_tracks_typed_and_passes_radio_parameters(fake_ytmusic):
    """P05: a rádio sai tipada e o wrapper pede ``radio=True`` à watch playlist."""
    api = YTMusicAPI()
    assert api.initialize() is True
    client = api._require_client()

    tracks = api.get_radio_tracks("seed", limit=7)

    assert isinstance(tracks, tuple)
    assert len(tracks) == 1
    assert isinstance(tracks[0], Track)
    assert tracks[0].video_id == "radio-1"
    assert tracks[0].duration_seconds == 180
    assert tracks[0].thumbnail_url == "http://x/radio.jpg"
    assert client.watch_calls == [("seed", True, 7)]


def test_get_stream_url_delega_para_o_modulo_folha():
    """Q-A + T11-R1: get_stream_url é o seam público e delega a ``urls``."""
    api = YTMusicAPI()
    assert api.get_stream_url("X") == watch_url("X")
    assert api.get_stream_url("X") == "https://music.youtube.com/watch?v=X"


def test_metodos_sem_consumidor_removidos():
    """Q-A: os 8 métodos mortos saíram da superfície pública (T10-R1)."""
    for name in (
        "search",
        "get_song",
        "get_artist",
        "get_playlist",
        "get_watch_playlist",
        "get_home",
        "get_mood_categories",
        "get_mood_playlists",
    ):
        assert not hasattr(YTMusicAPI, name), name


# ===== validação de forma da busca (T08 · §10.1, movida p/ api/adapt) =====


def _client_factory(songs: object, albums: object, videos: object = ()):
    """Cliente falso que devolve formas controladas por filtro (T08)."""

    class _ShapedClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def search(self, query, filter=None, limit=None, ignore_spelling=False):
            return {"songs": songs, "albums": albums, "videos": videos}[filter]

    return _ShapedClient


def test_search_catalog_rejects_non_list_songs(monkeypatch):
    """Músicas fora do contrato (dict) → InvalidResponseError (T08 · §10.1)."""
    monkeypatch.setattr(api_module, "YTMusic", _client_factory({"videoId": "x"}, []))
    api = YTMusicAPI()
    assert api.initialize() is True

    with pytest.raises(InvalidResponseError, match="esperado lista, recebido dict"):
        api.search_catalog("q")


def test_search_catalog_rejects_non_list_albums(monkeypatch):
    """Álbuns fora do contrato (None) → InvalidResponseError (T08 · §10.1)."""
    monkeypatch.setattr(api_module, "YTMusic", _client_factory([], None))
    api = YTMusicAPI()
    api.initialize()

    with pytest.raises(InvalidResponseError, match="esperado lista, recebido NoneType"):
        api.search_catalog("q")


def test_search_catalog_rejects_non_dict_item(monkeypatch):
    """Item não-dict na lista → InvalidResponseError (T08 · §10.1)."""
    monkeypatch.setattr(api_module, "YTMusic", _client_factory([{"videoId": "ok"}, "lixo"], []))
    api = YTMusicAPI()
    api.initialize()

    with pytest.raises(InvalidResponseError, match="esperado dict, recebido str"):
        api.search_catalog("q")


def test_get_album_rejects_non_dict(monkeypatch):
    """get_album fora do contrato (lista) → InvalidResponseError (T10-R1)."""

    class _BadAlbumClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_album(self, browse_id):
            return [{"videoId": "x"}]

    monkeypatch.setattr(api_module, "YTMusic", _BadAlbumClient)
    api = YTMusicAPI()
    api.initialize()

    with pytest.raises(InvalidResponseError, match="esperado dict, recebido list"):
        api.get_album("MPREb-x")


# ===== fronteira "nada de ytmusicapi acima de api/" (R1; A5) =====

#: Import de ytmusicapi (com subpacote) — NÃO casa "ytmusic_tui" nem o
#: singleton ``ytmusic`` (A5: a regra de fonte não pode pegá-los de tabela).
_YT_MUSICAPI_IMPORT = re.compile(
    r"^\s*(?:import\s+ytmusicapi\b|from\s+ytmusicapi(?:\.|\s|$))", re.MULTILINE
)


def test_fronteira_ytmusicapi_so_no_pacote_api():
    """Só módulos dentro de ``api/`` importam ytmusicapi (R1; A5)."""
    src = Path(ytmusic_tui.__file__).parent
    violations = []
    for path in sorted(src.rglob("*.py")):
        if path.parent == src / "api":
            continue
        if _YT_MUSICAPI_IMPORT.search(path.read_text(encoding="utf-8")):
            violations.append(str(path.relative_to(src)))

    assert violations == [], f"módulos fora de api/ importando ytmusicapi: {violations}"

    # Auto-prova do regex (A5): o singleton do projeto e os imports internos
    # não casam; o import/from de ytmusicapi (com subpacote) casam.
    assert not _YT_MUSICAPI_IMPORT.search("from ytmusic_tui.api import ytmusic\n")
    assert not _YT_MUSICAPI_IMPORT.search("from ytmusic_tui.api.models import Track\n")
    assert not _YT_MUSICAPI_IMPORT.search("import ytmusic_tui\n")
    assert _YT_MUSICAPI_IMPORT.search("from ytmusicapi import YTMusic\n")
    assert _YT_MUSICAPI_IMPORT.search("from ytmusicapi.exceptions import YTMusicError\n")
    assert _YT_MUSICAPI_IMPORT.search("    import ytmusicapi\n")


# ===== URL de watch única no módulo-folha ``urls`` (T11-R1) =====

#: Literal da URL de watch: desde o T11-R1 só pode existir em ``urls.py``.
_WATCH_LITERAL = "https://music.youtube.com/watch?v="


def _watch_literal_offenders(root: Path) -> list[str]:
    """Módulos (fora de ``urls.py``) que ainda montam o literal da URL."""
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "urls.py":
            continue
        if _WATCH_LITERAL in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    return offenders


def test_literal_da_url_de_watch_so_existe_no_modulo_folha(tmp_path):
    """T11-R1: o literal da URL de watch só existe em ``urls.py``.

    Controles +/− do detector: um arquivo plantado com o literal (mesmo em
    f-string) é pego; arquivo limpo e o próprio ``urls.py`` não são; depois,
    a varredura de verdade sobre ``src/`` — f-string montada fora de
    ``urls.py`` faria o teste falhar.
    """
    (tmp_path / "dirty.py").write_text(f'X = f"{_WATCH_LITERAL}{{video_id}}"', encoding="utf-8")
    (tmp_path / "clean.py").write_text('X = "https://example.com"', encoding="utf-8")
    (tmp_path / "urls.py").write_text(f'X = "{_WATCH_LITERAL}"', encoding="utf-8")

    assert _watch_literal_offenders(tmp_path) == ["dirty.py"]

    src = Path(ytmusic_tui.__file__).parent
    assert _watch_literal_offenders(src) == []


def test_busca_concorrente_isola_cliente_por_thread(monkeypatch):
    """A14: nenhum cliente é usado por duas threads ao mesmo tempo.

    O ``ThreadPoolExecutor`` reaproveita threads; cada uma mantém o próprio
    ``YTMusic``/sessão. O fake registra as threads que o usaram e a asserção
    falha se um cliente for compartilhado.
    """

    class _ThreadFake:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003 - fake
            self.users: set[int] = set()
            self._lock = threading.Lock()

        def search(self, query, filter=None, limit=None, ignore_spelling=False):
            with self._lock:
                self.users.add(threading.get_ident())
            time.sleep(0.005)
            if filter == "albums":
                return [{"resultType": "album", "title": "A", "browseId": "b"}]
            if filter == "videos":
                return [{"resultType": "video", "title": "V", "videoId": "v"}]
            return [{"resultType": "song", "title": "S", "videoId": "s"}]

    monkeypatch.setattr(api_module, "YTMusic", _ThreadFake)
    api = YTMusicAPI()

    for index in range(10):
        api.search_catalog(f"consulta-{index}", songs_limit=1, albums_limit=1, videos_limit=1)

    assert len(api._clients) >= 2  # UI + pelo menos um worker
    assert all(len(client.users) == 1 for client in api._clients)


def test_falha_na_busca_nao_espera_as_chamadas_lentas(monkeypatch):
    """A14: a primeira falha propaga sem aguardar as outras três chamadas."""

    class _Fake:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003 - fake
            pass

        def search(self, query, filter=None, limit=None, ignore_spelling=False):
            if filter == "songs":
                raise InvalidResponseError("formato inesperado")
            time.sleep(1.0)
            return []

    monkeypatch.setattr(api_module, "YTMusic", _Fake)
    api = YTMusicAPI()

    started = time.monotonic()
    with pytest.raises(InvalidResponseError):
        api.search_catalog("lenta")
    elapsed = time.monotonic() - started

    assert elapsed < 0.6, f"a falha esperou as chamadas lentas ({elapsed:.2f}s)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
