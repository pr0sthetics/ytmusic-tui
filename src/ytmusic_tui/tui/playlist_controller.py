"""Aba 🎵 Playlists locais (mixin de ``MainScreen``)."""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple, TypeVar, cast

from textual.widgets import Static, TabbedContent
from textual.worker import Worker

from ytmusic_tui.api.models import Track
from ytmusic_tui.library import library
from ytmusic_tui.library.errors import LibraryError, describe_library_error
from ytmusic_tui.library.models import Playlist
from ytmusic_tui.library.models import Track as LibraryTrack
from ytmusic_tui.library.service import LibraryAlbum
from ytmusic_tui.log import log_event, log_file_path
from ytmusic_tui.player import player
from ytmusic_tui.tui.dialogs import (
    PICKER_NEW_PLAYLIST,
    ConfirmScreen,
    NameInputScreen,
    PlaylistPickerScreen,
)
from ytmusic_tui.tui.guards import guard_write
from ytmusic_tui.tui.messages import (
    ALBUM_PLAYLIST_HINT,
    MISSING_LEGEND_TEXT,
    NO_TRACK_PLAYING,
    PLAYLIST_ADD_FAILED,
    PLAYLIST_ADD_NEEDS_DOWNLOAD,
    PLAYLIST_ADD_SEARCH_NEEDS_DOWNLOAD,
    PLAYLIST_DELETE_FAILED,
    PLAYLIST_EMPTY_TEXT,
    PLAYLIST_LOADING_TEXT,
    PLAYLIST_NAME_PLACEHOLDER,
    PLAYLIST_NEW_PROMPT,
    PLAYLIST_PICKER_TITLE_ALBUM,
    PLAYLIST_PICKER_TITLE_TRACK,
    PLAYLIST_RENAME_FAILED,
    PLAYLIST_RENAME_PROMPT,
    PLAYLIST_REORDER_FAILED,
    PLAYLISTS_EMPTY_TEXT,
    PLAYLISTS_LOADING_TEXT,
    PLAYLISTS_UNAVAILABLE_TOAST,
    album_playlist_nothing,
    album_playlist_summary,
    confirm_delete_playlist,
    escape_markup,
    playlist_created,
    playlist_deleted,
    playlist_move_edge,
    playlist_renamed,
    playlist_track_added,
    playlist_track_already,
    playlist_track_moved,
    playlist_track_removed,
    playlists_unavailable,
)
from ytmusic_tui.tui.widgets import PlaylistListView, SongListView, TrackListView

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _PlaylistViews(NamedTuple):
    """Widgets das duas camadas da aba Playlists (T17.1/R3)."""

    playlists: PlaylistListView
    message: Static
    header: Static
    tracks: TrackListView
    legend: Static

    @classmethod
    def query(cls, host: Any) -> "_PlaylistViews":
        return cls(
            host.query_one("#playlists-list", PlaylistListView),
            host.query_one("#playlists-message", Static),
            host.query_one("#playlist-header", Static),
            host.query_one("#playlist-tracks", TrackListView),
            host.query_one("#playlist-legend", Static),
        )


