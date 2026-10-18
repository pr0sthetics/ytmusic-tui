"""Testes dos adaptadores cru → modelo interno (T10-R1).

Cobrem a regra ausente × inválido campo a campo, as formas duplas que o
serviço usa de fato (A2: ``artists`` lista OU ``artist`` string; ``album``
dict OU str), a filtragem por ``resultType`` e o round-trip dos payloads
que o cache do T10-R4 vai gravar. Tudo offline, sem transporte.
"""

import json
import re

import pytest

from ytmusic_tui.api.adapt import (
    _ensure_result_list,
    album_detail_from_raw,
    album_from_raw,
    search_result_from_raws,
    track_from_raw,
)
from ytmusic_tui.api.models import Album, AlbumDetail, SearchResult, Track
from ytmusic_tui.errors import InvalidResponseError

# ===== Track =====


def test_track_completo_do_catalogo():
    """Uma faixa completa vira o modelo com todos os campos (thumb = maior)."""
    track = track_from_raw(
        {
            "videoId": "vid1",
            "title": "Música",
            "resultType": "song",
            "artists": [{"name": "A", "id": "a1"}, {"name": "B", "id": "b1"}],
            "duration": "3:20",
            "duration_seconds": 200,
            "album": {"name": "Álbum Y", "id": "MPREb1"},
            "thumbnails": [{"url": "http://x/small.jpg"}, {"url": "http://x/big.jpg"}],
            "isAvailable": True,
        }
    )

    assert track == Track(
        video_id="vid1",
        title="Música",
        artists=("A", "B"),
        duration_text="3:20",
        duration_seconds=200,
        album_name="Álbum Y",
        album_id="MPREb1",
        thumbnail_url="http://x/big.jpg",
        is_available=True,
    )


def test_track_campos_ausentes_sao_explicitos():
    """Ausente vira valor explícito — nada de KeyError nem default mágico."""
    track = track_from_raw({})

    assert track == Track(video_id="", title="")
    assert track.artists == ()
    assert track.duration_seconds is None  # ausente ≠ 0 (regra T10-R1)
    assert track.is_available is True


def test_track_album_dict_ou_str():
    """A2: ``album`` da faixa vem como dict (busca) ou str (get_album)."""
    named = track_from_raw({"videoId": "v", "album": "Só o nome"})
    assert named.album_name == "Só o nome"
    assert named.album_id == ""

    structured = track_from_raw({"videoId": "v", "album": {"name": "N", "id": "I"}})
    assert structured.album_name == "N"
    assert structured.album_id == "I"


def test_track_artists_lista_ou_str():
    """A2: artistas vêm como lista de dicts ou campo singular string."""
    listed = track_from_raw({"videoId": "v", "artists": [{"name": "A"}, {"name": "B"}]})
    assert listed.artists == ("A", "B")

    single = track_from_raw({"videoId": "v", "artist": "Solo"})
    assert single.artists == ("Solo",)

    # Entradas sem nome (ausente explícito) são ignoradas, sem sujar a lista.
    partial = track_from_raw({"videoId": "v", "artists": [{"id": "x"}, {"name": "C"}]})
    assert partial.artists == ("C",)


INVALID_TRACKS: list[tuple[dict, str]] = [
    ({"title": 5}, "campo 'title' esperado str, recebido int"),
    ({"videoId": []}, "campo 'videoId' esperado str, recebido list"),
    ({"artists": "Rock"}, "campo 'artists' esperado lista, recebido str"),
    ({"artists": ["A"]}, "campo 'artists[]' esperado dict, recebido str"),
    ({"artists": [{"name": 5}]}, "campo 'artists[].name' esperado str, recebido int"),
    ({"artist": 5}, "campo 'artist' esperado str, recebido int"),
    ({"duration_seconds": "abc"}, "campo 'duration_seconds' esperado int, recebido str"),
    ({"duration_seconds": True}, "campo 'duration_seconds' esperado int, recebido bool"),
    ({"isAvailable": "sim"}, "campo 'isAvailable' esperado bool, recebido str"),
    ({"album": 5}, "campo 'album' esperado dict ou str, recebido int"),
    ({"album": {"name": 5}}, "campo 'album.name' esperado str, recebido int"),
    ({"album": {"id": 5}}, "campo 'album.id' esperado str, recebido int"),
    ({"thumbnails": "x"}, "campo 'thumbnails' esperado lista, recebido str"),
    ({"thumbnails": [{"url": 5}]}, "campo 'thumbnails[].url' esperado str, recebido int"),
]


