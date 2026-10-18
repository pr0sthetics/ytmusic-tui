"""Widgets personalizados usados pelas telas da TUI."""

from typing import Any, TypeVar

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, ProgressBar, Static

from ytmusic_tui.api.models import Album, Track
from ytmusic_tui.library.models import Playlist
from ytmusic_tui.library.models import Track as LibraryTrack
from ytmusic_tui.library.service import LibraryAlbum
from ytmusic_tui.player import PlayerState, TrackInfo
from ytmusic_tui.tui.messages import (
    MISSING_MARK,
    PLAYER_ERROR_TEXT,
    PLAYER_LOADING_TEXT,
    SINGLES_SECTION_TEXT,
    escape_markup,
    playlist_label,
)

_WidgetT = TypeVar("_WidgetT", bound=Widget)


def _library_track_label(track: LibraryTrack) -> str:
    """Rótulo de faixa da biblioteca — com a marca de ausente (T17.1/R7).

    A marca vem primeiro para sobreviver ao truncamento da linha larga;
    a legenda abaixo da lista explica o significado.
    """
    artists = ", ".join(track.artist_names)
    label = f"{track.title} — {artists}" if artists else str(track.title)
    if track.is_missing:
        label = f"{MISSING_MARK} {label}"
    return label


class AutoHighlightListView(ListView):
    """``ListView`` que destaca o primeiro item ao receber foco.

    Sem isso, focar a lista pelo teclado (Tab) deixa ``index`` em ``None``
    e o Enter não seleciona nada (o usuário precisaria apertar ↓ antes).
    """

    def on_focus(self) -> None:
        if self.index is None and len(self):
            self.index = 0


