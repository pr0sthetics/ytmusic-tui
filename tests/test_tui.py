"""Smoke test headless da TUI: navegação entre abas e render da busca."""

import asyncio
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Input,
    Label,
    Static,
    TabbedContent,
    Tabs,
)

import ytmusic_tui.download as download_mod
from tests.security._fakes import FakeDownloadError, FakeYoutubeDL, fake_yt_dlp
from ytmusic_tui.api import ytmusic
from ytmusic_tui.api.models import Album, AlbumDetail, SearchResult, Track
from ytmusic_tui.config import settings
from ytmusic_tui.download import (
    DownloadOutcome,
    DownloadStatus,
    DownloadTask,
    download_manager,
)
from ytmusic_tui.errors import InvalidResponseError, NetworkError, RateLimitError
from ytmusic_tui.library import db_manager, library
from ytmusic_tui.library.errors import (
    AudioFileMissingError,
    DatabaseCorruptError,
    SchemaIncompatibleError,
)
from ytmusic_tui.library.migrations import SCHEMA_VERSION
from ytmusic_tui.library.models import Album as DbAlbum
from ytmusic_tui.library.models import Track as DbTrack
from ytmusic_tui.library.recovery import ReconciliationReport
from ytmusic_tui.library.service import AlbumPlaylistResult, LibraryAlbum
from ytmusic_tui.player import PlayerEvent, TrackInfo, player
from ytmusic_tui.tui import download_controller, library_controller
from ytmusic_tui.tui.app import MainScreen, PlayerEventMessage, YTMusicTUI
from ytmusic_tui.tui.dialogs import (
    ConfirmScreen,
    HelpScreen,
    NameInputScreen,
    PlaylistPickerScreen,
)
from ytmusic_tui.tui.messages import format_key
from ytmusic_tui.tui.playback_controller import ensure_audio_file
from ytmusic_tui.tui.widgets import (
    AlbumListView,
    LibraryListView,
    NowPlayingBar,
    PlaylistListView,
    SongListView,
    TrackListView,
)

#: Módulo real do player (para trocar ``mpv.MPV`` nos testes de falha do
#: handle); a instância global vem importada como ``player`` acima.
player_mod = sys.modules["ytmusic_tui.player"]


class _NoopStore:
    """Espelho de tarefas sem disco, para os testes da TUI (T04).

    Nenhum teste da TUI pode tocar o banco real do usuário: a recuperação
    da inicialização (``recover_interrupted``) e as ações r/d leriam/escreveriam
    ``download_tasks`` de verdade. Aqui tudo é vazio e nada persiste — os
    testes específicos injetam fakes próprios.
    """

    def save(self, *args, **kwargs) -> None: ...
    def update_status(self, *args, **kwargs) -> None: ...
    def delete(self, *args, **kwargs) -> None: ...
    def find(self, *args, **kwargs):
        return None

    def mark_active_as_interrupted(self) -> int:
        return 0

    def load_interrupted(self) -> list:
        return []

    def awaiting_decision_files(self) -> list:
        return []


@pytest.fixture(autouse=True)
def _no_disk_reconciliation(monkeypatch):
    """Neutraliza disco real (reconciliação e espelho de tarefas) em TODOS os testes.

    Sem isto, cada ``on_mount`` varreria o ``download_dir`` real e poderia
    importar/quarentenar/remover arquivos do usuário durante os testes; os
    testes que precisam do comportamento injetam o próprio fake.
    """
    monkeypatch.setattr(
        library_controller, "reconcile", lambda *args, **kwargs: ReconciliationReport()
    )
    monkeypatch.setattr(download_manager, "_store", _NoopStore())


def _song(video_id: str, title: str, artist: str = "Artista") -> Track:
    return Track(
        video_id=video_id,
        title=title,
        artists=(artist,),
        duration_text="3:20",
        duration_seconds=200,
        album_name="Álbum Y",
        thumbnail_url="http://example.com/x.jpg",
    )


def _album_item(
    browse_id: str = "MPREb1",
    title: str = "Álbum Teste",
    artist: str = "Artista X",
    year: str = "2019",
    kind: str = "Album",
) -> Album:
    return Album(
        browse_id=browse_id,
        title=title,
        artists=(artist,),
        year=year,
        kind=kind,
    )


