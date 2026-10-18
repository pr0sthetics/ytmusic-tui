"""Retry, timeouts e abort (T10-R3) — sleep/relógio fake, sem rede.

A política é pura (``delay_for``); o ``call_with_retry`` usa a classificação
do T10-R2 e o ``sleep`` injetável para provar o backoff sem esperar de
verdade. O abort tem teste próprio da semântica A1: a primeira tentativa
sempre roda e o abort só corta NOVAS tentativas.
"""

import functools
import time

import pytest
import requests

import ytmusic_tui.api as api_module
from ytmusic_tui.api import YTMusicAPI
from ytmusic_tui.api.models import SearchResult
from ytmusic_tui.api.retry import RetryPolicy, call_with_retry
from ytmusic_tui.errors import InvalidResponseError, NetworkError


class _Clock:
    """Sleep falso: registra os atrasos e simula o tempo (determinístico)."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.abort_flag: list[bool] = [False]
        self.abort_after: bool = False

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self.abort_after:
            self.abort_flag[0] = True


def _clock() -> _Clock:
    return _Clock()


# ===== política =====


def test_policy_backoff_progressivo_com_teto():
    policy = RetryPolicy(attempts=5, base_delay=0.5, max_delay=2.0)

    assert [policy.delay_for(n) for n in range(1, 6)] == [0.5, 1.0, 2.0, 2.0, 2.0]


def test_policy_attempts_invalido():
    with pytest.raises(ValueError, match="attempts"):
        call_with_retry(lambda: "nunca", policy=RetryPolicy(attempts=0))


# ===== retry =====


def test_transitorio_re_tenta_e_sucede():
    clock = _clock()
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise requests.exceptions.ConnectionError("caiu")
        return "ok"

    result = call_with_retry(operation, policy=RetryPolicy(), sleep=clock.sleep)

    assert result == "ok"
    assert len(calls) == 3
    assert clock.delays == [0.5, 1.0]  # backoff entre as tentativas


def test_nao_transitorio_nao_re_tenta():
    clock = _clock()
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        raise InvalidResponseError("formato mudou")

    with pytest.raises(InvalidResponseError):
        call_with_retry(operation, policy=RetryPolicy(), sleep=clock.sleep)

    assert len(calls) == 1
    assert clock.delays == []


def test_esgotamento_levanta_erro_classificado_com_contexto():
    clock = _clock()

    def operation() -> str:
        raise requests.exceptions.ReadTimeout("leitura lenta")

    with pytest.raises(NetworkError) as excinfo:
        call_with_retry(
            operation,
            policy=RetryPolicy(),
            context='busca "x"',
            sleep=clock.sleep,
        )

    assert str(excinfo.value).startswith('busca "x": tempo esgotado (leitura)')
    assert clock.delays == [0.5, 1.0]


def test_erro_cru_preservado_como_causa():
    def operation() -> str:
        raise requests.exceptions.ConnectionError("caiu")

    with pytest.raises(NetworkError) as excinfo:
        call_with_retry(operation, policy=RetryPolicy(attempts=1), sleep=lambda _s: None)

    assert isinstance(excinfo.value.__cause__, requests.exceptions.ConnectionError)


# ===== abort (A1) =====


def test_abort_nao_consulta_quando_a_primeira_tentativa_sucede():
    def _por_que_me_chamou() -> bool:
        raise AssertionError("abort não pode ser consultado sem falha (A1)")

    assert (
        call_with_retry(lambda: "ok", policy=RetryPolicy(), should_abort=_por_que_me_chamou) == "ok"
    )


def test_abort_corta_retries_mas_primeira_tentativa_roda():
    """Abort já setado: a 1ª tentativa roda, o retry não acontece (A1)."""
    clock = _clock()
    clock.abort_flag[0] = True
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        raise requests.exceptions.ConnectionError("caiu")

    with pytest.raises(NetworkError):
        call_with_retry(
            operation,
            policy=RetryPolicy(),
            sleep=clock.sleep,
            should_abort=lambda: clock.abort_flag[0],
        )

    assert len(calls) == 1
    assert clock.delays == []


def test_abort_durante_a_pausa_corta_a_proxima_tentativa():
    """A pausa pode durar até max_delay: abort nela também corta (A1)."""
    clock = _clock()
    clock.abort_after = True  # o abort "acontece" durante o sleep
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        raise requests.exceptions.ConnectionError("caiu")

    with pytest.raises(NetworkError):
        call_with_retry(
            operation,
            policy=RetryPolicy(),
            sleep=clock.sleep,
            should_abort=lambda: clock.abort_flag[0],
        )

    assert len(calls) == 1
    assert clock.delays == [0.5]  # pausou uma vez, abortou antes da próxima


# ===== timeouts / sessão =====


def test_new_session_aplica_timeouts_separados():
    session = api_module._new_session()

    request = session.request
    assert isinstance(request, functools.partial)
    assert request.keywords["timeout"] == (
        api_module.CONNECT_TIMEOUT_S,
        api_module.READ_TIMEOUT_S,
    )
    assert api_module.CONNECT_TIMEOUT_S < api_module.READ_TIMEOUT_S


def test_initialize_passa_sessao_com_timeouts(monkeypatch):
    captured: dict[str, object] = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(api_module, "YTMusic", _Fake)
    api = YTMusicAPI()
    assert api.initialize() is True

    session = captured["requests_session"]
    assert isinstance(session, requests.Session)
    assert session.request.keywords["timeout"] == (5.0, 15.0)


# ===== integração com search_catalog =====


class _FlakyTransport:
    """Transporte realista: falha nas primeiras N chamadas de "songs"."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.error = error or requests.exceptions.ConnectionError("caiu")
        self.song_calls = 0
        self.album_calls = 0
        self.videos_calls = 0

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        if filter == "songs":
            self.song_calls += 1
            if self.song_calls <= self.failures:
                raise self.error
            return [{"resultType": "song", "videoId": "v1", "title": "ok"}]
        if filter == "videos":
            self.videos_calls += 1
            return [{"resultType": "video", "videoId": "vid1", "title": "ok"}]
        self.album_calls += 1
        return [{"resultType": "album", "browseId": "b1", "title": "ok"}]


