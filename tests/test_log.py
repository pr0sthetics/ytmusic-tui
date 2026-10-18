"""Testes do logging em arquivo (JSON Lines — §13.1/T13).

O log nunca vai ao stderr (sujaria a TUI) e, desde o T13, cada linha é um
objeto JSON com campos fixos e privacidade mascarada pelo formatter
(§13.2). A rotação 0600 tem prova própria em
``tests/security/test_permissions.py``; aqui ela aparece para provar que
as linhas continuam JSON também nos arquivos rotacionados.
"""

import json
import logging
import stat
from datetime import datetime
from pathlib import Path

import pytest

import ytmusic_tui.log as log_module

#: Campos fixos de todo registro (§13.1); ``detail`` só com ``exc_info``.
_BASE_FIELDS = {
    "ts",
    "level",
    "component",
    "operation_id",
    "event",
    "error_code",
    "message",
}


@pytest.fixture
def log_env(tmp_path, monkeypatch):
    """Log configurado num tmp, sem handlers prévios (como o app configura)."""
    monkeypatch.setattr(log_module, "user_log_dir", lambda appname: str(tmp_path))
    package_logger = logging.getLogger("ytmusic_tui")
    monkeypatch.setattr(package_logger, "handlers", [])
    path = log_module.setup_logging()
    yield package_logger, path
    for handler in package_logger.handlers:
        handler.close()


def _flush(package_logger: logging.Logger) -> None:
    for handler in package_logger.handlers:
        handler.flush()


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_jsonl_com_campos_fixos_e_idempotencia(log_env):
    """Toda linha é JSON com os 7 campos; exc_info vira detail; setup é idempotente."""
    package_logger, path = log_env
    assert log_module.setup_logging() == path  # idempotente
    assert len(package_logger.handlers) == 1  # sem duplicar handler

    try:
        raise ValueError("falha de teste")
    except ValueError:
        logging.getLogger("ytmusic_tui.tui.app").exception("Falha ao abrir")

    _flush(package_logger)
    records = _lines(path)
    assert len(records) == 1
    record = records[0]
    assert set(record) == _BASE_FIELDS | {"detail"}
    assert record["level"] == "ERROR"
    assert record["component"] == "tui.app"
    assert record["message"] == "Falha ao abrir"
    assert record["operation_id"] is None
    assert record["event"] is None
    assert record["error_code"] is None
    assert "ValueError: falha de teste" in record["detail"]
    # ts é ISO-8601 com offset local (não UTC "pelado").
    assert datetime.fromisoformat(record["ts"]).utcoffset() is not None


def test_log_event_leva_operation_id_e_error_code(log_env):
    """Evento estruturado: campos extras presentes; sem exceção não tem detail."""
    package_logger, path = log_env

    log_module.log_event(
        "download",
        "download_failed",
        "download falhou",
        operation_id="task-1",
        error_code="FFMPEG_EXIT_1",
        level=logging.ERROR,
    )

    _flush(package_logger)
    record = _lines(path)[0]
    assert set(record) == _BASE_FIELDS
    assert record["component"] == "download"
    assert record["event"] == "download_failed"
    assert record["operation_id"] == "task-1"
    assert record["error_code"] == "FFMPEG_EXIT_1"
    assert record["level"] == "ERROR"
    assert record["message"] == "download falhou"


def test_mascara_payloads_sensiveis(log_env):
    """§13.2: HOME → ~, URL assinada sem token, headers de credencial redigidos."""
    package_logger, path = log_env
    home = str(Path.home())
    logger = logging.getLogger("ytmusic_tui.teste")

    logger.warning("arquivo em %s", Path(home) / "Musica" / "x.mp3")
    logger.warning("stream https://exemplo.com/a?sig=SEGREDO#frag falhou")
    logger.warning("Authorization: Bearer abc\nCookie: sess=xyz; a=b")
    try:
        raise RuntimeError(f"pasta {home}/segredo inacessível")
    except RuntimeError:
        logger.exception("erro de teste")

    _flush(package_logger)
    text = path.read_text(encoding="utf-8")
    records = _lines(path)

    assert home not in text
    assert records[0]["message"] == "arquivo em ~/Musica/x.mp3"
    assert "SEGREDO" not in text
    assert records[1]["message"] == "stream https://exemplo.com/a?… falhou"
    assert records[2]["message"] == "Authorization: [REDACTED]\nCookie: [REDACTED]"
    assert home not in records[3]["detail"]
    assert "~/segredo" in records[3]["detail"]