def _album_details(browse_id: str = "MPREb1") -> AlbumDetail:
    title, artist = "Álbum Teste", "Artista X"
    return AlbumDetail(
        browse_id=browse_id,
        title=title,
        kind="Album",
        year="2019",
        track_count=2,
        artists=(artist,),
        tracks=(
            Track(
                video_id="t1",
                title="Faixa 1",
                artists=(artist,),
                album_name=title,
                duration_text="3:00",
                duration_seconds=180,
                is_available=True,
            ),
            Track(
                video_id="t2",
                title="Faixa 2",
                artists=(artist,),
                album_name=title,
                duration_text="4:00",
                duration_seconds=240,
                is_available=True,
            ),
            Track(
                video_id="t3",
                title="Faixa Indisponível",
                artists=(artist,),
                album_name=title,
                is_available=False,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_switch_tabs_and_render_search():
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        screen = app.screen

        # Troca por todas as abas principais sem erro
        for tab_id in (
            "tab-search",
            "tab-library",
            "tab-downloads",
            "tab-settings",
            "tab-playlists",
        ):
            screen.query_one("#main-tabs", TabbedContent).active = tab_id
            await pilot.pause()

        # Renderiza resultados de busca e confere a lista navegável
        screen.query_one("#main-tabs", TabbedContent).active = "tab-search"
        await pilot.pause()

        results = SearchResult(
            tracks=(
                Track(
                    video_id="abc123",
                    title="Teste",
                    artists=("Artista X",),
                    duration_text="3:20",
                    duration_seconds=200,
                    album_name="Álbum Y",
                    thumbnail_url="http://example.com/x.jpg",
                ),
            ),
        )
        screen.display_search_results(results)
        await pilot.pause()

        view = screen.query_one("#search-songs", SongListView)
        assert len(view.songs) == 1
        assert view.songs[0].video_id == "abc123"
        # C1: o item precisa existir de fato no DOM (antes: 0 ListItems)
        assert len(view.children) == 1


@pytest.mark.asyncio
async def test_second_search_does_not_raise_duplicate_ids():
    """Regressão C2: uma segunda busca quebrava com DuplicateIds."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        view = screen.query_one("#search-songs", SongListView)

        screen.display_search_results([_song("v1", "Um")])
        await pilot.pause()
        assert len(view.children) == 1

        screen.display_search_results([_song("v2", "Dois"), _song("v3", "Três")])
        await pilot.pause()
        assert len(view.children) == 2

        # Títulos reais contêm colchetes; não podem ser interpretados como
        # markup do Rich (o texto sumia ou quebrava com MarkupError).
        screen.display_search_results([_song("v4", "Hit [prod. Y] [/b]")])
        await pilot.pause()
        assert len(view.children) == 1
        rendered = str(view.children[0].query_one(Label).render())
        assert "Hit [prod. Y] [/b]" in rendered


@pytest.mark.asyncio
async def test_nova_busca_abandona_retry_da_anterior(monkeypatch):
    """Nova busca seta o abort da anterior; a nova começa sem abort (T10-R3)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        aborts: list = []

        def _capture(query, songs_limit=25, albums_limit=15, should_abort=None):
            aborts.append(should_abort)
            return SearchResult()

        monkeypatch.setattr(ytmusic, "search_catalog", _capture)

        await screen.perform_search("primeira")
        await pilot.pause()
        await screen.perform_search("segunda")
        await pilot.pause()

        assert aborts[0] is not None and aborts[0]() is True  # abandonada
        assert aborts[1] is not None and aborts[1]() is False  # em curso


@pytest.mark.asyncio
async def test_config_tab_mostra_versoes_das_dependencias(monkeypatch):
    """R5: a aba Config informa versões instalada × testada (T10-R5)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        block = screen.query_one("#dependency-status", Static)
        rendered = str(block.render())

        assert "ytmusicapi" in rendered
        assert "yt-dlp" in rendered
        assert "testada" in rendered


@pytest.mark.asyncio
async def test_search_message_accepts_brackets():
    """Mensagens de erro da busca podem conter [colchetes]."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._render_search_message("Erro na busca: [Errno 5] falha de rede")
        await pilot.pause()
        message = screen.query_one("#search-message", Static)
        assert message.display is True
        assert screen.query_one("#search-songs").display is False


@pytest.mark.asyncio
async def test_search_shows_albums_with_type_marker(monkeypatch):
    """A busca exibe álbuns junto das músicas, marcados com tipo e ano."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        monkeypatch.setattr(
            ytmusic,
            "search_catalog",
            lambda *args, **kwargs: SearchResult(
                tracks=(_song("v1", "Música"),),
                albums=(_album_item(),),
            ),
        )

        await screen.perform_search("album teste")
        await pilot.pause()

        albums = screen.query_one("#search-albums", AlbumListView)
        songs = screen.query_one("#search-songs", SongListView)
        assert songs.display and len(songs.songs) == 1
        assert albums.display and len(albums.albums) == 1
        assert screen.query_one("#search-albums-title", Static).display

        label = str(albums.children[0].query_one(Label).render())
        assert "Álbum Teste" in label
        assert "Album" in label and "2019" in label  # marcador do tipo

        # Sem álbuns, a seção some e a lista de músicas ocupa o espaço
        screen.display_search_results(SearchResult(tracks=(_song("v2", "Só música"),)))
        await pilot.pause()
        assert not albums.display
        assert songs.display and len(songs.songs) == 1


@pytest.mark.asyncio
async def test_selecting_album_opens_tab_with_tracks(monkeypatch):
    """Enter num álbum abre a aba 💿 Álbum com artista e faixas; Esc volta."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one("#main-tabs", TabbedContent)

        monkeypatch.setattr(
            ytmusic,
            "search_catalog",
            lambda *args, **kwargs: SearchResult(albums=(_album_item(),)),
        )
        monkeypatch.setattr(ytmusic, "get_album", _album_details)

        await screen.perform_search("album teste")
        await pilot.pause()

        albums = screen.query_one("#search-albums", AlbumListView)
        albums.focus()
        albums.index = 0
        await pilot.press("enter")

        for _ in range(100):
            await pilot.pause(0.02)
            if screen._current_album is not None:
                break

        assert tabs.active == "tab-album"
        view = screen.query_one("#album-songs", SongListView)
        assert view.display
        # Faixa indisponível é filtrada
        assert [t.video_id for t in view.songs] == ["t1", "t2"]

        header = str(screen.query_one("#album-header", Static).render())
        assert "Álbum Teste" in header
        assert "Artista X" in header
        assert "2 faixas" in header

        # Esc na aba de álbum volta para a busca
        await pilot.press("escape")
        await pilot.pause()
        assert tabs.active == "tab-search"


@pytest.mark.asyncio
async def test_focusing_list_highlights_first_item_for_enter(monkeypatch):
    """Tab numa lista deve destacar o primeiro item para o Enter funcionar.

    Regressão: ao ganhar foco pelo teclado, o ``ListView`` ficava com
    ``index=None`` e o Enter não selecionava nada.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        monkeypatch.setattr(
            ytmusic,
            "search_catalog",
            lambda *args, **kwargs: SearchResult(
                tracks=(_song("v1", "Música 1"),),
                albums=(_album_item(),),
            ),
        )
        monkeypatch.setattr(ytmusic, "get_album", _album_details)
        await screen.perform_search("álbum teste")
        await pilot.pause()

        screen.query_one("#search-input", Input).focus()
        await pilot.pause()
        await pilot.press("tab")
        songs = screen.focused
        assert isinstance(songs, SongListView)
        assert songs.index == 0
        await pilot.press("tab")
        albums = screen.focused
        assert isinstance(albums, AlbumListView)
        assert albums.index == 0

        # Enter abre o álbum destacado (sem clique nem ↓ antes)
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause(0.02)
            if screen._current_album is not None:
                break
        assert screen.query_one("#main-tabs", TabbedContent).active == "tab-album"
        assert screen._current_album is not None


@pytest.mark.asyncio
async def test_clicking_album_result_opens_album(monkeypatch):
    """Clique do mouse num resultado de álbum abre a aba 💿 Álbum."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        monkeypatch.setattr(
            ytmusic,
            "search_catalog",
            lambda *args, **kwargs: SearchResult(albums=(_album_item(),)),
        )
        monkeypatch.setattr(ytmusic, "get_album", _album_details)
        await screen.perform_search("álbum teste")
        await pilot.pause()

        # Muda para a aba de busca: painéis ocultos não têm geometria
        # (region 0x0) e o clique do mouse cairia na posição errada.
        screen._switch_tab("tab-search")
        await pilot.pause()

        albums = screen.query_one("#search-albums", AlbumListView)
        item = albums.children[0]
        assert item.region.width > 0  # item realmente na tela
        await pilot.click(offset=(item.region.x + 2, item.region.y))

        for _ in range(100):
            await pilot.pause(0.02)
            if screen._current_album is not None:
                break
        assert screen.query_one("#main-tabs", TabbedContent).active == "tab-album"
        assert screen._current_album is not None
        assert len(screen.query_one("#album-songs", SongListView).songs) == 2


@pytest.mark.asyncio
async def test_keyboard_tab_switch_with_focus_inside_album_pane(monkeypatch):
    """Trocar de aba (Ctrl+L/Ctrl+S) com o foco dentro do painel do álbum.

    Regressão: ao esconder o painel com foco, o Textual transfere o foco para
    outro widget do mesmo painel, que reativa a aba via TabPane.Focused —
    a troca se desfazia (aba ficava presa no álbum).
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one("#main-tabs", TabbedContent)

        screen.display_album(_album_details())
        screen._switch_tab("tab-album")
        screen.query_one("#album-songs", SongListView).focus()
        await pilot.pause()
        assert tabs.active == "tab-album"

        # Ctrl+L com o foco dentro do painel do álbum
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert tabs.active == "tab-library"

        # E de novo de volta ao álbum (com foco na lista) + Ctrl+S
        tabs.active = "tab-album"
        await pilot.pause()
        screen.query_one("#album-songs", SongListView).focus()
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert tabs.active == "tab-search"


@pytest.mark.asyncio
async def test_album_tab_hidden_until_first_album(monkeypatch):
    """A aba 💿 Álbum fica oculta até algo ser aberto; o rótulo vira o nome."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        tabs = app.screen.query_one("#main-tabs", TabbedContent)
        assert "-hidden" in tabs.get_tab("tab-album").classes

        app.screen.display_album(_album_details())
        await pilot.pause()
        tab = tabs.get_tab("tab-album")
        assert "-hidden" not in tab.classes
        assert "Álbum Teste" in tab.label_text


@pytest.mark.asyncio
async def test_download_album_queues_tracks_and_skips_downloaded(monkeypatch):
    """Botão e tecla "a" baixam o álbum inteiro; o que já foi baixado é pulado."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        queued: list[dict] = []
        ensures: list[dict] = []
        monkeypatch.setattr(library, "ensure_track", lambda **kwargs: ensures.append(kwargs) or 1)
        monkeypatch.setattr(library, "get_track_by_source_id", lambda video_id: None)
        monkeypatch.setattr(
            download_manager,
            "queue_download",
            lambda **kwargs: queued.append(kwargs) or object(),
        )

        async def _noop(task):  # noqa: ANN001 - worker fake
            return None

        monkeypatch.setattr(screen, "_run_download", _noop)
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen.display_album(_album_details())
        screen.query_one("#main-tabs", TabbedContent).active = "tab-album"
        await pilot.pause()

        # Botão "Baixar álbum inteiro"
        await pilot.click("#btn-download-album")
        await pilot.pause()
        assert [q["video_id"] for q in queued] == ["t1", "t2"]
        assert all(q["album"] == "Álbum Teste" for q in queued)
        # Ordem de álbum (R2/T17.1): número 1-based e total conhecido juntos.
        assert [q["track_number"] for q in queued] == [1, 2]
        assert all(q["album_track_count"] == 2 for q in queued)
        assert [entry["track_number"] for entry in ensures] == [1, 2]
        assert all(entry["album_track_count"] == 2 for entry in ensures)
        assert any("2 faixa(s) enfileirada(s)" in t for t in toasts)

        # Tecla "a" (com foco na lista, fora de um Input), pulando a t1
        class _Downloaded:
            is_downloaded = True

        monkeypatch.setattr(
            library,
            "get_track_by_source_id",
            lambda video_id: _Downloaded() if video_id == "t1" else None,
        )
        queued.clear()
        ensures.clear()
        screen.query_one("#album-songs", SongListView).focus()
        await pilot.press("a")
        await pilot.pause()
        assert [q["video_id"] for q in queued] == ["t2"]
        assert [q["track_number"] for q in queued] == [2]
        assert any("1 já baixada(s)" in t for t in toasts)

        # Sem álbum aberto: avisa e não enfileira nada
        queued.clear()
        screen._current_album = None
        await pilot.press("a")
        await pilot.pause()
        assert queued == []
        assert any("Nenhum álbum aberto" in t for t in toasts)


# ===== T04: recuperação de downloads interrompidos na TUI (r/d) =====


@pytest.mark.asyncio
async def test_app_mount_recovers_interrupted_downloads(monkeypatch):
    """O on_mount do App chama a recuperação (T04-R2) depois da migração."""
    calls: list[int] = []
    monkeypatch.setattr(download_manager, "recover_interrupted", lambda: calls.append(1) or 0)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

    assert calls == [1]


@pytest.mark.asyncio
async def test_startup_migrates_isolated_tmp_db():
    """O on_mount migra um banco NOVO no caminho temporário, sem tocar o real.

    Regressão do isolamento (T05): nenhum teste pode abrir/migrar o banco
    real do usuário — a fixture global redireciona o ``db_manager`` para
    ``tmp_path``; aqui o arquivo é apagado de propósito para provar que o
    startup cria e estampa um banco novo do zero.
    """
    db_manager.close()
    db_manager.db_path.unlink(missing_ok=True)  # banco novo de verdade

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

    assert db_manager.db_path != settings.library_db_path
    assert db_manager.db_path.exists()
    assert db_manager.schema_version() == SCHEMA_VERSION
    assert app.db_error is None


@pytest.mark.asyncio
async def test_startup_schema_incompatible_does_not_crash(monkeypatch):
    """Erro tipado na abertura do banco não derruba o app (T05-R4)."""

    def _broken() -> None:
        raise SchemaIncompatibleError("esquema v999 é mais novo que o app")

    calls: list[int] = []
    monkeypatch.setattr(db_manager, "migrate", _broken)
    monkeypatch.setattr(download_manager, "recover_interrupted", lambda: calls.append(1) or 0)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        assert app.db_error is not None
        assert isinstance(app.db_error, SchemaIncompatibleError)
        assert calls == []  # sem esquema aberto, não tenta recuperar tarefas
        # A aba Biblioteca mostra o caso específico (T05-R4), não "vazia":
        # o usuário precisa saber que o banco é de um esquema mais novo.
        message = app.screen.query_one("#library-message", Static)
        rendered = str(message.render())
        assert message.display is True
        assert "Não foi possível carregar a biblioteca local" in rendered
        assert "não é compatível" in rendered
        assert "mais novo que o app" in rendered  # o detalhe original
        assert "nenhum arquivo é um banco válido" not in rendered
        assert "Nenhuma faixa na biblioteca local ainda" not in rendered
        assert app.screen.query_one("#library-list", LibraryListView).display is False


@pytest.mark.asyncio
async def test_main_screen_announces_interrupted_on_mount(monkeypatch):
    """A tela principal avisa, na montagem, que há decisão pendente (r/d)."""
    calls: list[int] = []
    monkeypatch.setattr(
        download_controller.DownloadController,
        "announce_interrupted",
        lambda self: calls.append(1),
    )

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

    assert calls == [1]


@pytest.mark.asyncio
async def test_announce_interrupted_toast_teaches_keys(monkeypatch):
    """Com interrompidas, o toast ensina r/d; sem elas, nada de toast."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        interrupted = DownloadTask(video_id="abc", title="Faixa", status=DownloadStatus.INTERRUPTED)
        monkeypatch.setattr(download_manager, "get_queue", lambda: [interrupted])
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen.announce_interrupted()

        assert len(toasts) == 1
        assert "1 download(s) interrompido(s)" in toasts[0]
        assert "r" in toasts[0] and "d" in toasts[0]

        toasts.clear()
        monkeypatch.setattr(download_manager, "get_queue", lambda: [])
        screen.announce_interrupted()
        assert toasts == []


@pytest.mark.asyncio
async def test_downloads_table_deduplicates_running_task(monkeypatch):
    """Regressão T04: fila + ativos repetiam a linha da tarefa RUNNING.

    ``get_queue()`` devolve a fila em memória, que ainda contém a tarefa em
    execução (ela só sai em cancel/clear): sem o dedup por ``id``, a tabela
    mostrava duas linhas — e o mapeamento linha ↔ tarefa de r/d ficava
    ambíguo.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        task = DownloadTask(video_id="abc", title="Faixa")
        task.transition_to(DownloadStatus.RUNNING)
        monkeypatch.setattr(download_manager, "get_queue", lambda: [task])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [task])

        screen.update_downloads_table()

        table = screen.query_one("#downloads-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_action_retry_download_retries_selected_interrupted(monkeypatch):
    """Tecla "r" na linha selecionada: retoma como nova tarefa e roda o worker."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        old = DownloadTask(
            video_id="abc", title="Faixa", id="task-antiga", status=DownloadStatus.INTERRUPTED
        )
        new = DownloadTask(video_id="abc", title="Faixa", id="task-nova", retry_of="task-antiga")
        monkeypatch.setattr(download_manager, "get_queue", lambda: [old])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        retried: list[str] = []
        monkeypatch.setattr(
            download_manager, "retry_interrupted", lambda task_id: retried.append(task_id) or new
        )
        started: list[DownloadTask] = []

        async def _run(task):  # noqa: ANN001 - worker fake
            started.append(task)

        monkeypatch.setattr(screen, "_run_download", _run)
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen.update_downloads_table()  # linha selecionável para o mapeamento
        screen.action_retry_download()
        await pilot.pause()

        assert retried == ["task-antiga"]
        assert started == [new]  # o fluxo completo roda como a tarefa nova
        assert any("Retomando" in t for t in toasts)


@pytest.mark.asyncio
async def test_action_discard_download_discards_selected_interrupted(monkeypatch):
    """Tecla "d" na linha selecionada: confirmação e só então descarta (T12-R4)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        old = DownloadTask(
            video_id="abc", title="Faixa", id="task-antiga", status=DownloadStatus.INTERRUPTED
        )
        monkeypatch.setattr(download_manager, "get_queue", lambda: [old])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        discarded: list[str] = []
        monkeypatch.setattr(
            download_manager,
            "discard_interrupted",
            lambda task_id: discarded.append(task_id) or [Path("/tmp/x.mp3.part")],
        )
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen.update_downloads_table()
        screen.action_discard_download()
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        assert discarded == []  # nada é removido antes do "sim"
        assert not any("descartado" in t for t in toasts)

        await pilot.press("y")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        assert discarded == ["task-antiga"]
        assert any("descartado" in t for t in toasts)
        assert "1 temporário(s) removido(s)" in toasts[-1]


@pytest.mark.parametrize("confirm_key", ["y", "enter"])
@pytest.mark.asyncio
async def test_tecla_d_abre_confirmacao_e_so_confirma_remove(monkeypatch, confirm_key):
    """T12-R4: "d" passa por modal; y ou Enter confirmam o descarte.

    Caminho real de tecla (check_action da aba 📥 + ação + modal): até o
    "sim", nenhum temporário nem linha são removidos.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        old = DownloadTask(
            video_id="abc", title="Faixa", id="task-antiga", status=DownloadStatus.INTERRUPTED
        )
        monkeypatch.setattr(download_manager, "get_queue", lambda: [old])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        discarded: list[str] = []
        monkeypatch.setattr(
            download_manager,
            "discard_interrupted",
            lambda task_id: discarded.append(task_id) or [],
        )
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen._switch_tab("tab-downloads")
        screen.update_downloads_table()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        confirm_text = str(app.screen.query_one("#confirm-message", Static).render())
        assert "Descartar download" in confirm_text
        assert "Faixa" in confirm_text
        assert discarded == []

        await pilot.press(confirm_key)
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        assert discarded == ["task-antiga"]
        assert any("descartado" in t for t in toasts)


@pytest.mark.parametrize("cancel_key", ["n", "escape"])
@pytest.mark.asyncio
async def test_descarte_cancelado_nao_remove_nada(monkeypatch, cancel_key):
    """T12-R4: n ou Esc cancelam — o manager não é chamado e um toast avisa."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        old = DownloadTask(
            video_id="abc", title="Faixa", id="task-antiga", status=DownloadStatus.INTERRUPTED
        )
        monkeypatch.setattr(download_manager, "get_queue", lambda: [old])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        discarded: list[str] = []
        monkeypatch.setattr(
            download_manager,
            "discard_interrupted",
            lambda task_id: discarded.append(task_id) or [],
        )
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen._switch_tab("tab-downloads")
        screen.update_downloads_table()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)

        await pilot.press(cancel_key)
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        assert discarded == []
        assert any("cancelado" in t.lower() for t in toasts)


@pytest.mark.asyncio
async def test_retomar_nao_tem_confirmacao(monkeypatch):
    """T12-R4: sem destruição não há modal — "r" retoma direto."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        old = DownloadTask(
            video_id="abc", title="Faixa", id="task-antiga", status=DownloadStatus.INTERRUPTED
        )
        new = DownloadTask(video_id="abc", title="Faixa", id="task-nova", retry_of="task-antiga")
        monkeypatch.setattr(download_manager, "get_queue", lambda: [old])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        retried: list[str] = []
        monkeypatch.setattr(
            download_manager, "retry_interrupted", lambda task_id: retried.append(task_id) or new
        )

        async def _run(task):  # noqa: ANN001 - worker fake
            return None

        monkeypatch.setattr(screen, "_run_download", _run)
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen._switch_tab("tab-downloads")
        screen.update_downloads_table()
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)  # nenhum modal no caminho
        assert retried == ["task-antiga"]
        assert any("Retomando" in t for t in toasts)


@pytest.mark.asyncio
async def test_retry_and_discard_require_interrupted_selection(monkeypatch):
    """r/d sem INTERRUPTED selecionada só orientam — não chamam o manager."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        running = DownloadTask(video_id="abc", title="Faixa")
        running.transition_to(DownloadStatus.RUNNING)
        monkeypatch.setattr(download_manager, "get_queue", lambda: [running])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        calls: list[str] = []
        monkeypatch.setattr(
            download_manager, "retry_interrupted", lambda task_id: calls.append(task_id)
        )
        monkeypatch.setattr(
            download_manager, "discard_interrupted", lambda task_id: calls.append(task_id)
        )
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen.update_downloads_table()
        screen.action_retry_download()
        screen.action_discard_download()

        assert calls == []
        assert len(toasts) == 2
        assert all("Selecione um download interrompido" in t for t in toasts)


@pytest.mark.asyncio
async def test_repeated_download_is_skipped_while_in_progress(monkeypatch):
    """Regressão: Ctrl+D duas vezes não pode enfileirar o mesmo vídeo de novo
    (dois downloads disputariam o mesmo arquivo de saída e o mesmo .part)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        queued: list[dict] = []
        monkeypatch.setattr(library, "ensure_track", lambda **kwargs: 1)
        monkeypatch.setattr(
            download_manager,
            "queue_download",
            lambda **kwargs: queued.append(kwargs) or object(),
        )
        # O vídeo passa a contar como "em andamento" após o primeiro Ctrl+D
        in_flight: set[str] = set()
        monkeypatch.setattr(
            download_manager,
            "is_active_or_pending",
            lambda video_id, **kwargs: video_id in in_flight,
        )

        async def _noop(task):  # noqa: ANN001 - worker fake
            return None

        monkeypatch.setattr(screen, "_run_download", _noop)
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        monkeypatch.setattr(
            player,
            "_current_track",
            TrackInfo(video_id="abc123", title="Faixa", artist="Artista"),
        )

        screen.query_one("#search-input", Input).focus()
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert len(queued) == 1

        in_flight.add("abc123")
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert len(queued) == 1  # não duplicou
        assert any("Já na fila" in t for t in toasts)