@pytest.mark.parametrize(
    "raw, snippet",
    INVALID_TRACKS,
    ids=[snippet.split(" esperado")[0].replace("campo ", "") for _, snippet in INVALID_TRACKS],
)
def test_track_campo_invalido(raw, snippet):
    """Presente-com-tipo-errado é resposta fora do contrato, com o detalhe."""
    with pytest.raises(InvalidResponseError, match=re.escape(snippet)):
        track_from_raw(raw)


# ===== Album e AlbumDetail =====


def test_album_completo_com_coercoes():
    """Ano int vira str e trackCount digitado vira int (sem perder o dado)."""
    album = album_from_raw(
        {
            "browseId": "MPREb1",
            "title": "Álbum",
            "type": "Single",
            "year": 2019,
            "trackCount": "3",
            "artists": [{"name": "A"}],
        }
    )

    assert album == Album(
        browse_id="MPREb1",
        title="Álbum",
        artists=("A",),
        year="2019",
        kind="Single",
        track_count=3,
    )


INVALID_ALBUMS: list[tuple[dict, str]] = [
    ({"year": 20.5}, "campo 'year' esperado str ou int, recebido float"),
    ({"trackCount": True}, "campo 'trackCount' esperado int, recebido bool"),
    ({"trackCount": 2.5}, "campo 'trackCount' esperado int, recebido float"),
]


@pytest.mark.parametrize(
    "raw, snippet",
    INVALID_ALBUMS,
    ids=[snippet.split(" esperado")[0].replace("campo ", "") for _, snippet in INVALID_ALBUMS],
)
def test_album_campo_invalido(raw, snippet):
    with pytest.raises(InvalidResponseError, match=re.escape(snippet)):
        album_from_raw(raw)


def test_album_detail_completo():
    """get_album cru vira AlbumDetail com as faixas tipadas na ordem."""
    detail = album_detail_from_raw(
        {
            "browseId": "MPREb1",
            "title": "Álbum teste",
            "type": "Album",
            "year": "2019",
            "trackCount": 2,
            "artists": [{"name": "Artista X"}],
            "tracks": [
                {"videoId": "t1", "title": "Faixa 1", "album": "Álbum teste"},
                {"videoId": "t2", "title": "Faixa 2", "album": "Álbum teste"},
            ],
        }
    )

    assert isinstance(detail, AlbumDetail)
    assert detail.title == "Álbum teste"
    assert detail.track_count == 2
    assert [t.video_id for t in detail.tracks] == ["t1", "t2"]
    assert detail.tracks[0].album_name == "Álbum teste"  # forma str (A2)


def test_album_detail_sem_tracks_e_explicito():
    """Ausência de faixas vira tupla vazia; trackCount ausente fica None."""
    detail = album_detail_from_raw({"browseId": "b", "title": "Vazio"})

    assert detail.tracks == ()
    assert detail.track_count is None


def test_album_detail_tracks_invalidos():
    with pytest.raises(InvalidResponseError, match="campo 'tracks' esperado lista, recebido str"):
        album_detail_from_raw({"browseId": "b", "title": "x", "tracks": "t1,t2"})

    with pytest.raises(InvalidResponseError, match="campo 'tracks\\[\\]' esperado dict"):
        album_detail_from_raw({"browseId": "b", "title": "x", "tracks": [1]})


