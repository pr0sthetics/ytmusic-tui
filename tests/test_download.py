"""Testes do gerenciador de downloads (offline, com yt-dlp falso).

Cobrem as regressões encontradas na revisão de código:
- o caminho final só aparece nos ``post_hooks`` (``progress_hooks`` trazem o
  arquivo baixado, antes da conversão; o task terminava sempre FAILED);
- ``noprogress`` precisa estar ativo (sem isso o yt-dlp imprime sobre a TUI);
- ``EmbedThumbnail``/``FFmpegMetadata`` são post-processadores (as chaves de
  CLI ``embedthumbnail``/``addmetadata`` eram ignoradas);
- cancelamento via ``abort_event`` (``future.cancel()`` não interrompia nada);
- nomes de arquivo limitados para não estourar ENAMETOOLONG.

E a máquina de estados da T02 (§4 do plano):
- limite de concorrência nunca é estourado, mesmo com 10 tarefas;
- cancelar não afeta outras tarefas; VALIDATING/terminais recusam cancelamento;
- o abort é observado no próximo ``progress_hook`` (T02-R3, com limite de
  tempo no teste);
- ``shutdown`` não bloqueia, marca INTERRUPTED e não deixa thread viva;
- CANCELLED nunca vira COMPLETED (corrida cancelar × worker, §4.4);
- falha ao validar/registrar o arquivo vira FAILED (nunca sucesso falso).

E o pipeline de publicação do T03 (§5.1–§5.2):
- disco cheio, conversão falha e temporário ausente → FAILED, nada publicado;
- destino existente (Q2a): válido → importa/registra sem baixar; inválido →
  FAILED sem apagar nada (nunca sobrescreve);
- reserva ocupada por outra tarefa → FAILED (exclusividade em disco).
"""

import asyncio
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import ytmusic_tui.download as download_mod
import ytmusic_tui.download.pipeline as pipeline_mod
from ytmusic_tui.download import DownloadOutcome, DownloadStatus, DownloadTask
from ytmusic_tui.download.pipeline import release, reserve
from ytmusic_tui.download.store import DownloadTaskStore
from ytmusic_tui.library.database import DatabaseManager


class _FakeDownloadError(Exception):
    pass


class _FakeExtractorError(Exception):
    """Irmã de ``_FakeDownloadError`` (como no yt-dlp real, que NÃO herda)."""


class _FakeUtils:
    DownloadError = _FakeDownloadError
    ExtractorError = _FakeExtractorError


class _FakeYoutubeDL:
    """Simula o ciclo do yt-dlp: baixa, converte e chama os hooks."""

    def __init__(self, opts: dict):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls: list[str]) -> None:
        template = self.opts["outtmpl"]
        intermediate = Path(template.replace("%(ext)s", "webm"))
        intermediate.parent.mkdir(parents=True, exist_ok=True)
        intermediate.write_bytes(b"audio-original")

        for hook in self.opts["progress_hooks"]:
            hook(
                {
                    "status": "downloading",
                    "filename": str(intermediate),
                    "_percent_str": " 42.5%",
                    "_speed_str": "2MiB/s",
                    "_eta_str": "00:03",
                }
            )
        for hook in self.opts["progress_hooks"]:
            hook({"status": "finished", "filename": str(intermediate), "total_bytes": 14})

        # Post-processamento "converte" para o arquivo final
        final = Path(template.replace("%(ext)s", "mp3"))
        final.write_bytes(b"audio-convertido-mp3")
        for hook in self.opts.get("post_hooks", []):
            hook(str(final))


class _FakeYtDlp:
    utils = _FakeUtils
    YoutubeDL = _FakeYoutubeDL


def _fake_ytdlp(youtube_dl_cls) -> SimpleNamespace:
    """Módulo yt_dlp falso em torno de uma classe YoutubeDL."""
    return SimpleNamespace(utils=_FakeUtils, YoutubeDL=youtube_dl_cls)


def _slow_youtube_dl(release: threading.Event):
    """yt-dlp falso que "baixa" devagar, cooperando com o abort (T02-R3).

    Cada iteração do laço dispara um ``progress_hook``; o abort é observado
    nele (mesma semântica do yt-dlp real — latência de ~1 tick por chunk).
    """

    class _SlowYoutubeDL:
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

            while not release.is_set():
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "downloading", "_percent_str": "50%"})
                time.sleep(0.01)

            for hook in self.opts.get("post_hooks", []):
                hook(str(final))

    return _SlowYoutubeDL


def _counting_youtube_dl(release: threading.Event, state: dict):
    """yt-dlp falso que conta execuções simultâneas e segura até ``release``."""

    class _CountingYoutubeDL:
        def __init__(self, opts: dict):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls: list[str]) -> None:
            with state["lock"]:
                state["running"] += 1
                state["peak"] = max(state["peak"], state["running"])
            try:
                release.wait(timeout=10)
                template = self.opts["outtmpl"]
                final = Path(template.replace("%(ext)s", "mp3"))
                final.parent.mkdir(parents=True, exist_ok=True)
                final.write_bytes(b"audio-convertido-mp3")
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "finished"})
                for hook in self.opts.get("post_hooks", []):
                    hook(str(final))
            finally:
                with state["lock"]:
                    state["running"] -= 1

    return _CountingYoutubeDL