@pytest.mark.asyncio
async def test_download_album_reports_tracks_in_progress(monkeypatch):
    """Faixas já em andamento não são reenfileiradas e entram no resumo."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        queued: list[dict] = []
        monkeypatch.setattr(library, "ensure_track", lambda **kwargs: 1)
        monkeypatch.setattr(library, "get_track_by_source_id", lambda video_id: None)
        monkeypatch.setattr(
            download_manager,
            "queue_download",
            lambda **kwargs: queued.append(kwargs) or object(),
        )
        monkeypatch.setattr(
            download_manager,
            "is_active_or_pending",
            lambda video_id, **kwargs: True,
        )
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen.download_album(_album_details())
        await pilot.pause()

        assert queued == []
        assert any("2 já em andamento" in t for t in toasts)


@pytest.mark.asyncio
async def test_library_refresh_twice_with_tracks(monkeypatch):
    """Regressão: atualizar a biblioteca duas vezes criava DuplicateIds."""

    class _Album:
        id = 1
        title = "Álbum [ao vivo]"
        release_year = None
        artists: list = []

    entry = LibraryAlbum(album=_Album(), track_count=2, missing_count=0)
    monkeypatch.setattr(library, "list_library_albums", lambda limit=500: [entry, entry])
    monkeypatch.setattr(library, "list_standalone_tracks", lambda limit=500: [])
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.refresh_library()
        await pilot.pause()
        await screen.refresh_library().wait()
        await pilot.pause()  # deixa o DOM assentar (clear/append são mensagens)

        view = screen.query_one("#library-list", LibraryListView)
        assert len(view.children) == 2
        assert view.display  # lista visível
        assert not screen.query_one("#library-message", Static).display


@pytest.mark.asyncio
async def test_library_load_error_is_shown_not_empty(monkeypatch, caplog):
    """Regressão: erro ao carregar a biblioteca não pode parecer "vazia".

    Antes, qualquer exceção virava uma lista vazia — quem estivesse com o
    banco inacessível acharia que perdeu as faixas baixadas.
    """

    def _broken(limit=500):
        raise RuntimeError("banco inacessível [Errno 5]")

    monkeypatch.setattr(library, "list_library_albums", _broken)
    app = YTMusicTUI()
    with caplog.at_level(logging.ERROR, logger="ytmusic_tui"):
        async with app.run_test(size=(120, 40)):
            screen = app.screen
            await screen.refresh_library().wait()
            message = screen.query_one("#library-message", Static)

            assert message.display is True
            rendered = str(message.render())
            assert "Não foi possível carregar a biblioteca" in rendered
            assert "banco inacessível [Errno 5]" in rendered  # colchetes ok
            # A mensagem de "biblioteca vazia" não pode aparecer no lugar
            assert "Nenhuma faixa na biblioteca local ainda" not in rendered
            assert screen.query_one("#library-list", LibraryListView).display is False

    # O erro fica registrado no log (nível ERROR, com a exceção)
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert any(
        "Falha ao carregar a biblioteca local" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_library_recovers_after_load_error(monkeypatch, caplog):
    """Depois de uma falha, um refresh com sucesso restaura lista e texto padrão."""

    class _Track:
        id = 1
        source_id = "vid1"
        title = "Faixa"
        artist_names = ["Artista"]
        is_downloaded = False
        local_path = None
        album = None
        is_missing = False

    flaky = {"fail": True}

    def _flaky_singles(limit=500):
        if flaky["fail"]:
            raise RuntimeError("boom")
        return [_Track()]

    monkeypatch.setattr(library, "list_library_albums", lambda limit=500: [])
    monkeypatch.setattr(library, "list_standalone_tracks", _flaky_singles)
    app = YTMusicTUI()
    with caplog.at_level(logging.ERROR, logger="ytmusic_tui"):
        async with app.run_test(size=(120, 40)):
            screen = app.screen
            await screen.refresh_library().wait()
            message = screen.query_one("#library-message", Static)
            assert "Não foi possível carregar" in str(message.render())

            flaky["fail"] = False
            await screen.refresh_library().wait()

            assert "Nenhuma faixa na biblioteca local ainda" in str(message.render())
            assert message.display is False
            view = screen.query_one("#library-list", LibraryListView)
            assert view.display is True
            # Seção "— Faixas avulsas —" + a faixa
            assert len(view.children) == 2
            assert [track.title for track in view.singles] == ["Faixa"]


@pytest.mark.asyncio
async def test_library_shows_specific_message_for_typed_error(monkeypatch, caplog):
    """Erro tipado na consulta mostra a mensagem específica do caso (R4)."""

    def _corrupt(limit=500):
        raise DatabaseCorruptError("o arquivo não é um banco válido ou está corrompido")

    monkeypatch.setattr(library, "list_library_albums", _corrupt)
    app = YTMusicTUI()
    with caplog.at_level(logging.ERROR, logger="ytmusic_tui"):
        async with app.run_test(size=(120, 40)):
            screen = app.screen
            await screen.refresh_library().wait()
            message = screen.query_one("#library-message", Static)

            assert message.display is True
            rendered = str(message.render())
            assert "Não foi possível carregar a biblioteca local" in rendered
            assert "corrompido" in rendered
            assert "restaure um backup" in rendered  # ação específica do caso
            assert "Nenhuma faixa na biblioteca local ainda" not in rendered
            assert screen.query_one("#library-list", LibraryListView).display is False

    assert any(
        "Falha ao carregar a biblioteca local" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_library_nivel_1_albuns_e_avulsas(monkeypatch):
    """T17.1/R6: nível 1 = álbuns (título·artista·N faixas) + avulsas."""
    for vid, title, album in (
        ("A1", "B do álbum", "Zeta"),
        ("A4", "C do álbum", "Zeta"),
        ("A2", "A do álbum", "Alfa"),
        ("A3", "Avulsa única", ""),
    ):
        assert library.ensure_track(vid, title, "Artista", album) is not None

    played: list[TrackInfo] = []
    monkeypatch.setattr(player, "play", lambda info: played.append(info) or True)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        # Ordem fixada no serviço (casefold): Alfa antes de Zeta.
        assert [item.album.title for item in view.albums] == ["Alfa", "Zeta"]
        assert [item.track_count for item in view.albums] == [1, 2]
        assert [track.title for track in view.singles] == ["Avulsa única"]

        labels = " | ".join(str(child.query_one(Label).render()) for child in view.children)
        assert "💿 Alfa — Artista  (1 faixa)" in labels
        assert "💿 Zeta — Artista  (2 faixas)" in labels
        assert "— Faixas avulsas —" in labels
        assert "Avulsa única" in labels

        # Enter numa avulsa toca (fila = avulsas visíveis, posição 3).
        view.focus()
        await pilot.pause()
        view.index = 3  # 0/1 = álbuns, 2 = seção, 3 = avulsa
        await pilot.press("enter")
        await pilot.pause()
        assert [info.title for info in played] == ["Avulsa única"]
        assert [info.title for info in screen._play_queue] == ["Avulsa única"]


@pytest.mark.asyncio
async def test_library_modo_album_expande_toca_e_volta(monkeypatch):
    """T17.1/R6: Enter expande o álbum; Enter toca com fila em ordem; Esc/← volta."""
    for vid, title, number in (
        ("N1", "Terceira", 3),
        ("N2", "Primeira", 1),
        ("N3", "Segunda", 2),
    ):
        assert (
            library.ensure_track(vid, title, "Artista", "Álbum Ordenado", track_number=number)
            is not None
        )

    played: list[TrackInfo] = []
    monkeypatch.setattr(player, "play", lambda info: played.append(info) or True)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        await pilot.press("enter")
        await screen.refresh_library().wait()
        await pilot.pause()

        header = screen.query_one("#library-album-header", Static)
        tracks = screen.query_one("#library-album-tracks", TrackListView)
        assert header.display is True
        rendered = str(header.render())
        assert "Álbum Ordenado" in rendered
        assert "Artista" in rendered
        assert "3 faixas" in rendered
        assert [track.title for track in tracks.tracks] == ["Primeira", "Segunda", "Terceira"]

        # "t" toca o álbum desde a primeira faixa, com a fila em ordem (R6).
        await pilot.press("t")
        await pilot.pause()
        assert [info.title for info in played] == ["Primeira"]
        assert [info.title for info in screen._play_queue] == ["Primeira", "Segunda", "Terceira"]
        assert screen._play_index == 0

        # Enter na 2ª faixa: toca com a fila NA ORDEM do álbum.
        tracks.index = 1
        await pilot.press("enter")
        await pilot.pause()
        assert played[-1].title == "Segunda"
        assert [info.title for info in screen._play_queue] == ["Primeira", "Segunda", "Terceira"]
        assert screen._play_index == 1

        # ← volta ao nível 1 (mesmo caminho do Esc — desvio documentado).
        await pilot.press("left")
        await screen.refresh_library().wait()
        await pilot.pause()
        assert view.display is True and header.display is False

        # Esc também volta quando o álbum reabre.
        await pilot.press("enter")
        await screen.refresh_library().wait()
        await pilot.pause()
        assert header.display is True
        await pilot.press("escape")
        await screen.refresh_library().wait()
        await pilot.pause()
        assert view.display is True and header.display is False


@pytest.mark.asyncio
async def test_library_ausente_visivel_na_lista_no_album_e_legenda():
    """T17.1/R7: ausente marcada nos dois níveis, legenda honesta e sem crash."""
    ids = {
        vid: library.ensure_track(vid, title, "Artista", "Álbum Ausente")
        for vid, title in (("M1", "Sumida"), ("M2", "Presente"))
    }
    ids["M3"] = library.ensure_track("M3", "Avulsa Sumida")
    with library.db.session() as session:
        # Marcadas como a reconciliação faria (§5.3): arquivo não encontrado.
        session.get(DbTrack, ids["M1"]).is_missing = True
        session.get(DbTrack, ids["M3"]).is_missing = True

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        labels = " | ".join(str(child.query_one(Label).render()) for child in view.children)
        assert "⚠ 1 ausente" in labels  # álbum com uma faixa ausente
        assert "⚠ Avulsa Sumida" in labels  # avulsa ausente
        legend = screen.query_one("#library-legend", Static)
        assert legend.display is True
        assert "última reconciliação" in str(legend.render())

        # No álbum: marca na faixa ausente; a presente fica limpa.
        view.focus()
        await pilot.pause()
        await pilot.press("enter")
        await screen.refresh_library().wait()
        await pilot.pause()
        tracks = screen.query_one("#library-album-tracks", TrackListView)
        labels = " | ".join(str(child.query_one(Label).render()) for child in tracks.children)
        assert "⚠ Sumida" in labels
        assert "⚠ Presente" not in labels
        assert screen.query_one("#library-legend", Static).display is True


@pytest.mark.asyncio
async def test_abas_abrem_na_busca_com_playlists_primeiro():
    """T17.1/R3: app abre na Busca; ordem das abas começa com 🎵 Playlists."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabbed = screen.query_one("#main-tabs", TabbedContent)
        assert tabbed.active == "tab-search"

        tabs = tabbed.query_one(Tabs)
        ids = [str(tab.id) for tab in tabs.query("Tab") if tab.id]

        def _index(fragment: str) -> int:
            return next(i for i, tab_id in enumerate(ids) if fragment in tab_id)

        assert _index("tab-playlists") < _index("tab-search")
        assert _index("tab-search") < _index("tab-album") < _index("tab-library")
        assert _index("tab-library") < _index("tab-downloads") < _index("tab-settings")
        # A aba de álbum existe, mas começa oculta (só abre pela busca).
        album_tab = tabbed.get_tab("tab-album")
        assert album_tab is not None and album_tab.has_class("-hidden")


@pytest.mark.asyncio
async def test_playlists_nivel_1_cria_abre_e_volta(monkeypatch):
    """T17.1/R3: nível 1 (nome · N faixas), Enter abre, fila em ordem, Esc/← voltam."""
    playlist = library.create_playlist("Minha Mix")
    for index in (1, 2, 3):
        track_id = library.ensure_track(f"P{index}", f"Faixa {index}", "Artista")
        assert track_id is not None
        assert library.add_track_to_playlist(playlist.id, track_id) is True

    played: list[TrackInfo] = []
    monkeypatch.setattr(player, "play", lambda info: played.append(info) or True)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabbed = screen.query_one("#main-tabs", TabbedContent)
        tabbed.active = "tab-playlists"
        await screen.refresh_playlists().wait()
        await pilot.pause()

        view = screen.query_one("#playlists-list", PlaylistListView)
        assert view.display is True
        assert [str(item.name) for item in view.playlists] == ["Minha Mix"]
        label = str(view.children[0].query_one(Label).render())
        assert label == "Minha Mix · 3 faixas"

        view.focus()
        await pilot.pause()
        await pilot.press("enter")
        await screen.refresh_playlists().wait()
        await pilot.pause()

        header = screen.query_one("#playlist-header", Static)
        tracks = screen.query_one("#playlist-tracks", TrackListView)
        assert header.display is True
        assert "Minha Mix" in str(header.render())
        assert "3 faixas" in str(header.render())
        assert [track.title for track in tracks.tracks] == ["Faixa 1", "Faixa 2", "Faixa 3"]

        # Enter na 2ª faixa: fila na ordem da playlist, começando nela.
        tracks.index = 1
        await pilot.press("enter")
        await pilot.pause()
        assert [info.title for info in played] == ["Faixa 2"]
        assert [info.title for info in screen._play_queue] == ["Faixa 1", "Faixa 2", "Faixa 3"]
        assert screen._play_index == 1

        # ← volta (mesmo caminho do Esc); Esc volta depois de reabrir.
        await pilot.press("left")
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert view.display is True and header.display is False

        await pilot.press("enter")
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert header.display is True
        await pilot.press("escape")
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert view.display is True and header.display is False


