"""Aba 📚 da biblioteca local (mixin de ``MainScreen``)."""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from textual.widgets import Static
from textual.worker import Worker

from ytmusic_tui.library import library
from ytmusic_tui.library.errors import LibraryError, describe_library_error
from ytmusic_tui.library.models import Track as LibraryTrack
from ytmusic_tui.library.recovery import reconcile
from ytmusic_tui.library.service import LibraryAlbum
from ytmusic_tui.log import log_event, log_file_path
from ytmusic_tui.player import player
from ytmusic_tui.tui.messages import (
    LIBRARY_EMPTY_TEXT,
    LIBRARY_LIKE_NEEDS_DOWNLOAD,
    LIBRARY_LOADING_TEXT,
    LIBRARY_UNAVAILABLE_TOAST,
    LIKE_ADDED,
    LIKE_REMOVED,
    MISSING_LEGEND_TEXT,
    NO_TRACK_PLAYING,
    escape_markup,
    library_unavailable,
    reconciliation_summary,
)
from ytmusic_tui.tui.widgets import LibraryListView, TrackListView

logger = logging.getLogger(__name__)

# Textos da aba Biblioteca (T12-R7: definidos em ``tui/messages.py`` e
# re-exportados aqui — a tela e os testes continuam importando daqui).


class _LibraryViews(NamedTuple):
    """Widgets das duas camadas da aba Biblioteca (T17.1/R6)."""

    albums: LibraryListView
    message: Static
    header: Static
    tracks: TrackListView
    legend: Static

    @classmethod
    def query(cls, host: Any) -> "_LibraryViews":
        return cls(
            host.query_one("#library-list", LibraryListView),
            host.query_one("#library-message", Static),
            host.query_one("#library-album-header", Static),
            host.query_one("#library-album-tracks", TrackListView),
            host.query_one("#library-legend", Static),
        )


