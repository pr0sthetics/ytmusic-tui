"""Tela principal da TUI: abas, atalhos e navegação."""

import asyncio
import contextlib
import logging
import threading
from pathlib import Path
from typing import Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Header,
    Input,
    ListView,
    Select,
    Static,
    Tab,
    TabbedContent,
    TabPane,
)

from ytmusic_tui.api import ytmusic
from ytmusic_tui.api.models import Album, AlbumDetail, SearchResult, Track
from ytmusic_tui.config import (
    AUDIO_QUALITY_VALUES,
    DOWNLOAD_FORMAT_VALUES,
    MAX_DOWNLOAD_CONCURRENT,
    MIN_DOWNLOAD_CONCURRENT,
    settings,
)
from ytmusic_tui.diagnostics import mask_diagnostics
from ytmusic_tui.download import download_manager
from ytmusic_tui.errors import ConfigError
from ytmusic_tui.player import player
from ytmusic_tui.tui.dialogs import HelpScreen
from ytmusic_tui.tui.download_controller import DownloadController
from ytmusic_tui.tui.library_controller import LIBRARY_EMPTY_TEXT, LibraryController
from ytmusic_tui.tui.messages import (
    ALBUM_EMPTY,
    ALBUM_LOADING,
    ALBUM_WITHOUT_ID,
    DOWNLOADS_EMPTY_TEXT,
    INVALID_VALUE,
    PLAYLISTS_EMPTY_TEXT,
    SEARCH_INIT_DETAIL,
    SEARCH_INIT_NEXT,
    SEARCH_NO_RESULTS,
    SEARCH_STARTING,
    album_error,
    audio_quality_set,
    concurrent_set,
    config_save_error,
    download_dir_error,
    download_dir_set,
    download_format_set,
    download_quality_set,
    escape_markup,
    search_error,
    volume_set,
)
from ytmusic_tui.tui.playback_controller import PlaybackController
from ytmusic_tui.tui.playlist_controller import PlaylistController
from ytmusic_tui.tui.queue import PlaybackQueue
from ytmusic_tui.tui.widgets import (
    AlbumListView,
    LibraryListView,
    NowPlayingBar,
    PlaylistListView,
    SearchInput,
    SongListView,
    TrackListView,
)
from ytmusic_tui.versions import render_dependencies

logger = logging.getLogger(__name__)

# ===== §12.2: estados de tela (T12 — cada tela declara seus estados) =====
# Busca:       inicial (dica fixa do compose; listas ocultas no on_mount) ·
#              carregando (SEARCH_STARTING) · resultados (listas visíveis,
#              mensagem oculta) · vazia (SEARCH_NO_RESULTS) · erro (3 partes,
#              T12-R5).
# Álbum:       carregando (ALBUM_LOADING) · com faixas · vazio (ALBUM_EMPTY)
#              · erro (album_error, 3 partes).
# Biblioteca:  carregando (LIBRARY_LOADING_TEXT) · com conteúdo (lista
#              visível) · vazia (LIBRARY_EMPTY_TEXT) · indisponível
#              (library_unavailable — motivo + "nada foi apagado").
# Playlists:   nível 1 ("nome · N faixas") · detalhe (posições) · playlist
#              vazia · nenhuma playlist · indisponível (playlists_unavailable)
#              — falha nunca vira "nenhuma playlist".
# Downloads:   fila vazia (DOWNLOADS_EMPTY_TEXT, tabela oculta) · ativa ·
#              concluída · com falhas — a tabela aparece quando há linhas.
# Player:      barra em parado/carregando/tocando/pausado/falha
#              (NowPlayingBar.update_state).
# Config:      normal · **sem estado de alterações pendentes** (decisão
#              §12.2: salva a cada alteração real, sem botão Aplicar) · erro
#              ao salvar (toast mascarado em 3 partes, _save_setting).


def _select_value(value: object, options: tuple[str, ...], default: str) -> str:
    """Valor de ``Select`` garantidamente válido (A02).

    A configuração já é validada no carregamento, mas a UI não pode apostar
    nisso: se um valor fora do enum chegar aqui (objeto mutado em teste,
    fallback ou código futuro), o widget usa o padrão em vez de levantar
    ``InvalidSelectValueError`` no mount e deixar o app inutilizável.
    """
    text = str(value)
    return text if text in options else default