def _counting_state() -> dict:
    return {"running": 0, "peak": 0, "lock": threading.Lock()}


async def _wait_until(condition, timeout: float = 5.0) -> None:
    """Espera (sem bloquear o loop de eventos) até ``condition()`` ser verdadeira."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condição não ficou verdadeira no prazo")
        await asyncio.sleep(0.005)


def _walk_to_status(task: DownloadTask, final: DownloadStatus) -> None:
    """Leva a tarefa ao estado final pedido só por transições válidas."""
    task.transition_to(DownloadStatus.RUNNING)
    if final is DownloadStatus.FAILED:
        task.transition_to(DownloadStatus.FAILED)
    elif final is DownloadStatus.CANCELLED:
        task.transition_to(DownloadStatus.CANCELLED)
    elif final is DownloadStatus.INTERRUPTED:
        task.transition_to(DownloadStatus.INTERRUPTED)
    else:
        assert final is DownloadStatus.COMPLETED
        task.transition_to(DownloadStatus.VALIDATING)
        task.transition_to(DownloadStatus.COMPLETED)


@pytest.fixture
def temp_db():
    """Banco temporário para o espelho de tarefas (T04)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = Path(handle.name)

    db_manager = DatabaseManager(db_path)
    db_manager.create_all()

    yield db_manager

    db_manager.close()
    db_path.unlink(missing_ok=True)


@pytest.fixture
def store(temp_db) -> DownloadTaskStore:
    """Store isolado: os testes nunca tocam o banco real do usuário."""
    return DownloadTaskStore(db=temp_db)


@pytest.fixture
def manager(monkeypatch, tmp_path, store):
    """DownloadManager com settings isoladas, yt-dlp falso e store temporário."""
    fake_settings = SimpleNamespace(
        download_dir=tmp_path,
        download_format="mp3",
        download_quality="320",
        embed_metadata=True,
        download_concurrent=1,
    )
    monkeypatch.setattr(download_mod, "settings", fake_settings)
    monkeypatch.setattr(download_mod, "_load_yt_dlp", lambda: _FakeYtDlp)
    return download_mod.DownloadManager(store=store)


def test_ydl_options_are_silent_and_embed_metadata(manager):
    """Opções essenciais: silêncio, post_hooks e PPs de metadados/capa."""
    task = DownloadTask(video_id="abc", title="T", artist="A", album="Alb")
    opts = manager._get_ydl_options(task)

    assert opts["quiet"] is True
    assert opts["no_warnings"] is True
    assert opts["noprogress"] is True  # sem isso o yt-dlp escreve na TUI
    assert opts["writethumbnail"] is True
    assert len(opts["post_hooks"]) == 1
    assert len(opts["progress_hooks"]) == 1

    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert keys == ["FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail"]
    # Sem already_have_thumbnail: o .webp temporário é removido ao final
    assert all("already_have_thumbnail" not in pp for pp in opts["postprocessors"])
    # Chaves que só existem na CLI não devem mais ser passadas
    assert "embedthumbnail" not in opts
    assert "addmetadata" not in opts


def test_ydl_options_without_metadata(manager, monkeypatch):
    """Sem embed_metadata não há capa/caminho de imagem nem tags."""
    monkeypatch.setattr(download_mod.settings, "embed_metadata", False)
    opts = manager._get_ydl_options(DownloadTask(video_id="abc"))
    assert opts["writethumbnail"] is False
    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert keys == ["FFmpegExtractAudio"]


def test_output_template_limits_component_length(manager):
    """Título/artista gigantes não podem estourar o limite do filesystem."""
    task = DownloadTask(
        video_id="abc",
        title="T" * 500,
        artist="A" * 500,
        album="L" * 500,
    )
    template = manager._get_output_template(task)
    filename = Path(template).name
    assert len(filename) <= 180  # título(100) + ' - ' + artista(60) + ext


def test_output_template_truncates_multibyte_by_bytes(manager, tmp_path):
    """O limite do filesystem é em bytes: título CJK/emoji não pode estourar.

    ENAMETOOLONG acontece a partir de 255 bytes por componente; um corte
    apenas por caracteres passa longe disso com multibyte (100 CJK = 300
    bytes).
    """
    task = DownloadTask(
        video_id="abc",
        title="音" * 500,
        artist="🎵" * 500,
        album="歌" * 500,
    )
    template = manager._get_output_template(task)
    assert "\ufffd" not in template  # nenhum caractere quebrado no meio

    rel = Path(template).relative_to(tmp_path)
    for part in rel.parts[:-1]:  # diretórios (artista/álbum)
        assert len(part.encode("utf-8")) <= 255
    # O arquivo ainda precisa caber com extensão (.flac), capa (.webp) e o
    # sufixo de download parcial do yt-dlp (.part)
    final = rel.parts[-1].replace("%(ext)s", "flac")
    assert len(final.encode("utf-8")) + len(".part") <= 255


