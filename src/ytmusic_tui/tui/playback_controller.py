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
from ytmusic_tui.tui.messages import (
    NO_TRACK_PLAYING,
    TRACK_WITHOUT_VIDEO_ID,
    play_started,
    playback_error,
)
from ytmusic_tui.tui.queue import PlaybackQueue, QueueItem
from ytmusic_tui.tui.widgets import NowPlayingBar, SongListView


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

    O estado da fila vive em ``self.queue`` (:class:`PlaybackQueue`) — a
    política de repeat/shuffle e as edições ficam lá, testáveis sem UI. A
    classe host (``MainScreen``) inicializa ``queue`` e ``_last_auto_next``
    e oferece ``_toast``; as listas ``#search-songs``/``#album-songs``/
    ``#library-*`` vêm do layout.
    """

    # Estado e membros fornecidos pela tela host (o mypy analisa cada mixin
    # isoladamente; as declarações abaixo existem só na checagem de tipos).
    queue: PlaybackQueue
    _last_auto_next: float
    if TYPE_CHECKING:

        def query_one(self, selector: Any, expect_type: Any = None) -> Any: ...
        def _toast(self, message: str) -> None: ...
        def _library_visible_tracks(self) -> list[LibraryTrack]: ...
        def _playlist_visible_tracks(self) -> list[LibraryTrack]: ...

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

    # ===== Seek, volume e mute (Fase 2/P02) =====

    #: Passo do seek e do volume pelas teclas (segundos e pontos).
    SEEK_STEP = 5.0
    VOLUME_STEP = 5

    def action_seek_backward(self) -> None:
        """Retrocede 5 s (tecla ``,``)."""
        self._seek_relative(-self.SEEK_STEP)

    def action_seek_forward(self) -> None:
        """Avança 5 s (tecla ``.``)."""
        self._seek_relative(self.SEEK_STEP)

    def _seek_relative(self, seconds: float) -> bool:
        """Seek relativo com aviso quando não há faixa (P02)."""
        if player.seek_relative(seconds):
            return True
        self._toast(NO_TRACK_PLAYING)
        return False

    def action_volume_up(self) -> None:
        """Aumenta o volume em 5 pontos (tecla ``+``)."""
        self._change_volume(self.VOLUME_STEP)

    def action_volume_down(self) -> None:
        """Diminui o volume em 5 pontos (tecla ``-``)."""
        self._change_volume(-self.VOLUME_STEP)

    def _change_volume(self, delta: int) -> int:
        """Aplica o passo e espelha na barra na hora."""
        volume = player.set_volume(player.get_volume() + delta)
        self._sync_volume_bar(volume)
        return volume

    def action_mute(self) -> None:
        """Alterna o mute (tecla ``m``)."""
        player.toggle_mute()
        self._sync_volume_bar(player.get_volume())

    def _sync_volume_bar(self, volume: int) -> None:
        """Atualiza o indicador de volume/mute da barra (P02)."""
        try:
            bar = self.query_one("#now-playing", NowPlayingBar)
        except Exception:
            return
        bar.update_volume(volume, muted=player.is_muted())

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

    def _queue_item_from_song(self, song: Track, source: str = "search") -> QueueItem:
        """Item de fila a partir de uma faixa da API."""
        return QueueItem(info=self._track_info_from_song(song), source=source)

    def _queue_item_from_library(self, track: LibraryTrack, source: str = "library") -> QueueItem:
        """Item de fila a partir de uma faixa da biblioteca.

        O ``track_id`` é o vínculo com a biblioteca local (curtidas,
        histórico e contadores); faixa só de stream fica sem id.
        """
        track_id = getattr(track, "id", None)
        return QueueItem(
            info=self._track_info_from_library(track),
            track_id=int(track_id) if track_id is not None else None,
            source=source,
        )

    # ===== Fila de reprodução =====

    def play_song(self, song: Track, view_id: str = "search-songs", source: str = "search") -> None:
        """Reproduz uma música vinda da API (resultado de busca ou álbum).

        A fila é a lista visível a partir da faixa escolhida — o gesto
        "toquei esta" continua valendo com o shuffle ligado.
        """
        info = self._track_info_from_song(song)
        if not info.video_id:
            self._toast(TRACK_WITHOUT_VIDEO_ID)
            return

        view = self.query_one(f"#{view_id}", SongListView)
        index = view.index if view.index is not None else 0
        items = [self._queue_item_from_song(item, source=source) for item in view.songs]
        self._set_queue(items, start=index)
        self._play_current()

    def play_library_track(self, track: LibraryTrack) -> None:
        """Reproduz uma faixa da biblioteca local (fila = faixas visíveis).

        A fila acompanha o nível em que o usuário está (álbum aberto ou
        avulsas — R6), começando na faixa selecionada. Arquivo registrado
        que sumiu do disco vira toast específico (T05-R4) — melhor avisar
        do que mandar o player abrir um caminho inexistente; a
        reconciliação marca a faixa na próxima passagem.
        """
        if not self._guard_audio_file(track):
            return
        self._play_from_list(self._library_visible_tracks(), track, source="library")

    def play_playlist_track(self, track: LibraryTrack) -> None:
        """Reproduz uma faixa de playlist (fila = ordem da playlist — R3).

        Mesma checagem de arquivo registrado da biblioteca (T05-R4); a
        fila segue a ordem das posições, começando na faixa escolhida.
        """
        if not self._guard_audio_file(track):
            return
        playlist_id = getattr(getattr(self, "_playlist_open", None), "id", None)
        source = f"playlist:{playlist_id}" if playlist_id is not None else "playlist"
        self._play_from_list(self._playlist_visible_tracks(), track, source=source)

    def _guard_audio_file(self, track: LibraryTrack) -> bool:
        """Confere o arquivo registrado; avisa e devolve False se sumiu."""
        try:
            ensure_audio_file(track)
        except AudioFileMissingError as error:
            self._toast(playback_error(describe_library_error(error)))
            return False
        return True

    def _play_from_list(
        self, visible: list[LibraryTrack], track: LibraryTrack, source: str = "library"
    ) -> None:
        """Toca ``track`` com a fila nas faixas visíveis, a partir dela."""
        try:
            index = visible.index(track)
        except ValueError:
            index = 0
        items = (
            [self._queue_item_from_library(item, source=source) for item in visible]
            if visible
            else [self._queue_item_from_library(track, source=source)]
        )
        self._set_queue(items, start=index)
        self._play_current()

    def _set_queue(self, items: list[QueueItem], start: int) -> QueueItem | None:
        """Define a fila; devolve o item que passou a ser o atual."""
        self.queue.set_items(items, start=max(0, start))
        return self.queue.current

    def _play_current(self) -> bool:
        """Toca a faixa atual da fila (a fila já decidiu qual é)."""
        item = self.queue.current
        if item is None:
            return False
        return self._start_playback(item.info)

    def _start_playback(self, info: TrackInfo) -> bool:
        """Inicia a reprodução de uma faixa."""
        if player.play(info):
            self._toast(play_started(info.title))
            return True
        return False

    def _play_relative(self, delta: int) -> bool:
        """Toca a faixa na posição atual + delta (fila da última lista usada)."""
        if delta == 1:
            item = self.queue.next(auto=False)
        elif delta == -1:
            item = self.queue.previous()
        else:
            item = self.queue.play_at(self.queue.index + delta)
        if item is None:
            return False
        return self._start_playback(item.info)

    def _maybe_auto_next(self) -> bool:
        """Avança para a próxima faixa (auto-play), com debounce.

        O fim da faixa gera dois eventos ("track_ended" e "ended"); o
        debounce evita avançar duas vezes. ``auto_play_next`` é o master
        switch: desligado, a fila não avança sozinha nem com repeat ligado.
        """
        if not settings.auto_play_next:
            return False
        now = time.monotonic()
        if now - self._last_auto_next < 1.0:
            return False
        item = self.queue.next(auto=True)
        if item is None:
            return False
        self._last_auto_next = now
        return self._start_playback(item.info)