@pytest.mark.asyncio
async def test_playlist_criar_renomear_excluir_com_modais(monkeypatch):
    """T17.1/R3: N cria, E renomeia, Delete exclui com confirmação; Esc cancela."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        # Fora da aba Playlists as teclas novas ficam inativas (check_action).
        await pilot.press("n")
        await pilot.pause()
        assert app.screen is screen

        tabbed = screen.query_one("#main-tabs", TabbedContent)
        tabbed.active = "tab-playlists"
        await screen.refresh_playlists().wait()
        await pilot.pause()

        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        # N abre o modal; nome vazio é recusado sem fechar.
        await pilot.press("n")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, NameInputScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NameInputScreen)
        assert "não pode ficar vazio" in str(modal.query_one("#name-error", Static).render())

        # Digita o nome e confirma.
        await pilot.press(*"Minha", "space", *"Lista")
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is screen
        await screen.refresh_playlists().wait()
        await pilot.pause()

        view = screen.query_one("#playlists-list", PlaylistListView)
        assert [str(item.name) for item in view.playlists] == ["Minha Lista"]
        assert any("Playlist criada: Minha Lista" in toast for toast in toasts)

        # Esc no modal cancela sem criar uma segunda playlist.
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await screen.refresh_playlists().wait()
        assert [str(item.name) for item in view.playlists] == ["Minha Lista"]

        # E renomeia; o campo abre com o nome atual.
        view.focus()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, NameInputScreen)
        input_widget = modal.query_one("#name-input", Input)
        assert input_widget.value == "Minha Lista"
        input_widget.value = "Renomeada"
        await pilot.press("enter")
        await pilot.pause()
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert [str(item.name) for item in view.playlists] == ["Renomeada"]
        assert any("Playlist renomeada: Renomeada" in toast for toast in toasts)

        # Delete pede confirmação; "n" cancela sem excluir.
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await pilot.pause()
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert [str(item.name) for item in view.playlists] == ["Renomeada"]

        # "y" confirma; a lista volta ao estado vazio próprio (não é erro).
        await pilot.press("delete")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert view.playlists == []
        assert any("Playlist excluída: Renomeada" in toast for toast in toasts)
        message = screen.query_one("#playlists-message", Static)
        assert message.display is True
        assert "Nenhuma playlist ainda" in str(message.render())


@pytest.mark.asyncio
async def test_playlist_remover_e_mover_faixas(monkeypatch):
    """T17.1/R3: Shift+↑/↓ movem numa transação; Delete remove faixa no detalhe."""
    playlist = library.create_playlist("Ordenada")
    for index in (1, 2, 3):
        track_id = library.ensure_track(f"M{index}", f"Faixa {index}", "Artista")
        assert track_id is not None
        library.add_track_to_playlist(playlist.id, track_id)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#main-tabs", TabbedContent).active = "tab-playlists"
        await screen.refresh_playlists().wait()
        await pilot.pause()

        view = screen.query_one("#playlists-list", PlaylistListView)
        view.focus()
        await pilot.pause()
        await pilot.press("enter")
        await screen.refresh_playlists().wait()
        await pilot.pause()

        tracks_view = screen.query_one("#playlist-tracks", TrackListView)
        assert [track.title for track in tracks_view.tracks] == [
            "Faixa 1",
            "Faixa 2",
            "Faixa 3",
        ]

        # Shift+↓ move a 1ª (selecionada) para baixo; a seleção acompanha.
        tracks_view.index = 0
        await pilot.press("shift+down")
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert [track.title for track in tracks_view.tracks] == [
            "Faixa 2",
            "Faixa 1",
            "Faixa 3",
        ]
        assert tracks_view.index == 1
        stored = library.get_playlist(playlist.id)
        assert stored is not None
        ordered = sorted(stored.tracks, key=lambda entry: entry.position)
        assert [entry.track.title for entry in ordered] == ["Faixa 2", "Faixa 1", "Faixa 3"]

        # Shift+↑ devolve ao topo.
        await pilot.press("shift+up")
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert [track.title for track in tracks_view.tracks] == [
            "Faixa 1",
            "Faixa 2",
            "Faixa 3",
        ]
        assert tracks_view.index == 0

        # Shift+↑ no topo: aviso, ordem intacta.
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        await pilot.press("shift+up")
        await pilot.pause()
        assert any("topo" in toast for toast in toasts)
        assert [track.title for track in tracks_view.tracks] == [
            "Faixa 1",
            "Faixa 2",
            "Faixa 3",
        ]

        # Delete remove a faixa selecionada da playlist; a biblioteca fica.
        await pilot.press("delete")
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert [track.title for track in tracks_view.tracks] == ["Faixa 2", "Faixa 3"]
        assert any("Faixa removida da playlist: Faixa 1" in toast for toast in toasts)
        stored = library.get_playlist(playlist.id)
        assert stored is not None
        assert sorted(entry.track.title for entry in stored.tracks) == ["Faixa 2", "Faixa 3"]
        assert library.get_track_by_source_id("M1") is not None  # só o vínculo saiu
        assert "2 faixas" in str(screen.query_one("#playlist-header", Static).render())


@pytest.mark.asyncio
async def test_playlists_vazio_e_erro_distintos(monkeypatch):
    """T17.1/R3: falha de leitura nunca aparece como "nenhuma playlist"."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#main-tabs", TabbedContent).active = "tab-playlists"
        await screen.refresh_playlists().wait()
        await pilot.pause()

        message = screen.query_one("#playlists-message", Static)
        assert message.display is True
        assert "Nenhuma playlist ainda" in str(message.render())

        def _broken():
            raise RuntimeError("banco inacessível [Errno 5]")

        monkeypatch.setattr(library, "list_playlists", _broken)
        await screen.refresh_playlists().wait()

        rendered = str(message.render())
        assert "Não foi possível carregar as playlists" in rendered
        assert "banco inacessível [Errno 5]" in rendered  # colchetes ok
        assert "Nenhuma playlist ainda" not in rendered
        assert screen.query_one("#playlists-list", PlaylistListView).display is False


@pytest.mark.asyncio
async def test_playlist_faixa_ausente_marcada_e_protegida(monkeypatch, tmp_path):
    """T17.1/R7: ausente marcada também na playlist; tocar avisa sem crash."""
    playlist = library.create_playlist("Com Ausente")
    present = library.ensure_track("PL1", "Presente")
    missing = library.ensure_track("PL2", "Sumida", "Artista")
    assert present is not None and missing is not None
    library.add_track_to_playlist(playlist.id, present)
    library.add_track_to_playlist(playlist.id, missing)
    with library.db.session() as session:
        row = session.get(DbTrack, missing)
        assert row is not None
        row.is_missing = True
        row.is_downloaded = True
        row.local_path = str(tmp_path / "sumida.mp3")  # nunca criado no disco

    played: list[TrackInfo] = []
    monkeypatch.setattr(player, "play", lambda info: played.append(info) or True)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#main-tabs", TabbedContent).active = "tab-playlists"
        await screen.refresh_playlists().wait()
        await pilot.pause()
        view = screen.query_one("#playlists-list", PlaylistListView)
        view.focus()
        await pilot.pause()
        await pilot.press("enter")
        await screen.refresh_playlists().wait()
        await pilot.pause()

        tracks = screen.query_one("#playlist-tracks", TrackListView)
        labels = " | ".join(str(child.query_one(Label).render()) for child in tracks.children)
        assert "⚠ Sumida" in labels
        assert "⚠ Presente" not in labels
        legend = screen.query_one("#playlist-legend", Static)
        assert legend.display is True
        assert "última reconciliação" in str(legend.render())

        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        tracks.index = 1  # a faixa ausente
        await pilot.press("enter")
        await pilot.pause()
        assert played == []  # o player nem foi acionado
        assert any("não está mais no disco" in toast for toast in toasts)


def test_bindings_sem_conflito_de_teclas():
    """T17.1/R4: nenhuma tecla duplicada na mesma tela; p/P nas ações certas."""
    keys: dict[str, list[str]] = {}
    for binding in MainScreen.BINDINGS:
        keys.setdefault(binding.key, []).append(binding.action)
    clashes = {key: actions for key, actions in keys.items() if len(actions) > 1}
    assert clashes == {}

    by_key = {binding.key: binding.action for binding in MainScreen.BINDINGS}
    assert by_key["p"] == "add_to_playlist"
    assert by_key["P"] == "add_album_to_playlist"


@pytest.mark.asyncio
async def test_p_adiciona_faixa_selecionada_da_biblioteca(monkeypatch):
    """T17.1/R4: p usa a faixa da Biblioteca (avulsa e no modo álbum), sem duplicar."""
    alvo = library.create_playlist("Alvo")
    assert library.ensure_track("AV1", "Avulsa", "Artista") is not None
    assert library.ensure_track("AB1", "Um", "Artista", "Álbum P") is not None
    assert library.ensure_track("AB2", "Dois", "Artista", "Álbum P") is not None

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        screen.query_one("#main-tabs", TabbedContent).active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        # Nível 1: álbum (0), seção de avulsas (1), avulsa (2).
        view.index = 2
        await pilot.press("p")
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, PlaylistPickerScreen)
        assert "faixa" in str(picker.query_one("#picker-title", Static).render()).lower()
        await pilot.press("enter")  # primeira playlist da lista
        await pilot.pause()

        assert any("➕ Avulsa → Alvo" in toast for toast in toasts)
        stored = library.get_playlist(alvo.id)
        assert stored is not None
        assert [entry.track.title for entry in stored.tracks] == ["Avulsa"]

        # De novo: já estava; continua só uma entrada (sem duplicata).
        toasts.clear()
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert any("Já estava em «Alvo»: Avulsa" in toast for toast in toasts)
        stored = library.get_playlist(alvo.id)
        assert stored is not None
        assert [entry.track.title for entry in stored.tracks] == ["Avulsa"]

        # Modo álbum: p usa a faixa destacada do álbum aberto.
        view.index = 0
        await pilot.press("enter")
        await screen.refresh_library().wait()
        await pilot.pause()
        tracks = screen.query_one("#library-album-tracks", TrackListView)
        tracks.index = 1
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        await pilot.press("enter")
        await pilot.pause()
        stored = library.get_playlist(alvo.id)
        assert stored is not None
        assert sorted(entry.track.title for entry in stored.tracks) == ["Avulsa", "Dois"]


@pytest.mark.asyncio
async def test_p_usa_faixa_atual_fora_da_biblioteca(monkeypatch):
    """T17.1/R4: espelho do l — fora da Biblioteca vale a faixa tocando.

    Sem nada tocando: recado de "nenhuma faixa". Tocando algo que não está
    na biblioteca local: "baixe primeiro" — sem abrir picker vazio.
    """
    alvo = library.create_playlist("Alvo")
    assert library.ensure_track("CUR1", "Tocando", "Artista") is not None

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        monkeypatch.setattr(player, "_current_track", None)

        # Na Busca (sem seleção da Biblioteca) e sem reprodução: avisa.
        await pilot.press("p")
        await pilot.pause()
        assert any("Nenhuma faixa em reprodução" in toast for toast in toasts)
        assert not isinstance(app.screen, PlaylistPickerScreen)

        # Tocando uma faixa que está na biblioteca: p adiciona a atual.
        monkeypatch.setattr(
            player,
            "_current_track",
            TrackInfo(video_id="CUR1", title="Tocando", artist="Artista"),
        )
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        await pilot.press("enter")
        await pilot.pause()
        stored = library.get_playlist(alvo.id)
        assert stored is not None
        assert [entry.track.title for entry in stored.tracks] == ["Tocando"]

        # Tocando algo fora da biblioteca local: baixe primeiro, sem picker.
        monkeypatch.setattr(
            player,
            "_current_track",
            TrackInfo(video_id="FORA", title="Só no YouTube", artist="Artista"),
        )
        toasts.clear()
        await pilot.press("p")
        await pilot.pause()
        assert any("Baixe a faixa primeiro" in toast for toast in toasts)
        assert not isinstance(app.screen, PlaylistPickerScreen)


@pytest.mark.asyncio
async def test_pickar_nova_playlist_cria_e_adiciona(monkeypatch):
    """T17.1/R4: "➕ Nova playlist…" encadeia o nome e já adiciona a faixa."""
    assert library.ensure_track("NV1", "Faixa Nova", "Artista") is not None

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        screen.query_one("#main-tabs", TabbedContent).active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        view.index = 1  # sem álbuns: seção (0) + avulsa (1)
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        # Sem playlists, a lista traz só "➕ Nova playlist…" (índice 0).
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NameInputScreen)
        await pilot.press(*"Criada", "space", *"Agora")
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is screen
        assert any("Playlist criada: Criada Agora" in toast for toast in toasts)
        assert any("➕ Faixa Nova → Criada Agora" in toast for toast in toasts)
        playlists_view = screen.query_one("#playlists-list", PlaylistListView)
        await screen.refresh_playlists().wait()
        await pilot.pause()
        assert [str(item.name) for item in playlists_view.playlists] == ["Criada Agora"]
        stored = library.get_playlist(playlists_view.playlists[0].id)
        assert stored is not None
        assert [entry.track.title for entry in stored.tracks] == ["Faixa Nova"]


