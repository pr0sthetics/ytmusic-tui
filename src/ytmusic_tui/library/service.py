"""Serviço de alto nível para operações da biblioteca local."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from ytmusic_tui.library.database import db_manager
from ytmusic_tui.library.models import (
    Album,
    AlbumArtist,
    Artist,
    PlaybackHistory,
    Playlist,
    PlaylistTrack,
    Track,
    TrackArtist,
    TrackSource,
    UserSettings,
    utcnow,
)


@dataclass(frozen=True)
class AlbumPlaylistResult:
    """Contagens do "álbum → playlist" (T17.1/R1/R5).

    ``album_total`` é o ``Album.track_count`` quando conhecido (0/None =
    desconhecido) — a UI usa para calcular honestamente quantas faixas do
    álbum ficaram fora da biblioteca ("baixe para incluí-las").
    """

    added: int
    already: int
    album_total: int | None = None


@dataclass(frozen=True)
class LibraryAlbum:
    """Álbum da biblioteca com o retrato exibido na aba (T17.1/R6/R7).

    ``track_count``/``missing_count`` contam as faixas **na biblioteca** (não
    o total do álbum na loja) — é o que existe agora, com o aviso "⚠ N
    ausentes" para o que a última reconciliação não achou no disco.
    """

    album: Album
    track_count: int
    missing_count: int

    @property
    def primary_artist_name(self) -> str:
        """Artista principal (primeiro marcado; senão o primeiro da lista)."""
        for link in self.album.artists:
            if link.is_primary:
                return str(link.artist.name)
        if self.album.artists:
            return str(self.album.artists[0].artist.name)
        return ""


def _album_track_order() -> list[Any]:
    """Ordenação fiel de faixa de álbum (T17.1/R1/R6).

    Com ``disc_number``/``track_number`` gravados (R2), a faixa ordena pelo
    número — o booleano ``track_number IS NULL`` joga as numeradas para a
    frente (0 < 1 no SQLite) e as antigas sem número caem para
    ``added_at``/``id``, sempre com ordem determinística.
    """
    return [
        Track.track_number.is_(None),
        Track.disc_number,
        Track.track_number,
        Track.added_at,
        Track.id,
    ]


class LibraryService:
    """Serviço para gerenciar biblioteca local de músicas."""

    def __init__(self) -> None:
        self.db = db_manager

    # ===== Artistas =====

    def get_or_create_artist(
        self,
        session: Session,
        source_id: str,
        name: str,
        source: TrackSource = TrackSource.YOUTUBE_MUSIC,
        thumbnail_url: str = "",
    ) -> Artist:
        """Busca ou cria artista."""
        artist = session.execute(
            select(Artist).where(Artist.source_id == source_id, Artist.source == source)
        ).scalar_one_or_none()

        if not artist:
            artist = Artist(
                source_id=source_id,
                name=name,
                source=source,
                thumbnail_url=thumbnail_url,
            )
            session.add(artist)
            session.flush()
        return artist

    def search_artists(self, query: str, limit: int = 50) -> list[Artist]:
        """Busca artistas por nome."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Artist)
                    .where(Artist.name.ilike(f"%{query}%"))
                    .order_by(Artist.name)
                    .limit(limit)
                )
                .scalars()
                .all()
            )

    def get_artist(self, artist_id: int) -> Artist | None:
        """Busca artista por ID local."""
        with self.db.session() as session:
            return session.get(Artist, artist_id)

    def get_artist_with_albums(self, artist_id: int) -> Artist | None:
        """Busca artista com álbuns carregados."""
        with self.db.session() as session:
            return session.execute(
                select(Artist)
                .options(selectinload(Artist.albums).selectinload(AlbumArtist.album))
                .where(Artist.id == artist_id)
            ).scalar_one_or_none()

    # ===== Álbuns =====

    def get_or_create_album(
        self,
        session: Session,
        source_id: str,
        title: str,
        source: TrackSource = TrackSource.YOUTUBE_MUSIC,
        **kwargs: Any,
    ) -> Album:
        """Busca ou cria álbum."""
        album = session.execute(
            select(Album).where(Album.source_id == source_id, Album.source == source)
        ).scalar_one_or_none()

        if not album:
            album = Album(source_id=source_id, title=title, source=source, **kwargs)
            session.add(album)
            session.flush()
        else:
            # Atualizar campos se fornecidos
            for key, value in kwargs.items():
                if value and hasattr(album, key):
                    setattr(album, key, value)
        return album

    def link_album_artist(
        self, session: Session, album: Album, artist: Artist, is_primary: bool = True
    ) -> None:
        """Associa artista a álbum."""
        existing = session.execute(
            select(AlbumArtist).where(
                AlbumArtist.album_id == album.id, AlbumArtist.artist_id == artist.id
            )
        ).scalar_one_or_none()

        if not existing:
            session.add(AlbumArtist(album_id=album.id, artist_id=artist.id, is_primary=is_primary))

    def search_albums(self, query: str, limit: int = 50) -> list[Album]:
        """Busca álbuns por título."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Album)
                    .options(selectinload(Album.artists).selectinload(AlbumArtist.artist))
                    .where(Album.title.ilike(f"%{query}%"))
                    .order_by(Album.title)
                    .limit(limit)
                )
                .scalars()
                .all()
            )

    def get_album_with_tracks(self, album_id: int) -> Album | None:
        """Busca álbum com faixas e artistas carregados."""
        with self.db.session() as session:
            return session.execute(
                select(Album)
                .options(
                    selectinload(Album.tracks)
                    .selectinload(Track.artists)
                    .selectinload(TrackArtist.artist),
                    selectinload(Album.artists).selectinload(AlbumArtist.artist),
                )
                .where(Album.id == album_id)
            ).scalar_one_or_none()

    # ===== Faixas =====

    def get_or_create_track(
        self,
        session: Session,
        source_id: str,
        title: str,
        source: TrackSource = TrackSource.YOUTUBE_MUSIC,
        **kwargs: Any,
    ) -> Track:
        """Busca ou cria faixa."""
        track = session.execute(
            select(Track).where(Track.source_id == source_id, Track.source == source)
        ).scalar_one_or_none()

        if not track:
            track = Track(source_id=source_id, title=title, source=source, **kwargs)
            session.add(track)
            session.flush()
        else:
            for key, value in kwargs.items():
                if value and hasattr(track, key):
                    setattr(track, key, value)
        return track

    def link_track_artist(
        self,
        session: Session,
        track: Track,
        artist: Artist,
        is_primary: bool = True,
        role: str = "main",
    ) -> None:
        """Associa artista a faixa."""
        existing = session.execute(
            select(TrackArtist).where(
                TrackArtist.track_id == track.id, TrackArtist.artist_id == artist.id
            )
        ).scalar_one_or_none()

        if not existing:
            session.add(
                TrackArtist(
                    track_id=track.id, artist_id=artist.id, is_primary=is_primary, role=role
                )
            )

    def link_track_album(self, session: Session, track: Track, album: Album) -> None:
        """Associa faixa a álbum."""
        track.album_id = album.id

    def search_tracks(
        self, query: str, limit: int = 50, offset: int = 0, source: TrackSource | None = None
    ) -> list[Track]:
        """Busca faixas por título ou artista."""
        with self.db.session() as session:
            stmt = (
                select(Track)
                .options(
                    selectinload(Track.artists).selectinload(TrackArtist.artist),
                    selectinload(Track.album),
                )
                .where(
                    or_(
                        Track.title.ilike(f"%{query}%"),
                        Track.id.in_(
                            select(TrackArtist.track_id)
                            .join(Artist)
                            .where(Artist.name.ilike(f"%{query}%"))
                        ),
                    )
                )
                .order_by(desc(Track.added_at))
                .limit(limit)
                .offset(offset)
            )

            if source:
                stmt = stmt.where(Track.source == source)

            return list(session.execute(stmt).scalars().all())

    def get_track(self, track_id: int) -> Track | None:
        """Busca faixa por ID com relacionamentos."""
        with self.db.session() as session:
            return session.execute(
                select(Track)
                .options(
                    selectinload(Track.artists).selectinload(TrackArtist.artist),
                    selectinload(Track.album)
                    .selectinload(Album.artists)
                    .selectinload(AlbumArtist.artist),
                )
                .where(Track.id == track_id)
            ).scalar_one_or_none()

    def get_track_by_source_id(
        self, source_id: str, source: TrackSource = TrackSource.YOUTUBE_MUSIC
    ) -> Track | None:
        """Busca faixa por ID da fonte (YouTube video ID).

        Carrega artistas e álbum de forma ansiosa: a instância retornada fica
        desacoplada da sessão e qualquer lazy load posterior falharia.
        """
        with self.db.session() as session:
            return session.execute(
                select(Track)
                .options(
                    selectinload(Track.artists).selectinload(TrackArtist.artist),
                    selectinload(Track.album)
                    .selectinload(Album.artists)
                    .selectinload(AlbumArtist.artist),
                )
                .where(Track.source_id == source_id, Track.source == source)
            ).scalar_one_or_none()

    def get_liked_tracks(self, limit: int = 100, offset: int = 0) -> list[Track]:
        """Retorna faixas marcadas como 'gostei'."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Track)
                    .options(
                        selectinload(Track.artists).selectinload(TrackArtist.artist),
                        selectinload(Track.album),
                    )
                    .where(Track.is_liked.is_(True))
                    .order_by(desc(Track.added_at))
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )

    def get_downloaded_tracks(self, limit: int = 100, offset: int = 0) -> list[Track]:
        """Retorna faixas baixadas localmente."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Track)
                    .options(
                        selectinload(Track.artists).selectinload(TrackArtist.artist),
                        selectinload(Track.album),
                    )
                    .where(Track.is_downloaded.is_(True), Track.local_path.is_not(None))
                    .order_by(desc(Track.added_at))
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )

    def get_local_paths(self) -> set[str]:
        """Todos os ``local_path`` registrados (insumo da reconciliação, §5.3)."""
        with self.db.session() as session:
            rows: list[str | None] = list(
                session.execute(
                    select(Track.local_path).where(Track.local_path.is_not(None))
                ).scalars()
            )
        return {str(path) for path in rows if path}

    def set_missing(self, local_paths: Iterable[Path], missing: bool = True) -> int:
        """Marca/limpa ``is_missing`` das faixas com esses ``local_path`` (Q8).

        Insumo da reconciliação: "registro sem arquivo" marca; registro cujo
        arquivo reapareceu limpa. Só grava em quem muda de estado (devolve
        quantas linhas mudaram); caminho sem faixa registrada é ignorado e
        lista vazia não abre sessão.
        """
        wanted = {str(path) for path in local_paths}
        if not wanted:
            return 0
        changed = 0
        with self.db.session() as session:
            tracks = (
                session.execute(select(Track).where(Track.local_path.in_(wanted))).scalars().all()
            )
            for track in tracks:
                if bool(track.is_missing) != missing:
                    track.is_missing = missing
                    changed += 1
        return changed

    def get_recent_tracks(self, limit: int = 50) -> list[Track]:
        """Faixas adicionadas recentemente."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Track)
                    .options(
                        selectinload(Track.artists).selectinload(TrackArtist.artist),
                        selectinload(Track.album),
                    )
                    .order_by(desc(Track.added_at))
                    .limit(limit)
                )
                .scalars()
                .all()
            )

    # ===== Visão da biblioteca local (T17.1/R6/R7) =====

    def list_library_albums(self, limit: int = 500) -> list[LibraryAlbum]:
        """Álbuns com faixas na biblioteca, em ordem de título (T17.1/R6).

        A ordem é fixada aqui (``str.casefold``) — a UI e os testes não
        dependem do *collation* do SQLite. Álbum sem faixas fica de fora:
        a lista mostra o que existe; ``missing_count`` alimenta o aviso
        "⚠ N ausentes" (R7).
        """
        with self.db.session() as session:
            albums = list(
                session.execute(
                    select(Album)
                    .options(
                        selectinload(Album.artists).selectinload(AlbumArtist.artist),
                        selectinload(Album.tracks),
                    )
                    .order_by(Album.title)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        summaries = [
            LibraryAlbum(
                album=album,
                track_count=len(album.tracks),
                missing_count=sum(1 for track in album.tracks if track.is_missing),
            )
            for album in albums
            if album.tracks
        ]
        return sorted(summaries, key=lambda item: str(item.album.title).casefold())

    def list_album_tracks(self, album_id: int, limit: int = 500) -> list[Track]:
        """Faixas de um álbum na ordem de álbum (T17.1/R6/R7)."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Track)
                    .options(
                        selectinload(Track.artists).selectinload(TrackArtist.artist),
                        selectinload(Track.album),
                    )
                    .where(Track.album_id == album_id)
                    .order_by(*_album_track_order())
                    .limit(limit)
                )
                .scalars()
                .all()
            )

    def list_standalone_tracks(self, limit: int = 500) -> list[Track]:
        """Faixas sem álbum ("Faixas avulsas"), em ordem de título (T17.1/R6)."""
        with self.db.session() as session:
            tracks = list(
                session.execute(
                    select(Track)
                    .options(
                        selectinload(Track.artists).selectinload(TrackArtist.artist),
                        selectinload(Track.album),
                    )
                    .where(Track.album_id.is_(None))
                    .order_by(Track.title)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return sorted(tracks, key=lambda track: str(track.title).casefold())

    def toggle_like(self, track_id: int) -> bool:
        """Alterna status de 'gostei' da faixa (uma transação — R3/T05)."""
        with self.db.session() as session:
            track = session.get(Track, track_id)
            if track:
                track.is_liked = not track.is_liked
                return track.is_liked
        return False

    def update_track_file(
        self, track_id: int, local_path: Path, file_size: int, file_format: str, bitrate: int
    ) -> None:
        """Atualiza info de arquivo local da faixa (e limpa a marca de ausente)."""
        with self.db.session() as session:
            track = session.get(Track, track_id)
            if track:
                track.local_path = str(local_path)
                track.file_size = file_size
                track.file_format = file_format
                track.bitrate = bitrate
                track.is_downloaded = True
                track.is_missing = False  # Q8: o arquivo foi (re)registrado agora

    def ensure_track(
        self,
        source_id: str,
        title: str,
        artist: str = "",
        album: str = "",
        thumbnail_url: str = "",
        duration_seconds: int = 0,
        track_number: int | None = None,
        disc_number: int | None = None,
        album_track_count: int | None = None,
    ) -> int | None:
        """Garante que a faixa exista na biblioteca e devolve o ID local.

        Usado antes de enfileirar um download: assim o arquivo baixado já
        nasce vinculado a uma faixa da biblioteca. Artistas e álbum
        desconhecidos (sem browseId) recebem IDs locais determinísticos.

        ``track_number``/``disc_number``/``album_track_count`` vêm do fluxo
        de álbum (T17.1/R2) e gravam a ordem na biblioteca — é o que a
        aba Álbum (R6) usa para listar as faixas numeradas. Só escrevem
        quando informados (``None`` não apaga o que já existe).

        Faixa, artistas, álbum e vínculos gravam numa transação só (R3/T05):
        falha no meio → rollback completo, nada parcial.
        """
        with self.db.session() as session:
            track = self.get_or_create_track(
                session,
                source_id,
                title,
                TrackSource.YOUTUBE_MUSIC,
                duration_seconds=duration_seconds,
                thumbnail_url=thumbnail_url,
            )
            if track_number is not None:
                track.track_number = track_number
            if disc_number is not None:
                track.disc_number = disc_number

            primary_artist: Artist | None = None
            names = [name.strip() for name in artist.split(",") if name.strip()]
            for index, name in enumerate(names):
                art = self.get_or_create_artist(
                    session,
                    source_id=f"local:{name.casefold()}",
                    name=name,
                    source=TrackSource.LOCAL_FILE,
                )
                self.link_track_artist(session, track, art, is_primary=index == 0)
                if index == 0:
                    primary_artist = art

            if album:
                alb = self.get_or_create_album(
                    session,
                    source_id=f"local:{album.casefold()}",
                    title=album,
                    source=TrackSource.LOCAL_FILE,
                )
                self.link_track_album(session, track, alb)
                if primary_artist:
                    self.link_album_artist(session, alb, primary_artist)
                # Total do álbum conhecido (R2/R5): 0 também é "desconhecido"
                # aqui — o contador de sistema do modelo usa 0 como vazio.
                if album_track_count:
                    alb.track_count = album_track_count

            session.flush()
            return track.id

    # ===== Playlists =====

    def create_playlist(
        self,
        name: str,
        description: str = "",
        source: TrackSource = TrackSource.LOCAL_FILE,
        source_id: str = "",
        thumbnail_url: str = "",
    ) -> Playlist:
        """Cria nova playlist."""
        with self.db.session() as session:
            playlist = Playlist(
                name=name,
                description=description,
                source=source,
                source_id=source_id or None,
                thumbnail_url=thumbnail_url,
            )
            session.add(playlist)
            session.flush()
            session.refresh(playlist)
            return playlist

    def get_playlist(self, playlist_id: int) -> Playlist | None:
        """Busca playlist com faixas."""
        with self.db.session() as session:
            return session.execute(
                select(Playlist)
                .options(
                    selectinload(Playlist.tracks)
                    .selectinload(PlaylistTrack.track)
                    .selectinload(Track.artists)
                    .selectinload(TrackArtist.artist),
                    selectinload(Playlist.tracks)
                    .selectinload(PlaylistTrack.track)
                    .selectinload(Track.album),
                )
                .where(Playlist.id == playlist_id)
            ).scalar_one_or_none()

    def list_playlists(self) -> list[Playlist]:
        """Lista todas as playlists."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Playlist).where(Playlist.is_system.is_(False)).order_by(Playlist.name)
                )
                .scalars()
                .all()
            )

    def rename_playlist(self, playlist_id: int, name: str) -> bool:
        """Renomeia playlist local; nome em branco é recusado (T17.1/R1).

        Playlists de sistema (``is_system``) são protegidas — nunca entram
        no fluxo da UI e não podem ser renomeadas por acidente.
        """
        new_name = name.strip()
        if not new_name:
            return False
        with self.db.session() as session:
            playlist = session.get(Playlist, playlist_id)
            if not playlist or playlist.is_system:
                return False
            playlist.name = new_name
            return True

    def delete_playlist(self, playlist_id: int) -> bool:
        """Apaga a playlist e todos os itens dela (T17.1/R1).

        O cascade do modelo remove as linhas de ``playlist_tracks`` na
        mesma transação (R3/T05); playlists de sistema são protegidas.
        """
        with self.db.session() as session:
            playlist = session.get(Playlist, playlist_id)
            if not playlist or playlist.is_system:
                return False
            session.delete(playlist)
            return True

    def add_track_to_playlist(self, playlist_id: int, track_id: int, position: int = -1) -> bool:
        """Adiciona faixa à playlist (deslocamento, entrada e contador numa
        transação só — R3/T05)."""
        with self.db.session() as session:
            playlist = session.get(Playlist, playlist_id)
            track = session.get(Track, track_id)
            if not playlist or not track:
                return False

            if position < 0:
                # Adicionar no final
                max_pos = (
                    session.execute(
                        select(func.max(PlaylistTrack.position)).where(
                            PlaylistTrack.playlist_id == playlist_id
                        )
                    ).scalar()
                    or 0
                )
                position = max_pos + 1
            else:
                # Deslocar faixas existentes
                session.execute(
                    update(PlaylistTrack)
                    .where(
                        PlaylistTrack.playlist_id == playlist_id,
                        PlaylistTrack.position >= position,
                    )
                    .values(position=PlaylistTrack.position + 1)
                )

            session.add(
                PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=position)
            )
            playlist.track_count = (playlist.track_count or 0) + 1
            return True

    def add_album_to_playlist(self, playlist_id: int, album_id: int) -> AlbumPlaylistResult | None:
        """Adiciona à playlist as faixas do álbum que estão na biblioteca (T17.1/R1).

        Uma transação só (R3/T05): faixas na ordem de álbum (``disc_number``/
        ``track_number``; sem número cai para ``added_at``/``id`` — R2/R6),
        posição ao fim da playlist, e quem já está é pulado (sem duplicatas).
        Devolve as contagens — inclui o contador de sistema da playlist —
        ou ``None`` quando playlist ou álbum não existem.
        """
        with self.db.session() as session:
            playlist = session.get(Playlist, playlist_id)
            album = session.get(Album, album_id)
            if not playlist or not album:
                return None

            tracks = list(
                session.execute(
                    select(Track).where(Track.album_id == album_id).order_by(*_album_track_order())
                )
                .scalars()
                .all()
            )
            existing = set(
                session.execute(
                    select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == playlist_id)
                )
                .scalars()
                .all()
            )
            max_pos = (
                session.execute(
                    select(func.max(PlaylistTrack.position)).where(
                        PlaylistTrack.playlist_id == playlist_id
                    )
                ).scalar()
                or 0
            )

            added = 0
            already = 0
            for track in tracks:
                if track.id in existing:
                    already += 1
                    continue
                max_pos += 1
                session.add(
                    PlaylistTrack(playlist_id=playlist_id, track_id=track.id, position=max_pos)
                )
                added += 1
            if added:
                playlist.track_count = (playlist.track_count or 0) + added
            return AlbumPlaylistResult(
                added=added,
                already=already,
                album_total=album.track_count or None,
            )

    def remove_track_from_playlist(self, playlist_id: int, track_id: int) -> bool:
        """Remove faixa da playlist (entrada e contador numa transação só —
        R3/T05)."""
        with self.db.session() as session:
            result = session.execute(
                select(PlaylistTrack).where(
                    PlaylistTrack.playlist_id == playlist_id, PlaylistTrack.track_id == track_id
                )
            ).scalar_one_or_none()

            if result:
                session.delete(result)
                playlist = session.get(Playlist, playlist_id)
                if playlist:
                    playlist.track_count = max(0, (playlist.track_count or 1) - 1)
                return True
        return False

    def reorder_playlist_tracks(self, playlist_id: int, track_ids: list[int]) -> bool:
        """Reordena faixas da playlist (todos os UPDATEs numa transação só —
        R3/T05: falha no meio → ordem original intacta)."""
        with self.db.session() as session:
            for pos, track_id in enumerate(track_ids, 1):
                session.execute(
                    update(PlaylistTrack)
                    .where(
                        PlaylistTrack.playlist_id == playlist_id,
                        PlaylistTrack.track_id == track_id,
                    )
                    .values(position=pos)
                )
            return True

    # ===== Histórico de reprodução =====

    def add_playback_history(
        self,
        track_id: int,
        duration_played: int = 0,
        completed: bool = False,
        source: str = "library",
    ) -> None:
        """Registra reprodução no histórico (histórico e contadores numa
        transação só — R3/T05)."""
        with self.db.session() as session:
            history = PlaybackHistory(
                track_id=track_id,
                duration_played=duration_played,
                completed=completed,
                source=source,
            )
            session.add(history)

            # Atualizar contadores na faixa
            track = session.get(Track, track_id)
            if track:
                track.play_count = (track.play_count or 0) + 1
                track.last_played = utcnow()

    def get_recently_played(self, limit: int = 50) -> list[Track]:
        """Faixas reproduzidas recentemente (únicas)."""
        with self.db.session() as session:
            subquery = (
                select(
                    PlaybackHistory.track_id,
                    func.max(PlaybackHistory.played_at).label("last_played"),
                )
                .group_by(PlaybackHistory.track_id)
                .subquery()
            )

            return list(
                session.execute(
                    select(Track)
                    .options(
                        selectinload(Track.artists).selectinload(TrackArtist.artist),
                        selectinload(Track.album),
                    )
                    .join(subquery, Track.id == subquery.c.track_id)
                    .order_by(desc(subquery.c.last_played))
                    .limit(limit)
                )
                .scalars()
                .all()
            )

    def get_most_played(self, limit: int = 50) -> list[Track]:
        """Faixas mais reproduzidas."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(Track)
                    .options(
                        selectinload(Track.artists).selectinload(TrackArtist.artist),
                        selectinload(Track.album),
                    )
                    .where(Track.play_count > 0)
                    .order_by(desc(Track.play_count))
                    .limit(limit)
                )
                .scalars()
                .all()
            )

    # ===== Configurações do usuário =====

    def set_setting(self, key: str, value: str) -> None:
        """Define configuração do usuário."""
        with self.db.session() as session:
            setting = session.get(UserSettings, key)
            if setting:
                setting.value = value
            else:
                session.add(UserSettings(key=key, value=value))

    def get_setting(self, key: str, default: str = "") -> str:
        """Obtém configuração do usuário."""
        with self.db.session() as session:
            setting = session.get(UserSettings, key)
            return setting.value if setting else default

    # ===== Estatísticas =====

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas da biblioteca."""
        with self.db.session() as session:
            stats = {
                "tracks": session.execute(select(func.count(Track.id))).scalar(),
                "artists": session.execute(select(func.count(Artist.id))).scalar(),
                "albums": session.execute(select(func.count(Album.id))).scalar(),
                "playlists": session.execute(select(func.count(Playlist.id))).scalar(),
                "liked_tracks": session.execute(
                    select(func.count(Track.id)).where(Track.is_liked.is_(True))
                ).scalar(),
                "downloaded_tracks": session.execute(
                    select(func.count(Track.id)).where(Track.is_downloaded.is_(True))
                ).scalar(),
                "total_duration": session.execute(
                    select(func.coalesce(func.sum(Track.duration_seconds), 0))
                ).scalar(),
                "total_size": session.execute(
                    select(func.coalesce(func.sum(Track.file_size), 0))
                ).scalar(),
            }
            return stats


# Instância global
library = LibraryService()