def test_cache_hit_nao_registra_a_consulta(tmp_path, caplog):
    """O log do cache hit é só o fato — a consulta do usuário não aparece (§13.2)."""
    from ytmusic_tui.api import YTMusicAPI, cache_key
    from ytmusic_tui.api.cache import ResponseCache
    from ytmusic_tui.api.models import SearchResult

    cache = ResponseCache(tmp_path / "api-cache")
    cache.put("search", cache_key("SEGREDO_QUERY", 25, 15, 20), SearchResult().to_payload())
    api = YTMusicAPI(cache=cache, sleep=lambda _seconds: None)

    with caplog.at_level(logging.DEBUG, logger="ytmusic_tui.api"):
        api.search_catalog("SEGREDO_QUERY")

    assert any("cache hit" in record.getMessage() for record in caplog.records)
    assert "SEGREDO_QUERY" not in caplog.text


def test_rotacao_mantem_json_e_0600(tmp_path, monkeypatch):
    """Rotação preservada (§13.1): linhas continuam JSON; modos seguem 0600."""
    monkeypatch.setattr(log_module, "user_log_dir", lambda appname: str(tmp_path))
    monkeypatch.setattr(log_module, "_MAX_BYTES", 200)
    package_logger = logging.getLogger("ytmusic_tui")
    monkeypatch.setattr(package_logger, "handlers", [])
    path = log_module.setup_logging()
    try:
        logger = logging.getLogger("ytmusic_tui.teste")
        # Duas linhas: o CPython 3.13 nunca rotaciona arquivo VAZIO (gh-116263).
        logger.info("linha de teste " + "x" * 400)
        logger.info("segunda linha " + "y" * 400)
        _flush(package_logger)

        rotated = path.with_name(path.name + ".1")
        assert rotated.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(rotated.stat().st_mode) == 0o600
        for candidate in (path, rotated):
            records = _lines(candidate)
            assert records, f"{candidate.name} deveria ter registros JSON"
            assert {record["component"] for record in records} == {"teste"}
    finally:
        for handler in package_logger.handlers:
            handler.close()


# ===== eventos obrigatórios (T13 §13.1) =====


def _neutralizar_reconciliacao(monkeypatch) -> None:
    """Impede que o ``on_mount`` varra o ``download_dir`` real (ver test_tui)."""
    from ytmusic_tui.library.recovery import ReconciliationReport
    from ytmusic_tui.tui import library_controller

    monkeypatch.setattr(
        library_controller, "reconcile", lambda *args, **kwargs: ReconciliationReport()
    )


def test_eventos_download_started_e_finished(monkeypatch, tmp_path, caplog):
    """Fluxo completo (yt-dlp falso) emite started/finished com o ``task.id``."""
    from types import SimpleNamespace

    import ytmusic_tui.download as download_mod
    from ytmusic_tui.download import DownloadStatus, DownloadTask
    from ytmusic_tui.download.store import DownloadTaskStore

    class _DownloadError(Exception):
        pass

    class _YoutubeDL:
        """Fake mínimo: "baixa", converte e dispara os ``post_hooks``."""

        def __init__(self, opts: dict):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls: list[str]) -> None:
            template = self.opts["outtmpl"]
            final = Path(template.replace("%(ext)s", "mp3"))
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(b"audio-convertido-mp3")
            for hook in self.opts.get("post_hooks", []):
                hook(str(final))

    monkeypatch.setattr(
        download_mod,
        "_load_yt_dlp",
        lambda: SimpleNamespace(
            utils=SimpleNamespace(DownloadError=_DownloadError), YoutubeDL=_YoutubeDL
        ),
    )
    monkeypatch.setattr(
        download_mod,
        "settings",
        SimpleNamespace(
            download_dir=tmp_path,
            download_format="mp3",
            download_quality="320",
            embed_metadata=False,
            download_concurrent=1,
        ),
    )
    monkeypatch.setattr(download_mod.library, "update_track_file", lambda *a, **k: None)

    manager = download_mod.DownloadManager(store=DownloadTaskStore())
    task = DownloadTask(video_id="v1", title="Música", track_id=7)

    with caplog.at_level(logging.INFO, logger="ytmusic_tui.download"):
        result = manager._download_task(task)

    assert result.status is DownloadStatus.COMPLETED
    events = {
        getattr(record, "event", None): record
        for record in caplog.records
        if getattr(record, "operation_id", None) == task.id
    }
    assert "download_started" in events
    assert "download_finished" in events
    assert events["download_started"].getMessage() == "Download iniciado"


