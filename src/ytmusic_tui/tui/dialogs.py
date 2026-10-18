"""Telas modais da TUI: Ajuda (T12-R1) e entrada de nome (T17.1/R3)."""

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static

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
    help_shortcut_lines,
    playlist_label,
)

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

    def __init__(self, title: str, placeholder: str = "", initial: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="name-box"):
            yield Static(self._title, id="name-title", markup=False)
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id="name-input",
            )
            yield Static("", id="name-error", markup=False)

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
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