def test_download_task_completes_with_final_path(manager, monkeypatch, tmp_path):
    """O task deve terminar COMPLETED com o caminho pós-conversão."""
    updates: list[tuple] = []
    monkeypatch.setattr(
        download_mod.library,
        "update_track_file",
        lambda *args, **kwargs: updates.append(args),
    )

    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb", track_id=7)
    result = manager._download_task(task)

    assert result.status == DownloadStatus.COMPLETED
    assert result.error == ""
    assert result.progress == 100.0
    assert result.file_path == tmp_path / "A" / "Alb" / "T - A.mp3"
    assert result.file_path.exists()

    # Registro na biblioteca com o caminho final
    assert updates and updates[0][0] == 7
    assert updates[0][1] == result.file_path
    # Reserva/temporário não deixam sobras (§5.2)
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_progress_hook_does_not_complete_prematurely(manager):
    """'finished' é só o download; a conversão ainda vai rodar."""
    task = DownloadTask(video_id="abc")
    manager._progress_hook(
        task,
        {
            "status": "downloading",
            "_percent_str": " 42.5%",
            "_speed_str": "2MiB/s",
            "_eta_str": "00:03",
        },
    )
    assert task.progress == 42.5
    assert task.speed == "2MiB/s"

    manager._progress_hook(task, {"status": "finished", "filename": "/x.m4a"})
    assert task.progress == 100.0
    assert task.status != DownloadStatus.COMPLETED


def test_cancel_aborts_download(manager):
    """cancel_download precisa sinalizar abort e remover da fila."""
    task = manager.queue_download(video_id="abc", title="T")
    assert manager.cancel_download("abc") is True
    assert task.abort_event.is_set()
    assert task.status == DownloadStatus.CANCELLED
    assert task not in manager.get_queue()

    # Já cancelada (terminal): cancelar de novo é recusado, sem efeito
    assert manager.cancel_download("abc") is False

    with pytest.raises(RuntimeError):
        manager._progress_hook(task, {"status": "downloading", "_percent_str": "1%"})

    assert manager.cancel_download("inexistente") is False


def test_cancel_rejected_while_validating_or_finished(manager):
    """Contrato: só PENDING/RUNNING cancelam; VALIDATING/terminal → False (§4.2).

    Em VALIDATING a janela é curta: cancelar deixaria o arquivo publicado
    sem registro (INV-2). A recusa não pode mudar nada — nem abortar.
    """
    task = manager.queue_download(video_id="abc", title="T")
    manager._active_downloads["abc"] = task
    task.transition_to(DownloadStatus.RUNNING)
    task.transition_to(DownloadStatus.VALIDATING)

    assert manager.cancel_download("abc") is False
    assert task.status is DownloadStatus.VALIDATING
    assert not task.abort_event.is_set()

    task.transition_to(DownloadStatus.COMPLETED)
    assert manager.cancel_download("abc") is False
    assert task.status is DownloadStatus.COMPLETED


def test_download_task_cancelled(manager, monkeypatch):
    """Abort durante o download marca CANCELLED (e não FAILED)."""

    def _download_and_cancel(self, urls):
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "_percent_str": "10%"})

    task = manager.queue_download(video_id="abc", title="T")
    task.abort_event.set()
    monkeypatch.setattr(_FakeYoutubeDL, "download", _download_and_cancel)

    result = manager._download_task(task)
    assert result.status == DownloadStatus.CANCELLED


def test_shutdown_does_not_block(manager):
    """shutdown não pode travar a saída do app (wait=False + abort)."""
    task = manager.queue_download(video_id="abc", title="T")
    manager._active_downloads[task.video_id] = task
    manager.shutdown()
    assert task.abort_event.is_set()


def test_is_active_or_pending_blocks_duplicates_but_allows_retry(manager):
    """O mesmo vídeo não pode ser enfileirado duas vezes enquanto está ativo."""
    assert manager.is_active_or_pending("x") is False

    task = manager.queue_download(video_id="x", title="T")
    assert manager.is_active_or_pending("x") is True  # pendente na fila
    assert manager.is_active_or_pending("y") is False

    # Qualquer estado final libera nova tentativa (retry = nova tarefa)
    for final in (
        DownloadStatus.COMPLETED,
        DownloadStatus.CANCELLED,
        DownloadStatus.FAILED,
        DownloadStatus.INTERRUPTED,
    ):
        manager._queue.remove(task)
        task = manager.queue_download(video_id="x", title="T")
        _walk_to_status(task, final)
        assert manager.is_active_or_pending("x") is False

    # Em voo (RUNNING/VALIDATING) bloqueia — na fila visível ou não
    task = manager.queue_download(video_id="x", title="T")
    task.transition_to(DownloadStatus.RUNNING)
    assert manager.is_active_or_pending("x") is True
    manager._active_downloads["x"] = task
    manager._queue.remove(task)
    assert manager.is_active_or_pending("x") is True

    task.transition_to(DownloadStatus.VALIDATING)
    assert manager.is_active_or_pending("x") is True