def test_evento_download_failed_com_codigo(caplog):
    """Falha emite ``download_failed`` com ``operation_id`` e ``error_code``."""
    from ytmusic_tui.download import DownloadManager, DownloadStatus, DownloadTask

    task = DownloadTask(video_id="v1")
    task.transition_to(DownloadStatus.RUNNING)

    with caplog.at_level(logging.ERROR, logger="ytmusic_tui.download"):
        DownloadManager._set_failed(task, "conexão caiu", error_code="DOWNLOAD_ERROR")

    record = next(r for r in caplog.records if getattr(r, "event", None) == "download_failed")
    assert record.operation_id == task.id
    assert record.error_code == "DOWNLOAD_ERROR"
    assert record.getMessage() == "Download falhou: conexão caiu"


def test_evento_download_failed_captura_ffmpeg(caplog):
    """Código do ffmpeg no texto vira ``FFMPEG_EXIT_N`` (§13.1 — conversão)."""
    from ytmusic_tui.download import DownloadManager, DownloadStatus, DownloadTask

    task = DownloadTask(video_id="v1")
    task.transition_to(DownloadStatus.RUNNING)

    with caplog.at_level(logging.ERROR, logger="ytmusic_tui.download"):
        DownloadManager._set_failed(
            task,
            "ERROR: Postprocessing: ffmpeg exited with code 1",
            error_code="DOWNLOAD_ERROR",
        )

    record = next(r for r in caplog.records if getattr(r, "event", None) == "download_failed")
    assert record.error_code == "FFMPEG_EXIT_1"


def test_eventos_api_retry_e_falha_final(caplog):
    """Retry re-tentado e desistência ficam no log com o erro classificado."""
    import requests

    from ytmusic_tui.api.retry import RetryPolicy, call_with_retry
    from ytmusic_tui.errors import APIError

    def _sempre_falha() -> None:
        raise requests.exceptions.ConnectionError("conexão recusada")

    with caplog.at_level(logging.INFO, logger="ytmusic_tui.api"), pytest.raises(APIError):
        call_with_retry(
            _sempre_falha,
            policy=RetryPolicy(attempts=2, base_delay=0.0),
            sleep=lambda _seconds: None,
        )

    events = [getattr(record, "event", None) for record in caplog.records]
    assert "api_retry" in events
    assert "api_failed" in events
    retry_record = next(r for r in caplog.records if getattr(r, "event", None) == "api_retry")
    failed_record = next(r for r in caplog.records if getattr(r, "event", None) == "api_failed")
    assert retry_record.error_code == "NetworkError"
    assert failed_record.error_code == "NetworkError"


def test_eventos_de_migracao(caplog, tmp_path):
    """``migration_applied`` (from→to) e ``migration_failed`` com rollback."""
    from sqlalchemy import create_engine

    from ytmusic_tui.library.errors import MigrationFailedError
    from ytmusic_tui.library.migrations import MigrationStep, migrate

    db = tmp_path / "library.db"
    engine = create_engine(f"sqlite:///{db}")

    def _quebra(connection) -> None:
        raise RuntimeError("DDL de teste falhou")

    def _migrar(steps):
        return migrate(engine, db_path=db, backup_dir=tmp_path / "backups", migrations=steps)

    with caplog.at_level(logging.INFO, logger="ytmusic_tui.library.migrations"):
        _migrar([MigrationStep(1, "inicial", lambda connection: None)])
    fresh = [r for r in caplog.records if getattr(r, "event", None) == "migration_applied"]
    assert len(fresh) == 1
    assert "v0 → v1" in fresh[0].getMessage()

    caplog.clear()
    with (
        caplog.at_level(logging.INFO, logger="ytmusic_tui.library.migrations"),
        pytest.raises(MigrationFailedError),
    ):
        _migrar(
            [
                MigrationStep(1, "inicial", lambda connection: None),
                MigrationStep(2, "quebrada", _quebra),
            ]
        )
    failed = [r for r in caplog.records if getattr(r, "event", None) == "migration_failed"]
    assert len(failed) == 1
    assert "v2" in failed[0].getMessage()
    assert failed[0].error_code == "RuntimeError"
    assert failed[0].exc_info is not None

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="ytmusic_tui.library.migrations"):
        _migrar(
            [
                MigrationStep(1, "inicial", lambda connection: None),
                MigrationStep(2, "ok", lambda connection: None),
            ]
        )
    applied = [r for r in caplog.records if getattr(r, "event", None) == "migration_applied"]
    assert len(applied) == 1
    assert "v1 → v2" in applied[0].getMessage()


def test_evento_interrupted_recovered(caplog, tmp_path):
    """Recuperação de tarefas interrompidas emite a contagem (T13)."""
    from ytmusic_tui.download import DownloadManager, DownloadTask
    from ytmusic_tui.download.store import DownloadTaskStore

    store = DownloadTaskStore()
    task = DownloadTask(video_id="v1", title="Música")
    store.save(task, download_dir=tmp_path / "music", final_path=tmp_path / "music" / "v1.mp3")

    manager = DownloadManager(store=store)
    with caplog.at_level(logging.INFO, logger="ytmusic_tui.download"):
        recovered = manager.recover_interrupted()

    assert recovered == 1
    record = next(r for r in caplog.records if getattr(r, "event", None) == "interrupted_recovered")
    assert "1 download(s)" in record.getMessage()