class TrackListView(AutoHighlightListView):
    """Lista de faixas da biblioteca local."""

    def __init__(self, tracks: list[LibraryTrack] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tracks: list[LibraryTrack] = []
        self.set_tracks(tracks or [])

    def set_tracks(self, tracks: list[LibraryTrack]) -> None:
        """Atualiza a lista sem remontar o widget (evita ``DuplicateIds``).

        Os itens são recriados in-place; por isso não usam ``id``. A marca
        de ausente (R7) também vale para as faixas de playlist.
        """
        self.tracks = list(tracks)
        self.clear()
        for track in self.tracks:
            self.append(ListItem(Label(_library_track_label(track), markup=False)))

    def get_selected_track(self) -> LibraryTrack | None:
        """Retorna faixa selecionada."""
        if self.index is not None and 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None


class LibraryListView(AutoHighlightListView):
    """Nível 1 da biblioteca: álbuns + seção de faixas avulsas (T17.1/R6).

    Cada linha guarda o próprio payload (``LibraryAlbum`` ou faixa) para o
    Enter decidir entre abrir o álbum e tocar a faixa; a seção "Faixas
    avulsas" é um item desabilitado — a navegação pula direto para as faixas.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.albums: list[LibraryAlbum] = []
        self.singles: list[LibraryTrack] = []
        self._entries: list[LibraryAlbum | LibraryTrack | None] = []

    def set_entries(self, albums: list[LibraryAlbum], singles: list[LibraryTrack]) -> None:
        """Atualiza a lista in-place (sem ``id`` nos itens — T12-R2)."""
        self.albums = list(albums)
        self.singles = list(singles)
        self._entries = []
        self.clear()
        for album in self.albums:
            self._entries.append(album)
            self.append(ListItem(Label(self._album_label(album), markup=False)))
        if self.singles:
            self._entries.append(None)  # cabeçalho de seção (desabilitado)
            self.append(ListItem(Label(SINGLES_SECTION_TEXT), disabled=True))
            for track in self.singles:
                self._entries.append(track)
                self.append(ListItem(Label(_library_track_label(track), markup=False)))

    @staticmethod
    def _album_label(entry: LibraryAlbum) -> str:
        label = f"💿 {entry.album.title}"
        if entry.primary_artist_name:
            label += f" — {entry.primary_artist_name}"
        count = entry.track_count
        label += f"  ({'1 faixa' if count == 1 else f'{count} faixas'})"
        if entry.missing_count:
            suffix = "ausente" if entry.missing_count == 1 else "ausentes"
            label += f"  {MISSING_MARK} {entry.missing_count} {suffix}"
        return label

    def get_selected_album(self) -> LibraryAlbum | None:
        """Álbum da linha selecionada (None em avulsa ou na seção)."""
        entry = self._selected_entry()
        return entry if isinstance(entry, LibraryAlbum) else None

    def get_selected_track(self) -> LibraryTrack | None:
        """Faixa avulsa da linha selecionada (None em álbum ou na seção)."""
        entry = self._selected_entry()
        return entry if isinstance(entry, LibraryTrack) else None

    def _selected_entry(self) -> LibraryAlbum | LibraryTrack | None:
        if self.index is not None and 0 <= self.index < len(self._entries):
            return self._entries[self.index]
        return None


class PlaylistListView(AutoHighlightListView):
    """Nível 1 da aba Playlists: ``nome · N faixas`` (T17.1/R3).

    As contagens saem das entradas carregadas junto (``list_playlists``
    com ``selectinload``) — a lista mostra o que existe de fato.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.playlists: list[Playlist] = []

    def set_playlists(self, playlists: list[Playlist]) -> None:
        """Atualiza a lista in-place (sem ``id`` nos itens — T12-R2)."""
        self.playlists = list(playlists)
        self.clear()
        for playlist in self.playlists:
            count = len(playlist.tracks)
            self.append(ListItem(Label(playlist_label(str(playlist.name), count), markup=False)))

    def get_selected_playlist(self) -> Playlist | None:
        """Playlist selecionada (None = nada destacado)."""
        if self.index is not None and 0 <= self.index < len(self.playlists):
            return self.playlists[self.index]
        return None


class SearchInput(Input):
    """Input de busca com placeholder."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(placeholder="🔍 Buscar músicas, artistas, álbuns...", **kwargs)


class SongListView(AutoHighlightListView):
    """Lista de músicas (resultados de busca ou faixas de álbum)."""

    def __init__(self, songs: list[Track] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.songs: list[Track] = []
        self.set_songs(songs or [])

    def set_songs(self, songs: list[Track]) -> None:
        """Atualiza os resultados exibidos (in-place, sem ``id`` nos itens)."""
        self.songs = list(songs)
        self.clear()
        for song in self.songs:
            artists = ", ".join(song.artists)
            label = f"{song.title} — {artists}" if artists else song.title
            if song.duration_text:
                label += f"  ({song.duration_text})"
            # markup=False: títulos reais contêm [colchetes] que o Rich
            # interpretaria como marcação (texto sumia ou quebrava).
            self.append(ListItem(Label(label, markup=False)))

    def get_selected_song(self) -> Track | None:
        """Retorna a música selecionada (modelo interno do T10-R1)."""
        if self.index is not None and 0 <= self.index < len(self.songs):
            return self.songs[self.index]
        return None


class AlbumListView(AutoHighlightListView):
    """Lista de álbuns (itens de busca)."""

    def __init__(self, albums: list[Album] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.albums: list[Album] = []
        self.set_albums(albums or [])

    def set_albums(self, albums: list[Album]) -> None:
        """Atualiza a lista exibida (in-place, sem ``id`` nos itens)."""
        self.albums = list(albums)
        self.clear()
        for album in self.albums:
            artists = ", ".join(album.artists)
            kind = album.kind or "Álbum"
            label = f"💿 {album.title}"
            if artists:
                label += f" — {artists}"
            meta = ", ".join(part for part in (kind, album.year) if part)
            label += f"  ({meta})"
            # markup=False: títulos reais contêm [colchetes] que o Rich
            # interpretaria como marcação.
            self.append(ListItem(Label(label, markup=False)))

    def get_selected_album(self) -> Album | None:
        """Retorna o álbum selecionado (modelo interno do T10-R1)."""
        if self.index is not None and 0 <= self.index < len(self.albums):
            return self.albums[self.index]
        return None


class NowPlayingBar(Static):
    """Barra de reprodução atual na parte inferior."""

    DEFAULT_CSS = """
    NowPlayingBar {
        dock: bottom;
        height: 4;
        background: $surface;
        border-top: solid $primary;
        padding: 0 2;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.current_track: TrackInfo | None = None

    def _child(self, selector: str, expect_type: type[_WidgetT]) -> _WidgetT | None:
        """Filho por seletor — ou ``None`` se a subárvore já foi podada (Q20).

        No teardown o Textual remove os filhos da barra antes de a fila de
        mensagens parar de drenar; ``query_one`` levantaria ``NoMatches`` e
        derrubaria o app (flake do L2, run 39132588234). Sem match, o update
        é simplesmente ignorado — a semântica normal não muda.
        """
        matches = self.query(selector)
        if not matches:
            return None
        return matches.first(expect_type)

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("", id="np-info"),
            Horizontal(
                Button("⏮", id="btn-prev", variant="default"),
                Button("⏯", id="btn-play-pause", variant="primary"),
                Button("⏭", id="btn-next", variant="default"),
                id="np-controls",
            ),
            Horizontal(
                Static("0:00", id="np-time-current"),
                ProgressBar(id="np-progress", show_percentage=False, show_eta=False),
                Static("0:00", id="np-time-total"),
                id="np-progress-container",
            ),
            Static("🔊 50%", id="np-volume"),
            id="np-container",
        )

    def update_track(self, track: TrackInfo) -> None:
        """Atualiza faixa em reprodução."""
        self.current_track = track
        info = self._child("#np-info", Static)
        if info is None:
            return
        # escape_markup(): o título/artista vêm do YouTube e podem conter
        # [colchetes] que o parser de markup do Textual comeria (T12-R6).
        info.update(f"[b]{escape_markup(track.title)}[/b] — {escape_markup(track.artist)}")

    def on_resize(self, event: events.Resize) -> None:
        """Adapta a barra ao tamanho do terminal (T12-R6).

        A linha única (faixa + controles + progresso + volume) não cabe em
        terminais estreitos: o volume sai primeiro (< 100 colunas) e, bem
        estreito (< 68), sai também o bloco de progresso — faixa e botões
        (o essencial de tocar/pausar) ficam sempre visíveis.
        """
        width = event.size.width
        volume = self._child("#np-volume", Static)
        if volume is not None:
            volume.display = width >= 100
        progress = self._child("#np-progress-container", Horizontal)
        if progress is not None:
            progress.display = width >= 68

    def update_position(self, position: float, duration: float) -> None:
        """Atualiza posição da reprodução."""
        progress = self._child("#np-progress", ProgressBar)
        time_current = self._child("#np-time-current", Static)
        time_total = self._child("#np-time-total", Static)

        if progress is not None:
            progress.progress = (position / duration * 100) if duration > 0 else 0
        if time_current is not None:
            time_current.update(self._format_time(position))
        if time_total is not None:
            time_total.update(self._format_time(duration))

    def update_state(self, state: PlayerState) -> None:
        """Atualiza o botão play/pause e o texto de estado (§12.2/T11-R3).

        Estados da §12.2: parado (`stop`/`ended`), **carregando** (`loading`,
        o player emite antes de abrir o stream), tocando (`play`/`resume`),
        pausado (`pause`) e falha (`error`). ``ERROR`` mostra "falha" no lugar
        da faixa e fica visível até o próximo estado (o toast do app traz o
        detalhe mascarado; um novo ``play`` chama ``update_track`` e substitui
        o texto).
        """
        btn = self._child("#btn-play-pause", Button)
        if btn is not None:
            if state == PlayerState.PLAYING:
                btn.label = "⏸"
            elif state == PlayerState.LOADING:
                btn.label = "⏳"
            elif state == PlayerState.ERROR:
                btn.label = "⚠"
            else:
                btn.label = "▶"
        info = self._child("#np-info", Static)
        if info is not None:
            if state == PlayerState.ERROR:
                info.update(f"[b]{PLAYER_ERROR_TEXT}[/b]")
            elif state == PlayerState.LOADING:
                info.update(PLAYER_LOADING_TEXT)

    def update_volume(self, volume: int) -> None:
        """Atualiza volume."""
        vol_widget = self._child("#np-volume", Static)
        if vol_widget is None:
            return
        icon = "🔇" if volume == 0 else "🔈" if volume < 30 else "🔉" if volume < 70 else "🔊"
        vol_widget.update(f"{icon} {volume}%")

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Formata tempo em MM:SS."""
        if seconds < 0:
            return "0:00"
        m, s = divmod(int(seconds), 60)
        return f"{m}:{s:02d}"