@pytest.mark.asyncio
async def test_esc_do_picker_cancela_sem_adicionar(monkeypatch):
    """T17.1/R4: Esc no picker não cria nem adiciona nada."""
    alvo = library.create_playlist("Alvo")
    assert library.ensure_track("ES1", "Faixa", "Artista") is not None

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#main-tabs", TabbedContent).active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        view.index = 1  # seção (0) + avulsa (1)
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen
        stored = library.get_playlist(alvo.id)
        assert stored is not None
        assert stored.tracks == []


@pytest.mark.asyncio
async def test_shift_p_album_para_playlist_resumo_honesto(monkeypatch):
    """T17.1/R5: resumo verdadeiro (adicionadas/já estavam/fora da biblioteca)."""
    alvo = library.create_playlist("Alvo")
    for number, title in ((1, "Um"), (2, "Dois")):
        assert (
            library.ensure_track(f"HP{number}", title, "Artista", "Honesto", track_number=number)
            is not None
        )

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        screen.query_one("#main-tabs", TabbedContent).active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        # Album.track_count conhecido (3): a parcela K aparece no recado.
        with library.db.session() as session:
            row = session.execute(select(DbAlbum).where(DbAlbum.title == "Honesto")).scalar_one()
            row.track_count = 3

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        view.index = 0  # só o álbum (sem avulsas)
        await pilot.press("enter")
        await screen.refresh_library().wait()
        await pilot.pause()

        await pilot.press("P")
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, PlaylistPickerScreen)
        assert "álbum" in str(picker.query_one("#picker-title", Static).render()).lower()
        await pilot.press("enter")
        await pilot.pause()

        summary = next(toast for toast in toasts if "Álbum" in toast)
        assert "2 adicionadas" in summary
        assert "0 já estavam" in summary
        assert "1 fora da biblioteca (baixe para incluí-la)" in summary
        stored = library.get_playlist(alvo.id)
        assert stored is not None
        ordered = sorted(stored.tracks, key=lambda entry: entry.position)
        assert [entry.track.title for entry in ordered] == ["Um", "Dois"]

        # Repetindo: nada novo entra, e o recado não maquia.
        toasts.clear()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        summary = next(toast for toast in toasts if "Álbum" in toast)
        assert "0 adicionadas" in summary
        assert "2 já estavam" in summary

        # Sem track_count (None): não dá para afirmar K — sai do recado.
        with library.db.session() as session:
            row = session.execute(select(DbAlbum).where(DbAlbum.title == "Honesto")).scalar_one()
            row.track_count = None
        toasts.clear()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        summary = next(toast for toast in toasts if "Álbum" in toast)
        assert "0 adicionadas" in summary
        assert "fora da biblioteca" not in summary


@pytest.mark.asyncio
async def test_shift_p_album_destacado_adiciona_album_inteiro(monkeypatch):
    """BUG-01: shift+P com o álbum só destacado (sem Enter) adiciona o álbum."""
    alvo = library.create_playlist("Alvo")
    for number, title in ((1, "Um"), (2, "Dois")):
        assert (
            library.ensure_track(f"BD{number}", title, "Artista", "Destacado", track_number=number)
            is not None
        )

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        screen.query_one("#main-tabs", TabbedContent).active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        view.index = 0  # álbum destacado — sem Enter, o álbum não abre
        await pilot.press("P")
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, PlaylistPickerScreen)
        assert "álbum" in str(picker.query_one("#picker-title", Static).render()).lower()
        await pilot.press("enter")
        await pilot.pause()

        summary = next(toast for toast in toasts if "Álbum" in toast)
        assert "2 adicionadas" in summary
        stored = library.get_playlist(alvo.id)
        assert stored is not None
        ordered = sorted(stored.tracks, key=lambda entry: entry.position)
        assert [entry.track.title for entry in ordered] == ["Um", "Dois"]


@pytest.mark.asyncio
async def test_shift_p_sem_album_alvo_mostra_dica(monkeypatch):
    """BUG-01: sem álbum alvo, o Shift+P explica o gesto em vez de silenciar."""
    assert library.ensure_track("AV1", "Avulsa", "Artista") is not None

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        screen.query_one("#main-tabs", TabbedContent).active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        view.index = len(view.albums) + 1  # pula a seção "Faixas avulsas"
        await pilot.press("P")
        await pilot.pause()
        assert app.screen is screen  # nenhum seletor abriu
        assert any("álbum" in toast.lower() for toast in toasts)


@pytest.mark.asyncio
async def test_shift_p_nada_a_adicionar_mensagem_clara(monkeypatch):
    """T17.1/R5: nada a adicionar tem mensagem própria (não é silêncio)."""
    library.create_playlist("Alvo")
    assert library.ensure_track("VAZ1", "Única", "Artista", "Vazio", track_number=1) is not None

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        screen.query_one("#main-tabs", TabbedContent).active = "tab-library"
        await screen.refresh_library().wait()
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        view.focus()
        await pilot.pause()
        view.index = 0
        await pilot.press("enter")
        await screen.refresh_library().wait()
        await pilot.pause()

        # Simula a corrida: as faixas sumiram da biblioteca entre a leitura e o add.
        monkeypatch.setattr(
            library,
            "add_album_to_playlist",
            lambda playlist_id, album_id: AlbumPlaylistResult(added=0, already=0, album_total=3),
        )
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert any(
            "Nada a adicionar de «Vazio»" in toast and "3 faixas fora da biblioteca" in toast
            for toast in toasts
        )


def test_bindings_resolve_to_actions():
    """Todo atalho anunciado deve ter uma action correspondente."""
    for owner in (MainScreen, YTMusicTUI, HelpScreen):
        for binding in owner.BINDINGS:
            action = f"action_{binding.action}"
            assert (
                hasattr(owner, action) or hasattr(MainScreen, action) or hasattr(YTMusicTUI, action)
            ), f"{owner.__name__}:{binding.key}"


def test_format_key_traduz_teclas_para_ajuda():
    """A ajuda exibe teclas legíveis, não a nomenclatura interna do Textual."""
    assert format_key("ctrl+q") == "Ctrl+Q"
    assert format_key("space") == "Espaço"
    assert format_key("left") == "←"
    assert format_key("right") == "→"
    assert format_key("f1") == "F1"
    assert format_key("l") == "L"


@pytest.mark.asyncio
async def test_queue_next_previous_and_auto_play(monkeypatch):
    """Regressão C4: next/previous/track_ended devem mover a fila tocada."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        played: list[TrackInfo] = []

        def _fake_play(info: TrackInfo) -> bool:
            played.append(info)
            return True

        monkeypatch.setattr(player, "play", _fake_play)

        screen._play_queue = [TrackInfo(video_id=str(i), title=f"F{i}") for i in range(3)]
        screen._play_index = 0

        assert screen._play_relative(1) is True
        assert played[-1].video_id == "1"
        assert screen._play_relative(1) is True
        assert screen._play_relative(1) is False  # fim da fila
        assert screen._play_relative(-1) is True
        assert played[-1].video_id == "1"

        # Evento "next" emitido pelo player (atalho/botão) avança a fila
        app._on_player_event(PlayerEvent(event="next", data={}))
        assert played[-1].video_id == "2"

        # Fim da faixa (eof) com auto-play ligado
        screen._play_index = 0
        screen._last_auto_next = 0.0
        app._on_player_event(PlayerEvent(event="track_ended", data={}))
        assert played[-1].video_id == "1"

        # Debounce: "ended"/"track_ended" duplicado não avança de novo
        count = len(played)
        app._on_player_event(PlayerEvent(event="track_ended", data={}))
        assert len(played) == count


def test_ensure_audio_file_raises_only_for_missing_registered_file(tmp_path):
    """Só faixa com arquivo registrado ausente levanta o erro tipado (R4)."""
    missing = tmp_path / "sumiu.mp3"
    existing = tmp_path / "existe.mp3"
    existing.write_bytes(b"audio")

    with pytest.raises(AudioFileMissingError, match="arquivo não encontrado"):
        ensure_audio_file(SimpleNamespace(is_downloaded=True, local_path=str(missing)))
    # Arquivo presente: nada a fazer.
    ensure_audio_file(SimpleNamespace(is_downloaded=True, local_path=str(existing)))
    # Sem download registrado, ``local_path`` não é tocado (streaming normal).
    ensure_audio_file(SimpleNamespace(is_downloaded=False, local_path=str(missing)))
    ensure_audio_file(SimpleNamespace(is_downloaded=True, local_path=None))


@pytest.mark.asyncio
async def test_play_library_track_missing_file_toasts_and_skips_play(monkeypatch, tmp_path):
    """Arquivo registrado que sumiu do disco: toast específico, sem play (R4)."""
    played: list[TrackInfo] = []
    monkeypatch.setattr(player, "play", lambda info: played.append(info) or True)

    track = SimpleNamespace(
        id=1,
        source_id="vid-sumiu",
        title="Sumiu",
        artist_names=["Artista"],
        is_downloaded=True,
        local_path=str(tmp_path / "sumiu.mp3"),  # nunca criado no disco
        album=None,
    )

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        screen.play_library_track(track)

        assert played == []  # o player nem foi acionado
        assert any("não está mais no disco" in toast for toast in toasts)
        assert any("sumiu.mp3" in toast for toast in toasts)  # detalhe do caminho


@pytest.mark.asyncio
async def test_play_library_track_with_existing_file_calls_player(monkeypatch, tmp_path):
    """Arquivo presente: a reprodução segue normal (a checagem não bloqueia)."""
    media = tmp_path / "Existe.mp3"
    media.write_bytes(b"audio")
    played: list[TrackInfo] = []
    monkeypatch.setattr(player, "play", lambda info: played.append(info) or True)

    track = SimpleNamespace(
        id=1,
        source_id="vid-existe",
        title="Existe",
        artist_names=["Artista"],
        is_downloaded=True,
        local_path=str(media),
        album=None,
    )

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        app.screen.play_library_track(track)

        assert len(played) == 1
        assert played[0].source_url == str(media)


@pytest.mark.asyncio
async def test_settings_inputs_apply(monkeypatch, tmp_path):
    """Campos da aba Config devem aplicar e persistir (antes: decorativos)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        saved: dict = {}
        monkeypatch.setattr(
            type(settings),
            "update_and_save",
            lambda self, **kwargs: saved.update(kwargs),
        )

        screen._apply_text_setting("setting-volume", "30")
        assert saved["mpv_volume"] == 30

        screen._apply_text_setting("setting-concurrent", "2")
        assert saved["download_concurrent"] == 2

        # A UI normaliza ANTES da API (T06-R4): a API recusa fora de 1–10.
        screen._apply_text_setting("setting-concurrent", "42")
        assert saved["download_concurrent"] == 10
        screen._apply_text_setting("setting-concurrent", "0")
        assert saved["download_concurrent"] == 1

        target = tmp_path / "downloads"
        screen._apply_text_setting("setting-download-dir", str(target))
        assert saved["download_dir"] == target
        assert target.exists()

        # Valor inválido não quebra a UI
        saved.clear()
        screen._apply_text_setting("setting-volume", "abc")
        assert "mpv_volume" not in saved


@pytest.mark.asyncio
async def test_settings_dir_error_toast_masks_home(monkeypatch, tmp_path):
    """OSError na Config vira toast mascarado (§8.3) e não derruba a TUI.

    C6/§12.2: falha ao **salvar** responde em 3 partes ("erro ao salvar" da
    tabela de estados); falha ao **usar** o diretório (mkdir) mantém a
    mensagem específica do diretório.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        def _boom(self, **kwargs):
            raise OSError(f"disco cheio ao gravar {Path.home()}/.config/config.toml")

        monkeypatch.setattr(type(settings), "update_and_save", _boom)
        screen._apply_text_setting("setting-download-dir", str(tmp_path / "downloads"))

        joined = "\n".join(toasts)
        assert "Não foi possível salvar a configuração" in joined
        assert "não foi gravada" in joined  # parte 2: o que foi preservado
        assert "Verifique permissões" in joined  # parte 3: próxima ação
        assert str(Path.home()) not in joined
        assert "~/.config" in joined

        # Diretório impossível (arquivo no caminho) → mensagem do diretório.
        toasts.clear()
        blocker = tmp_path / "bloqueado"
        blocker.write_text("x", encoding="utf-8")
        screen._apply_text_setting("setting-download-dir", str(blocker / "sub"))

        joined = "\n".join(toasts)
        assert "Não foi possível usar o diretório" in joined


@pytest.mark.asyncio
async def test_config_tab_widgets_apply_on_interaction(monkeypatch):
    """Wiring real: Enter no input de volume e toggle do checkbox salvam."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#main-tabs", TabbedContent).active = "tab-settings"
        await pilot.pause()

        saved: dict = {}
        monkeypatch.setattr(
            type(settings),
            "update_and_save",
            lambda self, **kwargs: saved.update(kwargs),
        )

        volume = screen.query_one("#setting-volume", Input)
        volume.query_ancestor(Collapsible).collapsed = False
        await pilot.pause()
        volume.focus()
        volume.value = "35"
        await pilot.press("enter")
        await pilot.pause()
        assert saved.get("mpv_volume") == 35

        autoplay = screen.query_one("#setting-autoplay", Checkbox)
        autoplay.value = not autoplay.value
        await pilot.pause()
        assert "auto_play_next" in saved


@pytest.mark.asyncio
async def test_app_starts_directly_on_main_screen():
    """Sem login, o app deve abrir direto na tela principal, sem modal de login."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        # Topo da pilha é a MainScreen (a base _default fica abaixo)
        assert app.screen is app.screen_stack[-1]


@pytest.mark.asyncio
async def test_quit_does_not_hang():
    """Regressão: shutdown do download manager bloqueava o Ctrl+Q."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.return_code == 0


