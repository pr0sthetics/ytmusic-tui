"""Testes para o serviço da biblioteca."""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from ytmusic_tui.library.database import DatabaseManager
from ytmusic_tui.library.models import Album, Playlist, PlaylistTrack, Track, TrackSource
from ytmusic_tui.library.service import LibraryService


@pytest.fixture
def temp_db():
    """Cria banco temporário para testes."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    db_manager = DatabaseManager(db_path)
    db_manager.create_all()

    yield db_manager

    db_manager.close()
    db_path.unlink(missing_ok=True)


@pytest.fixture
def library_service(temp_db):
    """Cria serviço de biblioteca com DB temporário."""
    # Criar serviço que usa o db temporário
    service = LibraryService()
    service.db = temp_db
    return service


def test_create_artist(library_service):
    """Testa criação de artista."""
    with library_service.db.session() as session:
        artist = library_service.get_or_create_artist(
            session, "UC123", "Test Artist", TrackSource.YOUTUBE_MUSIC
        )
        assert artist.id is not None
        assert artist.name == "Test Artist"
        assert artist.source_id == "UC123"


def test_create_album_with_artist(library_service):
    """Testa criação de álbum com artista."""
    with library_service.db.session() as session:
        artist = library_service.get_or_create_artist(
            session, "UC123", "Test Artist", TrackSource.YOUTUBE_MUSIC
        )
        album = library_service.get_or_create_album(
            session, "ALB123", "Test Album", TrackSource.YOUTUBE_MUSIC, release_year=2024
        )
        library_service.link_album_artist(session, album, artist)

        # Verificar relacionamento
        session.refresh(album)
        assert len(album.artists) == 1
        assert album.artists[0].artist.name == "Test Artist"


def test_create_track_with_artist_and_album(library_service):
    """Testa criação de faixa com artista e álbum."""
    track_id = None
    album_id = None

    with library_service.db.session() as session:
        artist = library_service.get_or_create_artist(
            session, "UC123", "Test Artist", TrackSource.YOUTUBE_MUSIC
        )
        album = library_service.get_or_create_album(
            session, "ALB123", "Test Album", TrackSource.YOUTUBE_MUSIC
        )
        album_id = album.id
        library_service.link_album_artist(session, album, artist)

        track = library_service.get_or_create_track(
            session, "VID123", "Test Track", TrackSource.YOUTUBE_MUSIC, duration_seconds=180
        )
        track_id = track.id
        library_service.link_track_artist(session, track, artist)
        library_service.link_track_album(session, track, album)

        session.flush()
        session.refresh(track)
        assert track.primary_artist.name == "Test Artist"
        assert track.album_id == album.id
        assert track.duration_seconds == 180

    # Verificar relacionamento em nova sessão
    track = library_service.get_track(track_id)
    assert track is not None
    assert track.album_id == album_id
    assert track.album.title == "Test Album"


def test_playlist_operations(library_service):
    """Testa operações de playlist."""
    playlist = library_service.create_playlist("Minha Playlist", "Descrição")
    assert playlist.id is not None
    assert playlist.name == "Minha Playlist"

    with library_service.db.session() as session:
        # Criar faixas
        artist = library_service.get_or_create_artist(
            session, "UC1", "Artist 1", TrackSource.YOUTUBE_MUSIC
        )
        track1 = library_service.get_or_create_track(
            session, "VID1", "Track 1", TrackSource.YOUTUBE_MUSIC
        )
        track2 = library_service.get_or_create_track(
            session, "VID2", "Track 2", TrackSource.YOUTUBE_MUSIC
        )
        library_service.link_track_artist(session, track1, artist)
        library_service.link_track_artist(session, track2, artist)

    # Adicionar à playlist
    library_service.add_track_to_playlist(playlist.id, track1.id)
    library_service.add_track_to_playlist(playlist.id, track2.id)

    # Verificar
    pl = library_service.get_playlist(playlist.id)
    assert pl is not None
    assert pl.track_count == 2
    assert len(pl.tracks) == 2


def test_rename_playlist_apara_e_recusa_branco(library_service):
    """T17.1/R1: rename apara o nome, recusa vazio e protege sistema."""
    playlist = library_service.create_playlist("Original")

    assert library_service.rename_playlist(playlist.id, "  Nova Playlist  ") is True
    reloaded = library_service.get_playlist(playlist.id)
    assert reloaded is not None
    assert reloaded.name == "Nova Playlist"

    # Branco (só espaços) é recusado e nada muda.
    assert library_service.rename_playlist(playlist.id, "   ") is False
    assert library_service.rename_playlist(playlist.id, "") is False
    assert library_service.get_playlist(playlist.id).name == "Nova Playlist"

    # Playlist inexistente → False.
    assert library_service.rename_playlist(9999, "Fantasma") is False

    # Playlist de sistema é protegida.
    system = library_service.create_playlist("Sistema")
    with library_service.db.session() as session:
        session.get(Playlist, system.id).is_system = True
    assert library_service.rename_playlist(system.id, "Hack") is False
    assert library_service.get_playlist(system.id).name == "Sistema"


def test_delete_playlist_remove_itens_e_protege_sistema(library_service):
    """T17.1/R1: delete apaga a playlist e os itens (cascade); sistema é protegida."""
    track_id = library_service.ensure_track("VID-DEL", "Vai junto")
    assert track_id is not None
    playlist = library_service.create_playlist("Para apagar")
    assert library_service.add_track_to_playlist(playlist.id, track_id) is True
    playlist_id = playlist.id

    assert library_service.delete_playlist(playlist_id) is True
    assert library_service.get_playlist(playlist_id) is None
    with library_service.db.session() as session:
        remaining = (
            session.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
            .scalars()
            .all()
        )
    assert list(remaining) == []
    # A faixa em si continua na biblioteca (só o vínculo saiu).
    assert library_service.get_track_by_source_id("VID-DEL") is not None

    assert library_service.delete_playlist(9999) is False

    system = library_service.create_playlist("Sistema")
    with library_service.db.session() as session:
        session.get(Playlist, system.id).is_system = True
    assert library_service.delete_playlist(system.id) is False
    assert library_service.get_playlist(system.id) is not None


def test_add_album_to_playlist_ordena_e_conta(library_service):
    """T17.1/R1: álbum → playlist ordena por número, pula repetidas e conta honestamente."""
    track_ids = {
        "A1": library_service.ensure_track("A1", "Terceira", "Artista", "Álbum Ordem"),
        "A2": library_service.ensure_track("A2", "Primeira", "Artista", "Álbum Ordem"),
        "A3": library_service.ensure_track("A3", "Segunda", "Artista", "Álbum Ordem"),
        "A4": library_service.ensure_track("A4", "Sem número", "Artista", "Álbum Ordem"),
    }
    # Números gravados como o fluxo de download fará (R2).
    with library_service.db.session() as session:
        session.get(Track, track_ids["A1"]).track_number = 3
        session.get(Track, track_ids["A2"]).track_number = 1
        session.get(Track, track_ids["A3"]).track_number = 2

    album = library_service.search_albums("Álbum Ordem")[0]
    playlist = library_service.create_playlist("Mistura")

    result = library_service.add_album_to_playlist(playlist.id, album.id)
    assert result is not None
    assert (result.added, result.already, result.album_total) == (4, 0, None)

    reloaded = library_service.get_playlist(playlist.id)
    assert reloaded.track_count == 4
    by_position = sorted(reloaded.tracks, key=lambda entry: entry.position)
    # Numeradas primeiro (1, 2, 3); sem número cai para o fim (added_at/id).
    assert [entry.track.title for entry in by_position] == [
        "Primeira",
        "Segunda",
        "Terceira",
        "Sem número",
    ]

    # Sem duplicatas: a segunda chamada só conta o que já estava.
    again = library_service.add_album_to_playlist(playlist.id, album.id)
    assert again is not None
    assert (again.added, again.already) == (0, 4)
    assert library_service.get_playlist(playlist.id).track_count == 4

    # album_total conhecido quando Album.track_count existe (parcela K do R5).
    with library_service.db.session() as session:
        session.get(Album, album.id).track_count = 6
    known = library_service.add_album_to_playlist(playlist.id, album.id)
    assert known is not None
    assert known.album_total == 6

    # Playlist ou álbum inexistente → None (sem erro).
    assert library_service.add_album_to_playlist(9999, album.id) is None
    assert library_service.add_album_to_playlist(playlist.id, 9999) is None


def test_add_album_respeita_posicoes_existentes(library_service):
    """T17.1/R1: entradas novas entram após a posição máxima; contador coerente."""
    manual_id = library_service.ensure_track("B1", "Antiga")
    assert manual_id is not None
    assert library_service.ensure_track("B2", "Do álbum", "Artista", "Álbum B") is not None
    album = library_service.search_albums("Álbum B")[0]

    playlist = library_service.create_playlist("Fila")
    assert library_service.add_track_to_playlist(playlist.id, manual_id) is True

    result = library_service.add_album_to_playlist(playlist.id, album.id)
    assert result is not None
    assert result.added == 1

    reloaded = library_service.get_playlist(playlist.id)
    assert reloaded.track_count == 2
    by_position = sorted(reloaded.tracks, key=lambda entry: entry.position)
    assert [entry.position for entry in by_position] == [1, 2]
    assert [entry.track.title for entry in by_position] == ["Antiga", "Do álbum"]


def test_like_toggle(library_service):
    """Testa toggle de like."""
    with library_service.db.session() as session:
        artist = library_service.get_or_create_artist(
            session, "UC1", "Artist", TrackSource.YOUTUBE_MUSIC
        )
        track = library_service.get_or_create_track(
            session, "VID1", "Track", TrackSource.YOUTUBE_MUSIC
        )
        library_service.link_track_artist(session, track, artist)
        track_id = track.id

    # Sessões independentes: toggles só após o commit do bloco acima.
    assert library_service.toggle_like(track_id) is True
    assert library_service.toggle_like(track_id) is False


def test_search_tracks(library_service):
    """Testa busca de faixas."""
    with library_service.db.session() as session:
        artist = library_service.get_or_create_artist(
            session, "UC1", "The Beatles", TrackSource.YOUTUBE_MUSIC
        )
        track1 = library_service.get_or_create_track(
            session, "VID1", "Hey Jude", TrackSource.YOUTUBE_MUSIC
        )
        track2 = library_service.get_or_create_track(
            session, "VID2", "Let It Be", TrackSource.YOUTUBE_MUSIC
        )
        library_service.link_track_artist(session, track1, artist)
        library_service.link_track_artist(session, track2, artist)

    # Buscar por título
    results = library_service.search_tracks("Hey Jude")
    assert len(results) == 1
    assert results[0].title == "Hey Jude"

    # Buscar por artista
    results = library_service.search_tracks("Beatles")
    assert len(results) == 2

    # Buscar inexistente
    results = library_service.search_tracks("Inexistente")
    assert len(results) == 0


def test_stats(library_service):
    """Testa estatísticas."""
    with library_service.db.session() as session:
        artist = library_service.get_or_create_artist(
            session, "UC1", "Artist", TrackSource.YOUTUBE_MUSIC
        )
        track = library_service.get_or_create_track(
            session, "VID1", "Track", TrackSource.YOUTUBE_MUSIC, duration_seconds=200
        )
        library_service.link_track_artist(session, track, artist)

    stats = library_service.get_stats()
    assert stats["tracks"] == 1
    assert stats["artists"] == 1
    assert stats["total_duration"] == 200


def test_ensure_track_creates_artist_album_and_is_idempotent(library_service):
    """ensure_track registra faixa + artistas + álbum antes do download."""
    track_id = library_service.ensure_track(
        source_id="VIDX",
        title="Música Nova",
        artist="Artista A, Artista B",
        album="Álbum Z",
        thumbnail_url="http://example.com/x.jpg",
        duration_seconds=210,
    )
    assert track_id is not None

    track = library_service.get_track_by_source_id("VIDX")
    assert track is not None
    assert track.title == "Música Nova"
    assert set(track.artist_names) == {"Artista A", "Artista B"}
    assert track.album is not None
    assert track.album.title == "Álbum Z"

    # Idempotente: chamar de novo não duplica nem muda o ID
    again = library_service.ensure_track(
        source_id="VIDX",
        title="Música Nova",
        artist="Artista A, Artista B",
        album="Álbum Z",
    )
    assert again == track_id
    stats = library_service.get_stats()
    assert stats["tracks"] == 1
    assert stats["artists"] == 2


def test_get_local_paths_returns_only_registered_files(library_service, tmp_path):
    """get_local_paths lista os caminhos registrados (insumo da reconciliação)."""
    assert library_service.get_local_paths() == set()

    track_id = library_service.ensure_track(source_id="VIDY", title="Com Arquivo")
    assert track_id is not None
    media = tmp_path / "Com Arquivo.mp3"
    media.write_bytes(b"audio")
    library_service.update_track_file(track_id, media, 5, "mp3", 320)

    assert library_service.get_local_paths() == {str(media)}


# ===== Q8/T05: estado "arquivo ausente" (is_missing) =====


def test_new_tracks_are_not_missing_by_default(library_service):
    """Faixa recém-criada nasce com is_missing=False (default do esquema)."""
    track_id = library_service.ensure_track(source_id="VIDN", title="Nova")
    assert track_id is not None
    track = library_service.get_track_by_source_id("VIDN")
    assert track is not None
    assert track.is_missing is False


def test_set_missing_marks_clears_and_counts_only_changes(library_service, tmp_path):
    """set_missing marca, não repete a marcação e limpa quando pedido (Q8)."""
    track_id = library_service.ensure_track(source_id="VIDM", title="Sumida")
    assert track_id is not None
    media = tmp_path / "Sumida.mp3"
    media.write_bytes(b"audio")
    library_service.update_track_file(track_id, media, 5, "mp3", 320)

    assert library_service.set_missing([media], missing=True) == 1
    track = library_service.get_track_by_source_id("VIDM")
    assert track is not None and track.is_missing is True

    # Repetir o mesmo estado não grava de novo (conta só mudanças reais).
    assert library_service.set_missing([media], missing=True) == 0

    assert library_service.set_missing([media], missing=False) == 1
    track = library_service.get_track_by_source_id("VIDM")
    assert track is not None and track.is_missing is False


def test_set_missing_ignores_unknown_paths_and_empty_list(library_service, tmp_path):
    """Caminho sem faixa registrada não muda nada (e lista vazia é no-op)."""
    assert library_service.set_missing([], missing=True) == 0
    assert library_service.set_missing([tmp_path / "nunca-registrado.mp3"]) == 0


def test_update_track_file_clears_missing_mark(library_service, tmp_path):
    """(Re)registrar o arquivo limpa a marca de ausente (Q8)."""
    track_id = library_service.ensure_track(source_id="VIDC", title="Voltou")
    assert track_id is not None
    media = tmp_path / "Voltou.mp3"
    media.write_bytes(b"audio")
    library_service.update_track_file(track_id, media, 5, "mp3", 320)
    assert library_service.set_missing([media], missing=True) == 1

    library_service.update_track_file(track_id, media, 7, "mp3", 320)

    track = library_service.get_track_by_source_id("VIDC")
    assert track is not None and track.is_missing is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
