"""L2 do player (T11-R2): teardown auditável com libmpv REAL.

Sem skip: o CI instala ``mpv``/``libmpv-dev`` e a máquina do autor tem a
biblioteca (padrão do T08). Nada de rede e nada de áudio de verdade: o
``yt-dlp`` do hook é um script falso executável (mesmo padrão do ffmpeg
falso do T08) que grava o próprio PID e dorme — o teste observa o processo
vivo → morto no ``shutdown()`` (INV-8: nada de órfão/zumbi pendurado) e que
eventos atrasados do handle morto não alcançam callbacks registrados.

O singleton do player é global da suíte; a fixture ``real_player`` o
devolve limpo no teardown (``shutdown`` + remoção dos callbacks registrados
no teste).
"""

from __future__ import annotations

import contextlib
import os
import signal
import threading
import time
import wave
from pathlib import Path

import pytest
from textual.widgets import Button, Static, TabbedContent

from ytmusic_tui.player import PlayerEvent, PlayerState, TrackInfo, player
from ytmusic_tui.tui.app import YTMusicTUI
from ytmusic_tui.tui.widgets import NowPlayingBar

#: Script do "yt-dlp" falso (R2): registra o próprio PID e dorme — tempo de
#: sobra para o teste flagrar o processo vivo e derrubar o player.
FAKE_YTDLP_SCRIPT = """#!/bin/sh
printf '%s\\n' "$$" > "$FAKE_YTDLP_PID_FILE"
sleep 300
"""


@pytest.fixture
def real_player():
    """Singleton do player com libmpv REAL, isolado por teste.

    O teardown garante ``shutdown()`` (nenhum handle fica vivo entre testes)
    e remove callbacks registrados AQUI — a lista global não acumula sonda
    de teste (T11-R2: nada de callback morto chamado).
    """
    instance = player
    before = list(instance._callbacks)
    try:
        yield instance
    finally:
        instance.shutdown()
        for callback in list(instance._callbacks):
            if callback not in before:
                instance.remove_callback(callback)