def test_is_active_or_pending_blocks_final_path_collisions(manager):
    """Faixas diferentes com o mesmo arquivo final não baixam juntas (T02-R5)."""
    manager.queue_download(video_id="a", title="T", artist="A", album="Alb")

    # Mesmo caminho final, vídeo diferente: bloqueia
    assert manager.is_active_or_pending("b", title="T", artist="A", album="Alb") is True
    # Caminho final diferente: libera
    assert manager.is_active_or_pending("b", title="Outra", artist="A", album="Alb") is False
    # Sem os três campos, só o vídeo é checado (compatibilidade)
    assert manager.is_active_or_pending("b") is False

    # Tarefa finalizada não bloqueia mais o caminho (retry é permitido)
    _walk_to_status(manager.get_queue()[0], DownloadStatus.FAILED)
    assert manager.is_active_or_pending("b", title="T", artist="A", album="Alb") is False


def test_clear_completed_includes_interrupted_and_deletes_no_file(manager, tmp_path):
    """clear_completed limpa a memória (incl. INTERRUPTED) sem tocar arquivos."""
    kept = manager.queue_download(video_id="run", title="Em andamento")
    kept.transition_to(DownloadStatus.RUNNING)

    done = manager.queue_download(video_id="done", title="Concluída")
    _walk_to_status(done, DownloadStatus.COMPLETED)
    arquivo = tmp_path / "A" / "Alb" / "T - A.mp3"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_bytes(b"audio")
    done.file_path = arquivo

    interrupted = manager.queue_download(video_id="int", title="Interrompida")
    _walk_to_status(interrupted, DownloadStatus.INTERRUPTED)

    manager.clear_completed()

    assert manager.get_queue() == [kept]
    # Limpeza de .part/temporários exige política própria (T04-R2): aqui,
    # nem o arquivo conflitante da tarefa interrompida é apagado.
    assert arquivo.exists()


@pytest.mark.asyncio
async def test_cancel_latency_is_bounded_by_next_progress_hook(manager, monkeypatch):
    """T02-R3: o abort é observado no próximo ``progress_hook`` — com limite.

    O fake dispara um hook a cada ~10 ms; o limite de 1 s é folgado para não
    deixar o teste instável no CI, mas já prova que o worker encerra logo
    após o cancelamento (e não ao "fim" do download).
    """
    release = threading.Event()
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_slow_youtube_dl(release))
    )

    task = manager.queue_download(video_id="abc", title="T")
    runner = asyncio.create_task(manager.download(task))
    await _wait_until(lambda: task.status is DownloadStatus.RUNNING)

    start = time.monotonic()
    assert manager.cancel_download("abc") is True
    await asyncio.wait_for(runner, timeout=5.0)
    latency = time.monotonic() - start

    assert task.status is DownloadStatus.CANCELLED
    assert latency < 1.0


@pytest.mark.asyncio
async def test_cancel_does_not_affect_other_downloads(manager, monkeypatch):
    """Cancelar uma tarefa não cancela as demais (§4.4)."""
    manager.set_concurrent(2)
    release = threading.Event()
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_slow_youtube_dl(release))
    )

    slow = manager.queue_download(video_id="a", title="Faixa A")
    keep = manager.queue_download(video_id="b", title="Faixa B")
    runner_a = asyncio.create_task(manager.download(slow))
    runner_b = asyncio.create_task(manager.download(keep))

    await _wait_until(
        lambda: slow.status is DownloadStatus.RUNNING and keep.status is DownloadStatus.RUNNING
    )

    assert manager.cancel_download("a") is True
    await asyncio.wait_for(runner_a, timeout=5.0)

    assert slow.status is DownloadStatus.CANCELLED
    assert keep.status is DownloadStatus.RUNNING  # segue intocada
    assert not keep.abort_event.is_set()

    release.set()
    await asyncio.wait_for(runner_b, timeout=5.0)
    assert keep.status is DownloadStatus.COMPLETED


@pytest.mark.asyncio
async def test_shutdown_interrupts_in_flight_and_leaves_no_live_threads(manager, monkeypatch):
    """T02-R4/§4.4: shutdown < 2 s, INTERRUPTED e nenhuma thread viva depois."""
    release = threading.Event()
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_slow_youtube_dl(release))
    )

    task = manager.queue_download(video_id="abc", title="T")
    runner = asyncio.create_task(manager.download(task))
    await _wait_until(lambda: task.status is DownloadStatus.RUNNING)

    # Captura as threads do executor em uso antes do shutdown (atributo
    # interno do stdlib; a asserção é de que elas terminam, não de
    # RUNNING/VALIDATING).
    threads = list(manager._executor._threads)
    assert threads

    start = time.monotonic()
    manager.shutdown()
    elapsed = time.monotonic() - start

    assert elapsed < 2.0  # shutdown não bloqueia a saída do app
    assert task.status is DownloadStatus.INTERRUPTED  # nunca fica RUNNING/VALIDATING

    # O worker coopera com o abort (próximo progress_hook) e a thread sai.
    for thread in threads:
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    # O future resolve, o estado final não é sobrescrito e não sobra ativo.
    await asyncio.wait_for(runner, timeout=2.0)
    assert task.status is DownloadStatus.INTERRUPTED
    assert manager.get_active_downloads() == []