class MainScreen(
    PlaybackController, DownloadController, LibraryController, PlaylistController, Screen
):
    """Tela principal com abas.

    O comportamento é composto pelos mixins de controller — reprodução
    (:class:`PlaybackController`), downloads (:class:`DownloadController`),
    biblioteca (:class:`LibraryController`) e playlists
    (:class:`PlaylistController`); aqui ficam o layout, os atalhos e a
    navegação entre abas.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair"),
        Binding("ctrl+s", "search", "Buscar"),
        Binding("space", "play_pause", "Play/Pause"),
        Binding("right", "next", "Próxima"),
        Binding("left", "previous_or_back", "Anterior"),
        Binding("comma", "seek_backward", "Voltar 5s"),
        Binding("full_stop", "seek_forward", "Avançar 5s"),
        Binding("plus,equals_sign", "volume_up", "Aumentar volume"),
        Binding("minus", "volume_down", "Diminuir volume"),
        Binding("m", "mute", "Mudo"),
        Binding("ctrl+d", "download", "Baixar", priority=True),
        Binding("l", "like", "Curtir"),
        Binding("a", "download_album", "Baixar álbum"),
        Binding("t", "play_album", "Tocar álbum"),
        Binding("r", "retry_download", "Retomar download"),
        Binding("d", "discard_download", "Descartar download"),
        Binding("c", "cancel_download", "Cancelar download"),
        Binding("C", "clear_downloads", "Limpar concluídos"),
        Binding("i", "show_download_error", "Detalhe do erro"),
        Binding("n", "new_playlist", "Nova playlist"),
        Binding("e", "rename_playlist", "Renomear playlist"),
        Binding("delete", "delete_playlist", "Excluir playlist/faixa"),
        Binding("shift+up", "move_playlist_track_up", "Mover faixa ↑"),
        Binding("shift+down", "move_playlist_track_down", "Mover faixa ↓"),
        Binding("p", "add_to_playlist", "Adicionar à playlist"),
        Binding("P", "add_album_to_playlist", "Adicionar álbum à playlist"),
        Binding("escape", "back_to_search", "Voltar", show=False),
        Binding("ctrl+l", "library", "Biblioteca"),
        Binding("f1", "help", "Ajuda"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Fila de reprodução (Fase 2/P01): a política vive em ``queue``; a
        # tela só reage a ela e é dona de *quando* tocar.
        self.queue: PlaybackQueue = PlaybackQueue()
        self._last_auto_next: float = 0.0
        # Álbum aberto na aba dedicada (modelo interno do T10-R1); usado pelo "a".
        self._current_album: AlbumDetail | None = None
        # Nova busca marca o abort de retry da anterior (T10-R3, A1); None = sem busca.
        self._search_abort: threading.Event | None = None
        # Épocas de render (T12-R2): só a operação mais recente pinta a tela.
        self._search_epoch: int = 0
        self._album_epoch: int = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with TabbedContent(initial="tab-search", id="main-tabs"):
            with TabPane("🎵 Playlists", id="tab-playlists"):
                yield Static("[b]🎵 Playlists locais[/b]", classes="section-title")
                with Vertical(id="playlists-body"):
                    yield Static(PLAYLISTS_EMPTY_TEXT, id="playlists-message")
                    yield PlaylistListView(id="playlists-list")
                    yield Static("", id="playlist-header", classes="section-title")
                    yield Static("", id="playlist-legend")
                    yield TrackListView(id="playlist-tracks")

            with TabPane("🔍 Busca", id="tab-search"):
                yield SearchInput(id="search-input")
                with Vertical(id="search-results"):
                    yield Static(
                        "Digite algo e pressione Enter para buscar.",
                        id="search-message",
                    )
                    yield Static("🎵 Músicas", id="search-songs-title", classes="list-title")
                    yield SongListView(id="search-songs")
                    yield Static("🎬 Vídeos", id="search-videos-title", classes="list-title")
                    yield SongListView(id="search-videos")
                    yield Static("💿 Álbuns", id="search-albums-title", classes="list-title")
                    yield AlbumListView(id="search-albums")

            with TabPane("💿 Álbum", id="tab-album"):
                yield Static("💿 Nenhum álbum aberto", id="album-header", classes="section-title")
                with Vertical(id="album-body"):
                    yield Static(
                        "Busque um álbum na aba Busca e pressione Enter para abri-lo aqui.",
                        id="album-message",
                    )
                    yield Button(
                        "⬇ Baixar álbum inteiro (a)", id="btn-download-album", variant="primary"
                    )
                    yield SongListView(id="album-songs")

            with TabPane("📚 Biblioteca", id="tab-library"):
                yield Static("[b]📚 Biblioteca local[/b]", classes="section-title")
                with Vertical(id="library-body"):
                    yield Static(LIBRARY_EMPTY_TEXT, id="library-message")
                    yield LibraryListView(id="library-list")
                    yield Static("", id="library-legend")
                    yield Static("", id="library-album-header", classes="section-title")
                    yield TrackListView(id="library-album-tracks")

            with TabPane("📥 Downloads", id="tab-downloads"):
                yield Static("[b]Gerenciador de Downloads[/b]", classes="section-title")
                yield Static(DOWNLOADS_EMPTY_TEXT, id="downloads-message")
                yield DataTable(id="downloads-table")

            with TabPane("⚙️ Config", id="tab-settings"):  # noqa: SIM117 (compose Textual)
                with Vertical(id="settings-content"):
                    yield Static("[b]Configurações[/b]", classes="section-title")
                    with Collapsible(title="Conta", collapsed=False):
                        yield Static(self._account_status_text(), id="account-status")
                        yield Static(
                            "Modo anônimo: busca, reprodução e download funcionam sem conta.\n"
                            "Curtidas são salvas apenas na biblioteca local.",
                            id="account-info",
                        )
                    with Collapsible(title="Player", collapsed=True):
                        yield Static("Volume padrão:")
                        yield Input(
                            value=str(settings.mpv_volume), id="setting-volume", type="integer"
                        )
                        yield Static("Qualidade de áudio:")
                        yield Select(
                            [
                                ("Baixa", "low"),
                                ("Média", "medium"),
                                ("Alta", "high"),
                                ("Melhor", "best"),
                            ],
                            value=_select_value(
                                settings.mpv_audio_quality, AUDIO_QUALITY_VALUES, "high"
                            ),
                            id="setting-audio-quality",
                        )
                    with Collapsible(title="Downloads", collapsed=True):
                        yield Static("Diretório:")
                        yield Input(value=str(settings.download_dir), id="setting-download-dir")
                        yield Static("Formato:")
                        yield Select(
                            [("MP3", "mp3"), ("Opus", "opus"), ("M4A", "m4a"), ("FLAC", "flac")],
                            value=_select_value(
                                settings.download_format, DOWNLOAD_FORMAT_VALUES, "mp3"
                            ),
                            id="setting-format",
                        )
                        yield Static("Qualidade (kbps):")
                        yield Input(value=settings.download_quality, id="setting-download-quality")
                        yield Static("Downloads simultâneos:")
                        yield Input(
                            value=str(settings.download_concurrent),
                            id="setting-concurrent",
                            type="integer",
                        )
                    with Collapsible(title="Interface", collapsed=True):
                        yield Checkbox(
                            "Notificações",
                            value=settings.show_notifications,
                            id="setting-notifications",
                        )
                        yield Checkbox(
                            "Auto-play próxima",
                            value=settings.auto_play_next,
                            id="setting-autoplay",
                        )
                    with Collapsible(title="Dependências", collapsed=True):
                        yield Static(render_dependencies(), id="dependency-status")
                        yield Static(
                            "Compara a versão instalada com a testada pelo projeto "
                            "(ytmusicapi e yt-dlp). Se aparecer 'atualize', rode "
                            "uv sync (ou atualize/reinstale o app).",
                            id="dependency-info",
                        )

        yield NowPlayingBar(id="now-playing", volume=settings.mpv_volume)

    def _account_status_text(self) -> str:
        """Texto de status da conta (o app opera sem login)."""
        return "Status: [green]Modo anônimo (sem conta)[/green]"

    def on_mount(self) -> None:
        self.query_one("#search-songs", SongListView).display = False
        self.query_one("#search-songs-title", Static).display = False
        self.query_one("#search-videos", SongListView).display = False
        self.query_one("#search-videos-title", Static).display = False
        self.query_one("#search-albums", AlbumListView).display = False
        self.query_one("#search-albums-title", Static).display = False
        self.query_one("#album-songs", SongListView).display = False
        self.query_one("#btn-download-album", Button).display = False
        # Biblioteca: o detalhe do álbum (R6) só aparece com um álbum aberto.
        self.query_one("#library-album-header", Static).display = False
        self.query_one("#library-album-tracks", TrackListView).display = False
        self.query_one("#library-legend", Static).display = False
        # Playlists: o detalhe (R3) só aparece com uma playlist aberta.
        self.query_one("#playlist-header", Static).display = False
        self.query_one("#playlist-legend", Static).display = False
        self.query_one("#playlist-tracks", TrackListView).display = False
        # A aba de álbum só aparece quando um álbum é aberto pela busca.
        self._set_album_tab_visible(False)
        self.refresh_library()
        self.refresh_playlists()
        self.update_downloads_table()
        # Tarefas interrompidas recuperadas na inicialização (T04-R2): avisa
        # uma vez que há decisão pendente (r retoma, d descarta).
        self.announce_interrupted()
        # Reconciliação de arquivos/temporários (§5.3/T03), fora do loop:
        # avisa por toast só quando há o que reportar.
        self.start_reconciliation()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Atualiza o conteúdo da aba ao ser ativada."""
        pane_id = getattr(event.pane, "id", None)
        if pane_id == "tab-library":
            self.refresh_library()
        elif pane_id == "tab-playlists":
            self.refresh_playlists()
        elif pane_id == "tab-downloads":
            self.update_downloads_table()

    # ===== Álbuns =====

    def _album_tab(self) -> Tab | None:
        """Retorna o ``Tab`` da aba de álbum (None se ainda não montada)."""
        with contextlib.suppress(Exception):
            tabs = self.query_one("#main-tabs", TabbedContent)
            return cast(Tab | None, tabs.get_tab("tab-album"))
        return None

    def _set_album_tab_visible(self, visible: bool, title: str | None = None) -> None:
        """Mostra/oculta a aba de álbum e ajusta o rótulo com o nome."""
        tab = self._album_tab()
        if tab is None:
            return
        if visible:
            tab.remove_class("-hidden")
            if title:
                tab.label = f"💿 {title[:18]}"
        else:
            tab.add_class("-hidden")

    def _render_album_header(self, album: Album | AlbumDetail) -> None:
        """Título da aba de álbum: nome, artista, ano e nº de faixas."""
        try:
            header = self.query_one("#album-header", Static)
        except Exception:
            return
        title = album.title or "?"
        artist = ", ".join(album.artists)
        year = album.year
        count = album.track_count
        if count is None and isinstance(album, AlbumDetail):
            count = len(album.tracks)
        parts = [part for part in (artist, year, f"{count} faixas" if count else "") if part]
        suffix = f" — {escape_markup(' · '.join(parts))}" if parts else ""
        header.update(f"💿 [b]{escape_markup(title)}[/b]{suffix}")

    def _render_album_message(self, text: str) -> None:
        """Mostra (ou oculta, com texto vazio) a mensagem da aba de álbum."""
        try:
            message = self.query_one("#album-message", Static)
        except Exception:
            return
        message.display = bool(text)
        if text:
            message.update(text)

    def open_album(self, browse_id: str, album_item: Album | None = None) -> None:
        """Abre o álbum na aba dedicada e carrega as faixas em background."""
        if not browse_id:
            self._toast(ALBUM_WITHOUT_ID)
            return
        if album_item:
            # Cabeçalho imediato com os dados da busca; o get_album completa.
            self._render_album_header(album_item)
        self._render_album_message(ALBUM_LOADING)
        self._set_album_tab_visible(True, album_item.title if album_item else None)
        self._switch_tab("tab-album")
        # Época da carga (T12-R2): uma conclusão atrasada de outra carga não
        # sobrepõe a mais recente. exclusive=True: abrir outro álbum cancela
        # a carga anterior; a época cobre a corrida "terminou logo depois".
        self._album_epoch += 1
        self.run_worker(
            self._load_album(browse_id, self._album_epoch), group="album", exclusive=True
        )

    async def _load_album(self, browse_id: str, epoch: int) -> None:
        """Carrega os detalhes do álbum fora da thread da UI."""
        try:
            details = await asyncio.to_thread(ytmusic.get_album, browse_id)
        except Exception as e:
            # T12-R5: erro em 3 partes; escape_markup(): o detalhe pode ter
            # [colchetes] que o parser de markup do Textual comeria (T12-R6).
            if epoch == self._album_epoch:
                self._render_album_message(escape_markup(album_error(str(e))))
            return
        self.display_album(details, epoch=epoch)

    def display_album(self, album: AlbumDetail, epoch: int | None = None) -> None:
        """Preenche a aba de álbum com cabeçalho e lista de faixas.

        ``epoch`` (T12-R2): quando informada e não for mais a época corrente,
        o resultado é descartado sem pintar — a resposta de uma carga antiga
        não pisa na mais recente. ``None`` (chamada direta/síncrona) pinta.
        """
        if epoch is not None and epoch != self._album_epoch:
            logger.debug(
                "Carga de álbum obsoleta descartada (época %s ≠ %s)", epoch, self._album_epoch
            )
            return
        self._current_album = album
        tracks = [track for track in album.tracks if track.video_id and track.is_available]
        self._render_album_header(album)
        self._set_album_tab_visible(True, album.title)
        try:
            view = self.query_one("#album-songs", SongListView)
            button = self.query_one("#btn-download-album", Button)
        except Exception:
            return
        view.set_songs(tracks)
        view.display = bool(tracks)
        button.display = bool(tracks)
        self._render_album_message("" if tracks else ALBUM_EMPTY)
        if tracks:
            # Foco na lista: Enter toca, "a" baixa o álbum, setas navegam.
            self.call_after_refresh(view.focus)

    # ===== Navegação e ações gerais =====

    def _switch_tab(self, tab_id: str) -> None:
        """Troca de aba de forma segura com o teclado.

        O ``TabbedContent`` reativa o painel de qualquer widget que receba
        foco programático (``TabPane.Focused``). Se a troca acontecer com o
        foco ainda dentro do painel antigo, o Textual transfere o foco para
        outro widget desse MESMO painel ao escondê-lo, o painel se reativa e
        a troca se desfaz. Remover o foco antes evita a regressão.
        """
        tabs = self.query_one("#main-tabs", TabbedContent)
        if tabs.active == tab_id:
            return
        with contextlib.suppress(Exception):
            self.set_focus(None)
        tabs.active = tab_id

    def action_search(self) -> None:
        """Foca no input de busca."""
        self._switch_tab("tab-search")
        # O painel pode ser exibido de forma assíncrona; foca depois.
        self.call_after_refresh(self._focus_search)

    def _focus_search(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#search-input", SearchInput).focus()

    def action_library(self) -> None:
        """Abre a aba da biblioteca local."""
        self._switch_tab("tab-library")
        self.refresh_library()

    def action_play_album(self) -> None:
        """Toca o álbum aberto na Biblioteca desde a primeira faixa (R6).

        A fila é a ordem do álbum; só vale no modo álbum da aba Biblioteca
        (fora dele ``check_action`` desativa a tecla — ``t`` não vira atalho
        solto pelo app).
        """
        tracks = self._library_visible_tracks()
        if not tracks:
            return
        self.play_library_track(tracks[0])

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disponibilidade de ações por tela (T12-R1).

        ``r`` (retomar) e ``d`` (descartar) só existem na aba 📥 Downloads:
        fora dela o atalho fica inativo — uma letra digitada em outro
        contexto não dispara comando de download (nem toast fora de hora).
        ``t`` (tocar álbum) só vale com um álbum aberto na Biblioteca (R6);
        ``P`` (álbum → playlist) vale na Biblioteca com o álbum aberto ou
        destacado, e sem álbum alvo explica o gesto em vez de silenciar
        (R5/BUG-01); ``n``/``e``/``delete``/``shift+↑``/
        ``shift+↓`` só valem na aba 🎵 Playlists, e renomear/excluir pedem
        uma playlist alvo (R3). ``p`` (adicionar faixa à playlist) existe em
        qualquer aba — espelho do ``l``, que usa a faixa atual fora da
        Biblioteca (R4).
        """
        if action in (
            "retry_download",
            "discard_download",
            "cancel_download",
            "clear_downloads",
            "show_download_error",
        ):
            try:
                tabs = self.query_one("#main-tabs", TabbedContent)
            except Exception:
                return False
            return bool(tabs.active == "tab-downloads")
        if action == "play_album":
            try:
                tabs = self.query_one("#main-tabs", TabbedContent)
            except Exception:
                return False
            return bool(tabs.active == "tab-library" and self.library_album_open())
        if action == "add_album_to_playlist":
            # Alvo: o álbum aberto ou o destacado no nível 1; sem álbum
            # nenhum a ação roda para explicar o gesto — nunca no-op mudo
            # (BUG-01 de campo).
            try:
                tabs = self.query_one("#main-tabs", TabbedContent)
            except Exception:
                return False
            return bool(tabs.active == "tab-library")
        if action in (
            "new_playlist",
            "rename_playlist",
            "delete_playlist",
            "move_playlist_track_up",
            "move_playlist_track_down",
        ):
            try:
                tabs = self.query_one("#main-tabs", TabbedContent)
            except Exception:
                return False
            if tabs.active != "tab-playlists":
                return False
            if action in ("rename_playlist", "delete_playlist"):
                return self.playlist_has_selection()
            if action in ("move_playlist_track_up", "move_playlist_track_down"):
                return self.playlist_open()
        return True

    def action_help(self) -> None:
        """Abre a ajuda modal montada dos atalhos reais (T12-R1)."""
        self.app.push_screen(HelpScreen([*self.app.BINDINGS, *self.BINDINGS]))

    def action_back_to_search(self) -> None:
        """Esc: volta um nível (aba 💿 Álbum → Busca; detalhes → nível 1)."""
        tabs = self.query_one("#main-tabs", TabbedContent)
        if tabs.active == "tab-album":
            self._switch_tab("tab-search")
            self.call_after_refresh(self._focus_search)
        elif tabs.active == "tab-library" and self.library_album_open():
            self.close_library_album()
        elif tabs.active == "tab-playlists" and self.playlist_open():
            self.close_playlist()

    def action_previous_or_back(self) -> None:
        """← : volta um nível (Biblioteca/Playlists); fora delas, faixa anterior.

        A tecla ← mantém o papel de "faixa anterior" na reprodução; dentro
        dos níveis internos (álbum da Biblioteca — R6; detalhe da playlist
        — R3) ela é o gesto de voltar, mesmo caminho do Esc.
        """
        if self._inner_level_open():
            self.action_back_to_search()
            return
        self.action_previous()

    def _inner_level_open(self) -> bool:
        """True quando há um nível interno aberto com gesto de voltar (R3/R6)."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
        except Exception:
            return False
        if tabs.active == "tab-library":
            return self.library_album_open()
        if tabs.active == "tab-playlists":
            return self.playlist_open()
        return False

    def _toast(self, message: str) -> None:
        """Mostra uma notificação (respeitando settings.show_notifications)."""
        if settings.show_notifications:
            self.notify(message, timeout=4, markup=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Lida com os botões de controle do player e de download de álbum."""
        button_id = event.button.id
        if button_id == "btn-prev":
            player.previous_track()
        elif button_id == "btn-play-pause":
            player.toggle_pause()
        elif button_id == "btn-next":
            player.next_track()
        elif button_id == "btn-download-album":
            self.action_download_album()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            # perform_search é assíncrono: precisa ser agendado como worker
            self.run_worker(self.perform_search(event.value), exclusive=True, group="search")
            return
        if event.input.id is None:
            return
        self._apply_text_setting(event.input.id, event.value)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Aplica seleções da aba Config.

        §12.2 (T12): a Config salva a cada **alteração real** — não existe
        botão Aplicar nem estado "pendente". O guard de valor existe porque
        no mount o Textual emite ``Changed`` com o valor inicial: sem ele, a
        abertura do app salvaria o TOML e avisaria à toa.
        """
        if event.select.id == "setting-audio-quality" and event.value:
            if str(event.value) == str(settings.mpv_audio_quality):
                return  # Changed de mount com o valor atual: não é alteração
            saved = self._save_setting(mpv_audio_quality=str(event.value))
            player.apply_audio_quality()
            if saved:
                self._toast(audio_quality_set(str(event.value)))
        elif event.select.id == "setting-format" and event.value:
            if str(event.value) == str(settings.download_format):
                return
            if self._save_setting(download_format=str(event.value)):
                self._toast(download_format_set(str(event.value)))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Aplica checkboxes da aba Config (§12.2: alteração real salva na hora)."""
        if event.checkbox.id == "setting-notifications":
            if event.value == settings.show_notifications:
                return
            self._save_setting(show_notifications=event.value)
        elif event.checkbox.id == "setting-autoplay":
            if event.value == settings.auto_play_next:
                return
            self._save_setting(auto_play_next=event.value)

    def _save_setting(self, **updates: Any) -> bool:
        """Grava uma alteração da Config; ``True`` = gravou, ``False`` = erro.

        Decisão §12.2: "salva a cada alteração real, sem estado pendente" —
        falha de escrita (§12.2 "erro ao salvar") não derruba a TUI: a
        alteração vale nesta sessão (memória) e um toast em 3 partes avisa
        que não foi gravada (§8.3: detalhe mascarado).
        """
        try:
            settings.update_and_save(**updates)
        except (OSError, ConfigError) as exc:
            # A04: TOML inválido/gravação atômica falharam; a alteração vale
            # nesta sessão (memória) e o toast avisa que não foi gravada.
            self._toast(config_save_error(mask_diagnostics(str(exc))))
            return False
        return True

    def _apply_text_setting(self, input_id: str, raw_value: str) -> None:
        """Aplica campos de texto da aba Config (Enter para confirmar).

        T12-R3 (inventário): ``settings.update_and_save`` grava um TOML
        pequeno e só em ação explícita do usuário — segue síncrono; falha de
        escrita vira toast (§8.3/§12.2, via ``_save_setting``).
        """
        value = raw_value.strip()
        try:
            if input_id == "setting-volume":
                volume = max(0, min(100, int(value)))
                if self._save_setting(mpv_volume=volume):
                    player.set_volume(volume)
                    self.query_one(NowPlayingBar).update_volume(volume)
                    self._toast(volume_set(volume))
            elif input_id == "setting-download-dir":
                path = Path(value).expanduser()
                path.mkdir(parents=True, exist_ok=True)
                if self._save_setting(download_dir=path):
                    self._toast(download_dir_set(path))
            elif input_id == "setting-download-quality":
                if self._save_setting(download_quality=value or "320"):
                    self._toast(download_quality_set(value))
            elif input_id == "setting-concurrent":
                # A UI normaliza antes da API (T06-R4): o valor salvo e
                # aplicado está sempre na faixa aceita pelo gerenciador.
                concurrent = max(MIN_DOWNLOAD_CONCURRENT, min(MAX_DOWNLOAD_CONCURRENT, int(value)))
                if self._save_setting(download_concurrent=concurrent):
                    download_manager.set_concurrent(concurrent)
                    self._toast(concurrent_set(concurrent))
        except (TypeError, ValueError):
            self._toast(INVALID_VALUE)
        except OSError as exc:
            # §8.3/T06: erro ao preparar o diretório (mkdir) traz caminho
            # absoluto — exibe mascarado.
            self._toast(download_dir_error(mask_diagnostics(str(exc))))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Reproduz o item selecionado (ou abre o álbum) em uma lista."""
        view = event.list_view
        if isinstance(view, SongListView):
            song = view.get_selected_song()
            if song and view.id:
                # A origem alimenta o histórico: a lista de faixas do álbum
                # aberto é ``album``; o resultado de busca é ``search``.
                source = "album" if view.id == "album-songs" else "search"
                self.play_song(song, view_id=view.id, source=source)
        elif isinstance(view, AlbumListView):
            album = view.get_selected_album()
            if album:
                self.open_album(album.browse_id, album)
        elif isinstance(view, LibraryListView):
            # R6: Enter num álbum expande dentro da aba; numa avulsa, toca.
            album_entry = view.get_selected_album()
            if album_entry is not None:
                self.open_library_album(album_entry)
                return
            track = view.get_selected_track()
            if track:
                self.play_library_track(track)
        elif isinstance(view, PlaylistListView):
            # R3: Enter numa playlist abre o detalhe dentro da aba.
            playlist = view.get_selected_playlist()
            if playlist:
                self.open_playlist(playlist)
        elif isinstance(view, TrackListView):
            track = view.get_selected_track()
            if track:
                if view.id == "playlist-tracks":
                    self.play_playlist_track(track)
                else:
                    self.play_library_track(track)

    # ===== Busca =====

    async def perform_search(self, query: str) -> None:
        """Executa busca de músicas, vídeos e álbuns."""
        if not query.strip():
            return

        # Toda busca aceita vira a época corrente (T12-R2) — inclusive a que
        # falhar na inicialização: resposta/conclusão de uma busca antiga não
        # pode reaparecer por cima da mais recente.
        self._search_epoch += 1
        epoch = self._search_epoch

        if not ytmusic.initialize():
            if epoch == self._search_epoch:
                self._render_search_message(
                    search_error(SEARCH_INIT_DETAIL, next_action=SEARCH_INIT_NEXT)
                )
            return

        # Nova busca abandona a anterior (T10-R3, A1): o evento faz o retry
        # parar de tentar — a PRIMEIRA tentativa de cada busca sempre roda.
        if self._search_abort is not None:
            self._search_abort.set()
        self._search_abort = threading.Event()
        abort = self._search_abort

        self._render_search_message(SEARCH_STARTING)

        try:
            # search_catalog: músicas + vídeos + álbuns em 3 requisições
            # paralelas (~2s no total, a mesma latência de uma busca só). A
            # busca sem filtro faria 1 requisição, mas caparia as músicas em
            # ~5 itens.
            results = await asyncio.to_thread(
                ytmusic.search_catalog, query, should_abort=abort.is_set
            )
            self.display_search_results(results, epoch=epoch)
        except Exception as e:
            if epoch != self._search_epoch:
                logger.debug("Erro de busca obsoleto descartado (época %s)", epoch)
                return
            # T12-R5: erro em 3 partes; escape_markup(): o detalhe pode ter
            # [colchetes] que o parser de markup do Textual comeria (T12-R6).
            self._render_search_message(escape_markup(search_error(str(e))))

    def _render_search_message(self, text: str) -> None:
        """Mostra uma mensagem no lugar dos resultados (in-place)."""
        try:
            message = self.query_one("#search-message", Static)
            hidden = [
                self.query_one("#search-songs", SongListView),
                self.query_one("#search-songs-title", Static),
                self.query_one("#search-videos", SongListView),
                self.query_one("#search-videos-title", Static),
                self.query_one("#search-albums", AlbumListView),
                self.query_one("#search-albums-title", Static),
            ]
        except Exception:
            return
        for widget in hidden:
            widget.display = False
        message.display = True
        message.update(text)

    def display_search_results(
        self, results: SearchResult | list[Track], epoch: int | None = None
    ) -> None:
        """Exibe resultados da busca: músicas, vídeos e álbuns (in-place).

        Aceita uma lista de músicas (atalho de teste) ou um ``SearchResult``.
        ``epoch`` (T12-R2): quando informada e não for mais a época corrente,
        o resultado é descartado sem pintar a tela; ``None`` pinta sempre.
        """
        if epoch is not None and epoch != self._search_epoch:
            logger.debug(
                "Resultado de busca obsoleto descartado (época %s ≠ %s)", epoch, self._search_epoch
            )
            return
        if isinstance(results, list):
            tracks, videos, albums = list(results), [], []
        else:
            tracks = list(results.tracks)
            videos = list(results.videos)
            albums = list(results.albums)
        tracks = [song for song in tracks if song.video_id]
        videos = [video for video in videos if video.video_id]
        albums = [album for album in albums if album.browse_id]
        if not tracks and not videos and not albums:
            self._render_search_message(SEARCH_NO_RESULTS)
            return

        try:
            songs_view = self.query_one("#search-songs", SongListView)
            songs_title = self.query_one("#search-songs-title", Static)
            videos_view = self.query_one("#search-videos", SongListView)
            videos_title = self.query_one("#search-videos-title", Static)
            albums_view = self.query_one("#search-albums", AlbumListView)
            albums_title = self.query_one("#search-albums-title", Static)
            message = self.query_one("#search-message", Static)
        except Exception:
            return

        songs_view.set_songs(tracks[:25])
        videos_view.set_songs(videos[:20])
        albums_view.set_albums(albums[:15])
        message.display = False
        # Cada seção aparece apenas quando tem itens; as alturas em `fr`
        # fazem as listas restantes absorverem o espaço quando alguma some.
        songs_view.display = songs_title.display = bool(tracks)
        videos_view.display = videos_title.display = bool(videos)
        albums_view.display = albums_title.display = bool(albums)