def test_ui_callbacks_tolerate_no_screen_on_stack():
    """Regressão do flake do teardown: timer pode ticar sem tela na pilha (T08).

    Na corrida baseline do T08, o ``set_interval`` de 0,5 s de
    ``_refresh_downloads_ui`` disparou entre o fechamento das telas e o
    ``stop`` do timer em ``on_unmount``: ``self.screen`` levantou
    ``ScreenStackError`` e o ``run_test`` falhou com a exceção do timer.
    Chamar os callbacks direto com a pilha vazia reproduz o instante exato,
    sem depender do agendamento do Textual.
    """
    app = YTMusicTUI()
    app._refresh_downloads_ui()
    app._handle_player_event(PlayerEvent(event="position", data={"position": 1, "duration": 3}))


@pytest.mark.asyncio
async def test_unmount_deregistra_o_callback_do_player():
    """T11-R2: o app desmontado sai da lista de callbacks do player.

    Sem isso, o callback de uma TUI fechada ficaria registrado no singleton:
    um novo ``start()`` (testes, novo mount no mesmo processo) voltaria a
    entregar eventos a ela — "callback morto chamado".
    """
    app = YTMusicTUI()
    async with app.run_test():
        assert app._on_player_event in player._callbacks
    assert app._on_player_event not in player._callbacks


@pytest.mark.asyncio
async def test_tabs_are_clickable_with_mouse():
    """Regressão: a regra catch-all `*:focus { border: ... }` do app.tcss dava
    borda ao `Tabs` quando ele ganhava foco com o clique; a borda estourava as
    2 linhas internas (rótulos + underline), o Underline caía por baixo do
    TabPane e as abas ficavam inalcançáveis ("barra azul sobrepondo o menu")."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabbed = screen.query_one("#main-tabs", TabbedContent)
        tabs = tabbed.query_one(Tabs)

        labels_row = tabs.query("Tab").first().region.y
        underline = tabs.query_one("Underline")
        underline_before = underline.region
        # O underline deve ficar imediatamente abaixo dos rótulos
        assert underline_before.y == labels_row + 1

        for tab_id, fragment in (
            ("tab-library", "Biblioteca"),
            ("tab-downloads", "Downloads"),
            ("tab-playlists", "Playlists"),
        ):
            tab = [t for t in tabs.query("Tab") if t.id and tab_id in t.id][0]
            await pilot.click(offset=(tab.region.x + 2, labels_row))
            await pilot.pause()
            assert tabbed.active == tab_id, fragment

        # Nenhum clique pode ter deformado a geometria das abas
        assert tabs.query_one("Underline").region == underline_before


@pytest.mark.asyncio
async def test_ctrl_d_downloads_even_with_search_input_focused(monkeypatch):
    """Regressão: o `Input` do Textual captura `ctrl+d` (`delete_right`); o
    binding de download precisa de `priority` para disparar mesmo com o foco
    na busca (era o caso de uso real: buscar → tocar → Ctrl+D)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        # Pipeline de download de verdade, mas sem rede/banco
        monkeypatch.setattr(library, "ensure_track", lambda **kwargs: 1)
        queued: list[dict] = []
        monkeypatch.setattr(
            download_manager,
            "queue_download",
            lambda **kwargs: queued.append(kwargs) or object(),
        )

        async def _noop(task):  # noqa: ANN001 - worker fake
            return None

        monkeypatch.setattr(screen, "_run_download", _noop)
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        monkeypatch.setattr(
            player,
            "_current_track",
            TrackInfo(video_id="abc123", title="Faixa", artist="Artista"),
        )

        search = screen.query_one("#search-input", Input)
        search.focus()
        search.value = "abc"
        await pilot.pause()

        await pilot.press("ctrl+d")
        await pilot.pause()
        assert len(queued) == 1
        assert queued[0]["video_id"] == "abc123"
        assert any("Iniciando download" in msg for msg in toasts)
        # O atalho tem prioridade: o Input não deve apagar caractere algum
        assert search.value == "abc"

        # Sem faixa em reprodução: avisa em vez de ignorar em silêncio
        monkeypatch.setattr(player, "_current_track", None)
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert len(queued) == 1
        assert "Nenhuma faixa em reprodução" in toasts


@pytest.mark.asyncio
async def test_download_failure_shows_toast_with_error(monkeypatch):
    """Falha de download vira toast com o erro do yt-dlp; a UI segue viva."""
    app = YTMusicTUI()

    async def _failing_download(task):
        # Só transições válidas: o status é escrito pela máquina, nunca à mão.
        task.transition_to(DownloadStatus.RUNNING)
        task.transition_to(DownloadStatus.FAILED)
        task.error = "Video unavailable"
        return task

    monkeypatch.setattr(download_manager, "download", _failing_download)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        await screen._run_download(DownloadTask(video_id="abc", title="Faixa"))

        assert any("Falha no download" in t and "Video unavailable" in t for t in toasts)


@pytest.mark.asyncio
async def test_download_failure_toast_masks_home_and_signed_url(monkeypatch, tmp_path):
    """Ponta a ponta (§8.3/T06): erro do yt-dlp chega à TUI mascarado.

    O worker REAL do gerenciador roda com um yt-dlp falso que falha com o
    tipo de mensagem que o yt-dlp de verdade produz (caminho absoluto do
    HOME + URL assinada com token). Nem o toast nem ``task.error`` (fonte
    da tabela de downloads) podem conter um nem outro; o contexto segue
    legível para o usuário entender a falha.
    """

    class _FailingYdl(FakeYoutubeDL):
        def download(self, urls: list[str]) -> None:
            raise FakeDownloadError(
                "ERROR: unable to download video data: HTTP Error 403: Forbidden "
                f"(arquivo: {Path.home()}/Music/f.mp3) "
                "url=https://rr1---sn-xyz.googlevideo.com/videoplayback?expire=1&sig=SEGREDO123"
            )

    monkeypatch.setattr(download_mod, "_load_yt_dlp", lambda: fake_yt_dlp(_FailingYdl))
    monkeypatch.setattr(settings, "download_dir", tmp_path / "library")
    # Testes anteriores desta suíte encerram o app e derrubam o executor do
    # singleton; o teste e2e injeta um executor próprio e vivo.
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(download_manager, "_executor", executor)

    app = YTMusicTUI()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            toasts: list[str] = []
            monkeypatch.setattr(screen, "_toast", toasts.append)

            task = download_manager.queue_download(
                video_id="abc", title="Faixa", artist="Artista", album="Álbum"
            )
            await screen._run_download(task)
    finally:
        executor.shutdown(wait=True)

    joined = "\n".join(toasts)
    assert "Falha no download" in joined
    assert str(Path.home()) not in joined
    assert "SEGREDO123" not in joined
    assert "googlevideo.com" in joined  # contexto continua legível

    assert task.status is DownloadStatus.FAILED
    assert task.error is not None
    assert str(Path.home()) not in task.error
    assert "SEGREDO123" not in task.error


@pytest.mark.asyncio
async def test_quit_with_in_flight_download_does_not_hang(monkeypatch):
    """Encerrar o app com um download em andamento não pode travar a saída."""
    app = YTMusicTUI()

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_download(task):
        started.set()
        await release.wait()
        return task

    monkeypatch.setattr(download_manager, "download", _slow_download)
    monkeypatch.setattr(library, "ensure_track", lambda **kwargs: 1)
    monkeypatch.setattr(download_manager, "is_active_or_pending", lambda video_id, **kwargs: False)
    monkeypatch.setattr(
        download_manager,
        "queue_download",
        lambda **kwargs: DownloadTask(video_id="abc", title="Faixa"),
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        screen._enqueue_download(TrackInfo(video_id="abc", title="Faixa"))
        await asyncio.wait_for(started.wait(), timeout=5)  # download iniciou

        await pilot.press("ctrl+q")
        await pilot.pause()
        release.set()  # destrava o worker caso o cancelamento não o pegue

    assert app.return_code == 0


@pytest.mark.asyncio
async def test_on_mount_starts_reconciliation(monkeypatch):
    """O ``on_mount`` da tela principal dispara a reconciliação (§5.3)."""
    calls: list[bool] = []
    monkeypatch.setattr(
        library_controller.LibraryController,
        "start_reconciliation",
        lambda self: calls.append(True),
    )

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

    assert calls == [True]


@pytest.mark.asyncio
async def test_reconciliation_findings_show_toast_and_refresh(monkeypatch):
    """Achados da reconciliação viram toast e atualizam a biblioteca."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        report = ReconciliationReport(
            imported_files=[Path("/tmp/orfao.mp3")],
            removed_temps=[Path("/tmp/velho.part")],
        )
        monkeypatch.setattr(library_controller, "reconcile", lambda *args, **kwargs: report)
        toasts: list[str] = []
        refreshed: list[bool] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        monkeypatch.setattr(screen, "refresh_library", lambda: refreshed.append(True))

        await screen._run_reconciliation()

        assert len(toasts) == 1
        assert toasts[0].startswith("🧹 Reconciliação:")
        assert "importado" in toasts[0] and "temporário" in toasts[0]
        assert refreshed == [True]


@pytest.mark.asyncio
async def test_reconciliation_without_findings_is_quiet(monkeypatch):
    """Sem achados não há toast nem refresh (inicialização silenciosa)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        monkeypatch.setattr(
            library_controller, "reconcile", lambda *args, **kwargs: ReconciliationReport()
        )
        toasts: list[str] = []
        refreshed: list[bool] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        monkeypatch.setattr(screen, "refresh_library", lambda: refreshed.append(True))

        await screen._run_reconciliation()

        assert toasts == []
        assert refreshed == []


@pytest.mark.asyncio
async def test_mpv_indisponivel_vira_toast_sem_traceback(monkeypatch):
    """T11-R3: libmpv ausente → toast "Erro no player: …" (ponta da UI).

    O start falha sob demanda (handle nunca criado), o evento ``error``
    chega ao app e a barra entra em falha — nada de exceção subindo para o
    loop da TUI.
    """

    class _Explode:
        def __init__(self, **kwargs):
            raise OSError("sem libmpv")

    player.shutdown()  # nenhum handle herdado de outro teste
    monkeypatch.setattr(player_mod.mpv, "MPV", _Explode)

    app = YTMusicTUI()
    async with app.run_test() as pilot:
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        await pilot.pause()

        assert player.play(TrackInfo(video_id="v", source_url="http://s/a.mp3")) is False

        assert toasts and toasts[0].startswith("Erro no player:")
        assert "mpv indisponível" in toasts[0]
        # T12-R5: o toast do player também responde as 3 perguntas
        assert "A biblioteca e os downloads não foram afetados." in toasts[0]
        assert "Escolha outra faixa" in toasts[0]
        bar = screen.query_one("#now-playing", NowPlayingBar)
        assert "falha" in str(bar.query_one("#np-info", Static).render())


@pytest.mark.asyncio
async def test_help_modal_lista_atalhos_reais_e_fecha_com_esc():
    """T12-R1: F1 abre ajuda modal montada dos bindings; Esc fecha.

    A fonte da lista é ``App.BINDINGS + MainScreen.BINDINGS`` — nenhuma
    cópia própria para ficar desatualizada (era o toast com texto fixo).
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()

        help_screen = app.screen
        assert isinstance(help_screen, HelpScreen)
        content = str(help_screen.query_one("#help-content", Static).render())
        for binding in [*YTMusicTUI.BINDINGS, *MainScreen.BINDINGS]:
            assert format_key(binding.key) in content, binding.key
        # Navegação básica documentada (R1) e sem duplicar tecla na lista
        for token in ("↑/↓", "Tab", "Enter", "Esc"):
            assert token in content, token
        assert content.count("Ctrl+Q") == 1

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)


@pytest.mark.asyncio
async def test_f1_tambem_fecha_a_ajuda():
    """F1 alterna a ajuda (Esc e F1 fecham a modal)."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)


@pytest.mark.asyncio
async def test_digitar_na_busca_nao_dispara_atalho_global(monkeypatch):
    """T12-R1: letras/espaço no campo de busca digitam — nunca disparam ação.

    ``a``, ``l``, ``d``, ``r`` e ``space`` são atalhos GLOBAIS da tela; o
    foco no ``Input`` precisa consumi-los como texto (era o risco de buscar
    "adele" e baixar um álbum no meio da digitação).
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        paused: list[bool] = []
        monkeypatch.setattr(player, "toggle_pause", lambda: paused.append(True))

        search = screen.query_one("#search-input", Input)
        search.focus()
        await pilot.pause()
        await pilot.press("a", "l", "d", "r", "space")
        await pilot.pause()

        assert search.value == "aldr "
        assert toasts == []
        assert paused == []

        # Ctrl+L continua sendo atalho (tecla com modificador) e não mexe
        # no texto digitado.
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert screen.query_one("#main-tabs", TabbedContent).active == "tab-library"
        assert search.value == "aldr "


@pytest.mark.asyncio
async def test_r_e_d_so_valem_na_aba_downloads(monkeypatch):
    """T12-R1: r/d restritos à aba 📥 Downloads via ``check_action``.

    Fora da aba o atalho fica inativo (nem toast, nem manager); dentro
    dela o comportamento de sempre continua (orientar sem seleção válida).
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        calls: list[str] = []
        monkeypatch.setattr(
            download_manager, "retry_interrupted", lambda task_id: calls.append(task_id)
        )
        monkeypatch.setattr(
            download_manager, "discard_interrupted", lambda task_id: calls.append(task_id)
        )
        monkeypatch.setattr(download_manager, "get_queue", lambda: [])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])

        screen._switch_tab("tab-search")
        await pilot.pause()
        await pilot.press("r")
        await pilot.press("d")
        await pilot.pause()
        assert toasts == []
        assert calls == []

        screen._switch_tab("tab-downloads")
        await pilot.pause()
        await pilot.press("r")
        await pilot.press("d")
        await pilot.pause()
        assert sum("Selecione um download interrompido" in t for t in toasts) == 2
        assert calls == []


