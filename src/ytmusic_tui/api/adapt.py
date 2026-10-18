"""Adaptadores: resposta crua do ytmusicapi → modelos internos (T10-R1).

Regra ausente × inválido (§11-R1): campo ausente/None vira valor explícito
do modelo; campo presente com tipo errado levanta ``InvalidResponseError``
com "esperado × recebido" — o formato de mensagem do T08 é preservado (a
validação lista/dict mora aqui desde o T10).

Formas duplas aceitas (A2 — como o serviço responde de fato):

- artistas: ``artists: [{"name": ...}]`` (busca) OU ``artist: "Nome"``
  (alguns detalhes de álbum usam o campo singular string);
- álbum da faixa: ``album: {"name": ..., "id": ...}`` OU ``album: "Nome"``
  (faixas de ``get_album`` trazem o nome como string).

Qualquer outra forma nesses campos é resposta fora do contrato. Os
adaptadores não têm I/O: o teste cobre cada regra offline.
"""

from typing import Any

from ytmusic_tui.api.models import Album, AlbumDetail, SearchResult, Track
from ytmusic_tui.errors import InvalidResponseError

__all__ = [
    "album_detail_from_raw",
    "album_from_raw",
    "search_result_from_raws",
    "track_from_raw",
]


def _invalid(field: str, expected: str, value: Any) -> InvalidResponseError:
    """Erro padronizado de campo fora do contrato (ausente não cai aqui)."""
    return InvalidResponseError(
        f"resposta inesperada da API: campo {field!r} esperado {expected}, "
        f"recebido {type(value).__name__}"
    )


def _ensure_result_list(raw: Any) -> list[dict[str, Any]]:
    """Valida a forma da resposta de busca (T08 · §10.1; movido no T10).

    ``ytmusicapi`` devolve listas de dicts; qualquer outra forma é resposta
    fora do contrato — erro tipado com "esperado × recebido", em vez do
    ``AttributeError`` genérico que a lista malformada causava antes (a UI
    mostra ``str(e)``; nenhuma mudança lá).
    """
    if not isinstance(raw, list):
        raise InvalidResponseError(
            f"resposta inesperada da API: esperado lista, recebido {type(raw).__name__}"
        )
    for item in raw:
        if not isinstance(item, dict):
            raise InvalidResponseError(
                f"resposta inesperada da API: esperado dict, recebido {type(item).__name__}"
            )
    return raw


def _str_field(raw: dict[str, Any], key: str, default: str = "") -> str:
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise _invalid(key, "str", value)
    return value


def _int_field(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool):  # bool é int em Python: barra antes do int.
        raise _invalid(key, "int", value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise _invalid(key, "int", value)


def _bool_field(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _invalid(key, "bool", value)
    return value


def _year_field(raw: dict[str, Any]) -> str:
    value = raw.get("year")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise _invalid("year", "str ou int", value)


def _artists_field(raw: dict[str, Any]) -> tuple[str, ...]:
    """Nomes de artistas: ``artists: [{name}]`` OU ``artist: str`` (A2)."""
    value = raw.get("artists")
    if value is None:
        single = raw.get("artist")
        if single is None:
            return ()
        if not isinstance(single, str):
            raise _invalid("artist", "str", single)
        return (single,) if single else ()
    if not isinstance(value, list):
        raise _invalid("artists", "lista", value)
    names: list[str] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise _invalid("artists[]", "dict", entry)
        name = entry.get("name")
        if name is None:
            continue
        if not isinstance(name, str):
            raise _invalid("artists[].name", "str", name)
        if name:
            names.append(name)
    return tuple(names)


def _album_fields(raw: dict[str, Any]) -> tuple[str, str]:
    """``album`` da faixa: dict (``name``/``id``) OU str com o nome (A2)."""
    value = raw.get("album")
    if value is None:
        return "", ""
    if isinstance(value, str):
        return value, ""
    if isinstance(value, dict):
        name = value.get("name")
        album_id = value.get("id")
        if name is not None and not isinstance(name, str):
            raise _invalid("album.name", "str", name)
        if album_id is not None and not isinstance(album_id, str):
            raise _invalid("album.id", "str", album_id)
        return name or "", album_id or ""
    raise _invalid("album", "dict ou str", value)


def _thumbnail_field(raw: dict[str, Any]) -> str:
    """Maior thumbnail (última da lista), como o playback já usava."""
    value = raw.get("thumbnails")
    if value is None:
        return ""
    if not isinstance(value, list):
        raise _invalid("thumbnails", "lista", value)
    for entry in reversed(value):
        if not isinstance(entry, dict):
            raise _invalid("thumbnails[]", "dict", entry)
        url = entry.get("url")
        if url is None:
            continue
        if not isinstance(url, str):
            raise _invalid("thumbnails[].url", "str", url)
        if url:
            return url
    return ""


def track_from_raw(raw: dict[str, Any]) -> Track:
    """Converte uma faixa crua (busca ou álbum) no modelo interno."""
    album_name, album_id = _album_fields(raw)
    return Track(
        video_id=_str_field(raw, "videoId"),
        title=_str_field(raw, "title"),
        artists=_artists_field(raw),
        duration_text=_str_field(raw, "duration"),
        duration_seconds=_int_field(raw, "duration_seconds"),
        album_name=album_name,
        album_id=album_id,
        thumbnail_url=_thumbnail_field(raw),
        is_available=_bool_field(raw, "isAvailable", default=True),
    )


def album_from_raw(raw: dict[str, Any]) -> Album:
    """Converte um item de busca de álbum no modelo interno."""
    return Album(
        browse_id=_str_field(raw, "browseId"),
        title=_str_field(raw, "title"),
        artists=_artists_field(raw),
        year=_year_field(raw),
        kind=_str_field(raw, "type"),
        track_count=_int_field(raw, "trackCount"),
        thumbnail_url=_thumbnail_field(raw),
    )


def album_detail_from_raw(raw: dict[str, Any]) -> AlbumDetail:
    """Converte o ``get_album`` cru em ``AlbumDetail`` (item + faixas)."""
    base = album_from_raw(raw)
    raw_tracks = raw.get("tracks")
    if raw_tracks is None:
        raw_tracks = []
    if not isinstance(raw_tracks, list):
        raise _invalid("tracks", "lista", raw_tracks)
    tracks: list[Track] = []
    for item in raw_tracks:
        if not isinstance(item, dict):
            raise _invalid("tracks[]", "dict", item)
        tracks.append(track_from_raw(item))
    return AlbumDetail(
        browse_id=base.browse_id,
        title=base.title,
        artists=base.artists,
        year=base.year,
        kind=base.kind,
        track_count=base.track_count,
        thumbnail_url=base.thumbnail_url,
        tracks=tuple(tracks),
    )


def search_result_from_raws(songs_raw: Any, albums_raw: Any, videos_raw: Any) -> SearchResult:
    """Valida (T08), filtra por tipo e adapta as três listas da busca (F10)."""
    songs = _ensure_result_list(songs_raw)
    albums = _ensure_result_list(albums_raw)
    videos = _ensure_result_list(videos_raw)
    tracks = tuple(
        track_from_raw(song)
        for song in songs
        if song.get("resultType") == "song" and song.get("videoId")
    )
    album_items = tuple(
        album_from_raw(album)
        for album in albums
        if album.get("resultType") == "album" and album.get("browseId")
    )
    video_items = tuple(
        track_from_raw(video)
        for video in videos
        if video.get("resultType") == "video" and video.get("videoId")
    )
    return SearchResult(tracks=tracks, albums=album_items, videos=video_items)
