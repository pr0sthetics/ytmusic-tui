"""Falha injetada no meio das operações multi-registro (R3/T05).

Prova a promessa do §7/R3: ``ensure_track``, curtir/descurtir, operações de
playlist e o histórico de reprodução gravam **numa transação só**. A injeção
derruba a n-ésima escrita (INSERT/UPDATE/DELETE) na tabela alvo via evento no
engine; o esperado é a exceção subir e nada ficar gravado — nem o que já
tinha sido escrito antes da falha.
"""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import event

from ytmusic_tui.library.database import DatabaseManager
from ytmusic_tui.library.service import LibraryService


@pytest.fixture
def temp_db():
    """Banco temporário (mesmo padrão de test_library.py)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = Path(handle.name)

    db_manager = DatabaseManager(db_path)
    db_manager.create_all()

    yield db_manager

    db_manager.close()
    db_path.unlink(missing_ok=True)


@pytest.fixture
def service(temp_db):
    """Serviço apontando para o banco temporário."""
    library = LibraryService()
    library.db = temp_db
    return library


def _fail_on_nth_write(db: DatabaseManager, table: str, nth: int = 1) -> None:
    """Derruba a ``nth``-ésima escrita (INSERT/UPDATE/DELETE) na ``table``.

    Fica ativo só durante o teste (o engine é exclusivo do banco temporário
    de cada teste); leituras passam direto.
    """
    writes = {"count": 0}

    @event.listens_for(db.engine, "before_cursor_execute")
    def _boom(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.upper().split())
        if not normalized.startswith(("INSERT", "UPDATE", "DELETE")):
            return
        if f" {table.upper()} " not in normalized:
            return
        writes["count"] += 1
        if writes["count"] == nth:
            raise RuntimeError(f"falha injetada: escrita #{nth} em {table}")


def test_ensure_track_rolls_back_completely_on_midway_failure(service, temp_db):
    """Falha ao vincular o 2º artista: faixa, artistas e álbum somem por inteiro."""
    _fail_on_nth_write(temp_db, "track_artists", nth=2)

    with pytest.raises(RuntimeError, match="falha injetada"):
        service.ensure_track("VID-INJ1", "Faixa com falha", artist="Um, Dois", album="Álbum")

    # Nada sobrou: nem a faixa, nem os artistas, nem o álbum (rollback total).
    assert service.get_track_by_source_id("VID-INJ1") is None
    assert service.search_artists("Um") == []
    assert service.search_artists("Dois") == []
    assert service.search_albums("Álbum") == []
    stats = service.get_stats()
    assert stats["tracks"] == 0
    assert stats["artists"] == 0
    assert stats["albums"] == 0


def test_toggle_like_keeps_previous_state_on_write_failure(service, temp_db):
    """Falha na gravação do "gostei": o valor anterior permanece (rollback)."""
    track_id = service.ensure_track("VID-INJ2", "Curtida")
    assert track_id is not None

    _fail_on_nth_write(temp_db, "tracks", nth=1)
    with pytest.raises(RuntimeError, match="falha injetada"):
        service.toggle_like(track_id)

    track = service.get_track_by_source_id("VID-INJ2")
    assert track is not None
    assert not track.is_liked


def test_add_track_to_playlist_rolls_back_entry_and_counter(service, temp_db):
    """Falha ao atualizar o contador: nem a entrada nem o count ficam."""
    track_id = service.ensure_track("VID-INJ3", "Entra")
    playlist = service.create_playlist("Injeção de adição")
    assert track_id is not None
    assert playlist.id is not None

    _fail_on_nth_write(temp_db, "playlists", nth=1)
    with pytest.raises(RuntimeError, match="falha injetada"):
        service.add_track_to_playlist(playlist.id, track_id)

    reloaded = service.get_playlist(playlist.id)
    assert reloaded is not None
    assert list(reloaded.tracks) == []
    assert reloaded.track_count == 0


def test_remove_track_from_playlist_keeps_entry_when_counter_fails(service, temp_db):
    """Falha ao decrementar o contador: a entrada removida volta no rollback."""
    track_id = service.ensure_track("VID-INJ4", "Sai")
    playlist = service.create_playlist("Injeção de remoção")
    assert track_id is not None
    assert playlist.id is not None
    assert service.add_track_to_playlist(playlist.id, track_id)

    _fail_on_nth_write(temp_db, "playlists", nth=1)
    with pytest.raises(RuntimeError, match="falha injetada"):
        service.remove_track_from_playlist(playlist.id, track_id)

    reloaded = service.get_playlist(playlist.id)
    assert reloaded is not None
    assert [entry.track_id for entry in reloaded.tracks] == [track_id]
    assert reloaded.track_count == 1


def test_reorder_playlist_tracks_restores_every_position_on_failure(service, temp_db):
    """Falha no 2º UPDATE da reordenação: a ordem original continua inteira."""
    track_ids: list[int] = []
    for index in range(3):
        track_id = service.ensure_track(f"VID-INJ5{index}", f"Faixa {index}")
        assert track_id is not None
        track_ids.append(track_id)
    playlist = service.create_playlist("Injeção de ordem")
    assert playlist.id is not None
    for track_id in track_ids:
        assert service.add_track_to_playlist(playlist.id, track_id)

    _fail_on_nth_write(temp_db, "playlist_tracks", nth=2)
    with pytest.raises(RuntimeError, match="falha injetada"):
        service.reorder_playlist_tracks(playlist.id, list(reversed(track_ids)))

    reloaded = service.get_playlist(playlist.id)
    assert reloaded is not None
    by_position = sorted(reloaded.tracks, key=lambda entry: entry.position)
    assert [entry.track_id for entry in by_position] == track_ids


def test_add_playback_history_rolls_back_history_and_counters(service, temp_db):
    """Falha ao atualizar a faixa: nem histórico nem contador ficam gravados."""
    track_id = service.ensure_track("VID-INJ6", "Tocou")
    assert track_id is not None

    _fail_on_nth_write(temp_db, "tracks", nth=1)
    with pytest.raises(RuntimeError, match="falha injetada"):
        service.add_playback_history(track_id, duration_played=10, completed=True)

    assert service.get_recently_played() == []
    assert service.get_most_played() == []


def test_add_album_to_playlist_rolls_back_entries_and_counter(service, temp_db):
    """T17.1/R1: falha ao atualizar o contador → nenhuma faixa do álbum entra."""
    assert service.ensure_track("VID-INJ7", "Uma", artist="A", album="Álbum Lote") is not None
    assert service.ensure_track("VID-INJ8", "Duas", artist="A", album="Álbum Lote") is not None
    album = service.search_albums("Álbum Lote")[0]
    playlist = service.create_playlist("Injeção de lote")

    _fail_on_nth_write(temp_db, "playlists", nth=1)
    with pytest.raises(RuntimeError, match="falha injetada"):
        service.add_album_to_playlist(playlist.id, album.id)

    reloaded = service.get_playlist(playlist.id)
    assert reloaded is not None
    assert list(reloaded.tracks) == []
    assert reloaded.track_count == 0


def test_add_album_to_playlist_rolls_back_partial_batch(service, temp_db):
    """T17.1/R1: falha no INSERT do lote → rollback completo, nada parcial."""
    assert service.ensure_track("VID-INJ9", "Uma", artist="A", album="Álbum Parcial") is not None
    assert service.ensure_track("VID-INJ10", "Duas", artist="A", album="Álbum Parcial") is not None
    album = service.search_albums("Álbum Parcial")[0]
    playlist = service.create_playlist("Injeção parcial")

    _fail_on_nth_write(temp_db, "playlist_tracks", nth=1)
    with pytest.raises(RuntimeError, match="falha injetada"):
        service.add_album_to_playlist(playlist.id, album.id)

    reloaded = service.get_playlist(playlist.id)
    assert reloaded is not None
    assert list(reloaded.tracks) == []
    assert reloaded.track_count == 0