class PlaylistController:
    """Mixin com a aba 🎵 Playlists: nível 1 + detalhe (T17.1/R3)."""

    #: Época do refresh (T12-R2/R3): só a leitura mais recente pinta a tela.
    _playlist_epoch: int = 0

    #: Playlist aberta no detalhe (None = nível 1).
    _playlist_open: Playlist | None = None

    #: Faixa a re-selecionar depois de mover/remover (R3).
    _playlist_select_id: int | None = None

    # Membros fornecidos pela tela host (o mypy analisa cada mixin
    # isoladamente; as declarações abaixo existem só na checagem de tipos).
    if TYPE_CHECKING:
        from textual.widget import Widget

        app: Any
        focused: Widget | None

        def query_one(self, selector: Any, expect_type: Any = None) -> Any: ...
        def run_worker(self, *args: Any, **kwargs: Any) -> Worker[Any]: ...
        def call_after_refresh(self, *args: Any, **kwargs: Any) -> Any: ...
        def _toast(self, message: str) -> None: ...
        def _startup_db_error(self) -> LibraryError | None: ...
        def _selected_library_track(self) -> LibraryTrack | None: ...
        def _selected_library_album(self) -> LibraryAlbum | None: ...

    def _guarded(self, operation: Callable[[], T], *, event: str, what: str) -> T | None:
        """Executa uma escrita da biblioteca com a guarda de erro (A06)."""
        return guard_write(
            operation,
            toast=self._toast,
            component="tui.playlists",
            event=event,
            what=what,
        )

    def refresh_playlists(self) -> Worker[None]:
        """Agenda a atualização da aba Playlists (nível 1 ou detalhe).

        Mesmo padrão da biblioteca (T12-R2/R3): leitura do banco em
        ``asyncio.to_thread`` dentro de um worker do Textual, ordenada pela
        época — só a leitura mais recente pinta. Falha nunca vira "nenhuma
        playlist": a mensagem própria traz o motivo (R3).
        """
        self._playlist_epoch += 1
        return self.run_worker(
            self._refresh_playlists_worker(self._playlist_epoch), group="playlists"
        )

    async def _refresh_playlists_worker(self, epoch: int) -> None:
        """Lê fora do loop e pinta se ainda for a época atual."""
        try:
            views = _PlaylistViews.query(self)
        except Exception:
            return

        startup_error = self._startup_db_error()
        if startup_error is not None:
            if epoch == self._playlist_epoch:
                self._show_playlists_unavailable(views, describe_library_error(startup_error))
            return

        self._render_playlists_loading(views)

        open_playlist = self._playlist_open
        loaded: Playlist | None = None
        tracks: list[LibraryTrack] = []
        try:
            playlists = await asyncio.to_thread(library.list_playlists)
            if open_playlist is not None:
                loaded = await asyncio.to_thread(library.get_playlist, cast(int, open_playlist.id))
                if loaded is not None:
                    entries = sorted(loaded.tracks, key=lambda entry: entry.position)
                    tracks = [cast(LibraryTrack, entry.track) for entry in entries]
        except LibraryError as error:
            self._log_playlists_failure(error)
            if epoch == self._playlist_epoch:
                self._show_playlists_unavailable(views, describe_library_error(error))
            return
        except Exception as error:
            self._log_playlists_failure(error)
            if epoch == self._playlist_epoch:
                self._show_playlists_unavailable(views, str(error))
            return

        if epoch != self._playlist_epoch:
            logger.debug("Refresh das playlists obsoleto descartado (época %s)", epoch)
            return

        if open_playlist is not None:
            if loaded is None:
                # A playlist aberta sumiu (excluída em outro fluxo/reload):
                # volta ao nível 1 em vez de pintar um detalhe órfão.
                self._playlist_open = None
                self._playlist_select_id = None
                self._render_playlists_list(views, playlists)
                return
            self._playlist_open = loaded
            self._render_playlist_detail(views, loaded, tracks)
            return
        self._render_playlists_list(views, playlists)

    def _log_playlists_failure(self, error: Exception) -> None:
        log_event(
            "tui.playlists",
            "db_playlists_load_failed",
            f"Falha ao carregar as playlists: {error}",
            level=logging.ERROR,
            error_code=type(error).__name__,
            exc_info=True,
        )

    # ===== Renderização (T17.1/R3/R7) =====

    def _render_playlists_loading(self, views: _PlaylistViews) -> None:
        """Mostra "carregando" apenas quando não há conteúdo a preservar."""
        if self._playlist_open is not None:
            if views.tracks.display and views.tracks.tracks:
                return
            views.message.update(PLAYLIST_LOADING_TEXT)
            views.message.display = True
            return
        if views.playlists.display and views.playlists.playlists:
            return
        views.message.update(PLAYLISTS_LOADING_TEXT)
        views.message.display = True

    def _render_playlists_list(self, views: _PlaylistViews, playlists: list[Playlist]) -> None:
        """Nível 1: playlists como ``nome · N faixas`` (R3)."""
        views.header.display = False
        views.tracks.display = False
        views.legend.display = False
        views.playlists.set_playlists(playlists)
        views.playlists.display = bool(playlists)
        views.message.display = not playlists
        views.message.update(PLAYLISTS_EMPTY_TEXT)
        if playlists and views.playlists.has_focus and views.playlists.index is None:
            views.playlists.index = 0

    def _render_playlist_detail(
        self,
        views: _PlaylistViews,
        playlist: Playlist,
        tracks: list[LibraryTrack],
    ) -> None:
        """Detalhe: cabeçalho + faixas na ordem de posição (R3/R7)."""
        views.playlists.display = False
        views.header.update(self._playlist_header(playlist, len(tracks)))
        views.header.display = True
        views.tracks.set_tracks(tracks)
        views.tracks.display = bool(tracks)
        views.message.display = not tracks
        views.message.update(PLAYLIST_EMPTY_TEXT)
        views.legend.display = any(track.is_missing for track in tracks)
        views.legend.update(MISSING_LEGEND_TEXT)
        # O ``clear()`` do set_tracks zera o índice e os itens novos montam no
        # próximo refresh; a restauração espera os itens existirem. A memória
        # em ``_playlist_select_id`` vale até abrir/fechar o detalhe, para um
        # refresh posterior não pular a seleção de volta para o topo (R3).
        self.call_after_refresh(self._restore_playlist_selection, views, tracks)

    @staticmethod
    def _playlist_header(playlist: Playlist, track_count: int) -> str:
        """Cabeçalho do detalhe: nome · N faixas (R3)."""
        count = "1 faixa" if track_count == 1 else f"{track_count} faixas"
        return f"🎵 [b]{escape_markup(str(playlist.name))}[/b] · {count}"

    def _restore_playlist_selection(
        self, views: _PlaylistViews, tracks: list[LibraryTrack]
    ) -> None:
        """Re-seleciona a faixa movida ou destaca a primeira (R3).

        Roda depois do refresh (itens montados): mover/remover exige o alvo
        de volta sob o cursor; fora disso, só destaca o primeiro item.
        """
        if not tracks:
            return
        target = self._playlist_select_id
        if target is not None:
            views.tracks.index = next(
                (index for index, track in enumerate(tracks) if track.id == target), 0
            )
            return
        if views.tracks.has_focus and views.tracks.index is None:
            views.tracks.index = 0

    def _show_playlists_unavailable(self, views: _PlaylistViews, reason: str) -> None:
        """Mostra o motivo exato da falha (nunca "nenhuma playlist") + o log."""
        views.playlists.set_playlists([])
        views.playlists.display = False
        views.tracks.set_tracks([])
        views.tracks.display = False
        views.header.display = False
        views.legend.display = False
        views.message.update(
            playlists_unavailable(escape_markup(reason), escape_markup(str(log_file_path())))
        )
        views.message.display = True
        self._toast(PLAYLISTS_UNAVAILABLE_TOAST)

    # ===== Navegação entre níveis (T17.1/R3) =====

    def open_playlist(self, playlist: Playlist) -> None:
        """Abre a playlist selecionada (Enter) — detalhe dentro da aba."""
        self._playlist_open = playlist
        self._playlist_select_id = None
        self.refresh_playlists()
        self.call_after_refresh(self._focus_playlist_tracks)

    def close_playlist(self) -> None:
        """Volta ao nível 1 (Esc/← no detalhe)."""
        self._playlist_open = None
        self._playlist_select_id = None
        self.refresh_playlists()
        self.call_after_refresh(self._focus_playlists_list)

    def playlist_open(self) -> bool:
        """True quando a aba Playlists está no detalhe (R3)."""
        return self._playlist_open is not None

    def playlist_has_selection(self) -> bool:
        """True quando há playlist aberta ou selecionada (E/Delete — R3)."""
        return self._selected_playlist() is not None

    def _selected_playlist(self) -> Playlist | None:
        """Playlist alvo dos comandos: a aberta ou a selecionada no nível 1."""
        if self._playlist_open is not None:
            return self._playlist_open
        try:
            view = self.query_one("#playlists-list", PlaylistListView)
            return cast("Playlist | None", view.get_selected_playlist())
        except Exception:
            return None

    def _focus_playlist_tracks(self) -> None:
        """Foca a lista de faixas do detalhe (já visível na aba)."""
        with contextlib.suppress(Exception):
            view = self.query_one("#playlist-tracks", TrackListView)
            view.focus()
            if view.tracks and view.index is None:
                view.index = 0

    def _focus_playlists_list(self) -> None:
        """Foca de volta o nível 1."""
        with contextlib.suppress(Exception):
            view = self.query_one("#playlists-list", PlaylistListView)
            view.focus()
            if view.playlists and view.index is None:
                view.index = 0

    def _playlist_visible_tracks(self) -> list[LibraryTrack]:
        """Faixas do detalhe aberto, na ordem — fila de reprodução (R3)."""
        try:
            return list(self.query_one("#playlist-tracks", TrackListView).tracks)
        except Exception:
            return []

    # ===== Criar / renomear / excluir (T17.1/R3) =====

    def action_new_playlist(self) -> None:
        """N: cria uma playlist (modal de nome; Esc cancela sem criar)."""
        self.app.push_screen(
            NameInputScreen(PLAYLIST_NEW_PROMPT, PLAYLIST_NAME_PLACEHOLDER),
            self._on_playlist_name_for_create,
        )

    def _on_playlist_name_for_create(self, name: str | None) -> None:
        if not name:
            return  # Esc cancela: nada muda.
        playlist = self._guarded(
            lambda: library.create_playlist(name),
            event="playlist_create_failed",
            what="criar a playlist",
        )
        if playlist is None:
            return
        self.refresh_playlists()
        self._toast(playlist_created(name))

    def action_rename_playlist(self) -> None:
        """E: renomeia a playlist aberta/selecionada (nome atual no campo)."""
        playlist = self._selected_playlist()
        if playlist is None:
            return
        self.app.push_screen(
            NameInputScreen(
                PLAYLIST_RENAME_PROMPT,
                PLAYLIST_NAME_PLACEHOLDER,
                initial=str(playlist.name),
                submit_label="Salvar",
            ),
            self._on_playlist_name_for_rename,
        )

    def _on_playlist_name_for_rename(self, name: str | None) -> None:
        playlist = self._selected_playlist()
        if not name or playlist is None:
            return
        renamed = self._guarded(
            lambda: library.rename_playlist(cast(int, playlist.id), name),
            event="playlist_rename_failed",
            what="renomear a playlist",
        )
        if renamed is None:
            return
        if not renamed:
            self._toast(PLAYLIST_RENAME_FAILED)
            return
        self.refresh_playlists()
        self._toast(playlist_renamed(name))

    def action_delete_playlist(self) -> None:
        """Delete: exclui a playlist (confirmado) ou remove a faixa do detalhe.

        No nível 1 o Delete pede confirmação e apaga a playlist inteira; no
        detalhe ele remove a faixa selecionada (ação leve e reversível —
        readicionar é só usar a tecla ``p`` na Biblioteca).
        """
        if self._playlist_open is not None:
            self._remove_selected_track()
            return
        playlist = self._selected_playlist()
        if playlist is None:
            return
        self.app.push_screen(
            ConfirmScreen(confirm_delete_playlist(str(playlist.name), len(playlist.tracks))),
            self._on_confirm_delete_playlist,
        )

    def _on_confirm_delete_playlist(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        playlist = self._selected_playlist()
        if playlist is None:
            return
        name = str(playlist.name)
        deleted = self._guarded(
            lambda: library.delete_playlist(cast(int, playlist.id)),
            event="playlist_delete_failed",
            what="excluir a playlist",
        )
        if deleted is None:
            return
        if not deleted:
            self._toast(PLAYLIST_DELETE_FAILED)
            return
        self.refresh_playlists()
        self._toast(playlist_deleted(name))

    # ===== Adicionar à playlist (T17.1/R4/R5) =====

    def action_add_to_playlist(self) -> None:
        """P: adiciona à playlist a faixa destacada ou a que está tocando.

        O gesto explícito vence: com uma faixa destacada na Biblioteca
        (avulsa ou dentro do álbum aberto) ou na Busca (lista de resultados
        com foco), ela tem prioridade; fora dessas, vale a faixa atual —
        como no ``l`` (curtir). Faixa destacada fora da biblioteca local:
        recado honesto com o caminho (R4/F9).
        """
        track = self._track_for_playlist_add()
        if track is None:
            return
        self._open_playlist_picker(
            PLAYLIST_PICKER_TITLE_TRACK,
            lambda picked: self._pick_into_playlist(
                picked, lambda playlist: self._add_track_to_playlist(track, playlist)
            ),
        )

    def action_add_album_to_playlist(self) -> None:
        """Shift+P: adiciona à playlist o álbum da Biblioteca (aberto ou destacado, R5)."""
        entry = self._selected_library_album()
        if entry is None:
            # Na Biblioteca a tecla fica ativa mesmo sem alvo: explicar o
            # gesto é melhor que silêncio (BUG-01 de campo).
            self._toast(ALBUM_PLAYLIST_HINT)
            return
        self._open_playlist_picker(
            PLAYLIST_PICKER_TITLE_ALBUM,
            lambda picked: self._pick_into_playlist(
                picked, lambda playlist: self._add_album_to_playlist(entry, playlist)
            ),
        )

    def _track_for_playlist_add(self) -> LibraryTrack | None:
        """Faixa alvo do ``p``: destaque (Biblioteca/Busca) ou a em reprodução."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
        except Exception:
            tabs = None
        active = tabs.active if tabs is not None else None
        if active == "tab-library":
            selected = self._selected_library_track()
            if selected is not None:
                return selected
        elif active == "tab-search":
            highlighted = self._highlighted_search_track()
            if highlighted is not None:
                return self._search_result_for_playlist_add(highlighted)
        playing = player.current_track
        if not playing or not playing.video_id:
            self._toast(NO_TRACK_PLAYING)
            return None
        lib_track = library.get_track_by_source_id(playing.video_id)
        if lib_track is None:
            self._toast(PLAYLIST_ADD_NEEDS_DOWNLOAD)
            return None
        return cast(LibraryTrack, lib_track)

    def _highlighted_search_track(self) -> Track | None:
        """Faixa destacada na Busca (🎵 ou 🎬), quando a lista tem o foco (F9).

        Foco no campo de busca não conta: sem destaque explícito, o ``p``
        segue com a faixa em reprodução. O auto-destaque do ``ListView``
        garante um item realçado quando a lista de resultados recebe o foco.
        """
        focused = self.focused
        if isinstance(focused, SongListView) and focused.id in ("search-songs", "search-videos"):
            return focused.get_selected_song()
        return None

    def _search_result_for_playlist_add(self, highlighted: Track) -> LibraryTrack | None:
        """Resolve a destacada da Busca na biblioteca; senão, avisa o caminho (F9).

        O ``Ctrl+D`` baixa a faixa **em reprodução** — com destaque diferente,
        o recado certo é tocar (Enter) e baixar (Ctrl+D) a destacada; quando a
        destacada já é a que toca, vale o recado original do R4.
        """
        if highlighted.video_id:
            lib_track = library.get_track_by_source_id(highlighted.video_id)
            if lib_track is not None:
                return cast(LibraryTrack, lib_track)
        playing = player.current_track
        if playing is not None and playing.video_id == highlighted.video_id:
            self._toast(PLAYLIST_ADD_NEEDS_DOWNLOAD)
        else:
            self._toast(PLAYLIST_ADD_SEARCH_NEEDS_DOWNLOAD)
        return None

    def _open_playlist_picker(
        self, title: str, on_pick: Callable[[Playlist | str | None], None]
    ) -> None:
        """Abre o modal de escolha com a lista fresca (R4/R5).

        A leitura das playlists é pequena (com as contagens) e roda antes do
        modal, como o resto dos fluxos de escrita da TUI; falha de banco
        avisa em vez de abrir um picker vazio que pareceria "sem playlists".
        """
        try:
            playlists = library.list_playlists()
        except LibraryError as error:
            self._log_playlists_failure(error)
            self._toast(PLAYLISTS_UNAVAILABLE_TOAST)
            return
        self.app.push_screen(PlaylistPickerScreen(playlists, title), on_pick)

    def _pick_into_playlist(
        self, picked: Playlist | str | None, on_playlist: Callable[[Playlist], None]
    ) -> None:
        """Resolve a escolha do picker; "➕ Nova playlist…" encadeia o nome (R4)."""
        if picked is None:
            return  # Esc cancela: nada criado, nada adicionado.
        if picked == PICKER_NEW_PLAYLIST:
            self.app.push_screen(
                NameInputScreen(PLAYLIST_NEW_PROMPT, PLAYLIST_NAME_PLACEHOLDER),
                lambda name: self._create_playlist_then(name, on_playlist),
            )
            return
        on_playlist(cast(Playlist, picked))

    def _create_playlist_then(
        self, name: str | None, on_playlist: Callable[[Playlist], None]
    ) -> None:
        """Cria a playlist pedida no fluxo e segue com ela (R4)."""
        if not name:
            return  # Esc cancela: nada criado, nada adicionado.
        playlist = self._guarded(
            lambda: library.create_playlist(name),
            event="playlist_create_failed",
            what="criar a playlist",
        )
        if playlist is None:
            return
        self.refresh_playlists()
        self._toast(playlist_created(name))
        on_playlist(playlist)

    def _add_track_to_playlist(self, track: LibraryTrack, playlist: Playlist) -> None:
        """Adiciona uma faixa à playlist, pulando repetida (R4)."""
        name = str(playlist.name)
        if self._playlist_has_track(playlist, cast(int, track.id)):
            self._toast(playlist_track_already(str(track.title), name))
            return
        added = self._guarded(
            lambda: library.add_track_to_playlist(cast(int, playlist.id), cast(int, track.id)),
            event="playlist_add_track_failed",
            what="adicionar a faixa à playlist",
        )
        if added is None:
            return  # a guarda já mostrou o toast de erro
        if not added:
            self._toast(PLAYLIST_ADD_FAILED)
            return
        self.refresh_playlists()
        self._toast(playlist_track_added(str(track.title), name))

    def _add_album_to_playlist(self, entry: LibraryAlbum, playlist: Playlist) -> None:
        """Adiciona as faixas do álbum que estão na biblioteca (R5).

        O recado é o que aconteceu: adicionadas, já estavam e — só quando o
        total do álbum é conhecido (``track_count`` no modelo) — quantas
        ficaram fora da biblioteca. Nada é maquiado para "tudo certo".
        """
        result = self._guarded(
            lambda: library.add_album_to_playlist(
                cast(int, playlist.id), cast(int, entry.album.id)
            ),
            event="playlist_add_album_failed",
            what="adicionar o álbum à playlist",
        )
        if result is None:
            return  # guarda já mostrou o toast; ou álbum/playlist sumiram
        missing: int | None = None
        if result.album_total is not None:
            missing = max(0, result.album_total - result.added - result.already)
        if result.added == 0 and result.already == 0:
            self._toast(album_playlist_nothing(str(entry.album.title), missing))
        else:
            self._toast(
                album_playlist_summary(
                    str(entry.album.title), result.added, result.already, missing
                )
            )
        self.refresh_playlists()

    @staticmethod
    def _playlist_has_track(playlist: Playlist, track_id: int) -> bool:
        """Checagem contra duplicata usando as entradas já carregadas (R4).

        O picker carrega as playlists com as entradas (``list_playlists``),
        então a checagem não custa consulta extra. Se por algum motivo as
        entradas não estiverem acessíveis, devolve False (fail-open): é
        melhor tentar adicionar do que bloquear uma adição legítima.
        """
        try:
            return any(cast(int, entry.track_id) == track_id for entry in playlist.tracks)
        except Exception:
            return False

    # ===== Faixas do detalhe (T17.1/R3) =====

    def _remove_selected_track(self) -> None:
        """Remove a faixa selecionada da playlist aberta (Delete no detalhe)."""
        if self._playlist_open is None:
            return
        try:
            view = self.query_one("#playlist-tracks", TrackListView)
        except Exception:
            return
        track = view.get_selected_track()
        if track is None:
            return
        playlist_id = cast(int, self._playlist_open.id)
        removed = self._guarded(
            lambda: library.remove_track_from_playlist(playlist_id, cast(int, track.id)),
            event="playlist_remove_track_failed",
            what="remover a faixa da playlist",
        )
        if not removed:
            return
        # A seleção fica na faixa que ocupar a posição deixada (R3): quem
        # removia a 3ª passa a ver a antiga 4ª sob o cursor, sem pular ao topo.
        remaining = [item for item in view.tracks if item.id != track.id]
        if remaining:
            index = next(
                (i for i, item in enumerate(view.tracks) if item.id == track.id),
                len(remaining) - 1,
            )
            self._playlist_select_id = cast(int, remaining[min(index, len(remaining) - 1)].id)
        self.refresh_playlists()
        self._toast(playlist_track_removed(str(track.title)))

    def action_move_playlist_track_up(self) -> None:
        """Shift+↑: move a faixa selecionada uma posição para cima (R3)."""
        self._move_playlist_track(up=True)

    def action_move_playlist_track_down(self) -> None:
        """Shift+↓: move a faixa selecionada uma posição para baixo (R3)."""
        self._move_playlist_track(up=False)

    def _move_playlist_track(self, *, up: bool) -> None:
        """Reordena a playlist numa transação só (``reorder_playlist_tracks``)."""
        if self._playlist_open is None:
            return
        try:
            view = self.query_one("#playlist-tracks", TrackListView)
        except Exception:
            return
        track = view.get_selected_track()
        tracks = list(view.tracks)
        if track is None or not tracks:
            return
        index = next((i for i, item in enumerate(tracks) if item.id == track.id), None)
        if index is None:
            return
        target = index - 1 if up else index + 1
        if target < 0 or target >= len(tracks):
            self._toast(playlist_move_edge(up=up))
            return
        tracks[index], tracks[target] = tracks[target], tracks[index]
        order = [cast(int, item.id) for item in tracks]
        playlist_id = cast(int, self._playlist_open.id)
        reordered = self._guarded(
            lambda: library.reorder_playlist_tracks(playlist_id, order),
            event="playlist_reorder_failed",
            what="reordenar a playlist",
        )
        if reordered is None:
            return  # a guarda já mostrou o toast de erro
        if not reordered:
            self._toast(PLAYLIST_REORDER_FAILED)
            return
        self._playlist_select_id = cast(int, track.id)
        self.refresh_playlists()
        self._toast(playlist_track_moved(str(track.title), up=up))