@pytest.mark.asyncio
async def test_concurrency_limit_respected_under_load(manager, monkeypatch):
    """Com 10 tarefas, nunca mais que ``download_concurrent`` rodam juntas (§4.4)."""
    manager.set_concurrent(2)
    release = threading.Event()
    state = _counting_state()
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_counting_youtube_dl(release, state))
    )

    tasks = [manager.queue_download(video_id=f"v{i}", title=f"Faixa {i}") for i in range(10)]
    runners = [asyncio.create_task(manager.download(task)) for task in tasks]

    await _wait_until(lambda: state["running"] >= 2)
    await asyncio.sleep(0.05)  # chance de violação, caso o limite esteja furado
    assert state["peak"] == 2  # exatamente 2: limite respeitado

    release.set()
    await asyncio.wait_for(asyncio.gather(*runners), timeout=10.0)
    assert state["peak"] == 2
    assert all(task.status is DownloadStatus.COMPLETED for task in tasks)


@pytest.mark.asyncio
async def test_set_concurrent_recreates_executor_and_respects_new_limit(manager, monkeypatch):
    """T02-R2: set_concurrent recria o executor; pendentes seguem; novo limite vale."""
    release = threading.Event()
    state = _counting_state()
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_counting_youtube_dl(release, state))
    )

    # Limite inicial 1 (fixture): B fica pendente atrás de A
    a = manager.queue_download(video_id="a", title="A")
    b = manager.queue_download(video_id="b", title="B")
    runner_a = asyncio.create_task(manager.download(a))
    runner_b = asyncio.create_task(manager.download(b))
    await _wait_until(lambda: a.status is DownloadStatus.RUNNING)
    assert b.status is DownloadStatus.PENDING  # ainda na fila do executor antigo

    # Recria o executor com 3 vagas; A e B seguem no antigo e completam
    manager.set_concurrent(3)
    release.set()
    await asyncio.wait_for(asyncio.gather(runner_a, runner_b), timeout=10.0)
    assert a.status is DownloadStatus.COMPLETED
    assert b.status is DownloadStatus.COMPLETED  # pendente seguiu no executor antigo

    # O limite novo vale para o executor novo: 4 tarefas → no máximo 3 juntas
    release2 = threading.Event()
    state2 = _counting_state()
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_counting_youtube_dl(release2, state2))
    )
    tasks = [manager.queue_download(video_id=f"c{i}", title=f"C{i}") for i in range(4)]
    runners = [asyncio.create_task(manager.download(task)) for task in tasks]

    await _wait_until(lambda: state2["running"] >= 3)
    await asyncio.sleep(0.05)
    assert state2["peak"] == 3
    assert state2["running"] == 3  # a 4ª ainda não começou

    release2.set()
    await asyncio.wait_for(asyncio.gather(*runners), timeout=10.0)
    assert all(task.status is DownloadStatus.COMPLETED for task in tasks)


def test_set_concurrent_validates_instead_of_clamping(manager):
    """T06-R4: inválido levanta (tipo → ``TypeError``; faixa → ``ValueError``).

    Antes, ``max(1, min(10, workers))`` ajustava em silêncio: um valor
    digitado errado virava um limite diferente do pedido sem ninguém saber.
    Agora a API recusa e NADA é recriado; quem normaliza para o usuário é a
    UI da aba Config (testado em ``test_tui.py``).
    """
    executor = manager._executor

    with pytest.raises(TypeError, match="int"):
        manager.set_concurrent("2")
    with pytest.raises(TypeError, match="int"):
        manager.set_concurrent(2.0)
    with pytest.raises(TypeError, match="int"):
        manager.set_concurrent(True)  # bool é subtipo de int, mas não é "quantidade"

    with pytest.raises(ValueError, match="fora da faixa"):
        manager.set_concurrent(0)
    with pytest.raises(ValueError, match="fora da faixa"):
        manager.set_concurrent(11)

    assert manager._executor is executor  # recusou sem recriar o executor

    # Extremos são válidos de verdade (não "clampados"): 1 e 10 passam.
    manager.set_concurrent(1)
    first = manager._executor
    assert first is not executor
    manager.set_concurrent(10)
    assert manager._executor is not first


@pytest.mark.asyncio
async def test_cancelled_never_becomes_completed(manager, monkeypatch):
    """Corrida cancelar × worker: CANCELLED nunca vira COMPLETED (§4.4).

    O fake ignora o abort e "termina com sucesso" — emula o yt-dlp
    concluindo entre o cancelamento e a validação. O estado CANCELLED
    precisa barrar as transições para VALIDATING/COMPLETED.
    """
    released = threading.Event()
    started = threading.Event()

    class _StubbornYoutubeDL:
        def __init__(self, opts: dict):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls: list[str]) -> None:
            started.set()
            released.wait(timeout=10)
            template = self.opts["outtmpl"]
            final = Path(template.replace("%(ext)s", "mp3"))
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(b"audio-convertido-mp3")
            for hook in self.opts.get("post_hooks", []):
                hook(str(final))

    monkeypatch.setattr(download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_StubbornYoutubeDL))
    task = manager.queue_download(video_id="abc", title="T")
    runner = asyncio.create_task(manager.download(task))
    await _wait_until(started.is_set)

    assert manager.cancel_download("abc") is True
    assert task.status is DownloadStatus.CANCELLED

    released.set()  # o "yt-dlp" termina com sucesso mesmo assim
    await asyncio.wait_for(runner, timeout=5.0)

    assert task.status is DownloadStatus.CANCELLED  # nunca COMPLETED


