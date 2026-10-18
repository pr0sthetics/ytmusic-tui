"""Aplicação TUI principal usando Textual.

A estrutura da TUI é dividida em módulos:

- ``widgets.py``: widgets personalizados;
- ``screens.py``: ``MainScreen`` (layout, atalhos e navegação);
- ``playback_controller.py``, ``download_controller.py`` e
  ``library_controller.py``: mixins de comportamento da tela principal.

Este módulo fica com a classe ``YTMusicTUI`` (ciclo de vida do app) e o
ponto de entrada ``main()``.
"""

import logging
import threading

from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.message import Message
from textual.timer import Timer
from textual.widgets import TabbedContent

from ytmusic_tui.api import ytmusic
from ytmusic_tui.download import download_manager
from ytmusic_tui.library import db_manager
from ytmusic_tui.library.errors import LibraryError
from ytmusic_tui.log import log_event, setup_logging
from ytmusic_tui.player import PlayerEvent, PlayerState, TrackInfo, player
from ytmusic_tui.tui.dialogs import HelpScreen
from ytmusic_tui.tui.messages import player_error
from ytmusic_tui.tui.screens import MainScreen
from ytmusic_tui.tui.widgets import NowPlayingBar
from ytmusic_tui.versions import versions_summary

logger = logging.getLogger(__name__)


class PlayerEventMessage(Message):
    """Evento do player entregue à thread da UI (T11.1/Q14).

    Nasce na thread de eventos do libmpv: entra na fila do app por
    ``post_message`` (thread-safe e não bloqueante) e o handler roda na
    thread principal — onde os widgets podem ser tocados.
    """

    def __init__(self, event: PlayerEvent) -> None:
        super().__init__()
        self.event = event


