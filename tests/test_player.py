"""Testes para o player mpv (lógica sem depender de rede/áudio)."""

import locale
import logging
import os
import shutil
import sys

import pytest

from ytmusic_tui.player import MPVPlayer, PlayerState, TrackInfo

# `ytmusic_tui/__init__.py` exporta a instância `player`, que sombreia o
# atributo de módulo. O módulo real fica em sys.modules.
player_mod = sys.modules["ytmusic_tui.player"]


class _FakeMpv:
    """Substituto de mpv.MPV sem libmpv."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.played: str | None = None
        self.end_file_cb = None  # observer de end-file capturado (T11-R2)
        self.idle_cb = None  # observer de idle-active capturado (T11-R3)
        self.pause_cb = None  # observer de pause capturado (T11-R3)
        self.seeks: list[tuple[float, str]] = []
        self.stopped = 0
        self.pause = False
        self.volume = kwargs.get("volume", 100)

    def property_observer(self, name):
        def wrapper(fn):
            if name == "idle-active":
                self.idle_cb = fn
            elif name == "pause":
                self.pause_cb = fn
            return fn

        return wrapper

    def event_callback(self, *event_types):
        def wrapper(fn):
            if event_types == ("end-file",):
                self.end_file_cb = fn
            return fn

        return wrapper

    def play(self, url):
        self.played = url

    def stop(self):
        self.stopped += 1

    def seek(self, amount, reference="relative"):
        self.seeks.append((amount, reference))

    def terminate(self):
        pass


def test_find_yt_dlp_prefers_venv_sibling(monkeypatch, tmp_path):
    """Deve priorizar o yt-dlp do ambiente (venv) sobre binários do sistema.

    Um yt-dlp antigo do sistema (ex.: /usr/bin/yt-dlp, versão 2025.x) é
    rejeitado pelo YouTube; a versão gerida pelo pip é a que funciona.
    """
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake = venv_bin / "yt-dlp"
    fake.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
    fake.chmod(0o755)

    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/bin/yt-dlp")

    assert MPVPlayer._find_yt_dlp() == str(fake)


def test_find_yt_dlp_falls_back_to_path(monkeypatch):
    """Sem yt-dlp no ambiente, usa o que existir no PATH."""
    monkeypatch.setattr(sys, "executable", "/tmp/venv-inexistente/bin/python")
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/bin/yt-dlp")

    assert MPVPlayer._find_yt_dlp() == "/usr/bin/yt-dlp"


def test_find_yt_dlp_none(monkeypatch):
    """Sem yt-dlp em lugar nenhum, retorna None (mpv usa o hook embutido)."""
    monkeypatch.setattr(sys, "executable", "/tmp/venv-inexistente/bin/python")
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)

    assert MPVPlayer._find_yt_dlp() is None


def test_ensure_mpv_started_restores_numeric_locale(monkeypatch, tmp_path):
    """O libmpv exige LC_NUMERIC='C'; o ytmusicapi muda o locale para en_US.

    Regressão: sem restaurar o LC_NUMERIC, criar o handle do mpv após uma
    busca (ytmusicapi) abortava o processo com segfault.
    """
    monkeypatch.setattr(player_mod.mpv, "MPV", _FakeMpv)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)

    try:
        original = locale.setlocale(locale.LC_NUMERIC, None)
        locale.setlocale(locale.LC_NUMERIC, "en_US.UTF-8")
    except locale.Error as exc:  # pragma: no cover - ambiente sem o locale
        pytest.skip(f"locale en_US.UTF-8 indisponível: {exc}")

    p = MPVPlayer()
    try:
        p._ensure_mpv_started()

        assert p._mpv is not None
        assert locale.setlocale(locale.LC_NUMERIC, None) == "C"
    finally:
        locale.setlocale(locale.LC_NUMERIC, original)


# ===== Ruído benigno do mpv/PipeWire fora da UI (Q19 · T16-R5) =====


def test_is_benign_mpv_noise_filters_only_the_known_pipewire_noise():
    """Linhas sintéticas: aviso benigno filtrado × erro real preservado."""
    # Formato real do libpipewire (relato de campo no WSL e reprodução local).
    benignas = [
        (
            "pw.conf",
            "[ conf.c: 1182 try_load_conf()] can't load config client.conf: "
            "No such file or directory",
        ),
        (
            "pw.conf",
            "[ conf.c:  425 conf_load()] error loading config 'client.conf': "
            "No such file or directory",
        ),
        (
            "pw.conf",
            "[ conf.c: 1215 pw_conf_load_conf_for_context()] can't load config client.conf",
        ),
    ]
    for prefix, texto in benignas:
        assert player_mod._is_benign_mpv_noise(prefix, texto), texto

    # Erros reais (capturados do log_handler no host, prova local do T16).
    reais = [
        ("ao", "Failed to initialize audio driver 'pipewire'"),
        ("cplayer", "Could not open/initialize audio device -> no sound."),
        ("cplayer", "Failed to open arquivo-inexistente.mp3."),
    ]
    for prefix, texto in reais:
        assert not player_mod._is_benign_mpv_noise(prefix, texto), texto


def test_on_mpv_log_filters_noise_and_logs_real_errors(monkeypatch):
    """O handler descarta ruído do pipewire e registra o resto como evento."""
    registros: list[tuple] = []
    monkeypatch.setattr(
        player_mod,
        "log_event",
        lambda component, event, message, **kw: registros.append((component, event, message, kw)),
    )
    p = MPVPlayer()

    p._on_mpv_log(
        "warn",
        "pw.conf",
        "[ conf.c: 1182 try_load_conf()] can't load config client.conf",
    )
    assert registros == []

    p._on_mpv_log("error", "cplayer", "Could not open/initialize audio device -> no sound.")
    assert len(registros) == 1
    component, event, message, kw = registros[0]
    assert component == "player"
    assert event == "mpv_log"
    assert "[cplayer]" in message and "no sound" in message
    assert kw["level"] == logging.ERROR


def test_ensure_mpv_started_silences_pipewire_and_keeps_mpv_off_terminal(
    monkeypatch,
):
    """Q19: stderr do pipewire silenciado; mpv fala pelo log_handler, não pelo terminal."""
    monkeypatch.setattr(player_mod.mpv, "MPV", _FakeMpv)
    monkeypatch.delenv("PIPEWIRE_DEBUG", raising=False)

    p = MPVPlayer()
    try:
        p._ensure_mpv_started()

        assert p._mpv is not None
        assert p._mpv.kwargs["terminal"] is False
        assert p._mpv.kwargs["loglevel"] == "warn"
        assert callable(p._mpv.kwargs["log_handler"])
        assert os.environ["PIPEWIRE_DEBUG"] == "0"
    finally:
        p.shutdown()


def test_ensure_mpv_started_keeps_user_pipewire_debug(monkeypatch):
    """Q19: quem já definiu PIPEWIRE_DEBUG manda — o app não sobrescreve."""
    monkeypatch.setattr(player_mod.mpv, "MPV", _FakeMpv)
    monkeypatch.setenv("PIPEWIRE_DEBUG", "3")

    p = MPVPlayer()
    try:
        p._ensure_mpv_started()
        assert os.environ["PIPEWIRE_DEBUG"] == "3"
    finally:
        p.shutdown()


# ===== Lifecycle explícito do mpv (T06-R3 · §8.2) =====


def test_start_is_idempotent_and_shutdown_allows_restart(monkeypatch):
    """R3: start idempotente; shutdown encerra e um novo start recria."""
    created: list[_FakeMpv] = []

    class _TrackedMpv(_FakeMpv):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.terminated = 0
            created.append(self)

        def terminate(self):
            self.terminated += 1

    monkeypatch.setattr(player_mod.mpv, "MPV", _TrackedMpv)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")

    p = MPVPlayer()
    assert p.start() is True
    first = p._mpv
    assert p.start() is True  # idempotente: não cria segundo handle
    assert p._mpv is first
    assert len(created) == 1

    p.shutdown()
    assert p._mpv is None
    assert first is not None and first.terminated == 1
    p.shutdown()  # idempotente também
    assert first.terminated == 1

    assert p.start() is True  # reinício permitido após shutdown
    assert len(created) == 2
    assert p._mpv is not first
    p.shutdown()
    assert created[1].terminated == 1


@pytest.mark.parametrize("error", [OSError("libmpv ausente"), RuntimeError("init recusado")])
def test_start_failure_becomes_error_state_without_raising(monkeypatch, error):
    """Falha ao criar o handle vira ERROR + evento, nunca exceção na TUI."""

    class _Explode:
        def __init__(self, **kwargs):
            raise error

    monkeypatch.setattr(player_mod.mpv, "MPV", _Explode)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")

    p = MPVPlayer()
    events: list[tuple[str, str]] = []
    p.on_event(lambda e: events.append((e.event, e.data.get("message", ""))))

    assert p.start() is False
    assert p._mpv is None
    assert p.state is PlayerState.ERROR
    assert any(name == "error" and "mpv indisponível" in msg for name, msg in events)


def test_play_starts_on_demand_and_tracks_source_url(monkeypatch):
    """play() inicia o handle sob demanda (compatível com o fluxo antigo)."""
    monkeypatch.setattr(player_mod.mpv, "MPV", _FakeMpv)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")

    p = MPVPlayer()
    info = TrackInfo(video_id="v", title="T", source_url="http://servidor/a.mp3")

    assert p.play(info) is True
    assert p._mpv is not None and p._mpv.played == "http://servidor/a.mp3"


def test_play_with_broken_mpv_returns_false_without_raising(monkeypatch):
    """Handle quebrado: play() devolve False e a UI recebe evento de erro."""

    class _Explode:
        def __init__(self, **kwargs):
            raise OSError("sem libmpv")

    monkeypatch.setattr(player_mod.mpv, "MPV", _Explode)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")

    p = MPVPlayer()
    events: list[str] = []
    p.on_event(lambda e: events.append(e.event))
    info = TrackInfo(video_id="v", title="T", source_url="http://servidor/a.mp3")

    assert p.play(info) is False
    assert p.state is PlayerState.ERROR
    assert "error" in events


def test_numeric_locale_is_restored_before_every_handle(monkeypatch):
    """LC_NUMERIC=C é definido ANTES de CADA criação do handle (R3)."""
    calls: list[tuple] = []

    class _TrackedMpv(_FakeMpv):
        def __init__(self, **kwargs):
            calls.append(("mpv",))
            super().__init__(**kwargs)

    real_setlocale = locale.setlocale

    def tracked_setlocale(category, value=None):
        calls.append(("setlocale", category, value))
        return real_setlocale(category, value)

    monkeypatch.setattr(player_mod.mpv, "MPV", _TrackedMpv)
    monkeypatch.setattr(player_mod.locale, "setlocale", tracked_setlocale)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")

    p = MPVPlayer()
    p.start()
    p.shutdown()
    p.start()
    p.shutdown()

    assert calls == [
        ("setlocale", locale.LC_NUMERIC, "C"),
        ("mpv",),
        ("setlocale", locale.LC_NUMERIC, "C"),
        ("mpv",),
    ]


def test_missing_yt_dlp_warns_once_and_keeps_hook_default(monkeypatch, caplog):
    """Extra aprovado no T06: aviso ÚNICO quando o yt-dlp não existe."""
    monkeypatch.setattr(player_mod.MPVPlayer, "_find_yt_dlp", staticmethod(lambda: None))
    monkeypatch.setattr(player_mod.mpv, "MPV", _FakeMpv)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")

    p = MPVPlayer()
    with caplog.at_level(logging.WARNING, logger="ytmusic_tui.player"):
        p.start()
        p.shutdown()
        p.start()
        p.shutdown()

    assert p._mpv is None  # encerrado ao final do teste
    avisos = [r for r in caplog.records if "yt-dlp" in r.getMessage()]
    assert len(avisos) == 1

    p2 = MPVPlayer()
    p2.start()
    assert "script_opts" not in p2._mpv.kwargs
    p2.shutdown()


def test_yt_dlp_path_is_passed_to_the_hook(monkeypatch):
    """Com yt-dlp localizado, o hook do mpv usa o binário do ambiente."""
    monkeypatch.setattr(
        player_mod.MPVPlayer, "_find_yt_dlp", staticmethod(lambda: "/opt/venv/yt-dlp")
    )
    monkeypatch.setattr(player_mod.mpv, "MPV", _FakeMpv)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")

    p = MPVPlayer()
    p.start()

    assert p._mpv is not None
    assert p._mpv.kwargs["script_opts"] == "ytdl_hook-ytdl_path=/opt/venv/yt-dlp"
    p.shutdown()


# ===== Teardown: guarda de eventos e callbacks (T11-R2) =====


class _FakeEndFile:
    """Evento ``end-file`` mínimo, com ``as_dict`` como o python-mpv entrega."""

    def __init__(self, data: dict):
        self._data = data

    def as_dict(self) -> dict:
        return self._data


def _started_player(monkeypatch) -> tuple[MPVPlayer, _FakeMpv]:
    """Player com fake mpv já iniciado (handle e observer capturados)."""
    monkeypatch.setattr(player_mod.mpv, "MPV", _FakeMpv)
    monkeypatch.setattr(player_mod.settings, "mpv_volume", 42)
    monkeypatch.setattr(player_mod.settings, "mpv_audio_quality", "high")
    p = MPVPlayer()
    assert p.start() is True
    assert p._mpv is not None
    return p, p._mpv


def test_events_after_shutdown_never_reach_callbacks(monkeypatch):
    """T11-R2: evento atrasado do handle morto é descartado pela guarda.

    O fake captura o observer de ``end-file`` registrado pelo ``start``;
    depois do ``shutdown()``, chamá-lo — como a thread de eventos do libmpv
    faria na janela de encerramento — não entrega NADA ao callback.
    """
    p, fake = _started_player(monkeypatch)
    received: list[str] = []
    p.on_event(lambda e: received.append(e.event))
    assert fake.end_file_cb is not None

    p.next_track()  # antes do shutdown: entrega normal
    assert received == ["next"]

    p.shutdown()
    fake.end_file_cb(_FakeEndFile({"reason": "error", "error": "caiu"}))
    p.next_track()

    assert received == ["next"]  # nada depois do shutdown


def test_start_after_shutdown_resumes_events(monkeypatch):
    """T11-R2: um novo ``start()`` reabre a entrega de eventos."""
    p, _ = _started_player(monkeypatch)
    received: list[str] = []
    p.on_event(lambda e: received.append(e.event))

    p.shutdown()
    p.next_track()
    assert received == []

    assert p.start() is True
    p.next_track()
    assert received == ["next"]


def test_failed_start_after_shutdown_still_emits_error(monkeypatch):
    """T11-R2: a guarda não engole o erro do start falho (regressão do T06).

    Se a guarda fosse um simples "handle é None", o caminho mais provável em
    produção (libmpv ausente) ficaria mudo — o evento ``error`` é justamente
    o que precisa chegar à UI como toast.
    """
    p, _ = _started_player(monkeypatch)
    received: list[str] = []
    p.on_event(lambda e: received.append(e.event))
    p.shutdown()

    class _Explode:
        def __init__(self, **kwargs):
            raise OSError("sem libmpv")

    monkeypatch.setattr(player_mod.mpv, "MPV", _Explode)
    assert p.start() is False
    assert received == ["error"]
    assert p.state is PlayerState.ERROR


def test_remove_callback_is_idempotent_and_ignores_unknown(monkeypatch):
    """T11-R2: ``remove_callback`` pode repetir e nunca quebra com estranho."""
    p, _ = _started_player(monkeypatch)
    received: list[str] = []

    def cb(event):
        received.append(event.event)

    p.on_event(cb)
    p.next_track()
    p.remove_callback(cb)
    p.remove_callback(cb)  # idempotente: remover de novo não quebra
    p.remove_callback(lambda e: None)  # nunca registrado: ignorado
    p.next_track()

    assert received == ["next"]


# ===== Falhas de stream: evento → estado (T11-R3) =====


def test_end_file_error_decodes_bytes_and_becomes_error_event(monkeypatch):
    """T11-R3: end-file error do mpv real (campos em bytes) vira evento.

    O ``as_dict()`` do python-mpv entrega ``reason``/``file_error`` como
    bytes; sem o decode, a comparação com "error" nunca casava e a falha de
    stream sumia da UI (o mesmo valia para o "eof", que não chegava como
    ``track_ended``).
    """
    p, fake = _started_player(monkeypatch)
    events: list[tuple[str, str]] = []
    p.on_event(lambda e: events.append((e.event, e.data.get("message", ""))))

    fake.end_file_cb(_FakeEndFile({"reason": b"error", "file_error": b"loading failed"}))

    assert p.state is PlayerState.ERROR
    assert events == [("error", "loading failed")]


def test_end_file_eof_decodes_to_track_ended(monkeypatch):
    """T11-R3: "eof" (bytes) chega como ``track_ended`` — o auto-next anda."""
    p, fake = _started_player(monkeypatch)
    events: list[str] = []
    p.on_event(lambda e: events.append(e.event))

    fake.end_file_cb(_FakeEndFile({"reason": b"eof"}))

    assert events == ["track_ended"]


def test_end_file_error_without_detail_has_clear_fallback(monkeypatch):
    """T11-R3: sem ``file_error``, a mensagem não vira "Erro desconhecido"."""
    p, fake = _started_player(monkeypatch)
    events: list[tuple[str, str]] = []
    p.on_event(lambda e: events.append((e.event, e.data.get("message", ""))))

    fake.end_file_cb(_FakeEndFile({"reason": "error"}))  # str: defensivo

    assert events == [("error", "erro ao carregar o stream")]


def test_idle_after_stream_error_keeps_the_failure_visible(monkeypatch):
    """T11-R3: mpv fica idle depois de falhar; isso NÃO é fim de faixa.

    Sem a guarda, o "ended" do idle apagava o estado ERROR e a barra
    voltava a "parado" logo depois do toast.
    """
    p, fake = _started_player(monkeypatch)
    track = TrackInfo(video_id="v", title="T")
    events: list[str] = []
    p.on_event(lambda e: events.append(e.event))
    assert p.play(track) is True

    fake.end_file_cb(_FakeEndFile({"reason": b"error", "file_error": b"loading failed"}))
    # Sequência REAL do mpv ao descarregar a faixa com erro (observada com
    # libmpv 0.40): "pause" muda (reset) e só então o idle ativa.
    fake.pause_cb("pause", False)
    fake.idle_cb("idle-active", True)

    assert events == ["loading", "play", "error"]
    assert p.state is PlayerState.ERROR
    assert p.current_track is track  # a faixa que falhou continua na barra


def test_pause_change_after_error_does_not_revive_the_player(monkeypatch):
    """T11-R3: o mpv mexe em "pause" ao descarregar com erro; estado fica ERROR.

    Sem a guarda, o reset de "pause" (False) colocava o player em PLAYING e
    o "ended" do idle seguinte apagava a falha da barra.
    """
    p, fake = _started_player(monkeypatch)
    events: list[str] = []
    p.on_event(lambda e: events.append(e.event))
    assert p.play(TrackInfo(video_id="v")) is True

    fake.end_file_cb(_FakeEndFile({"reason": b"error", "file_error": b"loading failed"}))
    fake.pause_cb("pause", False)

    assert p.state is PlayerState.ERROR
    assert events == ["loading", "play", "error"]


def test_idle_after_playback_end_still_emits_ended(monkeypatch):
    """T11-R3 (controle): fim normal — idle vira ``ended`` e limpa a faixa."""
    p, fake = _started_player(monkeypatch)
    assert p.play(TrackInfo(video_id="v", source_url="http://s/a.mp3")) is True
    events: list[str] = []
    p.on_event(lambda e: events.append(e.event))

    fake.idle_cb("idle-active", True)

    assert events == ["ended"]
    assert p.state is PlayerState.STOPPED
    assert p.current_track is None


def test_play_switches_track_mid_playback(monkeypatch):
    """T11-R3: play() em reprodução troca a faixa no MESMO handle."""
    p, fake = _started_player(monkeypatch)
    events: list[str] = []
    p.on_event(lambda e: events.append(e.event))

    assert p.play(TrackInfo(video_id="a", source_url="http://s/a.mp3")) is True
    assert p.play(TrackInfo(video_id="b", source_url="http://s/b.mp3")) is True

    assert fake.played == "http://s/b.mp3"
    assert p.current_track is not None and p.current_track.video_id == "b"
    assert events.count("play") == 2


def test_play_apos_pausa_despausa_automaticamente(monkeypatch):
    """Relato de campo (2026-10-18, BUG-02): escolher outra faixa com o
    player pausado deve tocar direto — o ``pause`` do mpv persiste entre
    cargas, então o ``play()`` precisa zerá-lo (antes a faixa nova entrava
    pausada e a barra exibia "tocando")."""
    p, fake = _started_player(monkeypatch)

    assert p.play(TrackInfo(video_id="a", source_url="http://s/a.mp3")) is True
    p.pause()
    assert fake.pause is True

    assert p.play(TrackInfo(video_id="b", source_url="http://s/b.mp3")) is True
    assert fake.pause is False
    assert p.state == PlayerState.PLAYING


def test_volume_is_clamped_to_0_100(monkeypatch):
    """T11-R3: volume clampado 0–100 no handle do mpv."""
    p, fake = _started_player(monkeypatch)

    p.set_volume(150)
    assert fake.volume == 100
    p.set_volume(-5)
    assert fake.volume == 0
    p.set_volume(42)
    assert fake.volume == 42
    assert p.get_volume() == 42


def test_seek_uses_absolute_and_relative_references(monkeypatch):
    """T11-R3: seek absoluto × relativo com a referência certa no mpv."""
    p, fake = _started_player(monkeypatch)
    assert p.play(TrackInfo(video_id="v", source_url="http://s/a.mp3")) is True

    p.seek(30.0)
    p.seek_relative(-10.0)

    assert fake.seeks == [(30.0, "absolute"), (-10.0, "relative")]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