def test_library_update_failure_marks_failed_not_completed(manager, monkeypatch, tmp_path):
    """Falha ao registrar na biblioteca → FAILED (nunca sucesso falso)."""

    def _boom(*args, **kwargs):
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(download_mod.library, "update_track_file", _boom)

    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb", track_id=7)
    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert "biblioteca" in result.error
    # O arquivo FOI publicado (ordem do §5.1) e permanece no disco sem
    # registro — "arquivo sem registro" é recuperável pela reconciliação
    # (§5.3/T03); o que não pode é a tarefa terminar COMPLETED.
    final = tmp_path / "A" / "Alb" / "T - A.mp3"
    assert result.file_path == final
    assert final.exists()


def test_missing_final_file_fails_validation(manager, monkeypatch, tmp_path):
    """VALIDAÇÃO mínima: sem post_hook (arquivo não capturado) → FAILED."""

    def _download_without_post_hook(self, urls):
        for hook in self.opts["progress_hooks"]:
            hook({"status": "finished"})

    monkeypatch.setattr(_FakeYoutubeDL, "download", _download_without_post_hook)
    task = manager.queue_download(video_id="abc", title="T")
    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert "ausente" in result.error
    # Nada publicado e nenhum temporário deixado para trás
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_empty_final_file_fails_validation(manager, monkeypatch, tmp_path):
    """VALIDAÇÃO mínima: arquivo final vazio → FAILED (nunca COMPLETED)."""

    def _download_producing_empty_file(self, urls):
        template = self.opts["outtmpl"]
        final = Path(template.replace("%(ext)s", "mp3"))
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"")
        for hook in self.opts.get("post_hooks", []):
            hook(str(final))

    monkeypatch.setattr(_FakeYoutubeDL, "download", _download_producing_empty_file)
    task = manager.queue_download(video_id="abc", title="T")
    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert "vazio" in result.error
    # O temporário vazio é descartado na limpeza da reserva: nada publicado.
    assert result.file_path is not None and not result.file_path.exists()
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_download_fails_on_disk_full_and_cleans_temp(manager, monkeypatch, tmp_path):
    """§5.4: disco cheio na publicação → FAILED, nada publicado, sem sobras."""

    def _full(_src, _dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pipeline_mod.os, "replace", _full)

    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")
    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert "publicação" in result.error
    assert not (tmp_path / "A" / "Alb" / "T - A.mp3").exists()
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_download_fails_when_conversion_fails(manager, monkeypatch, tmp_path):
    """§5.4: falha na conversão (código ≠ 0) → FAILED e nada publicado."""

    def _ffmpeg_fails(self, urls):
        template = self.opts["outtmpl"]
        intermediate = Path(template.replace("%(ext)s", "webm"))
        intermediate.parent.mkdir(parents=True, exist_ok=True)
        intermediate.write_bytes(b"audio-original")
        raise _FakeDownloadError("ffmpeg falhou (código 1)")

    monkeypatch.setattr(_FakeYoutubeDL, "download", _ffmpeg_fails)
    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")
    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert "ffmpeg" in result.error
    assert not (tmp_path / "A").exists()
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_download_fails_when_temp_vanishes_before_publish(manager, monkeypatch, tmp_path):
    """§5.4: temporário ausente na hora de publicar → FAILED."""

    def _vanishing(self, urls):
        template = self.opts["outtmpl"]
        temp = Path(template.replace("%(ext)s", "mp3"))
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"audio")
        temp.unlink()  # sumiu entre o post_hook e a publicação
        for hook in self.opts.get("post_hooks", []):
            hook(str(temp))

    monkeypatch.setattr(_FakeYoutubeDL, "download", _vanishing)
    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")
    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert "ausente" in result.error
    assert not (tmp_path / "A").exists()


def test_existing_valid_file_is_adopted_not_downloaded(manager, monkeypatch, tmp_path):
    """§5.4 (Q2a, caso válido): arquivo existe → não baixa, registra e conclui."""

    def _bomb(self, urls):
        raise AssertionError("não deveria baixar: o arquivo já existe")

    monkeypatch.setattr(_FakeYoutubeDL, "download", _bomb)
    ensures: list[dict] = []
    updates: list[tuple] = []
    monkeypatch.setattr(
        download_mod.library, "ensure_track", lambda **kwargs: ensures.append(kwargs) or 9
    )
    monkeypatch.setattr(
        download_mod.library, "update_track_file", lambda *args, **kwargs: updates.append(args)
    )

    final = tmp_path / "A" / "Alb" / "T - A.mp3"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"audio-do-usuario")

    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")
    result = manager._download_task(task)

    assert result.status is DownloadStatus.COMPLETED
    assert final.read_bytes() == b"audio-do-usuario"  # nunca sobrescreve
    assert result.file_path == final
    # R8: o desfecho fica registrado — adotado ≠ baixado.
    assert result.outcome is DownloadOutcome.EXISTING_ADOPTED
    # Registro garantido: faixa criada (ensure_track) se faltar e arquivo vinculado
    assert ensures and ensures[0]["artist"] == "A" and ensures[0]["album"] == "Alb"
    # bitrate 0 no import: desconhecido sem reanalisar o arquivo
    assert updates and updates[0] == (9, final, len(b"audio-do-usuario"), "mp3", 0)


