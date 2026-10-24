"""Telas modais da TUI: Ajuda (T12-R1), entrada de nome (T17.1/R3) e fila (P01)."""

from collections.abc import Callable, Iterable

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from ytmusic_tui.library.models import Playlist
from ytmusic_tui.tui.messages import (
    HELP_FOOTER,
    HELP_KEYS_TITLE,
    HELP_NAV_LINES,
    HELP_NAV_TITLE,
    HELP_TITLE,
    PLAYLIST_NAME_REQUIRED,
    PLAYLIST_PICKER_EMPTY,
    PLAYLIST_PICKER_NEW,
    QUEUE_CURRENT_MARK,
    QUEUE_EMPTY_TEXT,
    QUEUE_ITEM_MARK,
    QUEUE_TITLE,
    help_shortcut_lines,
    playlist_label,
    queue_item_label,
)
from ytmusic_tui.tui.queue import PlaybackQueue, QueueItem

#: Sentinela devolvido pelo picker quando o usuário escolhe "➕ Nova playlist…".
PICKER_NEW_PLAYLIST = "new"


class HelpScreen(ModalScreen[None]):
    """Ajuda modal com navegação e atalhos reais (F1 abre, Esc fecha).

    O conteúdo dos atalhos é montado a partir de ``App.BINDINGS +
    MainScreen.BINDINGS`` no momento de abrir — a ajuda não guarda uma
    cópia própria da lista (era o toast com texto duplicado que ficava
    desatualizado).
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Fechar"),
        Binding("f1", "dismiss", "Fechar"),
    ]

    def __init__(self, bindings: Iterable[BindingType]) -> None:
        super().__init__()
        self._help_bindings: list[BindingType] = list(bindings)

    def compose(self) -> ComposeResult:
        lines = [
            f"[b]{HELP_TITLE}[/b]",
            "",
            f"[b]{HELP_NAV_TITLE}[/b]",
            *HELP_NAV_LINES,
            "",
            f"[b]{HELP_KEYS_TITLE}[/b]",
            *help_shortcut_lines(self._help_bindings),
            "",
            HELP_FOOTER,
        ]
        with Vertical(id="help-box"):
            yield Static("\n".join(lines), id="help-content", markup=True)


class ErrorDetailScreen(ModalScreen[None]):
    """Detalhe completo do erro de uma tarefa de download (A09).

    A tabela mostra só um resumo curto; ``i`` abre este modal com o texto
    integral (já mascarado por ``_set_failed``). Esc/Enter/i fecham — sem
    botão destrutivo: ler o erro nunca altera nada.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Fechar"),
        Binding("enter", "dismiss", "Fechar"),
        Binding("i", "dismiss", "Fechar"),
    ]

    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self._title = title
        self._detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-box"):
            yield Static(self._title, id="detail-title", markup=False)
            yield Static(self._detail, id="detail-body", markup=False)


