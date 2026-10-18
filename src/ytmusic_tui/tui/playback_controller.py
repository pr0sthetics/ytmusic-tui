"""Fila de reprodução e ações do player (mixin de ``MainScreen``)."""

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ytmusic_tui.api import ytmusic
from ytmusic_tui.api.models import Track
from ytmusic_tui.config import settings
from ytmusic_tui.library.errors import AudioFileMissingError, describe_library_error
from ytmusic_tui.library.models import Track as LibraryTrack
from ytmusic_tui.player import TrackInfo, player
from ytmusic_tui.tui.messages import TRACK_WITHOUT_VIDEO_ID, play_started, playback_error
from ytmusic_tui.tui.widgets import SongListView


def ensure_audio_file(track: LibraryTrack) -> None:
    """Confere que o arquivo registrado da faixa existe no disco (T05-R4).

    Só vale para faixa com arquivo registrado (``is_downloaded`` e
    ``local_path``): se ele não está mais lá, levanta
    ``AudioFileMissingError`` — a UI mostra a mensagem específica em vez de
    mandar o player abrir um caminho que não existe.
    """
    if track.is_downloaded and track.local_path:
        path = Path(str(track.local_path))
        if not path.exists():
            raise AudioFileMissingError(f"arquivo não encontrado: {path}")


class PlaybackController:
    """Mixin com a fila de reprodução e as ações do player.

    A classe host (``MainScreen``) inicializa ``_play_queue``,
    ``_play_index`` e ``_last_auto_next`` e oferece ``_toast``; as listas
    ``#search-songs``/``#album-songs``/``#library-*`` vêm do layout.
    """

    # Estado e membros fornecidos pela tela host (o mypy analisa cada mixin
    # isoladamente; as declarações abaixo existem só na checagem de tipos).
    _play_queue: list[TrackInfo]
    _play_index: int
    _last_auto_next: float
    if TYPE_CHECKING:

        def query_one(self, selector: Any, expect_type: Any = None) -> Any: ...
        def _toast(self, message: str) -> None: ...
        def _library_visible_tracks(self) -> list[LibraryTrack]: ...

    # ===== Ações do player (atalhos e botões) =====

    def action_play_pause(self) -> None:
        """Alterna play/pause."""
        player.toggle_pause()

    def action_next(self) -> None:
        """Próxima faixa."""
        player.next_track()

    def action_previous(self) -> None:
        """Faixa anterior."""
        player.previous_track()

    # ===== Conversões para TrackInfo =====

    @staticmethod
    def _track_info_from_song(song: Track) -> TrackInfo:
        """Converte a faixa interna da API (T10-R1) no TrackInfo do player."""
        return TrackInfo(
            video_id=song.video_id,
            title=song.title,
            artist=", ".join(song.artists),
            album=song.album_name,
            duration=float(song.duration_seconds or 0),
            thumbnail_url=song.thumbnail_url,
            source_url=ytmusic.get_stream_url(song.video_id),
        )

    @staticmethod
    def _track_info_from_library(track: LibraryTrack) -> TrackInfo:
        """Converte uma faixa da biblioteca em TrackInfo."""
        if track.is_downloaded and track.local_path:
            source_url = str(track.local_path)
        else:
            source_url = ytmusic.get_stream_url(str(track.source_id))
        return TrackInfo(
            video_id=str(track.source_id),
            title=str(track.title),
            artist=", ".join(track.artist_names),
            album=str(track.album.title) if track.album else "",
            source_url=source_url,
        )

    # ===== Fila de reprodução =====

    def play_song(self, song: Track, view_id: str = "search-songs") -> None:
        """Reproduz uma música vinda da API (resultado de busca ou álbum)."""
        info = self._track_info_from_song(song)
        if not info.video_id:
            self._toast(TRACK_WITHOUT_VIDEO_ID)
            return

        view = self.query_one(f"#{view_id}", SongListView)
        index = view.index if view.index is not None else 0
        self._play_queue = [self._track_info_from_song(s) for s in view.songs]
        self._play_index = index if 0 <= index < len(self._play_queue) else 0
        self._start_playback(info)

    def play_library_track(self, track: LibraryTrack) -> None:
        """Reproduz uma faixa da biblioteca local (fila = faixas visíveis).

        A fila acompanha o nível em que o usuário está (álbum aberto ou
        avulsas — R6), começando na faixa selecionada. Arquivo registrado
        que sumiu do disco vira toast específico (T05-R4) — melhor avisar
        do que mandar o player abrir um caminho inexistente; a
        reconciliação marca a faixa na próxima passagem.
        """
        try:
            ensure_audio_file(track)
        except AudioFileMissingError as error:
            self._toast(playback_error(describe_library_error(error)))
            return
        visible = self._library_visible_tracks()
        try:
            index = visible.index(track)
        except ValueError:
            index = 0
        self._play_queue = (
            [self._track_info_from_library(item) for item in visible]
            if visible
            else [self._track_info_from_library(track)]
        )
        self._play_index = index if 0 <= index < len(self._play_queue) else 0
        self._start_playback(self._track_info_from_library(track))

    def _start_playback(self, info: TrackInfo) -> None:
        """Inicia a reprodução de uma faixa."""
        if player.play(info):
            self._toast(play_started(info.title))

    def _play_relative(self, delta: int) -> bool:
        """Toca a faixa na posição atual + delta (fila da última lista usada)."""
        new_index = self._play_index + delta
        if not (0 <= new_index < len(self._play_queue)):
            return False
        self._play_index = new_index
        self._start_playback(self._play_queue[new_index])
        return True

    def _maybe_auto_next(self) -> bool:
        """Avança para a próxima faixa (auto-play), com debounce.

        O fim da faixa gera dois eventos ("track_ended" e "ended"); o
        debounce evita avançar duas vezes.
        """
        if not settings.auto_play_next:
            return False
        now = time.monotonic()
        if now - self._last_auto_next < 1.0:
            return False
        if self._play_index + 1 >= len(self._play_queue):
            return False
        self._last_auto_next = now
        return self._play_relative(1)