def test_evento_orphan_quarantined(caplog, tmp_path):
    """Arquivo inválido sem registro vai para quarentena com evento (T13)."""
    from ytmusic_tui.library.recovery import reconcile

    download_dir = tmp_path / "music"
    download_dir.mkdir()
    broken = download_dir / "Artista - Faixa.mp3"
    broken.write_bytes(b"")

    with caplog.at_level(logging.WARNING, logger="ytmusic_tui.library.recovery"):
        report = reconcile(download_dir)

    assert report.quarantined_files == [broken]
    record = next(r for r in caplog.records if getattr(r, "event", None) == "orphan_quarantined")
    assert "quarentena" in record.getMessage()


async def test_eventos_app_start_e_app_stop(monkeypatch, caplog):
    """Ciclo do app registra as versões no início e no fim (T13 §13.1)."""
    from ytmusic_tui.tui.app import YTMusicTUI

    _neutralizar_reconciliacao(monkeypatch)

    with caplog.at_level(logging.INFO, logger="ytmusic_tui.tui.app"):
        app = YTMusicTUI()
        async with app.run_test(size=(80, 24)):
            pass

    events = [getattr(record, "event", None) for record in caplog.records]
    assert "app_start" in events
    assert "app_stop" in events
    start = next(r for r in caplog.records if getattr(r, "event", None) == "app_start")
    message = start.getMessage()
    assert "app v" in message and "Python" in message
    assert "ytmusicapi" in message and "yt-dlp" in message


async def test_app_stop_e_o_ultimo_passo_do_unmount(monkeypatch, caplog):
    """T13: o ``app_stop`` sai DEPOIS do teardown, nunca antes.

    Entre o início do ``on_unmount`` e a guarda do ``player.shutdown()``
    existe a janela do deadlock do T11 (thread do mpv presa em
    ``call_from_thread`` × ``terminate()``). O ``app_stop`` carrega o
    ``versions_summary()`` (~ms, ``importlib.metadata``): registrá-lo no topo
    do unmount alargava a janela e a suíte travava na L2 do player. Este
    teste trava a ordem que fecha o log só no fim.
    """
    from ytmusic_tui.tui import app as app_module
    from ytmusic_tui.tui.app import YTMusicTUI

    _neutralizar_reconciliacao(monkeypatch)

    ordem: list[str] = []

    def _espiao(objeto, metodo: str, nome: str) -> None:
        original = getattr(objeto, metodo)

        def registrado(*args, **kwargs):
            ordem.append(nome)
            return original(*args, **kwargs)

        monkeypatch.setattr(objeto, metodo, registrado)

    _espiao(app_module.player, "shutdown", "player.shutdown")
    _espiao(app_module.download_manager, "shutdown", "downloads.shutdown")
    _espiao(app_module.db_manager, "close", "db.close")

    class _Captura(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if getattr(record, "event", None) == "app_stop":
                ordem.append("log:app_stop")

    captura = _Captura()
    logger = logging.getLogger("ytmusic_tui.tui.app")
    logger.addHandler(captura)
    try:
        with caplog.at_level(logging.INFO, logger="ytmusic_tui.tui.app"):
            app = YTMusicTUI()
            async with app.run_test(size=(80, 24)):
                pass
    finally:
        logger.removeHandler(captura)

    assert ordem == [
        "player.shutdown",
        "downloads.shutdown",
        "db.close",
        "log:app_stop",
    ]


async def test_evento_db_open_failed(monkeypatch, caplog):
    """Falha ao preparar o banco vira evento com o erro tipado (T13 §13.1)."""
    from ytmusic_tui.library import db_manager
    from ytmusic_tui.library.errors import LibraryError
    from ytmusic_tui.tui.app import YTMusicTUI

    _neutralizar_reconciliacao(monkeypatch)

    def _falha() -> None:
        raise LibraryError("banco indisponível de teste")

    monkeypatch.setattr(db_manager, "migrate", _falha)

    with caplog.at_level(logging.ERROR, logger="ytmusic_tui.tui.app"):
        app = YTMusicTUI()
        async with app.run_test(size=(80, 24)):
            pass

    record = next(r for r in caplog.records if getattr(r, "event", None) == "db_open_failed")
    assert record.error_code == "LibraryError"
    assert "banco indisponível de teste" in record.getMessage()