class ConfirmScreen(ModalScreen[bool]):
    """Confirmação modal de ação destrutiva (T12-R4).

    Teclado primeiro: ``y``/Enter confirmam, ``n``/Esc cancelam. O resultado
    sai por ``dismiss`` para o callback de ``push_screen`` — a ação
    destrutiva só roda depois do "sim". Texto sem markup (título de faixa
    pode conter ``[colchetes]``).
    """

    BINDINGS = [
        Binding("y", "confirm", "Confirmar"),
        Binding("enter", "confirm", "Confirmar"),
        Binding("n", "cancel", "Cancelar"),
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._message, id="confirm-message", markup=False)
            with Horizontal(id="confirm-buttons"):
                yield Button("Confirmar", id="confirm-yes", variant="error")
                yield Button("Cancelar", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """A17: mouse também confirma/cancela (o README promete mouse)."""
        if event.button.id == "confirm-yes":
            self.action_confirm()
        elif event.button.id == "confirm-no":
            self.action_cancel()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class NameInputScreen(ModalScreen[str | None]):
    """Pede um nome: criar/renomear playlist (T17.1/R3).

    Teclado primeiro: Enter confirma (o nome é aparamado; vazio é recusado
    com aviso no próprio modal), Esc cancela devolvendo ``None`` — o
    callback de ``push_screen`` só cria/renomeia depois de um nome válido.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(
        self,
        title: str,
        placeholder: str = "",
        initial: str = "",
        submit_label: str = "Criar",
    ) -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial
        self._submit_label = submit_label

    def compose(self) -> ComposeResult:
        with Vertical(id="name-box"):
            yield Static(self._title, id="name-title", markup=False)
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id="name-input",
            )
            yield Static("", id="name-error", markup=False)
            with Horizontal(id="name-buttons"):
                yield Button(self._submit_label, id="name-submit", variant="primary")
                yield Button("Cancelar", id="name-cancel")

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """A17: confirma/cancela pelo mouse, com a mesma validação do Enter."""
        if event.button.id == "name-submit":
            self._submit(self.query_one("#name-input", Input).value)
        elif event.button.id == "name-cancel":
            self.action_cancel()

    def _submit(self, raw_name: str) -> None:
        name = raw_name.strip()
        if not name:
            self.query_one("#name-error", Static).update(PLAYLIST_NAME_REQUIRED)
            return
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PlaylistPickerScreen(ModalScreen["Playlist | str | None"]):
    """Escolhe uma playlist (ou cria uma nova) para receber faixas (T17.1/R4/R5).

    Devolve a playlist escolhida, o sentinela :data:`PICKER_NEW_PLAYLIST`
    para "➕ Nova playlist…" ou ``None`` (Esc cancela). A última linha da
    lista é sempre a opção de criar — com zero playlists o picker continua
    útil. As contagens vêm das entradas carregadas pelo host antes de abrir
    o modal; aqui só se desenha o que foi entregue.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(self, playlists: list[Playlist], title: str) -> None:
        super().__init__()
        self._playlists = list(playlists)
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(self._title, id="picker-title", markup=False)
            if not self._playlists:
                yield Static(PLAYLIST_PICKER_EMPTY, id="picker-empty", markup=False)
            yield ListView(id="picker-list")
            with Horizontal(id="picker-buttons"):
                yield Button("Cancelar", id="picker-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """A17: fecha o picker pelo mouse sem escolher playlist."""
        if event.button.id == "picker-cancel":
            self.action_cancel()

    def on_mount(self) -> None:
        view = self.query_one("#picker-list", ListView)
        for playlist in self._playlists:
            count = len(playlist.tracks)
            view.append(ListItem(Label(playlist_label(str(playlist.name), count), markup=False)))
        view.append(ListItem(Label(PLAYLIST_PICKER_NEW, markup=False)))
        view.focus()
        view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = self.query_one("#picker-list", ListView).index
        if index is None:
            return
        if 0 <= index < len(self._playlists):
            self.dismiss(self._playlists[index])
            return
        self.dismiss(PICKER_NEW_PLAYLIST)

    def action_cancel(self) -> None:
        self.dismiss(None)


class QueueScreen(ModalScreen[None]):
    """Fila de reprodução: ver, tocar agora, remover e reordenar (Fase 2/P01).

    Opera diretamente sobre a ``PlaybackQueue`` (o modelo é do host) e avisa
    quem abriu por ``on_play`` quando uma faixa precisa começar a tocar. Um
    listener na fila mantém a lista em dia — se o auto-next trocar a faixa
    com o modal aberto, o marcador ``▶`` acompanha.

    Clique ou Enter numa linha toca a faixa (é o comportamento do
    ``ListView`` do Textual); os botões dão "▶ Tocar" e "✕ Remover"
    explícitos para o mouse. ``Esc`` fecha.
    """

    BINDINGS = [
        Binding("escape", "close", "Fechar"),
        Binding("delete", "remove_selected", "Remover"),
        Binding("shift+up", "move_up", "Mover ↑"),
        Binding("shift+down", "move_down", "Mover ↓"),
    ]

    def __init__(self, queue: PlaybackQueue, on_play: Callable[[QueueItem], None]) -> None:
        super().__init__()
        self._queue = queue
        self._on_play = on_play
        #: Uid por linha, para restaurar a seleção depois de um refresh.
        self._uids: list[int] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="queue-box"):
            yield Static(QUEUE_TITLE, id="queue-title", markup=False)
            yield Static(QUEUE_EMPTY_TEXT, id="queue-empty", markup=False)
            yield ListView(id="queue-list")
            with Horizontal(id="queue-buttons"):
                yield Button("▶ Tocar", id="queue-play", variant="primary")
                yield Button("✕ Remover", id="queue-remove")
                yield Button("Fechar", id="queue-close")

    def on_mount(self) -> None:
        self._queue.add_listener(self._refresh)
        self._rebuild()
        self.query_one("#queue-list", ListView).focus()

    def on_unmount(self) -> None:
        """Sai da lista de listeners: o modal não existe mais."""
        self._queue.remove_listener(self._refresh)

    # ===== Renderização =====

    def _refresh(self) -> None:
        """Listener da fila: redesenha se o modal ainda estiver montado."""
        if not self.is_mounted:
            return
        self._rebuild()

    def _rebuild(self) -> None:
        """Redesenha as linhas, preservando a seleção pelo uid."""
        try:
            view = self.query_one("#queue-list", ListView)
            empty = self.query_one("#queue-empty", Static)
        except Exception:
            return

        selected = self._selected_uid()
        current = self._queue.current
        items = self._queue.items

        empty.display = not items
        view.display = bool(items)
        view.clear()
        self._uids = []
        for item in items:
            is_current = current is not None and item.uid == current.uid
            mark = QUEUE_CURRENT_MARK if is_current else QUEUE_ITEM_MARK
            view.append(ListItem(Label(queue_item_label(item, mark), markup=False)))
            self._uids.append(item.uid)

        if not items:
            view.index = None
            return
        index = (
            self._uids.index(selected)
            if selected in self._uids
            else min(self._queue.index, len(self._uids) - 1)
        )
        view.index = max(0, index)

    def _selected_uid(self) -> int | None:
        """Uid da linha selecionada agora (None sem seleção)."""
        view = self.query_one("#queue-list", ListView)
        if view.index is None or not 0 <= view.index < len(self._uids):
            return None
        return self._uids[view.index]

    def _selected_index(self) -> int | None:
        view = self.query_one("#queue-list", ListView)
        if view.index is None or not 0 <= view.index < len(self._queue):
            return None
        return view.index

    # ===== Ações =====

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter ou clique numa linha: toca a faixa agora."""
        if event.list_view.id != "queue-list":
            return
        self.action_play_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Mouse: tocar, remover ou fechar (o README promete mouse)."""
        if event.button.id == "queue-play":
            self.action_play_selected()
        elif event.button.id == "queue-remove":
            self.action_remove_selected()
        elif event.button.id == "queue-close":
            self.action_close()

    def action_play_selected(self) -> None:
        """Toca a linha selecionada sem fechar o modal."""
        index = self._selected_index()
        if index is None:
            return
        item = self._queue.play_at(index)
        if item is not None:
            self._on_play(item)

    def action_remove_selected(self) -> None:
        """Remove a linha selecionada (a fila decide o que passa a tocar).

        Se a faixa removida era a que tocava, a que assumiu o lugar começa a
        tocar de verdade — ``queue.remove`` só move o índice. Remover uma
        faixa futura ou já tocada não interrompe nada.
        """
        uid = self._selected_uid()
        if uid is None:
            return
        before = self._queue.current
        self._queue.remove(uid)
        after = self._queue.current
        if after is not None and (before is None or after.uid != before.uid):
            self._on_play(after)

    def action_move_up(self) -> None:
        """Sobe a linha selecionada (Shift+↑)."""
        self._move(-1)

    def action_move_down(self) -> None:
        """Desce a linha selecionada (Shift+↓)."""
        self._move(1)

    def _move(self, delta: int) -> None:
        uid = self._selected_uid()
        if uid is None:
            return
        self._queue.move(uid, delta)

    def action_close(self) -> None:
        """Fecha o modal (Esc)."""
        self.dismiss(None)
