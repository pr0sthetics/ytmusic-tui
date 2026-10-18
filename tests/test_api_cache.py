"""Testes do cache da API (T10-R4) — relógio fake, sem rede.

O ``ResponseCache`` puro (TTL, versão, corrupção, poda, permissões e
concorrência) e o efeito no ``YTMusicAPI``: segunda busca/álbum não bate no
transporte. O conteúdo é varrido para provar que URL de stream nunca é
cacheada.
"""

import json
import os
import stat
import threading

import pytest

import ytmusic_tui.api as api_module
from ytmusic_tui.api import YTMusicAPI
from ytmusic_tui.api.cache import (
    ENVELOPE_VERSION,
    ResponseCache,
    cache_key,
)
from ytmusic_tui.api.models import AlbumDetail, SearchResult


class _FakeClock:
    """Relógio controlado para TTL determinístico."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def cache(tmp_path, clock) -> ResponseCache:
    return ResponseCache(tmp_path / "api-cache", clock=clock)


# ===== comportamento puro =====


def test_round_trip_dentro_do_ttl(cache):
    payload = {"tracks": [], "albums": []}
    cache.put("search", "k1", payload)

    assert cache.get("search", "k1") == payload
    assert cache.get("search", "inexistente") is None


def test_ttl_da_busca_expira_em_5_min(cache, clock):
    cache.put("search", "k1", {"ok": True})

    clock.advance(299.0)
    assert cache.get("search", "k1") == {"ok": True}

    clock.advance(2.0)  # 301s no total
    assert cache.get("search", "k1") is None
    # Expirou → arquivo removido do disco.
    assert list((cache._root).glob("*.json")) == []


def test_ttl_do_album_dura_24h(cache, clock):
    cache.put("album", "k1", {"ok": True})

    clock.advance(23 * 3600.0)
    assert cache.get("album", "k1") == {"ok": True}

    clock.advance(2 * 3600.0)  # 25h no total
    assert cache.get("album", "k1") is None


def test_envelope_de_versao_diferente_e_ignorado(cache, clock):
    path = cache._root / "search-k1.json"
    cache._root.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": ENVELOPE_VERSION + 1, "cached_at": clock.now, "payload": {}}),
        encoding="utf-8",
    )

    assert cache.get("search", "k1") is None
    assert path.exists()  # ignorado, não destruído (pode ser de outra versão)


def test_json_corrompido_e_removido(cache):
    path = cache._root / "search-k1.json"
    cache._root.mkdir(parents=True, exist_ok=True)
    path.write_text("{{{ não é JSON", encoding="utf-8")

    assert cache.get("search", "k1") is None
    assert not path.exists()


def test_escrita_atomica_e_envelope_valido(cache, clock):
    cache.put("search", "k1", {"tracks": []})

    files = list(cache._root.glob("*"))
    assert [p.name for p in files] == ["search-k1.json"]  # nenhum .tmp- sobrou
    envelope = json.loads(files[0].read_text(encoding="utf-8"))
    assert envelope == {
        "version": ENVELOPE_VERSION,
        "cached_at": clock.now,
        "payload": {"tracks": []},
    }


def test_teto_poda_os_mais_antigos(tmp_path, clock):
    cache = ResponseCache(tmp_path / "api-cache", clock=clock, max_bytes=1500)
    cache.put("search", "antigo", {"blob": "x" * 1000})
    old = cache._root / "search-antigo.json"
    os.utime(old, (clock.now - 100.0, clock.now - 100.0))

    cache.put("search", "novo", {"blob": "y" * 1000})

    assert cache.get("search", "antigo") is None  # podado
    assert cache.get("search", "novo") == {"blob": "y" * 1000}
    assert [p.name for p in cache._root.glob("*.json")] == ["search-novo.json"]


def test_permissoes_0700_no_diretorio_e_0600_no_arquivo(cache):
    """A4: ``cache_dir/api`` nasce 0700; arquivos do mkstemp, 0600."""
    cache.put("search", "k1", {"ok": True})

    assert stat.S_IMODE(cache._root.stat().st_mode) == 0o700
    file_path = cache._root / "search-k1.json"
    assert stat.S_IMODE(file_path.stat().st_mode) == 0o600


def test_falha_de_disco_e_best_effort(tmp_path):
    """Cache quebrado não levanta: put/get viram no-op com aviso no log."""
    blocked = tmp_path / "bloqueado"
    blocked.write_text("sou um arquivo, não um diretório", encoding="utf-8")
    cache = ResponseCache(blocked / "api")

    cache.put("search", "k1", {"ok": True})  # não levanta
    assert cache.get("search", "k1") is None


def test_cache_key_estavel_e_distinta():
    assert cache_key("q", 25, 15) == cache_key("q", 25, 15)
    assert cache_key("q", 25, 15) != cache_key("q", 15, 25)
    assert len(cache_key("q")) == 32


def test_put_concorrente_mantem_arquivo_valido(cache):
    threads = [
        threading.Thread(target=cache.put, args=("search", "mesma", {"i": i})) for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payload = cache.get("search", "mesma")
    assert isinstance(payload, dict) and 0 <= payload["i"] < 8
    files = list(cache._root.glob("*.json"))
    assert [p.name for p in files] == ["search-mesma.json"]  # nenhum tmp sobrou


# ===== efeito no YTMusicAPI (transporte falso) =====


def _fake_client(counters: dict[str, int]):
    """Cliente falso com contadores e dados realistas (URLs de imagem)."""

    class _Fake:
        def __init__(self, **kwargs) -> None:
            pass

        def search(self, query, filter=None, limit=None, ignore_spelling=False):
            if filter == "songs":
                counters["songs"] += 1
                return [
                    {
                        "resultType": "song",
                        "videoId": "v1",
                        "title": "Música",
                        "duration": "1:00",
                        "duration_seconds": 60,
                        "artists": [{"name": "A"}],
                        "album": {"name": "Álbum", "id": "b1"},
                        "thumbnails": [{"url": "https://i.ytimg.com/vi/v1/hq.jpg"}],
                        "isAvailable": True,
                    }
                ]
            if filter == "videos":
                counters["videos"] += 1
                return [
                    {
                        "resultType": "video",
                        "videoId": "vid1",
                        "title": "Clipe",
                        "duration": "2:00",
                        "duration_seconds": 120,
                        "artists": [{"name": "A"}],
                        "thumbnails": [{"url": "https://i.ytimg.com/vi/vid1/hq.jpg"}],
                        "isAvailable": True,
                    }
                ]
            counters["albums"] += 1
            return [
                {
                    "resultType": "album",
                    "browseId": "b1",
                    "title": "Álbum",
                    "type": "Album",
                    "year": "2020",
                    "artists": [{"name": "A"}],
                    "trackCount": 1,
                    "thumbnails": [{"url": "https://i.ytimg.com/vi/b1/hq.jpg"}],
                }
            ]

        def get_album(self, browse_id):
            counters["album_detail"] += 1
            return {
                "browseId": browse_id,
                "title": "Álbum",
                "type": "Album",
                "year": "2020",
                "trackCount": 1,
                "artists": [{"name": "A"}],
                "tracks": [
                    {
                        "videoId": "v1",
                        "title": "Música",
                        "artists": [{"name": "A"}],
                        "album": "Álbum",
                        "duration": "1:00",
                        "duration_seconds": 60,
                        "isAvailable": True,
                    }
                ],
            }

    return _Fake


def _api_with_cache(monkeypatch, tmp_path, clock, counters):
    cache = ResponseCache(tmp_path / "api-cache", clock=clock)
    monkeypatch.setattr(api_module, "YTMusic", _fake_client(counters))
    api = YTMusicAPI(cache=cache, sleep=lambda _seconds: None)
    assert api.initialize() is True
    return api, cache


def test_search_catalog_acerta_cache_na_segunda_chamada(monkeypatch, tmp_path, clock):
    counters = {"songs": 0, "albums": 0, "videos": 0, "album_detail": 0}
    api, _cache = _api_with_cache(monkeypatch, tmp_path, clock, counters)

    first = api.search_catalog("q")
    second = api.search_catalog("q")

    assert second == first
    assert isinstance(second, SearchResult)
    assert second.tracks[0].video_id == "v1"
    assert second.videos[0].video_id == "vid1"
    # rede só uma vez
    assert (counters["songs"], counters["albums"], counters["videos"]) == (1, 1, 1)

    api.search_catalog("outra query")
    # query nova = rede
    assert (counters["songs"], counters["albums"], counters["videos"]) == (2, 2, 2)


def test_get_album_acerta_cache_na_segunda_chamada(monkeypatch, tmp_path, clock):
    counters = {"songs": 0, "albums": 0, "videos": 0, "album_detail": 0}
    api, _cache = _api_with_cache(monkeypatch, tmp_path, clock, counters)

    first = api.get_album("b1")
    second = api.get_album("b1")

    assert second == first
    assert isinstance(second, AlbumDetail)
    assert counters["album_detail"] == 1

    api.get_album("b2")
    assert counters["album_detail"] == 2


def test_ttl_expirado_volta_a_bater_na_rede(monkeypatch, tmp_path, clock):
    counters = {"songs": 0, "albums": 0, "videos": 0, "album_detail": 0}
    api, _cache = _api_with_cache(monkeypatch, tmp_path, clock, counters)

    api.search_catalog("q")
    clock.advance(301.0)
    api.search_catalog("q")

    assert (counters["songs"], counters["albums"], counters["videos"]) == (2, 2, 2)


def test_cache_nunca_guarda_url_de_stream(monkeypatch, tmp_path, clock):
    """O arquivo de cache não pode conter URL de stream — só dados de modelo."""
    counters = {"songs": 0, "albums": 0, "videos": 0, "album_detail": 0}
    api, cache = _api_with_cache(monkeypatch, tmp_path, clock, counters)

    api.search_catalog("q")
    api.get_album("b1")

    files = list(cache._root.glob("*.json"))
    assert files, "esperava arquivos de cache gravados"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "watch?v=" not in text
        assert "googlevideo" not in text
    # Sanidade: o cache TEM dados (thumbnail é dado de modelo, pode ficar).
    combined = "".join(path.read_text(encoding="utf-8") for path in files)
    assert "i.ytimg.com" in combined


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
