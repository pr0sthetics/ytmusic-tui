"""Player de áudio usando mpv (biblioteca libmpv via python-mpv)."""

import contextlib
import locale
import logging
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import mpv

from ytmusic_tui.config import settings
from ytmusic_tui.diagnostics import mask_diagnostics
from ytmusic_tui.log import log_event

logger = logging.getLogger(__name__)

# Níveis do python-mpv (strings) → níveis da biblioteca padrão.
_MPV_LOG_LEVELS = {
    "fatal": logging.CRITICAL,
    "error": logging.ERROR,
    "warn": logging.WARNING,
}

# Q19/T16: ruído benigno conhecido do mpv/libpipewire. O libpipewire grava os
# avisos de configuração (client.conf ausente) direto no stderr do processo —
# fora do log do mpv —, mas se algum dia passarem pelo log_handler não podem
# virar evento (relato de campo no WSL; reproduzido localmente com config dir
# vazio: "[W][...] pw.conf | [conf.c: 1182 try_load_conf()] can't load config
# client.conf..."). O critério é por token porque é assim que o libpipewire se
# identifica — erros reais do player (ex.: "Failed to initialize audio
# driver") não casam.
_BENIGN_MPV_NOISE = re.compile(r"pw\.conf|conf\.c:\s*\d+|client\.conf", re.IGNORECASE)


def _is_benign_mpv_noise(prefix: str, text: str) -> bool:
    """True quando a mensagem do mpv é ruído benigno conhecido (Q19/T16)."""
    return bool(_BENIGN_MPV_NOISE.search(f"{prefix} {text}"))


def _as_text(value: Any) -> str:
    """Normaliza campo de evento do python-mpv (``bytes``) para texto.

    O ``as_dict()`` do python-mpv 1.0 entrega os campos como ``bytes``
    (decoder padrão); sem normalizar, ``reason == "error"``/``"eof"`` nunca
    casava e a falha de stream sumia (T11-R3).
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if value is None:
        return ""
    return str(value)


class PlayerState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    LOADING = "loading"
    ERROR = "error"


@dataclass
class TrackInfo:
    """Informações da faixa em reprodução."""

    video_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    position: float = 0.0
    thumbnail_url: str = ""
    source_url: str = ""  # URL de stream


@dataclass
class PlayerEvent:
    """Evento do player."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)