def _pid_state(pid: int) -> str | None:
    """Estado em ``/proc``: ``None`` se sumiu; ``Z`` = morto sem ser reapado."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[0]


def _wait_for_file(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.05)
    pytest.fail(f"{path.name} não apareceu a tempo (o fake yt-dlp não rodou)")


def _write_silent_wav(path: Path, seconds: float = 0.5) -> None:
    """WAV local e hermético para o mpv real chegar ao EOF sozinho."""
    rate = 8000
    with wave.open(str(path), "w") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"\x00\x00" * int(rate * seconds))


def test_shutdown_reaps_the_ytdl_hook_child(real_player, monkeypatch, tmp_path):
    """T11-R2/INV-8: PID vivo durante a carga → morto depois do ``shutdown()``.

    O fake yt-dlp grava o PID e dorme; depois do shutdown o processo tem de
    sumir do ``/proc`` dentro do timeout — vivo OU zumbi falha. A prova é
    com libmpv REAL: é o ``terminate()`` do mpv que derruba e reapa o filho
    do hook no destroy.
    """
    fake = tmp_path / "yt-dlp"
    fake.write_text(FAKE_YTDLP_SCRIPT, encoding="utf-8")
    fake.chmod(0o755)
    pid_file = tmp_path / "ytdl.pid"
    monkeypatch.setenv("FAKE_YTDLP_PID_FILE", str(pid_file))
    monkeypatch.setattr(real_player, "_find_yt_dlp", lambda: str(fake))

    assert real_player.start() is True
    assert real_player.play(
        TrackInfo(video_id="FAKE", source_url="https://www.youtube.com/watch?v=FAKE")
    )

    _wait_for_file(pid_file, timeout=10)
    pid = int(pid_file.read_text().strip())
    assert _pid_state(pid) is not None  # vivo: o hook está executando o fake

    try:
        real_player.shutdown()

        deadline = time.monotonic() + 5
        state = _pid_state(pid)
        while state is not None and time.monotonic() < deadline:
            time.sleep(0.05)
            state = _pid_state(pid)
        assert state is None, f"filho do ytdl_hook não morreu no shutdown (state={state})"
    finally:
        # Nunca deixar órfão pendurado no CI mesmo se a asserção falhar.
        if _pid_state(pid) is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_real_handle_teardown_drops_events_without_crash(real_player, tmp_path):
    """T11-R2: com handle real, nada é entregue depois do ``shutdown()``.

    Uma carga que falha rápido (arquivo local inexistente, hermético) gera
    eventos reais do handle antes do teardown; depois do shutdown, eventos
    da API (``next_track``) são descartados pela guarda e a thread de
    eventos já foi junta — nada chega ao callback.
    """
    events: list[str] = []
    real_player.on_event(lambda e: events.append(e.event))
    assert real_player.start() is True
    assert real_player.play(TrackInfo(video_id="X", source_url=str(tmp_path / "nao-existe.mp3")))

    deadline = time.monotonic() + 5
    while not events and time.monotonic() < deadline:
        time.sleep(0.05)
    assert events, "mpv real não emitiu nenhum evento antes do teardown"

    real_player.shutdown()
    assert real_player._mpv is None
    settled = len(events)

    # Evento da API depois do shutdown: descartado pela guarda (T11-R2).
    real_player.next_track()
    time.sleep(0.3)  # janela para eventos atrasados do handle morto (se houvesse)

    assert len(events) == settled


def test_real_playback_end_emits_track_ended(real_player, tmp_path):
    """Fim natural no mpv real precisa emitir ``track_ended``.

    Regressão de 2026-10-26: com ``keep-open=yes`` o mpv pausava no último
    frame e o auto-next/rádio nunca rodava. O WAV é silencioso e local —
    nada de rede ou áudio audível.
    """
    audio = tmp_path / "curto.wav"
    _write_silent_wav(audio)
    events: list[str] = []
    real_player.on_event(lambda e: events.append(e.event))

    assert real_player.start() is True
    assert real_player.play(TrackInfo(video_id="LOCAL", source_url=str(audio)))

    deadline = time.monotonic() + 8
    while "track_ended" not in events and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "track_ended" in events, f"mpv real não emitiu fim de faixa: {events}"

    deadline = time.monotonic() + 3
    while "ended" not in events and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "ended" in events
    assert real_player.state is PlayerState.STOPPED


@pytest.mark.asyncio
async def test_app_teardown_with_real_handle_does_not_crash(real_player):
    """T11-R2: ``run_test`` com handle real — encerrar o app não quebra.

    O guard C0 (pilha de telas vazia) e a guarda de eventos cobrem a janela
    entre o fechamento das telas e o ``shutdown`` do player; a barra recebe
    um evento real (``next``) antes do teardown.
    """
    app = YTMusicTUI()
    async with app.run_test():
        assert real_player.start() is True
        real_player.next_track()  # player → callback do app → tela (sem fila)

    # run_test saiu: o unmount removeu o callback e encerrou o handle real.
    assert real_player._mpv is None
    assert app._on_player_event not in real_player._callbacks


@pytest.mark.asyncio
async def test_mpv_real_error_reaches_bar_and_toast(real_player, monkeypatch, tmp_path):
    """T11-R3: end-file error do mpv REAL → barra em falha + toast.

    Carga de arquivo local inexistente (hermética, sem rede): o mpv real
    emite ``end-file`` reason ``error``; o evento vira estado ERROR na
    barra ("falha") e toast "Erro no player: …" — sem traceback no app.
    """
    app = YTMusicTUI()
    async with app.run_test() as pilot:
        screen = app.screen
        toasts: list[str] = []
        monkeypatch.setattr(screen, "_toast", toasts.append)
        await pilot.pause()

        assert real_player.play(
            TrackInfo(video_id="X", source_url=str(tmp_path / "nao-existe.mp3"))
        )

        deadline = time.monotonic() + 5
        while not toasts and time.monotonic() < deadline:
            await pilot.pause(0.1)

        assert toasts, "o erro do mpv real não chegou à UI"
        assert toasts[0].startswith("Erro no player:")
        bar = screen.query_one("#now-playing", NowPlayingBar)
        assert "falha" in str(bar.query_one("#np-info", Static).render())
        assert bar.query_one("#btn-play-pause", Button).label == "⚠"

    assert real_player._mpv is None  # o unmount encerrou o handle real


@pytest.mark.asyncio
async def test_ui_stays_responsive_while_hook_loads(real_player, monkeypatch, tmp_path):
    """T11-R3: UI responsiva com a carga "pendurada" no hook ytdl.

    O fake yt-dlp dorme (mpv "carregando" por tempo indeterminado); com o
    handle real nessa carga, o app tem de seguir processando teclas — nada
    no player segura o loop da TUI.
    """
    fake = tmp_path / "yt-dlp"
    fake.write_text(FAKE_YTDLP_SCRIPT, encoding="utf-8")
    fake.chmod(0o755)
    pid_file = tmp_path / "ytdl.pid"
    monkeypatch.setenv("FAKE_YTDLP_PID_FILE", str(pid_file))
    monkeypatch.setattr(real_player, "_find_yt_dlp", lambda: str(fake))

    app = YTMusicTUI()
    async with app.run_test() as pilot:
        screen = app.screen
        assert real_player.play(
            TrackInfo(video_id="FAKE", source_url="https://www.youtube.com/watch?v=FAKE")
        )
        _wait_for_file(pid_file, timeout=10)  # hook rodando: carga pendurada

        await pilot.press("ctrl+l")  # a tecla precisa ser processada mesmo assim
        tabs = screen.query_one("#main-tabs", TabbedContent)
        assert tabs.active == "tab-library"


@pytest.mark.asyncio
async def test_teardown_completes_with_callback_in_flight(real_player, tmp_path):
    """T11.1/Q14: entrega cross-thread em voo não pode travar o teardown.

    Reproduz a janela do incidente de 2026-10-17 (dumps do R5/T14): a
    thread de eventos do libmpv presa entregando um evento ao app
    (``call_from_thread`` esperava o loop) × o ``join()`` do ``terminate()``
    esperando essa thread. O gate segura a thread do mpv com o evento já em
    voo; o ``shutdown()`` roda em thread auxiliar com teto próprio,
    enquanto o loop (esta thread) fica parado — o cenário exato do CI.
    Sem a correção o teardown não completa (falha clara, sem pendurar);
    com a entrega não bloqueante (``post_message``) completa de imediato.
    """
    gate_entered = threading.Event()
    gate_release = threading.Event()

    def gate(event: PlayerEvent) -> None:
        # Só o primeiro evento da thread do libmpv interessa: segura a
        # entrega do app ainda pendente (a janela da corrida).
        if threading.current_thread() is threading.main_thread():
            return
        if gate_entered.is_set():
            return
        gate_entered.set()
        gate_release.wait(timeout=10)

    real_player.on_event(gate)
    app = YTMusicTUI()
    async with app.run_test():
        assert real_player.start() is True
        # Carga que falha (arquivo local inexistente, hermético): o erro
        # nasce na thread de eventos do mpv real, atrás do gate.
        assert real_player.play(
            TrackInfo(video_id="X", source_url=str(tmp_path / "nao-existe.mp3"))
        )
        assert gate_entered.wait(timeout=10), "o evento real do mpv não chegou ao gate"

        # Solta o gate e encerra o player FORA do loop: a entrega cross-thread
        # concorre com o terminate/join e o loop fica bloqueado no deadline,
        # sem processar a entrega (o cenário do CI).
        gate_release.set()
        completed = threading.Event()

        def run_shutdown() -> None:
            real_player.shutdown()
            completed.set()

        helper = threading.Thread(target=run_shutdown, daemon=True, name="teardown-com-deadline")
        helper.start()
        try:
            assert completed.wait(timeout=5), (
                "shutdown() não completou: a thread de eventos do libmpv "
                "ficou presa na entrega cross-thread, à espera do loop "
                "(deadlock do teardown — Q14/T11.1)"
            )
        finally:
            helper.join(timeout=5)

    # O unmount do run_test reencerra o player (idempotente) sem travar.
    assert real_player._mpv is None