@pytest.mark.asyncio
async def test_navegacao_completa_sem_mouse(monkeypatch):
    """T12-R1: buscar → tocar → biblioteca → sair, só com teclado."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one("#main-tabs", TabbedContent)

        await pilot.press("ctrl+s")
        await pilot.pause()
        search = screen.query_one("#search-input", Input)
        assert search.has_focus

        monkeypatch.setattr(
            ytmusic,
            "search_catalog",
            lambda *args, **kwargs: SearchResult(tracks=(_song("v1", "Faixa teclado"),)),
        )
        for key in "abc":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause(0.05)

        songs = screen.query_one("#search-songs", SongListView)
        assert len(songs.songs) == 1

        played: list[TrackInfo] = []
        monkeypatch.setattr(player, "play", lambda info: played.append(info) or True)
        await pilot.press("tab")
        await pilot.pause()
        assert songs.has_focus
        await pilot.press("enter")
        await pilot.pause()
        assert [info.video_id for info in played] == ["v1"]

        await pilot.press("ctrl+l")
        await pilot.pause()
        assert tabs.active == "tab-library"

        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_busca_antiga_nao_pisa_na_mais_recente(monkeypatch):
    """T12-R2: A (lenta) conclui depois de B (rápida) — a tela segue com B.

    A conclusão fora de ordem é forçada com fakes controlados por evento:
    A fica presa no ``to_thread`` enquanto B completa e pinta; quando A
    destrava, a época antiga faz o resultado ser descartado.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        release = threading.Event()

        def _search(query, songs_limit=25, albums_limit=15, should_abort=None):
            if query == "lenta":
                release.wait(timeout=5)
                return SearchResult(tracks=(_song("velha", "Resposta antiga"),))
            return SearchResult(tracks=(_song("nova", "Resposta nova"),))

        monkeypatch.setattr(ytmusic, "search_catalog", _search)

        task_a = asyncio.create_task(screen.perform_search("lenta"))
        await pilot.pause(0.02)  # A já está presa no to_thread
        await screen.perform_search("rápida")
        await pilot.pause()

        songs = screen.query_one("#search-songs", SongListView)
        assert [s.video_id for s in songs.songs] == ["nova"]

        release.set()
        await asyncio.wait_for(task_a, timeout=5)
        await pilot.pause()

        assert [s.video_id for s in songs.songs] == ["nova"]
        assert screen._search_epoch == 2


@pytest.mark.asyncio
async def test_erro_de_busca_antiga_nao_apaga_resultado_novo(monkeypatch):
    """T12-R2: a falha de uma busca abandonada não pinta a tela."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        release = threading.Event()

        def _search(query, songs_limit=25, albums_limit=15, should_abort=None):
            if query == "lenta":
                release.wait(timeout=5)
                raise RuntimeError("rede caiu")
            return SearchResult(tracks=(_song("nova", "Resposta nova"),))

        monkeypatch.setattr(ytmusic, "search_catalog", _search)

        task_a = asyncio.create_task(screen.perform_search("lenta"))
        await pilot.pause(0.02)
        await screen.perform_search("rápida")
        await pilot.pause()

        release.set()
        await asyncio.wait_for(task_a, timeout=5)
        await pilot.pause()

        songs = screen.query_one("#search-songs", SongListView)
        message = screen.query_one("#search-message", Static)
        assert [s.video_id for s in songs.songs] == ["nova"]
        assert message.display is False
        assert "Erro na busca" not in str(message.render())


# ===== T12-R5: erros em 3 partes =====


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (
            NetworkError(
                f'busca "teste": falha de rede: conexão recusada em {Path.home()}/.cache/x '
                "(url https://exemplo.com/x?token=segredo#frag)"
            ),
            "falha de rede",
        ),
        (
            RateLimitError(
                'busca "teste": limite de requisições do YouTube Music atingido — '
                "tente de novo em instantes"
            ),
            "limite de requisições",
        ),
        (
            InvalidResponseError(
                'busca "teste": resposta fora do contrato do ytmusicapi '
                "(possível mudança de formato): KeyError('contents')"
            ),
            "resposta fora do contrato",
        ),
    ],
)
@pytest.mark.asyncio
async def test_erro_de_busca_sai_em_3_partes_mascarado(monkeypatch, error, detail):
    """T12-R5: rede, limite e resposta inválida no mesmo formato de 3 partes.

    A mensagem diz (1) o que falhou, (2) o que foi preservado e (3) a próxima
    ação; o detalhe técnico sai mascarado (§8.3) e nunca vira traceback.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        def _search(query, songs_limit=25, albums_limit=15, should_abort=None):
            raise error

        monkeypatch.setattr(ytmusic, "search_catalog", _search)

        await screen.perform_search("teste")
        await pilot.pause()

        message = screen.query_one("#search-message", Static)
        assert message.display is True
        rendered = str(message.render())

        assert "Erro na busca" in rendered
        assert detail in rendered
        assert "Nada foi baixado nem apagado." in rendered  # parte 2
        assert "Tente de novo" in rendered  # parte 3
        assert "Traceback" not in rendered
        # §8.3: HOME vira ~ e a URL perde query/fragment
        assert str(Path.home()) not in rendered
        assert "token=segredo" not in rendered
        assert "#frag" not in rendered


@pytest.mark.asyncio
async def test_erro_de_album_sai_em_3_partes(monkeypatch):
    """T12-R5: falha ao carregar o álbum explica o que fazer sem perder nada."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        def _get_album(browse_id):
            raise InvalidResponseError("get_album: resposta fora do contrato do ytmusicapi")

        monkeypatch.setattr(ytmusic, "get_album", _get_album)
        captured: list = []
        monkeypatch.setattr(screen, "run_worker", lambda worker, **kwargs: captured.append(worker))

        screen.open_album("MPREb-x")
        await captured[0]
        await pilot.pause()

        rendered = str(screen.query_one("#album-message", Static).render())
        assert "Erro ao carregar álbum" in rendered
        assert "Nada foi baixado nem apagado." in rendered
        assert "Escolha outro álbum" in rendered


@pytest.mark.asyncio
async def test_falha_de_download_sai_em_3_partes(monkeypatch):
    """T12-R5: falha de download unifica o aviso do T11 no formato de 3 partes."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        async def _download(task):
            task.transition_to(DownloadStatus.RUNNING)
            task.error = "Video unavailable"
            task.transition_to(DownloadStatus.FAILED)
            return task

        monkeypatch.setattr(download_manager, "download", _download)
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        task = DownloadTask(video_id="abc", title="Faixa", id="task-fail")
        await screen._run_download(task)

        assert len(toasts) == 1
        assert toasts[0].startswith("❌ Falha no download: Video unavailable")
        assert "Nada foi publicado na biblioteca" in toasts[0]
        assert "tente baixar de novo" in toasts[0]


# ===== T12 §12.2: estados por tela =====


@pytest.mark.asyncio
async def test_estados_da_busca(monkeypatch):
    """§12.2: inicial, carregando, resultados, vazia e erro na aba Busca."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        message = screen.query_one("#search-message", Static)
        songs = screen.query_one("#search-songs", SongListView)

        # inicial: dica visível e listas ocultas (on_mount)
        assert message.display and "Digite algo" in str(message.render())
        assert not songs.display

        # carregando: busca presa no to_thread até o teste liberar
        release = threading.Event()

        def _search(*args, **kwargs):
            release.wait(timeout=5)
            return SearchResult(tracks=(_song("v1", "Música"),))

        monkeypatch.setattr(ytmusic, "search_catalog", _search)
        task = asyncio.create_task(screen.perform_search("teste"))
        for _ in range(100):
            await pilot.pause(0.01)
            if message.display and "Buscando" in str(message.render()):
                break
        assert "Buscando" in str(message.render())
        assert not songs.display
        release.set()
        await asyncio.wait_for(task, timeout=5)
        await pilot.pause()

        # resultados: listas visíveis e mensagem oculta
        assert not message.display
        assert songs.display and [s.video_id for s in songs.songs] == ["v1"]

        # vazia: mensagem explica, sem listas
        monkeypatch.setattr(ytmusic, "search_catalog", lambda *a, **k: SearchResult())
        await screen.perform_search("nada")
        await pilot.pause()
        assert message.display and "Nenhum resultado encontrado" in str(message.render())
        assert not songs.display

        # erro: 3 partes (T12-R5)
        def _fail(*args, **kwargs):
            raise NetworkError("busca: falha de rede")

        monkeypatch.setattr(ytmusic, "search_catalog", _fail)
        await screen.perform_search("quebrada")
        await pilot.pause()
        rendered = str(message.render())
        assert "Erro na busca" in rendered
        assert "Nada foi baixado nem apagado." in rendered
        assert not songs.display


@pytest.mark.asyncio
async def test_estados_da_aba_downloads(monkeypatch):
    """§12.2: fila vazia, ativa, concluída e com falhas na aba Downloads."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        table = screen.query_one("#downloads-table", DataTable)
        message = screen.query_one("#downloads-message", Static)

        # vazia: dica visível, tabela oculta
        monkeypatch.setattr(download_manager, "get_queue", lambda: [])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        screen.update_downloads_table()
        await pilot.pause()
        assert message.display and "Nenhum download ainda" in str(message.render())
        assert not table.display

        # ativa
        active = DownloadTask(video_id="a", title="Em curso", id="t-ativa")
        active.transition_to(DownloadStatus.RUNNING)
        monkeypatch.setattr(download_manager, "get_queue", lambda: [active])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [active])
        screen.update_downloads_table()
        await pilot.pause()
        assert not message.display
        assert table.display and table.row_count == 1
        assert "⬇️" in str(table.get_row_at(0))

        # concluída + com falhas: as duas linhas convivem na tabela
        done = DownloadTask(video_id="b", title="Feita", id="t-ok")
        for status in (DownloadStatus.RUNNING, DownloadStatus.VALIDATING, DownloadStatus.COMPLETED):
            done.transition_to(status)
        done.outcome = DownloadOutcome.EXISTING_ADOPTED  # R8: adotada do disco
        failed = DownloadTask(video_id="c", title="Quebrada", id="t-fail")
        failed.transition_to(DownloadStatus.RUNNING)
        failed.transition_to(DownloadStatus.FAILED)
        failed.error = "sem rede"

        monkeypatch.setattr(download_manager, "get_queue", lambda: [done, failed])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        screen.update_downloads_table()
        await pilot.pause()
        assert table.row_count == 2
        joined = str([table.get_row_at(i) for i in range(2)])
        assert "✅" in joined and "❌" in joined
        # T17.1/R8: o desfecho da adoção tem coluna própria ("Já existia").
        assert "Já existia" in str(table.get_row_at(0))
        assert "Já existia" not in str(table.get_row_at(1))


@pytest.mark.asyncio
async def test_desfecho_ja_existia_tem_toast_proprio(monkeypatch):
    """T17.1/R8: concluído por adoção (Q2a) não se confunde com baixado agora."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        async def _fake_download(task):  # worker fake: a tarefa já terminou
            return task

        monkeypatch.setattr(download_manager, "download", _fake_download)
        monkeypatch.setattr(download_manager, "get_queue", lambda: [])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [])
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        adopted = DownloadTask(video_id="d", title="Veio do disco")
        for status in (
            DownloadStatus.RUNNING,
            DownloadStatus.VALIDATING,
            DownloadStatus.COMPLETED,
        ):
            adopted.transition_to(status)
        adopted.outcome = DownloadOutcome.EXISTING_ADOPTED

        await screen._run_download(adopted)
        assert any("Já existia" in t and "sem baixar" in t for t in toasts)

        # Download de verdade continua com o toast normal.
        downloaded = DownloadTask(video_id="e", title="Baixada")
        for status in (
            DownloadStatus.RUNNING,
            DownloadStatus.VALIDATING,
            DownloadStatus.COMPLETED,
        ):
            downloaded.transition_to(status)
        await screen._run_download(downloaded)
        assert any("Download concluído" in t for t in toasts)


@pytest.mark.asyncio
async def test_estados_da_barra_do_player(monkeypatch):
    """§12.2: parado, carregando, tocando, pausado e falha na barra."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        monkeypatch.setattr(app.screen, "_toast", lambda message: None)

        bar = app.screen.query_one("#now-playing", NowPlayingBar)
        info = bar.query_one("#np-info", Static)
        btn = bar.query_one("#btn-play-pause", Button)

        # carregando: evento emitido antes de abrir o stream
        app._handle_player_event(PlayerEvent(event="loading"))
        assert "carregando" in str(info.render())
        assert "⏳" in str(btn.label)

        # tocando
        track = TrackInfo(video_id="v1", title="Faixa Teste", artist="Artista")
        app._handle_player_event(PlayerEvent(event="play", data={"track": track}))
        assert "Faixa Teste" in str(info.render())
        assert "⏸" in str(btn.label)

        # pausado
        app._handle_player_event(PlayerEvent(event="pause"))
        assert "⏸" not in str(btn.label)

        # falha (visível até o próximo estado)
        app._handle_player_event(PlayerEvent(event="error", data={"message": "boom"}))
        assert "falha" in str(info.render())
        assert "⚠" in str(btn.label)

        # parado
        app._handle_player_event(PlayerEvent(event="stop"))
        assert "Faixa Teste" not in str(info.render())
        assert "⏸" not in str(btn.label) and "⚠" not in str(btn.label)