class MPVPlayer:
    """Wrapper do mpv (libmpv) com observers e eventos."""

    # Formatos de stream por nível de qualidade (settings.mpv_audio_quality).
    AUDIO_QUALITY_FORMATS = {
        "low": "worstaudio/worst",
        "medium": "bestaudio[abr<=96]/bestaudio",
        "high": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "best": "bestaudio/best",
    }

    def __init__(self) -> None:
        self._mpv: mpv.MPV | None = None
        self._state = PlayerState.STOPPED
        self._current_track: TrackInfo | None = None
        self._callbacks: list[Callable[[PlayerEvent], None]] = []
        self._observers_started = False
        self._last_position_emit = 0.0
        self._warned_missing_yt_dlp = False
        #: Estado do volume/mute vive aqui, não só no mpv (Fase 2/P02): a UI
        #: pode ajustar antes do handle existir e o ``start()`` aplica o que
        #: foi pedido — antes isso era um no-op silencioso que a barra
        #: mostrava como se tivesse valido.
        self._volume = max(0, min(100, settings.mpv_volume))
        self._muted = False
        #: Guarda de encerramento (T11-R2): depois do ``shutdown()``, eventos
        #: atrasados do handle morto são descartados; um novo ``start()``
        #: reabre a entrega.
        self._shutdown = False
        self.last_event_error: str | None = None

    def _ensure_mpv_started(self) -> None:
        """Garante que mpv está rodando."""
        if self._mpv is not None:
            return

        player_kwargs = {
            "vo": "null",  # Sem vídeo
            "audio_client_name": "ytmusic-tui",
            "volume": self._volume,
            "ytdl": True,
            "ytdl_format": self._ytdl_format(),
            "force_window": False,
            # O auto-next/rádio dependem de ``end-file`` com reason ``eof``.
            # Com keep-open o mpv pausa no último frame e não emite o evento,
            # então a fila nunca avança sozinha (regressão encontrada em uso
            # real em 2026-10-26). Com idle, o handle continua vivo.
            "keep_open": False,
            "idle": True,
            # Q19/T16: mensagens do mpv vão para o log interno (log_handler),
            # nunca direto no terminal — nada de texto cru por cima da TUI.
            "terminal": False,
        }

        # O hook ytdl do mpv usa por padrão um youtube-dl embutido e
        # desatualizado, rejeitado pelo YouTube. Apontamos para o yt-dlp
        # atual do projeto para permitir reprodução de streams reais.
        # (python-mpv exige o valor como string "chave=valor".)
        yt_dlp_path = self._find_yt_dlp()
        if yt_dlp_path:
            player_kwargs["script_opts"] = f"ytdl_hook-ytdl_path={yt_dlp_path}"
        elif not self._warned_missing_yt_dlp:
            # Aviso ÚNICO (T06/§8.4): sem yt-dlp, o mpv cai no hook embutido
            # (antigo) e streams do YouTube falham; não faz sentido repetir o
            # aviso a cada reinício do handle.
            self._warned_missing_yt_dlp = True
            logger.warning(
                "yt-dlp não encontrado (nem ao lado de %s, nem no PATH): o mpv "
                "usará o hook embutido, que costuma falhar em streams do YouTube",
                Path(sys.executable).parent,
            )

        # Q19/T16: o libpipewire grava os avisos de config (client.conf
        # ausente) direto no stderr do processo — fora do log do mpv e por
        # cima da TUI (relato de campo no WSL; reproduzido localmente com
        # PIPEWIRE_CONFIG_DIR vazio). PIPEWIRE_DEBUG=0 silencia a biblioteca;
        # quem já definiu o seu valor manda (setdefault).
        os.environ.setdefault("PIPEWIRE_DEBUG", "0")

        # IMPORTANTE: o ytmusicapi muda o locale (LC_ALL) para en_US.UTF-8 no
        # construtor do YTMusic. O libmpv exige LC_NUMERIC="C" — qualquer outro
        # locale (ex.: vírgula decimal) faz o mpv abortar com segfault ao criar
        # o handle (observado em ambiente com LANG=en_US.UTF-8).
        locale.setlocale(locale.LC_NUMERIC, "C")

        self._mpv = mpv.MPV(log_handler=self._on_mpv_log, loglevel="warn", **player_kwargs)

        # P02: o volume já vem no construtor; o mute é estado nosso e precisa
        # ser reaplicado a cada handle novo (novo ``start()`` ou reinício).
        if self._muted:
            with contextlib.suppress(Exception):
                self._mpv.mute = True

        # Configurar observadores
        self._setup_observers()
        self._observers_started = True

    def _on_mpv_log(self, level: str, prefix: str, text: str) -> None:
        """Registra mensagens do mpv no log interno — sem ruído e sem UI.

        Roda na thread de eventos do libmpv (Q19/T16): só decide e loga —
        nunca espera a thread da UI. Avisos benignos conhecidos do pipewire
        não entram; o resto vira evento ``mpv_log`` (mascarado pelo
        formatter). Erros reais do player seguem aparecendo na UI pelo
        caminho de eventos de sempre.
        """
        if _is_benign_mpv_noise(prefix, text):
            return
        log_event(
            "player",
            "mpv_log",
            f"[{prefix}] {text}",
            level=_MPV_LOG_LEVELS.get(level, logging.INFO),
        )

    def start(self) -> bool:
        """Inicializa o handle do mpv (idempotente) — lifecycle explícito (R3).

        Devolve ``True`` quando o handle está disponível. Falha ao criar o
        handle (libmpv ausente, init recusado) vira estado ERROR + evento
        ``error`` (a UI mostra um toast) e ``False`` — nunca uma exceção do
        mpv subindo para o loop da TUI. ``shutdown()`` é o encerramento
        explícito; depois dele, um novo ``start()`` cria um handle novo e
        reabre a entrega de eventos (T11-R2).
        """
        # Reabre a entrega ANTES de qualquer emissão: o start que falha
        # (libmpv ausente) também precisa do evento ``error`` chegando à UI.
        self._shutdown = False
        if self._mpv is not None:
            return True
        try:
            self._ensure_mpv_started()
        except Exception as e:
            logger.exception("Falha ao iniciar o mpv")
            self._state = PlayerState.ERROR
            self._emit("error", {"message": f"mpv indisponível: {e}"})
            return False
        return True

    def _ytdl_format(self) -> str:
        """Formato de stream do yt-dlp conforme a qualidade configurada."""
        return self.AUDIO_QUALITY_FORMATS.get(
            settings.mpv_audio_quality, self.AUDIO_QUALITY_FORMATS["high"]
        )

    def apply_audio_quality(self) -> None:
        """Aplica a qualidade configurada ao mpv em execução (se possível)."""
        if self._mpv:
            with contextlib.suppress(Exception):
                self._mpv.ytdl_format = self._ytdl_format()

    @staticmethod
    def _find_yt_dlp() -> str | None:
        """Localiza o yt-dlp atualizado para o hook do mpv.

        Prioridade: o script do próprio ambiente Python (ex.: .venv/bin/yt-dlp,
        a versão gerida pelo pip) e só depois qualquer binário no ``PATH``.
        Um yt-dlp antigo do sistema (ex.: /usr/bin/yt-dlp) costuma ser
        rejeitado pelo YouTube, fazendo o stream falhar silenciosamente.
        """
        # sys.executable pode ser um symlink (ex.: .venv/bin/python -> /usr/bin).
        # Não usar .resolve(), senão caímos no binário do sistema.
        sibling = Path(sys.executable).parent / "yt-dlp"
        if sibling.is_file():
            return str(sibling)
        exe = shutil.which("yt-dlp")
        if exe:
            return exe
        return None

    def _setup_observers(self) -> None:
        """Configura observadores de propriedades do mpv."""
        if not self._mpv:
            return

        @self._mpv.property_observer("time-pos")
        def _on_time_pos(_name: str, value: Any) -> None:
            if value is not None and self._current_track:
                self._current_track.position = value
                # O mpv dispara time-pos a cada tick; limitar a ~4 eventos/s
                # evita flood de atualizações na UI.
                now = time.monotonic()
                if now - self._last_position_emit >= 0.25:
                    self._last_position_emit = now
                    self._emit(
                        "position",
                        {"position": value, "duration": self._current_track.duration},
                    )

        @self._mpv.property_observer("duration")
        def _on_duration(_name: str, value: Any) -> None:
            if value is not None and self._current_track:
                self._current_track.duration = value
                self._emit("duration", {"duration": value})

        @self._mpv.property_observer("pause")
        def _on_pause(_name: str, value: Any) -> None:
            if value is None:
                return
            # Depois de uma falha o mpv ainda mexe em "pause" ao descarregar
            # a faixa; isso NÃO pode "reviver" o player (a barra perderia o
            # "falha"). O estado ERROR é pegajoso até um novo play/stop
            # (T11-R3).
            if self._state == PlayerState.ERROR:
                return
            # Depois do fim natural (ou de um ``stop``) o mpv fica idle e
            # ainda pode emitir ``pause=false`` ao descarregar a faixa. Isso
            # não pode ressuscitar PLAYING: o estado ficaria inconsistente e
            # o ``Espaço`` alternaria um handle sem faixa em vez de retomar a
            # fila. Só um novo ``play`` sai de STOPPED.
            if self._state == PlayerState.STOPPED:
                return
            if value:
                self._state = PlayerState.PAUSED
                self._emit("pause", {})
            else:
                self._state = PlayerState.PLAYING
                self._emit("resume", {})

        @self._mpv.property_observer("idle-active")
        def _on_idle(_name: str, value: Any) -> None:
            # Uma carga que FALHOU também deixa o mpv idle: ali o "ended"
            # seria um fim de faixa falso em cima do erro recém-emitido (a
            # barra piscaria "falha" e voltaria a parado). O estado ERROR
            # fica até um novo play (T11-R3).
            if value and self._state not in (PlayerState.STOPPED, PlayerState.ERROR):
                self._state = PlayerState.STOPPED
                self._current_track = None
                self._emit("ended", {})

        @self._mpv.event_callback("end-file")
        def _on_end_file(event: Any) -> None:
            # python-mpv entrega um MpvEvent (não um dict) e os campos de
            # texto como bytes (T11-R3) — normalizar antes de comparar.
            try:
                data = event.as_dict()
            except Exception:
                return
            reason = _as_text(data.get("reason"))
            if reason == "eof":
                self._emit("track_ended", {"reason": "eof"})
            elif reason == "error":
                self._state = PlayerState.ERROR
                detail = _as_text(data.get("file_error")).strip()
                self._emit(
                    "error",
                    {"message": detail or "erro ao carregar o stream"},
                )

    def _emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emite evento para callbacks registrados.

        §8.3/T06: eventos de erro carregam texto de terceiros (mpv/ytdl
        hook) que pode trazer caminho do HOME ou URL assinada — só a
        ``message`` do erro passa por ``mask_diagnostics``; dados de faixa e
        de posição NUNCA são tocados (são do usuário, não diagnóstico).

        Guarda de encerramento (T11-R2): depois do ``shutdown()`` e antes do
        próximo ``start()``, eventos atrasados do handle morto são
        descartados — nenhum callback (possivelmente de uma TUI já
        desmontada) é chamado.
        """
        if self._shutdown:
            return
        payload = data or {}
        if event == "error" and "message" in payload:
            payload = {**payload, "message": mask_diagnostics(str(payload["message"]))}
        player_event = PlayerEvent(event=event, data=payload)
        for callback in self._callbacks:
            try:
                callback(player_event)
            except Exception as e:
                # Nunca imprimir na TUI; guardar para diagnóstico.
                self.last_event_error = str(e)

    def on_event(self, callback: Callable[[PlayerEvent], None]) -> None:
        """Registra callback para eventos do player."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[PlayerEvent], None]) -> None:
        """Remove callback registrado."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    @property
    def state(self) -> PlayerState:
        return self._state

    @property
    def current_track(self) -> TrackInfo | None:
        return self._current_track

    def play(self, track_info: TrackInfo) -> bool:
        """Inicia reprodução de uma faixa (início do mpv sob demanda)."""
        if not self.start():
            return False
        if not self._mpv:
            return False

        try:
            self._state = PlayerState.LOADING
            self._emit("loading", {"track": track_info})

            # Carregar URL no mpv
            self._mpv.play(track_info.source_url)
            # Escolher uma faixa é intenção de ouvir (BUG-02): o ``pause`` do
            # mpv persiste entre cargas — sem zerar, a faixa nova entrava
            # pausada (e a barra mostrava "tocando").
            self._mpv.pause = False

            # P02: a posição é da faixa, não do player — reusar o TrackInfo
            # (ou vir de um objeto reciclado) não pode herdar o tempo antigo.
            track_info.position = 0.0

            self._current_track = track_info
            self._state = PlayerState.PLAYING
            self._emit("play", {"track": track_info})
            return True

        except Exception as e:
            self._state = PlayerState.ERROR
            self._emit("error", {"message": str(e)})
            return False

    def pause(self) -> None:
        """Pausa reprodução."""
        if self._mpv:
            self._mpv.pause = True

    def resume(self) -> None:
        """Retoma reprodução."""
        if self._mpv:
            self._mpv.pause = False

    def toggle_pause(self) -> None:
        """Alterna pause/play."""
        if self._mpv:
            self._mpv.pause = not self._mpv.pause

    def stop(self) -> None:
        """Para reprodução."""
        if self._mpv:
            self._mpv.stop()
            self._state = PlayerState.STOPPED
            self._current_track = None
            self._emit("stop", {})

    def seek(self, position: float) -> bool:
        """Busca para a posição em segundos (clampada à faixa).

        Devolve ``False`` sem faixa carregada — a UI mostra um aviso em vez
        de um no-op mudo. O teto é ``duração - 0.5`` para o seek não disparar
        o fim da faixa por arredondamento.
        """
        if self._mpv is None or self._current_track is None:
            return False
        duration = float(self._current_track.duration or 0.0)
        target = max(0.0, float(position))
        if duration > 0:
            target = min(target, max(0.0, duration - 0.5))
        with contextlib.suppress(Exception):
            self._mpv.seek(target, reference="absolute")
            return True
        return False

    def seek_relative(self, seconds: float) -> bool:
        """Busca relativa em segundos (``±5`` das teclas), com clamp.

        Usa o alvo absoluto calculado a partir da posição atual: assim o
        clamp de início/fim vale e a UI pode refletir a posição nova na hora.
        """
        if self._mpv is None or self._current_track is None:
            return False
        return self.seek(self.get_position() + float(seconds))

    def get_position(self) -> float:
        """Posição atual em segundos (0.0 sem faixa ou sem handle)."""
        if self._mpv is not None:
            with contextlib.suppress(Exception):
                value = self._mpv.time_pos
                if value is not None:
                    return float(value)
        if self._current_track is not None:
            return float(self._current_track.position or 0.0)
        return 0.0

    def set_volume(self, volume: int) -> int:
        """Define o volume (0-100); devolve o valor aplicado.

        P02: guarda o valor mesmo sem handle — ``start()`` o aplica depois.
        """
        self._volume = max(0, min(100, int(volume)))
        if self._mpv is not None:
            with contextlib.suppress(Exception):
                self._mpv.volume = self._volume
        self._emit("volume", {"volume": self._volume, "muted": self._muted})
        return self._volume

    def get_volume(self) -> int:
        """Volume em vigor (0-100)."""
        return self._volume

    def set_muted(self, muted: bool) -> bool:
        """Define o mute; devolve o estado aplicado."""
        self._muted = bool(muted)
        if self._mpv is not None:
            with contextlib.suppress(Exception):
                self._mpv.mute = self._muted
        self._emit("mute", {"muted": self._muted})
        return self._muted

    def toggle_mute(self) -> bool:
        """Alterna o mute (tecla ``m``); devolve o estado novo."""
        return self.set_muted(not self._muted)

    def is_muted(self) -> bool:
        """True quando o áudio está mudo."""
        return self._muted

    def next_track(self) -> None:
        """Próxima faixa (emite evento para queue manager)."""
        self._emit("next", {})

    def previous_track(self) -> None:
        """Faixa anterior."""
        self._emit("previous", {})

    def set_speed(self, speed: float) -> None:
        """Define velocidade de reprodução."""
        if self._mpv:
            self._mpv.speed = max(0.25, min(4.0, speed))

    def get_property(self, name: str) -> Any:
        """Obtém propriedade do mpv."""
        if self._mpv:
            return getattr(self._mpv, name, None)
        return None

    def shutdown(self) -> None:
        """Encerra player completamente (idempotente; permite novo ``start``).

        T11-R2: fecha a entrega de eventos ANTES do ``terminate()`` — a
        janela de encerramento (thread de eventos do libmpv morrendo) não
        alcança callbacks. O ``terminate()`` do python-mpv destroi o core e
        junta a thread de eventos; o mpv mata e reapa os filhos do hook
        ytdl no destroy (INV-8 — provado com libmpv real na L2).
        """
        self._shutdown = True
        if self._mpv:
            with contextlib.suppress(Exception):
                self._mpv.terminate()
            self._mpv = None
        self._observers_started = False
        self._state = PlayerState.STOPPED
        self._current_track = None


# Instância global
player = MPVPlayer()