def test_existing_invalid_file_fails_without_overwrite(manager, monkeypatch, tmp_path):
    """§5.4 (Q2a, caso inválido): arquivo 0 byte → FAILED, nada é apagado."""

    def _bomb(self, urls):
        raise AssertionError("não deveria baixar: o arquivo já existe")

    monkeypatch.setattr(_FakeYoutubeDL, "download", _bomb)

    final = tmp_path / "A" / "Alb" / "T - A.mp3"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"")

    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")
    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert "inválido" in result.error
    assert result.outcome is DownloadOutcome.NONE  # não foi adotada
    assert final.exists() and final.stat().st_size == 0  # intocado


def test_ordem_de_album_viaja_na_tarefa_e_no_registro(manager, monkeypatch, tmp_path):
    """T17.1/R2: número/disco/total vão da tarefa para o registro na biblioteca."""
    ensures: list[dict] = []
    monkeypatch.setattr(download_mod.library, "ensure_track", lambda **kw: ensures.append(kw) or 7)
    monkeypatch.setattr(download_mod.library, "update_track_file", lambda *args, **kw: None)

    task = manager.queue_download(
        video_id="vid-ordem",
        title="T",
        artist="A",
        album="Alb",
        track_number=3,
        disc_number=1,
        album_track_count=9,
    )
    assert (task.track_number, task.disc_number, task.album_track_count) == (3, 1, 9)

    # Tarefa sem track_id (recuperação tardia): o registro já grava a ordem.
    final = tmp_path / "ordem.mp3"
    final.write_bytes(b"audio")
    manager._register_track_file(task, final, bitrate=0)

    assert ensures[0]["track_number"] == 3
    assert ensures[0]["disc_number"] == 1
    assert ensures[0]["album_track_count"] == 9


def test_lote_com_existente_e_nova_distingue_desfechos(manager, monkeypatch, tmp_path):
    """T17.1/R8 (Q13b): no lote, adotada ≠ baixada — e nada é sobrescrito."""
    monkeypatch.setattr(download_mod.library, "update_track_file", lambda *args, **kw: None)
    monkeypatch.setattr(download_mod.library, "ensure_track", lambda **kw: 1)

    existing_bytes = b"audio-do-usuario"
    final_existing = tmp_path / "A" / "Alb" / "Existente - A.mp3"
    final_existing.parent.mkdir(parents=True)
    final_existing.write_bytes(existing_bytes)

    adopted = manager.queue_download(video_id="velha", title="Existente", artist="A", album="Alb")
    result_a = manager._download_task(adopted)
    new = manager.queue_download(video_id="nova", title="Nova", artist="A", album="Alb")
    result_b = manager._download_task(new)

    assert result_a.status is DownloadStatus.COMPLETED
    assert result_a.outcome is DownloadOutcome.EXISTING_ADOPTED
    assert final_existing.read_bytes() == existing_bytes  # nunca sobrescreve

    assert result_b.status is DownloadStatus.COMPLETED
    assert result_b.outcome is DownloadOutcome.NONE
    assert result_b.file_path == tmp_path / "A" / "Alb" / "Nova - A.mp3"
    assert result_b.file_path.exists()


def test_second_task_for_same_destination_fails_busy(manager, monkeypatch, tmp_path):
    """§5.4: duas tarefas para o mesmo arquivo — a segunda é recusada (reserva)."""

    def _bomb(self, urls):
        raise AssertionError("não deveria baixar: a reserva é da outra tarefa")

    monkeypatch.setattr(_FakeYoutubeDL, "download", _bomb)

    final = tmp_path / "A" / "Alb" / "T - A.mp3"
    reservation = reserve(final, tmp_path)
    try:
        task = manager.queue_download(video_id="b", title="T", artist="A", album="Alb")
        result = manager._download_task(task)

        assert result.status is DownloadStatus.FAILED
        assert "Outra tarefa" in result.error
        assert not final.exists()
    finally:
        release(reservation)


# ===== T04-R1: espelho persistido do ciclo de vida da tarefa =====


def test_queue_download_persists_row_before_starting(manager, store, tmp_path):
    """Enfileirar grava a pista (PENDING) com os caminhos da tarefa."""
    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")

    row = store.find(task.id)
    assert row is not None
    assert row.status == DownloadStatus.PENDING.value
    assert row.video_id == "abc"
    assert row.final_path == str(tmp_path / "A" / "Alb" / "T - A.mp3")
    # download_dir persistido junto: o pai do final não é o download_dir (§5.3)
    assert row.download_dir == str(tmp_path)