@pytest.mark.asyncio
async def test_barra_podada_no_teardown_nao_quebra(monkeypatch):
    """Q20/T17.1: barra presente com filhos podados não estoura ``NoMatches``.

    Força a janela exata do teardown (flake do L2, run 39132588234): a
    árvore já foi podada — a ``NowPlayingBar`` segue na pilha, os filhos
    não — e a fila de mensagens ainda drena. Um evento do player processado
    nesse instante estourava ``NoMatches`` em ``#btn-play-pause`` e derrubava
    o app. A barra precisa ignorar updates quando já não tem filhos.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        monkeypatch.setattr(screen, "_toast", lambda message: None)
        bar = screen.query_one("#now-playing", NowPlayingBar)

        # O que o teardown faz primeiro: os filhos da barra saem (ela fica).
        await bar.remove_children()
        assert len(bar.query("#btn-play-pause")) == 0

        # Mensagem NA FILA (o evento "resume" do flake) + os demais estados.
        app.post_message(PlayerEventMessage(PlayerEvent(event="resume", data={})))
        await pilot.pause(0.1)
        app._handle_player_event(PlayerEvent(event="loading", data={}))
        app._handle_player_event(
            PlayerEvent(event="play", data={"track": TrackInfo(video_id="v1", title="T")})
        )
        app._handle_player_event(
            PlayerEvent(event="position", data={"position": 1.0, "duration": 2.0})
        )
        app._handle_player_event(PlayerEvent(event="pause", data={}))
        app._handle_player_event(PlayerEvent(event="stop", data={}))
        app._handle_player_event(PlayerEvent(event="error", data={"message": "boom"}))


@pytest.mark.asyncio
async def test_player_event_message_descartado_no_fechamento(monkeypatch):
    """Q20/T17.1: com o app fechando, a mensagem na fila é descartada.

    O guarda corta o trabalho cedo: durante o teardown não há UI para
    atualizar — o evento tardio nem chega a tocar a barra.
    """
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        entregues: list[PlayerEvent] = []
        monkeypatch.setattr(app, "_handle_player_event", entregues.append)
        monkeypatch.setattr(app, "_is_shutting_down", lambda: True, raising=False)

        app.on_player_event_message(PlayerEventMessage(PlayerEvent(event="resume")))

        assert entregues == []


@pytest.mark.asyncio
async def test_config_nao_salva_no_mount_mas_salva_alteracao_real(monkeypatch):
    """§12.2 Config: abrir não salva nada (guard); alteração real salva na hora.

    Sem o guard, o ``Changed`` de mount dos Select/Checkbox salvava o TOML e
    avisava à toa — a decisão é "salva a cada alteração real, sem estado
    pendente". O erro ao salvar vira toast em 3 partes mascarado.
    """
    saved: list[dict] = []
    monkeypatch.setattr(
        type(settings), "update_and_save", lambda self, **kwargs: saved.append(kwargs)
    )

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)

        # mount: os Changed iniciais não são alteração real
        assert saved == []

        # mesma escolha de novo: continua sem gravar nem avisar
        same = SimpleNamespace(
            select=SimpleNamespace(id="setting-format"), value=settings.download_format
        )
        screen.on_select_changed(same)
        assert saved == [] and toasts == []

        # alteração real: grava e avisa
        changed = SimpleNamespace(select=SimpleNamespace(id="setting-format"), value="flac")
        screen.on_select_changed(changed)
        assert saved[-1] == {"download_format": "flac"}
        assert toasts and "Formato de download: flac" in toasts[0]

        # erro ao salvar: toast em 3 partes, mascarado, TUI de pé
        def _boom(self, **kwargs):
            raise OSError(f"disco cheio em {Path.home()}/.config/config.toml")

        monkeypatch.setattr(type(settings), "update_and_save", _boom)
        toasts.clear()
        quality = SimpleNamespace(select=SimpleNamespace(id="setting-audio-quality"), value="best")
        screen.on_select_changed(quality)
        assert "Não foi possível salvar a configuração" in toasts[0]
        assert "não foi gravada" in toasts[0]
        assert "~/.config" in toasts[0] and str(Path.home()) not in toasts[0]


# ===== T12-R6: redimensionamento e Unicode =====


@pytest.mark.parametrize("size", [(80, 24), (60, 20), (120, 40), (200, 50)])
@pytest.mark.asyncio
async def test_layout_utilizavel_em_varios_tamanhos(size):
    """T12-R6: 80×24, 60×20, 120×40 e 200×50 sem estourar a tela.

    "Utilizável" = áreas principais presentes e nenhum widget visível da
    barra de reprodução fora dos limites do terminal (a barra é a candidata
    natural a estouro: info + controles + progresso + volume numa linha).
    """
    app = YTMusicTUI()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        screen = app.screen
        width = size[0]

        assert screen.query_one("#main-tabs", TabbedContent).display
        assert screen.query_one("#search-input", Input).display

        bar = screen.query_one("#now-playing", NowPlayingBar)
        assert bar.display
        for widget in bar.query("Static, Button, ProgressBar, Horizontal"):
            if not widget.display:
                continue
            region = widget.region
            assert region.x >= 0, f"#{widget.id} x={region.x} (tela {size})"
            assert region.x + region.width <= width, (
                f"#{widget.id} estourou: {region} (tela {size})"
            )


@pytest.mark.asyncio
async def test_titulos_unicode_em_listas_barra_e_tabela(monkeypatch):
    """T12-R6: emoji/CJK/[colchetes] não quebram listas, barra e tabela."""
    exotic = "曲 名前 🎵 [LIVE] — テスト"
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        # Lista de resultados da busca
        monkeypatch.setattr(
            ytmusic,
            "search_catalog",
            lambda *a, **k: SearchResult(
                tracks=(_song("v1", exotic, artist="アーティスト [BANDA]"),)
            ),
        )
        await screen.perform_search("unicode")
        await pilot.pause()

        view = screen.query_one("#search-songs", SongListView)
        assert [s.video_id for s in view.songs] == ["v1"]
        label = str(view.children[0].query_one(Label).render())
        assert "曲 名前" in label and "[BANDA]" in label

        # Barra de reprodução
        bar = screen.query_one("#now-playing", NowPlayingBar)
        bar.update_track(TrackInfo(video_id="v1", title=exotic, artist="アーティスト [BANDA] 🎸"))
        rendered = str(bar.query_one("#np-info", Static).render())
        assert "曲 名前" in rendered and "[LIVE]" in rendered

        # Tabela de downloads
        task = DownloadTask(video_id="v1", title=exotic, id="t-uni")
        task.transition_to(DownloadStatus.RUNNING)
        monkeypatch.setattr(download_manager, "get_queue", lambda: [task])
        monkeypatch.setattr(download_manager, "get_active_downloads", lambda: [task])
        screen.update_downloads_table()
        await pilot.pause()

        table = screen.query_one("#downloads-table", DataTable)
        assert table.row_count == 1
        assert "曲 名前" in str(table.get_row_at(0))


@pytest.mark.asyncio
async def test_album_antigo_nao_pisa_no_mais_recente(monkeypatch):
    """T12-R2: a carga antiga de álbum concluindo depois não sobrepõe a nova."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        release = threading.Event()

        def _get_album(browse_id):
            if browse_id == "MPREb-lento":
                release.wait(timeout=5)
                return _album_details("MPREb-lento")
            return _album_details("MPREb-rapido")

        monkeypatch.setattr(ytmusic, "get_album", _get_album)

        captured: list = []
        monkeypatch.setattr(screen, "run_worker", lambda worker, **kwargs: captured.append(worker))

        screen.open_album("MPREb-lento")  # época 1
        screen.open_album("MPREb-rapido")  # época 2
        task_a = asyncio.create_task(captured[0])
        await captured[1]  # a carga nova completa primeiro
        await pilot.pause()
        current = screen._current_album
        assert current is not None and current.browse_id == "MPREb-rapido"

        release.set()
        await asyncio.wait_for(task_a, timeout=5)
        await pilot.pause()

        current = screen._current_album
        assert current is not None and current.browse_id == "MPREb-rapido"
        assert screen._album_epoch == 2


@pytest.mark.asyncio
async def test_erro_de_carga_antiga_de_album_nao_apaga_o_novo(monkeypatch):
    """T12-R2: o erro de uma carga abandonada de álbum também é descartado."""
    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        release = threading.Event()

        def _get_album(browse_id):
            if browse_id == "MPREb-lento":
                release.wait(timeout=5)
                raise RuntimeError("resposta inválida da API")
            return _album_details("MPREb-rapido")

        monkeypatch.setattr(ytmusic, "get_album", _get_album)

        captured: list = []
        monkeypatch.setattr(screen, "run_worker", lambda worker, **kwargs: captured.append(worker))

        screen.open_album("MPREb-lento")
        screen.open_album("MPREb-rapido")
        task_a = asyncio.create_task(captured[0])
        await captured[1]
        await pilot.pause()

        release.set()
        await asyncio.wait_for(task_a, timeout=5)
        await pilot.pause()

        message = screen.query_one("#album-message", Static)
        assert "Erro ao carregar álbum" not in str(message.render())
        current = screen._current_album
        assert current is not None and current.browse_id == "MPREb-rapido"


@pytest.mark.asyncio
async def test_ui_responsiva_com_leitura_da_biblioteca_travada(monkeypatch):
    """T12-R3: leitura do banco pendurada não bloqueia o loop nem as teclas.

    A leitura fica presa num evento (thread real, via ``to_thread``); com
    ela pendurada a UI precisa continuar processando teclas e exibir o
    estado "carregando" — depois de destravada, volta ao estado vazio.
    """
    release = threading.Event()

    def _slow_albums(limit=500):
        release.wait(timeout=10)
        return []

    monkeypatch.setattr(library, "list_library_albums", _slow_albums)
    monkeypatch.setattr(library, "list_standalone_tracks", lambda limit=500: [])

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabs = screen.query_one("#main-tabs", TabbedContent)

        # Teclas continuam funcionando com a leitura pendurada
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert tabs.active == "tab-search"
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert tabs.active == "tab-library"

        message = screen.query_one("#library-message", Static)
        assert message.display is True
        assert library_controller.LIBRARY_LOADING_TEXT in str(message.render())

        release.set()
        await screen.refresh_library().wait()

        assert "Nenhuma faixa na biblioteca local ainda" in str(message.render())


@pytest.mark.asyncio
async def test_leitura_antiga_da_biblioteca_nao_pisa_na_mais_nova(monkeypatch):
    """T12-R2/R3: leitura antiga concluindo depois não sobrepõe a mais nova."""
    release = threading.Event()
    entered = threading.Event()
    stale = {"blocked": False}

    class _Album:
        def __init__(self, title: str) -> None:
            self.id = 1
            self.title = title
            self.release_year = None
            self.artists: list = []

    def _make_entry(title: str) -> LibraryAlbum:
        return LibraryAlbum(album=_Album(title), track_count=1, missing_count=0)

    def _albums(limit=500):
        if stale["blocked"]:
            entered.set()
            release.wait(timeout=10)
            return [_make_entry("Antigo")]
        return [_make_entry("Novo")]

    monkeypatch.setattr(library, "list_library_albums", _albums)
    monkeypatch.setattr(library, "list_standalone_tracks", lambda limit=500: [])

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        await screen.refresh_library().wait()  # estado estável antes do teste

        captured: list = []
        monkeypatch.setattr(screen, "run_worker", lambda worker, **kwargs: captured.append(worker))

        # A leitura "antiga" fica presa DE VERDADE na thread antes de a nova rodar.
        stale["blocked"] = True
        screen.refresh_library()  # época N (antiga — fica presa)
        task_old = asyncio.create_task(captured[0])
        assert await asyncio.to_thread(entered.wait, 10)

        stale["blocked"] = False
        screen.refresh_library()  # época N+1 (nova)
        await captured[1]  # a nova pinta primeiro
        await pilot.pause()

        view = screen.query_one("#library-list", LibraryListView)
        assert [entry.album.title for entry in view.albums] == ["Novo"]

        release.set()
        await asyncio.wait_for(task_old, timeout=10)
        await pilot.pause()

        assert [entry.album.title for entry in view.albums] == ["Novo"]


@pytest.mark.asyncio
async def test_playlists_mostram_carregando_durante_leitura(monkeypatch):
    """T17.1/R3: leitura pendurada mostra "carregando"; depois, estado vazio."""
    release = threading.Event()

    def _slow_playlists():
        release.wait(timeout=10)
        return []

    monkeypatch.setattr(library, "list_playlists", _slow_playlists)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#main-tabs", TabbedContent).active = "tab-playlists"
        await pilot.pause()

        message = screen.query_one("#playlists-message", Static)
        assert message.display is True
        assert "Carregando playlists..." in str(message.render())

        release.set()
        await screen.refresh_playlists().wait()

        assert "Nenhuma playlist ainda" in str(message.render())


@pytest.mark.asyncio
async def test_leitura_antiga_das_playlists_nao_pisa_na_mais_nova(monkeypatch):
    """T17.1/R3: época — leitura antiga concluindo depois não sobrepõe a nova."""
    release = threading.Event()
    entered = threading.Event()
    stale = {"blocked": False}

    class _Playlist:
        def __init__(self, name: str) -> None:
            self.id = 1
            self.name = name
            self.tracks: list = []

    def _playlists():
        if stale["blocked"]:
            entered.set()
            release.wait(timeout=10)
            return [_Playlist("Antigo")]
        return [_Playlist("Novo")]

    monkeypatch.setattr(library, "list_playlists", _playlists)

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#main-tabs", TabbedContent).active = "tab-playlists"
        await screen.refresh_playlists().wait()  # estado estável antes do teste

        captured: list = []
        monkeypatch.setattr(screen, "run_worker", lambda worker, **kwargs: captured.append(worker))

        # A leitura "antiga" fica presa DE VERDADE na thread antes de a nova rodar.
        stale["blocked"] = True
        screen.refresh_playlists()  # época N (antiga — fica presa)
        task_old = asyncio.create_task(captured[0])
        assert await asyncio.to_thread(entered.wait, 10)

        stale["blocked"] = False
        screen.refresh_playlists()  # época N+1 (nova)
        await captured[1]  # a nova pinta primeiro
        await pilot.pause()

        view = screen.query_one("#playlists-list", PlaylistListView)
        assert [str(item.name) for item in view.playlists] == ["Novo"]

        release.set()
        await asyncio.wait_for(task_old, timeout=10)
        await pilot.pause()

        assert [str(item.name) for item in view.playlists] == ["Novo"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
