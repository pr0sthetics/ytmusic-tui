"""Aba 🎵 Playlists locais (mixin de ``MainScreen``)."""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from textual.widgets import Static
from textual.worker import Worker

from ytmusic_tui.library import library
from ytmusic_tui.library.errors import LibraryError, describe_library_error
from ytmusic_tui.library.models import Playlist
from ytmusic_tui.library.models import Track as LibraryTrack
from ytmusic_tui.log import log_event, log_file_path
from ytmusic_tui.tui.dialogs import ConfirmScreen, NameInputScreen
from ytmusic_tui.tui.messages import (
    MISSING_LEGEND_TEXT,
    PLAYLIST_DELETE_FAILED,
    PLAYLIST_EMPTY_TEXT,
    PLAYLIST_LOADING_TEXT,
    PLAYLIST_NAME_PLACEHOLDER,
    PLAYLIST_NEW_PROMPT,
    PLAYLIST_RENAME_FAILED,
    PLAYLIST_RENAME_PROMPT,
    PLAYLIST_REORDER_FAILED,
    PLAYLISTS_EMPTY_TEXT,
    PLAYLISTS_LOADING_TEXT,
    PLAYLISTS_UNAVAILABLE_TOAST,
    confirm_delete_playlist,
    escape_markup,
    playlist_created,
    playlist_deleted,
    playlist_move_edge,
    playlist_renamed,
    playlist_track_moved,
    playlist_track_removed,
    playlists_unavailable,
)
from ytmusic_tui.tui.widgets import PlaylistListView, TrackListView

logger = logging.getLogger(__name__)


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
        app: Any

        def query_one(self, selector: Any, expect_type: Any = None) -> Any: ...
        def run_worker(self, *args: Any, **kwargs: Any) -> Worker[Any]: ...
        def call_after_refresh(self, *args: Any, **kwargs: Any) -> Any: ...
        def _toast(self, message: str) -> None: ...
        def _startup_db_error(self) -> LibraryError | None: ...

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
        library.create_playlist(name)
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
            ),
            self._on_playlist_name_for_rename,
        )

    def _on_playlist_name_for_rename(self, name: str | None) -> None:
        playlist = self._selected_playlist()
        if not name or playlist is None:
            return
        if not library.rename_playlist(cast(int, playlist.id), name):
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
        if not library.delete_playlist(cast(int, playlist.id)):
            self._toast(PLAYLIST_DELETE_FAILED)
            return
        self.refresh_playlists()
        self._toast(playlist_deleted(name))

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
        removed = library.remove_track_from_playlist(
            cast(int, self._playlist_open.id), cast(int, track.id)
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
        if not library.reorder_playlist_tracks(cast(int, self._playlist_open.id), order):
            self._toast(PLAYLIST_REORDER_FAILED)
            return
        self._playlist_select_id = cast(int, track.id)
        self.refresh_playlists()
        self._toast(playlist_track_moved(str(track.title), up=up))