def test_worker_persists_running_then_validating(manager, store, monkeypatch):
    """O espelho acompanha as transições da máquina (RUNNING → VALIDATING)."""
    seen: list[str] = []
    original = store.update_status

    def _record(task_id: str, status: DownloadStatus) -> None:
        seen.append(status.value)
        original(task_id, status)

    monkeypatch.setattr(store, "update_status", _record)
    monkeypatch.setattr(download_mod.library, "update_track_file", lambda *args, **kwargs: None)

    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb", track_id=7)
    result = manager._download_task(task)

    assert result.status is DownloadStatus.COMPLETED
    assert seen == [DownloadStatus.RUNNING.value, DownloadStatus.VALIDATING.value]


@pytest.mark.asyncio
async def test_terminal_download_removes_persisted_row(manager, store, monkeypatch):
    """Terminal da sessão não deixa linha: o resultado é da memória/UI."""
    monkeypatch.setattr(download_mod.library, "update_track_file", lambda *args, **kwargs: None)

    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb", track_id=7)
    assert store.find(task.id) is not None

    result = await manager.download(task)

    assert result.status is DownloadStatus.COMPLETED
    assert store.find(task.id) is None


def test_cancel_removes_persisted_row(manager, store):
    """Cancelar uma tarefa enfileirada remove a linha na hora."""
    task = manager.queue_download(video_id="abc", title="T")
    assert store.find(task.id) is not None

    assert manager.cancel_download("abc") is True

    assert store.find(task.id) is None


def test_shutdown_keeps_interrupted_row(manager, store):
    """INTERRUPTED é a única linha que fica: é a pista da próxima execução."""
    task = manager.queue_download(video_id="abc", title="T")
    manager._active_downloads[task.video_id] = task
    task.transition_to(DownloadStatus.RUNNING)

    manager.shutdown()

    row = store.find(task.id)
    assert row is not None
    assert row.status == DownloadStatus.INTERRUPTED.value


# ===== T11-R4/Q-D: falha de extração do yt-dlp vira ExtractionError =====


def _extraction_fails_with(error: Exception):
    """yt-dlp falso cujo ``download`` levanta a falha de extração dada."""

    class _FailYoutubeDL:
        def __init__(self, opts: dict):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls: list[str]) -> None:
            raise error

    return _FailYoutubeDL


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (
            _FakeExtractorError("ERROR: [youtube] abc: Unable to extract player response"),
            "Unable to extract player response",
        ),
        (
            _FakeDownloadError("ERROR: [youtube] abc: unable to extract player response"),
            "unable to extract player response",
        ),
        (
            _FakeDownloadError("ERROR: [youtube] abc: No video formats found!"),
            "No video formats found!",
        ),
        (
            _FakeDownloadError("ERROR: Unsupported URL: gefaked://x"),
            "Unsupported URL: gefaked://x",
        ),
        (
            _FakeDownloadError("ERROR: [youtube] abc: Sign in to confirm you're not a bot"),
            "Sign in to confirm you're not a bot",
        ),
    ],
    ids=["tipo", "marcador-unable", "marcador-formats", "marcador-url", "marcador-signin"],
)
def test_extraction_failure_is_labeled_with_next_action(
    manager, monkeypatch, tmp_path, error, detail
):
    """R4: extração falhou → rótulo da raiz + ação; nada publicado (INV-1).

    Vale para o tipo ``ExtractorError`` e para o mesmo erro embrulhado num
    ``DownloadError`` (como o yt-dlp real entrega); o detalhe original vai
    preservado (sem o prefixo "ERROR:") e o "nada foi baixado nem publicado"
    registra o INV-1.
    """
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_extraction_fails_with(error))
    )
    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")

    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert result.error is not None
    assert result.error.startswith("Falha de extração: ")
    assert detail in result.error
    assert "ERROR:" not in result.error
    assert "nada foi baixado nem publicado" in result.error
    assert "atualize o yt-dlp e tente de novo" in result.error
    # INV-1: extração é a PRIMEIRA etapa — nada no destino nem no temporário.
    assert not (tmp_path / "A").exists()
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_real_extractor_error_type_is_recognized(manager, monkeypatch, tmp_path):
    """R4: o ``ExtractorError`` do yt-dlp REAL é reconhecido pelo tipo."""
    yt_dlp = pytest.importorskip("yt_dlp")
    error = yt_dlp.utils.ExtractorError("ERROR: [youtube] abc: falha de extração tipada")

    monkeypatch.setattr(
        download_mod,
        "_load_yt_dlp",
        lambda: SimpleNamespace(utils=yt_dlp.utils, YoutubeDL=_extraction_fails_with(error)),
    )
    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")

    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert result.error is not None
    assert result.error.startswith("Falha de extração: ")
    assert "falha de extração tipada" in result.error


def test_other_download_errors_keep_todays_message(manager, monkeypatch, tmp_path):
    """R4 (controle −): ``DownloadError`` fora da extração segue como hoje."""
    error = _FakeDownloadError("HTTP Error 503: Service Unavailable")
    monkeypatch.setattr(
        download_mod, "_load_yt_dlp", lambda: _fake_ytdlp(_extraction_fails_with(error))
    )
    task = manager.queue_download(video_id="abc", title="T", artist="A", album="Alb")

    result = manager._download_task(task)

    assert result.status is DownloadStatus.FAILED
    assert result.error == "HTTP Error 503: Service Unavailable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