# ===== SearchResult =====


def test_search_result_filtra_tipos_e_ids_ausentes():
    """Só entram song/video com videoId e album com browseId (T08 preservado)."""
    result = search_result_from_raws(
        [
            {"resultType": "song", "videoId": "v1", "title": "ok"},
            {"resultType": "video", "videoId": "v2", "title": "vídeo fora"},
            {"resultType": "song", "title": "sem id"},
        ],
        [
            {"resultType": "album", "browseId": "b1", "title": "ok"},
            {"resultType": "artist", "browseId": "b2", "title": "artista fora"},
        ],
        [
            {"resultType": "video", "videoId": "v3", "title": "ok"},
            {"resultType": "song", "videoId": "v4", "title": "canção fora"},
            {"resultType": "video", "title": "sem id"},
        ],
    )

    assert isinstance(result, SearchResult)
    assert [t.video_id for t in result.tracks] == ["v1"]
    assert [a.browse_id for a in result.albums] == ["b1"]
    assert [v.video_id for v in result.videos] == ["v3"]


def test_search_result_id_invalido_em_item_filtrado():
    """videoId presente com tipo errado é fora do contrato (não é 'ausente')."""
    with pytest.raises(InvalidResponseError, match="campo 'videoId' esperado str"):
        search_result_from_raws([{"resultType": "song", "videoId": 5}], [], [])


def test_search_result_valida_formas_do_t08():
    """As mensagens do T08 seguem idênticas (L2 e unit dependem delas)."""
    with pytest.raises(InvalidResponseError, match="esperado lista, recebido dict"):
        search_result_from_raws({"videoId": "x"}, [], [])

    with pytest.raises(InvalidResponseError, match="esperado lista, recebido NoneType"):
        search_result_from_raws([], None, [])

    with pytest.raises(InvalidResponseError, match="esperado lista, recebido NoneType"):
        search_result_from_raws([], [], None)

    with pytest.raises(InvalidResponseError, match="esperado dict, recebido str"):
        _ensure_result_list([{"videoId": "ok"}, "lixo"])


# ===== Payload (formato do cache do T10-R4) =====


def test_payload_round_trip_e_json_safe():
    """to_payload/from_payload fecham o ciclo e o payload passa por JSON."""
    track = Track(
        video_id="v",
        title="T",
        artists=("A", "B"),
        duration_text="1:00",
        duration_seconds=60,
        album_name="Á",
        album_id="I",
        thumbnail_url="u",
        is_available=False,
    )
    album = Album(
        browse_id="b",
        title="Á",
        artists=("A",),
        year="2019",
        kind="EP",
        track_count=2,
        thumbnail_url="u",
    )
    detail = AlbumDetail(
        browse_id="b",
        title="Á",
        artists=("A",),
        year="2019",
        kind="EP",
        track_count=2,
        thumbnail_url="u",
        tracks=(track,),
    )
    result = SearchResult(tracks=(track,), albums=(album,), videos=(track,))

    for model in (track, album, detail, result):
        payload = json.loads(json.dumps(model.to_payload()))  # precisa ser JSON-safe
        assert type(model).from_payload(payload) == model


def test_from_payload_tolera_campos_extras_e_ausentes():
    """O payload é nosso (envelope versionado); a leitura não explode."""
    assert Track.from_payload({"video_id": "v", "extra": 1}) == Track(video_id="v", title="")
    assert AlbumDetail.from_payload(
        {"browse_id": "b", "tracks": [{"video_id": "t1"}, "lixo"]}
    ) == AlbumDetail(browse_id="b", title="", tracks=(Track(video_id="t1", title=""),))
    assert SearchResult.from_payload({"videos": [{"video_id": "vi"}, "lixo"]}) == SearchResult(
        videos=(Track(video_id="vi", title=""),)
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