def _install_transport(monkeypatch, transport) -> YTMusicAPI:
    api = YTMusicAPI(policy=RetryPolicy(), sleep=lambda _s: None)
    monkeypatch.setattr(api_module, "YTMusic", lambda **kwargs: transport)
    assert api.initialize() is True
    return api


def test_search_catalog_transitorio_sucede_na_segunda(monkeypatch):
    transport = _FlakyTransport(failures=1)
    api = _install_transport(monkeypatch, transport)

    result = api.search_catalog("q")

    assert isinstance(result, SearchResult)
    assert [t.video_id for t in result.tracks] == ["v1"]
    assert [v.video_id for v in result.videos] == ["vid1"]
    assert transport.song_calls == 2
    assert transport.album_calls == 1
    assert transport.videos_calls == 1


def test_search_catalog_abortado_para_de_tentar(monkeypatch):
    """A1 com o wiring completo: 1 tentativa e o erro sobe classificado."""
    transport = _FlakyTransport(failures=99)
    api = _install_transport(monkeypatch, transport)

    with pytest.raises(NetworkError):
        api.search_catalog("q", should_abort=lambda: True)

    assert transport.song_calls == 1
    assert transport.album_calls == 1
    assert transport.videos_calls == 1


def test_search_catalog_sem_abort_esgota_o_teto(monkeypatch):
    transport = _FlakyTransport(failures=99)
    api = _install_transport(monkeypatch, transport)

    with pytest.raises(NetworkError, match='busca "q"'):
        api.search_catalog("q")

    assert transport.song_calls == 3  # teto da política
    assert transport.videos_calls == 1


def test_get_album_re_tenta_transitorio(monkeypatch):
    calls: list[int] = []

    class _FlakyAlbum:
        def __init__(self, **kwargs):
            pass

        def get_album(self, browse_id):
            calls.append(1)
            if len(calls) == 1:
                raise requests.exceptions.ReadTimeout("lento")
            return {"browseId": browse_id, "title": "Álbum"}

    monkeypatch.setattr(api_module, "YTMusic", _FlakyAlbum)
    api = YTMusicAPI(policy=RetryPolicy(), sleep=lambda _s: None)
    api.initialize()

    details = api.get_album("MPREb1")

    assert details.title == "Álbum"
    assert len(calls) == 2


def test_sleep_default_e_time_sleep():
    """Sem injeção, o comportamento de produção usa o relógio real."""
    api = YTMusicAPI()
    assert api._sleep is time.sleep


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