class LibraryController:
    """Mixin com a listagem da biblioteca local (por álbum) e as curtidas."""

    #: Época do refresh (T12-R2/R3): só a leitura mais recente pinta a tela.
    _library_epoch: int = 0

    #: Álbum aberto na própria aba (T17.1/R6); None = nível de álbuns.
    _library_album: LibraryAlbum | None = None

    # Membros fornecidos pela tela host (o mypy analisa cada mixin
    # isoladamente; as declarações abaixo existem só na checagem de tipos).
    if TYPE_CHECKING:

        def query_one(self, selector: Any, expect_type: Any = None) -> Any: ...
        def run_worker(self, *args: Any, **kwargs: Any) -> Worker[Any]: ...
        def _toast(self, message: str) -> None: ...
        def call_after_refresh(self, *args: Any, **kwargs: Any) -> Any: ...

    def refresh_library(self) -> Worker[None]:
        """Agenda a atualização da biblioteca (nível de álbuns ou álbum aberto).

        T12-R3/T17.1-R6: a leitura do banco (limitada a 500 linhas por
        consulta) roda em ``asyncio.to_thread`` dentro de um worker do
        Textual — o loop da UI não espera o SQLite. Chamadas concorrentes
        são ordenadas pela época (T12-R2): só a leitura mais recente pinta.
        O handle devolvido permite aguardar a atualização (testes e fluxos
        que precisam do resultado).
        """
        self._library_epoch += 1
        return self.run_worker(self._refresh_library_worker(self._library_epoch), group="library")

    async def _refresh_library_worker(self, epoch: int) -> None:
        """Lê fora do loop e pinta o resultado se ainda for o atual.

        Uma falha ao consultar o banco NÃO pode parecer uma biblioteca
        vazia: o usuário acharia que perdeu as faixas baixadas. O erro é
        registrado no log e mostrado na própria aba — com a mensagem
        específica do caso quando é tipado (T05-R4); falha na abertura do
        banco na inicialização também aparece aqui.
        """
        try:
            views = _LibraryViews.query(self)
        except Exception:
            return

        startup_error = self._startup_db_error()
        if startup_error is not None:
            if epoch == self._library_epoch:
                self._show_library_unavailable(views, describe_library_error(startup_error))
            return

        self._render_library_loading(views)

        album_open = self._library_album
        try:
            albums, singles = await asyncio.to_thread(self._load_library_overview)
            tracks: list[LibraryTrack] = []
            if album_open is not None:
                tracks = await asyncio.to_thread(
                    library.list_album_tracks, cast(int, album_open.album.id), 500
                )
        except LibraryError as error:
            self._log_library_failure(error)
            if epoch == self._library_epoch:
                self._show_library_unavailable(views, describe_library_error(error))
            return
        except Exception as error:
            self._log_library_failure(error)
            if epoch == self._library_epoch:
                self._show_library_unavailable(views, str(error))
            return

        if epoch != self._library_epoch:
            logger.debug("Refresh da biblioteca obsoleto descartado (época %s)", epoch)
            return

        if album_open is not None:
            # O álbum aberto pode ter desaparecido num refresh (faixas
            # removidas da biblioteca): resolve o retrato fresco ou volta
            # ao nível 1 — nunca pinta um cabeçalho órfão.
            current = next((item for item in albums if item.album.id == album_open.album.id), None)
            if current is None:
                self._library_album = None
                self._render_library_overview(views, albums, singles)
                return
            self._library_album = current
            self._render_library_album(views, current, tracks)
            return
        self._render_library_overview(views, albums, singles)

    @staticmethod
    def _load_library_overview() -> tuple[list[LibraryAlbum], list[LibraryTrack]]:
        """Leitura do nível 1 (uma passada de thread para os dois lados)."""
        return library.list_library_albums(limit=500), library.list_standalone_tracks(limit=500)

    def _log_library_failure(self, error: Exception) -> None:
        log_event(
            "tui.library",
            "db_library_load_failed",
            f"Falha ao carregar a biblioteca local: {error}",
            level=logging.ERROR,
            error_code=type(error).__name__,
            exc_info=True,
        )

    # ===== Renderização (T17.1/R6/R7) =====

    def _render_library_loading(self, views: _LibraryViews) -> None:
        """Mostra "carregando" apenas quando não há conteúdo a preservar (§12.2)."""
        if self._library_album is not None:
            if views.tracks.display and views.tracks.tracks:
                return
        elif views.albums.display and (views.albums.albums or views.albums.singles):
            return
        views.message.update(LIBRARY_LOADING_TEXT)
        views.message.display = True

    def _render_library_overview(
        self, views: _LibraryViews, albums: list[LibraryAlbum], singles: list[LibraryTrack]
    ) -> None:
        """Nível 1: álbuns (+ seção de avulsas quando existem) — R6/R7.

        A seção "Faixas avulsas" só aparece quando há faixas sem álbum; o
        banco indisponível nunca cai aqui (mensagem própria, não "vazio").
        """
        views.header.display = False
        views.tracks.display = False
        views.albums.set_entries(albums, singles)
        views.albums.display = bool(albums or singles)
        views.message.display = not (albums or singles)
        views.message.update(LIBRARY_EMPTY_TEXT)
        has_missing = any(item.missing_count for item in albums) or any(
            track.is_missing for track in singles
        )
        self._render_missing_legend(views, has_missing)
        if (albums or singles) and views.albums.has_focus and views.albums.index is None:
            views.albums.index = 0

    def _render_library_album(
        self, views: _LibraryViews, entry: LibraryAlbum, tracks: list[LibraryTrack]
    ) -> None:
        """Modo álbum (expandido na aba): cabeçalho + faixas em ordem (R6)."""
        views.albums.display = False
        views.message.display = False
        views.header.update(self._library_album_header(entry, len(tracks)))
        views.header.display = True
        views.tracks.set_tracks(tracks)
        views.tracks.display = True
        self._render_missing_legend(views, any(track.is_missing for track in tracks))
        if tracks and views.tracks.has_focus and views.tracks.index is None:
            views.tracks.index = 0

    def _render_missing_legend(self, views: _LibraryViews, visible: bool) -> None:
        """Legenda das ausências (R7): só aparece quando há o que explicar."""
        views.legend.display = visible
        views.legend.update(MISSING_LEGEND_TEXT)

    @staticmethod
    def _library_album_header(entry: LibraryAlbum, track_count: int) -> str:
        """Cabeçalho do modo álbum: título · artista · ano · N faixas (R6)."""
        album = entry.album
        parts = [f"💿 [b]{escape_markup(str(album.title))}[/b]"]
        if entry.primary_artist_name:
            parts.append(escape_markup(entry.primary_artist_name))
        if album.release_year:
            parts.append(escape_markup(str(album.release_year)))
        parts.append("1 faixa" if track_count == 1 else f"{track_count} faixas")
        return " · ".join(parts)

    def _show_library_unavailable(self, views: _LibraryViews, reason: str) -> None:
        """Mostra o motivo exato da falha (nunca uma lista "vazia") + o log."""
        views.albums.set_entries([], [])
        views.albums.display = False
        views.tracks.set_tracks([])
        views.tracks.display = False
        views.header.display = False
        views.legend.display = False
        views.message.update(
            library_unavailable(escape_markup(reason), escape_markup(str(log_file_path())))
        )
        views.message.display = True
        self._toast(LIBRARY_UNAVAILABLE_TOAST)

    # ===== Modo álbum (expansão na própria aba — T17.1/R6) =====

    def open_library_album(self, entry: LibraryAlbum) -> None:
        """Abre o álbum selecionado (Enter) expandindo dentro da aba."""
        self._library_album = entry
        self.refresh_library()
        self.call_after_refresh(self._focus_library_tracks)

    def close_library_album(self) -> None:
        """Volta ao nível de álbuns (Esc/← no modo álbum)."""
        self._library_album = None
        self.refresh_library()
        self.call_after_refresh(self._focus_library_albums)

    def library_album_open(self) -> bool:
        """True quando a aba Biblioteca está no modo álbum (R6)."""
        return self._library_album is not None

    def _selected_library_album(self) -> LibraryAlbum | None:
        """Alvo do ``Shift+P`` (R5): o álbum aberto ou, no nível 1, o destacado.

        O álbum aberto tem prioridade (modo álbum); sem ele vale a linha de
        álbum destacada — destacar e adicionar é o gesto natural de quem não
        abriu com Enter (BUG-01 de campo: antes isso era um no-op mudo).
        """
        if self._library_album is not None:
            return self._library_album
        try:
            view = self.query_one("#library-list", LibraryListView)
        except Exception:
            return None
        return cast("LibraryAlbum | None", view.get_selected_album())

    def _selected_library_track(self) -> LibraryTrack | None:
        """Faixa selecionada na Biblioteca agora (avulsa ou dentro do álbum).

        É o alvo preferido do ``p`` (T17.1/R4): com um álbum aberto vale a
        faixa destacada do álbum; no nível 1, a avulsa destacada. Sem
        destaque (ou fora da Biblioteca), devolve None — quem chama decide
        cair para a faixa em reprodução.
        """
        try:
            if self._library_album is not None:
                view = self.query_one("#library-album-tracks", TrackListView)
            else:
                view = self.query_one("#library-list", LibraryListView)
        except Exception:
            return None
        return cast("LibraryTrack | None", view.get_selected_track())

    def _focus_library_tracks(self) -> None:
        """Foca a lista de faixas do álbum aberto (já visível na aba)."""
        with contextlib.suppress(Exception):
            view = self.query_one("#library-album-tracks", TrackListView)
            view.focus()
            if view.tracks and view.index is None:
                view.index = 0

    def _focus_library_albums(self) -> None:
        """Foca de volta o nível 1 (álbuns + avulsas)."""
        with contextlib.suppress(Exception):
            view = self.query_one("#library-list", LibraryListView)
            view.focus()
            if (view.albums or view.singles) and view.index is None:
                view.index = 0

    def _library_visible_tracks(self) -> list[LibraryTrack]:
        """Faixas visíveis na Biblioteca (álbum aberto ou avulsas) — fila de play."""
        try:
            if self._library_album is not None:
                return list(self.query_one("#library-album-tracks", TrackListView).tracks)
            return list(self.query_one("#library-list", LibraryListView).singles)
        except Exception:
            return []

    # ===== Reconciliação de arquivos (§5.3/T03) =====

    def start_reconciliation(self) -> None:
        """Dispara a reconciliação de arquivos no início da sessão (§5.3).

        Roda num worker porque a varredura de disco pode ser lenta (INV-12):
        a UI não espera. Achados viram toast + atualização da biblioteca;
        detalhes ficam no log.
        """
        self.run_worker(self._run_reconciliation(), group="reconcile")

    async def _run_reconciliation(self) -> None:
        """Executa a varredura fora do loop e resume o resultado na UI."""
        try:
            report = await asyncio.to_thread(reconcile)
        except Exception as error:
            # Falha na reconciliação não apaga nada; fica registrada no log.
            log_event(
                "tui.library",
                "db_reconcile_failed",
                f"Falha na reconciliação de arquivos: {error}",
                level=logging.ERROR,
                error_code=type(error).__name__,
                exc_info=True,
            )
            return
        if report.has_findings:
            self._toast(reconciliation_summary(report.summary()))
            self.refresh_library()

    def _startup_db_error(self) -> LibraryError | None:
        """Erro tipado da abertura do banco na app (None = tudo certo).

        Lido por ``getattr`` porque o mixin não herda de ``Screen``: o
        membro é da classe host (``YTMusicTUI``), e importar o tipo dela
        aqui criaria ciclo (app → screens → controllers).
        """
        app = getattr(self, "app", None)
        return cast("LibraryError | None", getattr(app, "db_error", None))

    def action_like(self) -> None:
        """Curte/descurte a faixa atual (apenas na biblioteca local, sem conta)."""
        track = player.current_track
        if not track or not track.video_id:
            self._toast(NO_TRACK_PLAYING)
            return

        lib_track = library.get_track_by_source_id(track.video_id)
        if not lib_track:
            self._toast(LIBRARY_LIKE_NEEDS_DOWNLOAD)
            return
        liked = library.toggle_like(cast(int, lib_track.id))
        self._toast(LIKE_ADDED if liked else LIKE_REMOVED)