class YTMusicTUI(App):
    """Aplicação principal."""

    TITLE = "YouTube Music TUI"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair", show=True),
        Binding("f1", "help", "Ajuda", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._ui_timer: Timer | None = None
        #: Erro tipado da abertura do banco (T05): a UI mostra a mensagem
        #: específica do caso; falha de banco nunca derruba o app.
        self.db_error: LibraryError | None = None

    def compose(self) -> ComposeResult:
        # Nada a compor: as telas são instaladas em on_mount. Retornar uma
        # `Screen` aqui a tornaria filha da tela padrão, e não a tela ativa.
        return
        yield  # pragma: no cover

    def on_mount(self) -> None:
        """Inicialização após montagem."""
        # T13 (§13.1): primeiro registro da sessão — com versões, para o
        # log dizer sozinho QUAL combinação de app/Python/dependências rodou.
        log_event("tui.app", "app_start", versions_summary())
        # Abrir o banco na versão do esquema (T05-R1/R2): banco novo nasce
        # no esquema atual; legado ganha backup e migra; incompatível vira
        # erro tipado — nunca create_all silencioso. Falha não derruba o
        # app: a aba Biblioteca mostra o caso (T05-R4).
        # T12-R3 (inventário): migração/recuperação ficam no loop de
        # propósito — são uma vez por sessão e precisam terminar antes de
        # qualquer operação de banco (mover criaria janela de corrida).
        try:
            db_manager.migrate()
        except LibraryError as error:
            self.db_error = error
            log_event(
                "tui.app",
                "db_open_failed",
                f"Falha ao preparar o banco da biblioteca: {error}",
                level=logging.ERROR,
                error_code=type(error).__name__,
            )

        # T04-R2: tarefas deixadas por uma execução anterior viram INTERRUPTED
        # e entram na fila, para a UI oferecer retomar (r) / descartar (d).
        # Depois da migração: a tabela do espelho precisa existir.
        if self.db_error is None:
            download_manager.recover_interrupted()

        # Inicializar API (cliente anônimo; nenhum login necessário)
        ytmusic.initialize()

        # Configurar callbacks do player
        player.on_event(self._on_player_event)

        # Timer único da UI: a tabela de downloads muda sem gerar eventos;
        # a barra de reprodução é atualizada por eventos do player.
        self._ui_timer = self.set_interval(0.5, self._refresh_downloads_ui)

        # Instalar a tela principal
        self.push_screen(MainScreen())

    def action_help(self) -> None:
        """Ajuda via ``App.BINDINGS`` (T12-R1): a MainScreen também a define.

        O binding ``f1`` do app só é alcançado quando nenhuma tela com o
        próprio ``f1`` está no topo (ex.: outra modal aberta); garantir a
        action aqui evita "atalho anunciado sem ação".
        """
        self.push_screen(HelpScreen([*self.BINDINGS, *MainScreen.BINDINGS]))

    def _on_player_event(self, event: PlayerEvent) -> None:
        """Callback de eventos do player (pode vir da thread do libmpv)."""
        if threading.current_thread() is threading.main_thread():
            self._handle_player_event(event)
            return
        # Observers/eventos do mpv rodam na thread de eventos do libmpv;
        # widgets do Textual só podem ser tocados na thread principal.
        # T11.1 (Q14): entrega por `post_message` — thread-safe e NÃO
        # bloqueante. O `call_from_thread` anterior prendia a thread do
        # libmpv até o loop processar o evento; se a entrega caísse na
        # janela do encerramento (loop parado no `on_unmount`), o `join()`
        # do `terminate()` esperava para sempre essa thread — o deadlock
        # dos dumps do CI (2026-10-17). Evento em voo durante o teardown
        # pode ser descartado pela guarda de fechamento do `post_message`,
        # como manda a semântica do T11-R2.
        self.post_message(PlayerEventMessage(event))

    def on_player_event_message(self, message: PlayerEventMessage) -> None:
        """Entrega na thread da UI o evento que nasceu na thread do libmpv.

        T17.1/Q20: durante o teardown a fila ainda drena por um instante,
        mas a árvore já foi podada — o evento tardio é descartado; não há
        UI para atualizar (a blindagem da ``NowPlayingBar`` cobre o resto).
        """
        if self._is_shutting_down():
            return
        self._handle_player_event(message.event)

    def _is_shutting_down(self) -> bool:
        """True quando o app está fechando (Q20/T17.1).

        No teardown do Textual ``_running`` vira ``False`` no início do
        ``_shutdown``, antes de as telas serem podadas — é o discriminador
        exato da janela em que a mensagem não tem mais onde pintar.
        """
        return not self.is_running

    def _current_main_screen(self) -> MainScreen | None:
        """Tela principal ativa, ou ``None`` se não houver tela na pilha.

        O timer da UI e os observers do player podem disparar durante o
        encerramento — entre o fechamento das telas e o ``stop`` do timer em
        ``on_unmount``. Nesse instante ``self.screen`` levanta
        ``ScreenStackError``; o app não pode morrer no teardown (flake
        reproduzido pela suíte completa na corrida baseline do T08).
        """
        try:
            screen = self.screen
        except ScreenStackError:
            return None
        return screen if isinstance(screen, MainScreen) else None

    def _handle_player_event(self, event: PlayerEvent) -> None:
        """Atualiza a UI em resposta a um evento do player."""
        screen = self._current_main_screen()
        if screen is None:
            return
        try:
            np_bar = screen.query_one("#now-playing", NowPlayingBar)
        except Exception:
            return

        name = event.event
        if name == "loading":
            # §12.2 (T12): "carregando" é estado visível — o player emite
            # antes de abrir o stream; "play" chega em seguida com a faixa.
            np_bar.update_state(PlayerState.LOADING)
        elif name == "play":
            np_bar.update_track(event.data.get("track", TrackInfo()))
            np_bar.update_state(PlayerState.PLAYING)
            np_bar.update_volume(player.get_volume())
        elif name == "pause":
            np_bar.update_state(PlayerState.PAUSED)
        elif name == "resume":
            np_bar.update_state(PlayerState.PLAYING)
        elif name == "stop":
            np_bar.update_track(TrackInfo())
            np_bar.update_state(PlayerState.STOPPED)
        elif name == "ended":
            # Se o auto-play já iniciou outra faixa, não limpar a barra.
            if player.current_track is None:
                np_bar.update_track(TrackInfo())
                np_bar.update_state(PlayerState.STOPPED)
        elif name == "track_ended":
            screen._maybe_auto_next()
        elif name == "next":
            screen._play_relative(1)
        elif name == "previous":
            screen._play_relative(-1)
        elif name == "position":
            np_bar.update_position(
                event.data.get("position", 0),
                event.data.get("duration", 0),
            )
        elif name == "error":
            np_bar.update_state(PlayerState.ERROR)
            # T12-R5: falha do player em 3 partes (o detalhe já vem mascarado
            # do ``_emit``; o formatador reaplica §8.3 de qualquer forma).
            screen._toast(player_error(str(event.data.get("message", ""))))

    def _refresh_downloads_ui(self) -> None:
        """Atualiza a tabela de downloads (somente quando a aba está ativa)."""
        screen = self._current_main_screen()
        if screen is None:
            return
        try:
            tabs = screen.query_one("#main-tabs", TabbedContent)
            if tabs.active == "tab-downloads":
                screen.update_downloads_table()
        except Exception:
            pass

    def on_unmount(self) -> None:
        """Limpeza ao sair."""
        if self._ui_timer:
            self._ui_timer.stop()
        # T11-R2: sai da lista antes de encerrar — um app desmontado não pode
        # voltar a receber eventos se o player for reiniciado (novo mount no
        # mesmo processo, como nos testes).
        player.remove_callback(self._on_player_event)
        player.shutdown()
        download_manager.shutdown()
        db_manager.close()
        # T13 (§13.1): o "app_stop" fecha a sessão do log — e fica no FIM do
        # unmount de propósito: nada é registrado depois dele. (O
        # posicionamento também estreitava a janela de deadlock do
        # `call_from_thread`; o T11.1/Q14 fechou a janela na raiz — a
        # entrega player→UI não bloqueia mais a thread do libmpv.)
        log_event("tui.app", "app_stop", versions_summary())


def main() -> None:
    """Ponto de entrada principal."""
    try:
        setup_logging()
    except OSError:
        # Sem permissão para gravar o log, o app segue funcionando; o
        # NullHandler evita que mensagens caiam no stderr e sujem a TUI.
        logging.getLogger("ytmusic_tui").addHandler(logging.NullHandler())
    app = YTMusicTUI()
    app.run()


if __name__ == "__main__":
    main()
