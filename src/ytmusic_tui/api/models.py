"""Modelos internos da API (T10-R1).

Nenhuma camada acima de ``api/`` vê objeto do ytmusicapi: as respostas cruas
são adaptadas (``api.adapt``) para estes modelos antes de cruzar a fronteira.

Regra ausente × inválido (§11-R1): campo ausente/None vira valor explícito
(``""``/``()``/``None``/default), nunca "magia"; campo presente com tipo
errado é ``InvalidResponseError`` — quem valida é o adaptador.

Os modelos são imutáveis e serializáveis (``to_payload``/``from_payload``):
o cache do T10-R4 grava o payload NESTE formato — o arquivo de cache nunca
guarda a resposta crua do serviço.
"""

from dataclasses import dataclass
from typing import Any

__all__ = ["Album", "AlbumDetail", "SearchResult", "Track"]

#: Payload JSON-safe de um modelo (formato do cache do T10-R4).
JsonPayload = dict[str, Any]


def _payload_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _payload_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _payload_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


@dataclass(frozen=True, slots=True)
class Track:
    """Faixa: resultado de busca ou item de álbum."""

    video_id: str
    title: str
    artists: tuple[str, ...] = ()
    duration_text: str = ""  # como o catálogo exibe ("3:45")
    duration_seconds: int | None = None  # ausente ≠ 0 (regra T10-R1)
    album_name: str = ""
    album_id: str = ""
    thumbnail_url: str = ""
    is_available: bool = True

    def to_payload(self) -> JsonPayload:
        """Forma serializável (JSON-safe) para o cache."""
        return {
            "video_id": self.video_id,
            "title": self.title,
            "artists": list(self.artists),
            "duration_text": self.duration_text,
            "duration_seconds": self.duration_seconds,
            "album_name": self.album_name,
            "album_id": self.album_id,
            "thumbnail_url": self.thumbnail_url,
            "is_available": self.is_available,
        }

    @classmethod
    def from_payload(cls, payload: JsonPayload) -> "Track":
        """Reconstrói o modelo do payload (cache); tolerante a campos a mais."""
        artists = payload.get("artists")
        return cls(
            video_id=_payload_str(payload.get("video_id")),
            title=_payload_str(payload.get("title")),
            artists=tuple(name for name in (artists if isinstance(artists, list) else [])),
            duration_text=_payload_str(payload.get("duration_text")),
            duration_seconds=_payload_int(payload.get("duration_seconds")),
            album_name=_payload_str(payload.get("album_name")),
            album_id=_payload_str(payload.get("album_id")),
            thumbnail_url=_payload_str(payload.get("thumbnail_url")),
            is_available=_payload_bool(payload.get("is_available"), default=True),
        )


@dataclass(frozen=True, slots=True)
class Album:
    """Álbum: item de busca (sem faixas)."""

    browse_id: str
    title: str
    artists: tuple[str, ...] = ()
    year: str = ""
    kind: str = ""  # como o serviço rotula ("Album"/"Single"/"EP")
    track_count: int | None = None
    thumbnail_url: str = ""

    def to_payload(self) -> JsonPayload:
        """Forma serializável (JSON-safe) para o cache."""
        return {
            "browse_id": self.browse_id,
            "title": self.title,
            "artists": list(self.artists),
            "year": self.year,
            "kind": self.kind,
            "track_count": self.track_count,
            "thumbnail_url": self.thumbnail_url,
        }

    @classmethod
    def from_payload(cls, payload: JsonPayload) -> "Album":
        """Reconstrói o modelo do payload (cache); tolerante a campos a mais."""
        artists = payload.get("artists")
        return cls(
            browse_id=_payload_str(payload.get("browse_id")),
            title=_payload_str(payload.get("title")),
            artists=tuple(name for name in (artists if isinstance(artists, list) else [])),
            year=_payload_str(payload.get("year")),
            kind=_payload_str(payload.get("kind")),
            track_count=_payload_int(payload.get("track_count")),
            thumbnail_url=_payload_str(payload.get("thumbnail_url")),
        )


@dataclass(frozen=True, slots=True)
class AlbumDetail(Album):
    """Álbum aberto (``get_album``): o item de busca + as faixas tocáveis."""

    tracks: tuple[Track, ...] = ()

    def to_payload(self) -> JsonPayload:
        """Forma serializável (JSON-safe) para o cache."""
        # Chamada direta à base: ``super()`` sem argumentos quebra com
        # ``slots=True`` (o decorator recria a classe e o ``__class__`` cell
        # do método continua apontando para a versão antiga).
        payload = Album.to_payload(self)
        payload["tracks"] = [track.to_payload() for track in self.tracks]
        return payload

    @classmethod
    def from_payload(cls, payload: JsonPayload) -> "AlbumDetail":
        """Reconstrói o modelo do payload (cache); tolerante a campos a mais."""
        base = Album.from_payload(payload)
        raw_tracks = payload.get("tracks")
        tracks = tuple(
            Track.from_payload(item)
            for item in (raw_tracks if isinstance(raw_tracks, list) else [])
            if isinstance(item, dict)
        )
        return cls(
            browse_id=base.browse_id,
            title=base.title,
            artists=base.artists,
            year=base.year,
            kind=base.kind,
            track_count=base.track_count,
            thumbnail_url=base.thumbnail_url,
            tracks=tracks,
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Resultado padronizado de ``search_catalog`` (T10-R1).

    ``videos`` (F10) reutiliza ``Track``: o clipe toca e baixa pelo mesmo
    pipeline de áudio das músicas (``source_url`` = watch do videoId).
    """

    tracks: tuple[Track, ...] = ()
    albums: tuple[Album, ...] = ()
    videos: tuple[Track, ...] = ()

    def to_payload(self) -> JsonPayload:
        """Forma serializável (JSON-safe) para o cache."""
        return {
            "tracks": [track.to_payload() for track in self.tracks],
            "albums": [album.to_payload() for album in self.albums],
            "videos": [video.to_payload() for video in self.videos],
        }

    @classmethod
    def from_payload(cls, payload: JsonPayload) -> "SearchResult":
        """Reconstrói o modelo do payload (cache); tolerante a campos a mais."""
        raw_tracks = payload.get("tracks")
        raw_albums = payload.get("albums")
        raw_videos = payload.get("videos")
        return cls(
            tracks=tuple(
                Track.from_payload(item)
                for item in (raw_tracks if isinstance(raw_tracks, list) else [])
                if isinstance(item, dict)
            ),
            albums=tuple(
                Album.from_payload(item)
                for item in (raw_albums if isinstance(raw_albums, list) else [])
                if isinstance(item, dict)
            ),
            videos=tuple(
                Track.from_payload(item)
                for item in (raw_videos if isinstance(raw_videos, list) else [])
                if isinstance(item, dict)
            ),
        )
