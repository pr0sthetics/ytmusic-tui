"""Gerenciador de downloads usando yt-dlp (import tardio)."""

import asyncio
import logging
import re
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ytmusic_tui.config import MAX_DOWNLOAD_CONCURRENT, MIN_DOWNLOAD_CONCURRENT, settings
from ytmusic_tui.diagnostics import mask_diagnostics

# Pipeline de disco (§5.1/T03): validação, reserva, publicação e fsync.
from ytmusic_tui.download.pipeline import (
    TMP_DIR_NAME,
    DestinationBusy,
    InvalidInputError,
    MediaValidationError,
    PathEscapeError,
    PipelineError,
    build_output_template,
    discard_task_files,
    final_path_for,
    publish_file,
    release,
    reserve,
    resolve_within,
    validate_input,
    validate_media_file,
)

# Espelho persistido das tarefas (T04-R1): pista de recuperação para a
# próxima execução. Falha de escrita nunca quebra o download (ver `_persist`).
from ytmusic_tui.download.store import DownloadTaskStore, hydrate_interrupted

# Estados e máquina de transições vivem em `task.py` (T02) e são
# re-exportados aqui para os imports existentes (TUI, testes).
from ytmusic_tui.download.task import (
    ACTIVE_STATUSES,
    DownloadOptions,
    DownloadOutcome,
    DownloadStatus,
    DownloadTask,
    InvalidTransitionError,
)
from ytmusic_tui.errors import ConfigError, ExtractionError
from ytmusic_tui.library.service import library
from ytmusic_tui.log import log_event

#: URL de watch do YouTube Music — reexportada do módulo-folha ``urls``
#: (T11-R1); o nome ``WATCH_URL`` é o que os testes de ponta a ponta de
#: segurança substituem por um servidor local (§8.4).
from ytmusic_tui.urls import WATCH_URL_TEMPLATE as WATCH_URL

__all__ = [
    "DownloadManager",
    "DownloadOptions",
    "DownloadOutcome",
    "DownloadStatus",
    "DownloadTask",
    "InvalidTransitionError",
    "download_manager",
]

logger = logging.getLogger(__name__)

#: Código de saída do ffmpeg no texto do yt-dlp (§13.1/T13): o app não lança
#: subprocesso próprio — a conversão é do post-processador do yt-dlp, e o
#: código só existe na mensagem de erro. ``FFMPEG_EXIT_N`` no ``error_code``.
_FFMPEG_EXIT_RE = re.compile(r"ffmpeg exited with code (\d+)", re.IGNORECASE)

# O import de yt_dlp é pesado (~0.4s) e não é necessário para abrir a TUI.
# Carregamos sob demanda e guardamos o módulo para reuso/testes.
_yt_dlp_module: Any | None = None

#: Limites de rede do yt-dlp (§8.2/T06 · R2): sem eles, socket morto ou
#: resposta que estola deixam o worker preso por tempo indeterminado. Pior
#: caso de estol ≈ ``socket_timeout × (retries + 1)``. São decisão de código
#: (constantes documentadas), não configuração de usuário.
YDL_SOCKET_TIMEOUT = 30
YDL_RETRIES = 3
YDL_FRAGMENT_RETRIES = 3


def _snapshot_options() -> DownloadOptions:
    """Congela a configuração relevante para uma tarefa (A05).

    Chamado no enfileiramento e na retomada. A partir daí o worker lê só o
    snapshot — alterações na aba Config não mudam uma tarefa já em voo.
    """
    return DownloadOptions(
        audio_format=settings.download_format,
        quality=settings.download_quality,
        download_dir=settings.download_dir,
        embed_metadata=settings.embed_metadata,
    )


def _load_yt_dlp() -> Any:
    """Importa (uma única vez) e devolve o módulo yt_dlp."""
    global _yt_dlp_module
    if _yt_dlp_module is None:
        import yt_dlp

        _yt_dlp_module = yt_dlp
    return _yt_dlp_module


#: Marcas estáveis das mensagens de falha de EXTRAÇÃO do yt-dlp (T11-R4/Q-D).
#: O tipo ``ExtractorError`` é o sinal primário, mas o yt-dlp embrulha quase
#: tudo num ``DownloadError`` (conferido na versão instalada) — as frases
#: cobrem o erro embrulhado, incluindo o bot-check do YouTube.
_EXTRACTION_MARKERS = (
    "unable to extract",
    "no video formats",
    "unsupported url",
    "sign in to confirm",
)


def _extraction_error(error: Exception, yt_dlp: Any) -> ExtractionError | None:
    """Traduz falha de extração do yt-dlp para a taxonomia raiz (T11-R4/Q-D).

    A extração é a PRIMEIRA etapa (resolver a URL de watch): quando ela
    falha, nada foi baixado nem publicado (INV-1). Devolve o
    ``ExtractionError`` com o detalhe original preservado + a próxima ação,
    ou ``None`` quando não é extração — aí o chamador mantém o tratamento de
    hoje (mensagem crua de ``DownloadError``; ``Erro inesperado`` no resto).

    O player NÃO passa por aqui: a mensagem do mpv vai como veio —
    classificar texto de terceiro não é confiável (decisão do T11-R4).
    """
    utils = getattr(yt_dlp, "utils", None)
    extractor_error = getattr(utils, "ExtractorError", None)
    typed = isinstance(extractor_error, type) and isinstance(error, extractor_error)
    if not typed and not any(marker in str(error).lower() for marker in _EXTRACTION_MARKERS):
        return None

    detail = str(error).strip()
    if detail.upper().startswith("ERROR:"):
        detail = detail[len("ERROR:") :].strip()
    return ExtractionError(
        f"Falha de extração: {detail} — nada foi baixado nem publicado; "
        "atualize o yt-dlp e tente de novo"
    )


def _which_ffmpeg() -> str | None:
    """Localiza o ffmpeg no PATH (seam: a suíte não depende do host)."""
    return shutil.which("ffmpeg")


def _missing_dependency() -> str | None:
    """Mensagem da primeira dependência ausente, ou ``None`` (§8.4/T06).

    O yt-dlp é importado tarde (recurso do T01) e pode não estar instalado;
    o ffmpeg é exigido pelos post-processadores (``FFmpegExtractAudio`` e
    ``EmbedThumbnail``) e é procurado no PATH, como o próprio yt-dlp faz.
    A checagem roda ANTES de reservar: dependência ausente vira erro claro
    na tarefa — nunca crash no meio do download nem temporário órfão.
    """
    try:
        _load_yt_dlp()
    except ImportError as e:
        return f"yt-dlp não está instalado ({e}); instale com: pip install yt-dlp"
    if _which_ffmpeg() is None:
        return "ffmpeg não encontrado no PATH — necessário para converter/etiquetar o áudio"
    return None


class DownloadManager:
    """Gerencia downloads de músicas do YouTube Music."""

    def __init__(self, store: DownloadTaskStore | None = None) -> None:
        self._queue: list[DownloadTask] = []
        self._active_downloads: dict[str, DownloadTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=settings.download_concurrent)
        # Espelho persistido (T04-R1): injetável para testes; a instância
        # global usa o banco da biblioteca.
        self._store = store or DownloadTaskStore()

    def _persist(self, label: str, action: Callable[[], None]) -> None:
        """Executa uma gravação best-effort do espelho de estado (T04-R1).

        O estado persistido é uma **pista de recuperação**, não a fonte de
        verdade: falha de escrita (banco travado, disco cheio) nunca quebra
        o download nem fabrica sucesso falso — fica no log, nunca em
        silêncio. A inicialização seguinte trata a linha que sobrar.
        """
        try:
            action()
        except Exception:
            logger.warning("Falha ao persistir %s (download segue)", label, exc_info=True)

    def _persist_status(self, task: DownloadTask) -> None:
        """Persiste o status atual da tarefa (após uma transição da máquina)."""
        self._persist(
            f"status {task.status.value} de {task.id}",
            lambda: self._store.update_status(task.id, task.status),
        )

    def set_concurrent(self, workers: int) -> None:
        """Recria o executor com novo limite de downloads simultâneos (T06-R4).

        Entrada validada, nunca ajustada em silêncio: tipo errado (inclui
        ``bool``, que é ``int`` em Python) → ``TypeError``; fora de
        ``MIN_DOWNLOAD_CONCURRENT``–``MAX_DOWNLOAD_CONCURRENT`` → ``ValueError``
        — e nada é recriado. Quem normaliza para o usuário é quem chama (a aba
        Config já limita antes de salvar); a API interna prefere recusar a
        aceitar um limite diferente do pedido.

        Tarefas já enviadas ao executor antigo seguem nele (``wait=False``
        sem ``cancel_futures``); novas submissões usam o executor novo.
        """
        if isinstance(workers, bool) or not isinstance(workers, int):
            raise TypeError(f"workers deve ser int (recebido: {type(workers).__name__})")
        if not (MIN_DOWNLOAD_CONCURRENT <= workers <= MAX_DOWNLOAD_CONCURRENT):
            raise ValueError(
                f"workers fora da faixa {MIN_DOWNLOAD_CONCURRENT}–"
                f"{MAX_DOWNLOAD_CONCURRENT}: {workers}"
            )
        old = self._executor
        self._executor = ThreadPoolExecutor(max_workers=workers)
        old.shutdown(wait=False)

    def _options_for(self, task: DownloadTask) -> DownloadOptions:
        """Snapshot de configuração da tarefa (A05).

        Criado no enfileiramento; tarefas construídas diretamente (testes,
        chamadas internas) recebem um snapshot na primeira leitura, que já
        fica congelado para o resto da execução.
        """
        if task.options is None:
            task.options = _snapshot_options()
        return task.options

    def _get_output_template(
        self, task: DownloadTask, options: DownloadOptions | None = None
    ) -> str:
        """Template de saída do yt-dlp — normalização única do §8.1/T06."""
        snapshot = options or self._options_for(task)
        return build_output_template(
            download_dir=snapshot.download_dir,
            artist=task.artist,
            title=task.title,
            album=task.album,
        )

    def _get_ydl_options(
        self,
        task: DownloadTask,
        outtmpl: str | None = None,
        options: DownloadOptions | None = None,
    ) -> dict[str, Any]:
        """Opções do yt-dlp para download.

        Usa APENAS o snapshot ``options`` (A05), nunca o ``settings`` atual:
        mudar formato/qualidade/metadados com a tarefa em voo não altera o
        pós-processamento pela metade. ``outtmpl`` permite apontar a saída
        para o temporário da reserva (§5.1/T03); sem ele, usa o template
        final (compatibilidade).
        """
        snapshot = options or self._options_for(task)
        output_template = (
            outtmpl if outtmpl is not None else self._get_output_template(task, snapshot)
        )

        # Formato de áudio
        format_map = {
            "mp3": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
            "opus": "bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio",
            "m4a": "bestaudio[ext=m4a]/bestaudio",
            "flac": "bestaudio[ext=flac]/bestaudio",
        }
        audio_format = format_map.get(snapshot.audio_format, "bestaudio")

        opts: dict[str, Any] = {
            "format": audio_format,
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,  # sem isso, o progresso é impresso e corrompe a TUI
            "ignoreerrors": False,
            "extract_flat": False,
            # R2 (§8.2/T06): limites reais de rede — socket que estola não
            # prende o worker para sempre (pior caso ≈ timeout × retries).
            "socket_timeout": YDL_SOCKET_TIMEOUT,
            "retries": YDL_RETRIES,
            "fragment_retries": YDL_FRAGMENT_RETRIES,
            "writethumbnail": snapshot.embed_metadata,
            "postprocessors": [],
            "progress_hooks": [lambda d: self._progress_hook(task, d)],
            # post_hooks recebem o caminho FINAL (após os post-processadores,
            # ex.: .mp3 convertido) — progress_hooks só têm o arquivo baixado.
            "post_hooks": [lambda path: self._capture_final_path(task, path)],
        }

        # Post-processadores para conversão + metadados/capa
        if snapshot.audio_format == "mp3":
            opts["postprocessors"].append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": snapshot.quality,
                }
            )
        elif snapshot.audio_format == "opus":
            opts["postprocessors"].append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "opus",
                    "preferredquality": snapshot.quality,
                }
            )
        elif snapshot.audio_format == "m4a":
            opts["postprocessors"].append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                }
            )
        elif snapshot.audio_format == "flac":
            opts["postprocessors"].append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "flac",
                }
            )

        if snapshot.embed_metadata:
            # O EmbedThumbnail remove o .webp temporário ao terminar (quando
            # "already_have_thumbnail" não é passado) e o FFmpegMetadata grava
            # as tags — ambos válidos para qualquer formato de saída.
            opts["postprocessors"].append(
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                }
            )
            opts["postprocessors"].append(
                {
                    "key": "EmbedThumbnail",
                }
            )

        return opts

    def _capture_final_path(self, task: DownloadTask, path: str) -> None:
        """Guarda o caminho final informado pelo post_hook do yt-dlp."""
        task.file_path = Path(path)

    def _progress_hook(self, task: DownloadTask, data: dict[str, Any]) -> None:
        """Hook de progresso do yt-dlp (roda na thread do executor)."""
        if task.abort_event.is_set():
            # Qualquer exceção em um progress hook aborta o download.
            # É o ponto de observação do cancelamento (T02-R3).
            raise RuntimeError("Download cancelado")

        status = data.get("status")
        if status == "downloading":
            # O estado RUNNING é definido pelo worker antes de chamar o
            # yt-dlp; aqui só atualizamos progresso/velocidade/ETA (T02-R6:
            # a UI lê estado, nunca escreve).
            percent = str(data.get("_percent_str", "0%"))
            try:
                task.progress = float(percent.replace("%", "").strip())
            except ValueError:
                task.progress = 0.0
            task.speed = data.get("_speed_str", "") or ""
            task.eta = data.get("_eta_str", "") or ""
        elif status == "finished":
            # Arquivo baixado; o pós-processamento (conversão, tags) ainda
            # está por vir — o status final é definido em _download_task.
            task.progress = 100.0

    def _final_path_for(self, task: DownloadTask, options: DownloadOptions | None = None) -> Path:
        """Caminho final publicado — mesma composição do template (§8.1)."""
        snapshot = options or self._options_for(task)
        return final_path_for(
            download_dir=snapshot.download_dir,
            artist=task.artist,
            title=task.title,
            album=task.album,
            extension=snapshot.audio_format,
        )

    def _configured_bitrate(self, options: DownloadOptions) -> int:
        """Bitrate do snapshot (fallback 320 quando não numérico)."""
        return int(options.quality) if options.quality.isdigit() else 320

    def _register_track_file(
        self,
        task: DownloadTask,
        final_path: Path,
        *,
        bitrate: int,
        options: DownloadOptions | None = None,
    ) -> None:
        """Registra o arquivo publicado na biblioteca (passo 7 do §5.1).

        ``bitrate=0`` no caminho de import (Q2(a)/reconciliação): o valor é
        desconhecido sem reanalisar o arquivo, mas ``update_track_file``
        exige o argumento — zero fica documentado como "desconhecido".
        ``options.audio_format`` vem do snapshot (A05), não da config atual.
        """
        snapshot = options or self._options_for(task)
        track_id = task.track_id
        if not track_id:
            track_id = (
                library.ensure_track(
                    source_id=task.video_id,
                    title=task.title,
                    artist=task.artist,
                    album=task.album,
                    thumbnail_url=task.thumbnail_url,
                    # A ordem viajou com a tarefa (T17.1/R2): retomada
                    # tardia ainda grava a posição quando o download veio
                    # de um álbum.
                    track_number=task.track_number,
                    disc_number=task.disc_number,
                    album_track_count=task.album_track_count,
                )
                or 0
            )
            task.track_id = track_id
        if not track_id:
            raise RuntimeError("não foi possível criar a faixa na biblioteca")
        library.update_track_file(
            track_id,
            final_path,
            final_path.stat().st_size,
            snapshot.audio_format,
            bitrate,
        )

    def _adopt_existing_file(
        self,
        task: DownloadTask,
        final_path: Path,
        options: DownloadOptions | None = None,
    ) -> DownloadTask:
        """Política Q2(a): arquivo já existe → não baixa; valida e registra.

        Nunca sobrescreve: o existente precisa passar na validação (§5.1,
        passo 5) para ser registrado; inválido → ``FAILED`` com mensagem
        clara. ``COMPLETED`` só com arquivo válido **e** registro feito.
        """
        snapshot = options or self._options_for(task)
        try:
            validate_media_file(final_path, snapshot.audio_format)
        except MediaValidationError as e:
            logger.warning("Arquivo existente inválido em %s: %s", final_path, e)
            self._set_failed(
                task, f"Arquivo existente inválido: {e}", error_code="INVALID_EXISTING"
            )
            return task

        task.file_path = final_path
        task.progress = 100.0
        try:
            self._register_track_file(task, final_path, bitrate=0, options=snapshot)
        except Exception as e:
            logger.exception("Falha ao registrar arquivo existente de %s na biblioteca", task.id)
            self._set_failed(
                task,
                f"Falha ao registrar na biblioteca: {e}",
                error_code="LIBRARY_REGISTER_FAILED",
            )
            return task

        if not task.try_transition(DownloadStatus.COMPLETED):
            return task
        # T17.1/R8: o desfecho fica registrado na tarefa — a aba Downloads
        # mostra "Já existia" em vez de fingir que baixou.
        task.outcome = DownloadOutcome.EXISTING_ADOPTED
        log_event("download", "download_finished", "Download concluído", operation_id=task.id)
        return task

    @staticmethod
    def _set_failed(task: DownloadTask, error: str, *, error_code: str | None = None) -> None:
        """Marca FAILED se a máquina permitir; nunca sobrescreve terminal.

        §8.3/T06: este é o ponto ÚNICO em que ``task.error`` é escrito com
        texto de terceiro (yt-dlp/OS) — a mensagem é mascarada aqui, então a
        tabela de downloads, o toast de falha e o espelho nunca veem caminho
        do HOME nem URL com token. O log recebe o texto cru; o formatter o
        mascara (§13.2/T13).

        T13 §13.1: emite ``download_failed`` com ``operation_id = task.id`` e
        ``error_code`` do chamador; código de saída do ffmpeg presente no
        texto prevalece (extração/conversão), ex.: ``FFMPEG_EXIT_1``.

        Em corrida com cancelamento/shutdown (que fecham a tarefa antes), a
        transição já não é válida e o estado final existente é preservado.
        """
        masked = mask_diagnostics(error)
        if not task.try_transition(DownloadStatus.FAILED):
            return
        task.error = masked
        ffmpeg_exit = _FFMPEG_EXIT_RE.search(error)
        if ffmpeg_exit is not None:
            error_code = f"FFMPEG_EXIT_{ffmpeg_exit.group(1)}"
        log_event(
            "download",
            "download_failed",
            f"Download falhou: {error}",
            operation_id=task.id,
            error_code=error_code,
            level=logging.ERROR,
        )

    @staticmethod
    def _finish_cancelled(task: DownloadTask) -> None:
        """Fecha como CANCELLED se a máquina permitir (não sobrescreve terminal)."""
        if task.try_transition(DownloadStatus.CANCELLED):
            task.error = "Cancelado"

    def _download_task(self, task: DownloadTask) -> DownloadTask:
        """Executa o download em thread separada, pela ordem do §5.1 (T03).

        ``COMPLETED`` só depois de publicar (6) e registrar (7); qualquer
        falha vira ``FAILED`` — nunca sucesso falso (INV-1). As transições
        usam ``try_transition`` (A15) e a configuração vem do snapshot
        ``task.options`` (A05), nunca do ``settings`` mutável.
        """
        options = self._options_for(task)
        # Cancelada na fila ou interrompida pelo shutdown antes de começar:
        # o estado final já existe — o worker apenas encerra.
        if not task.try_transition(DownloadStatus.RUNNING):
            return task
        self._persist_status(task)
        log_event("download", "download_started", "Download iniciado", operation_id=task.id)

        download_dir = options.download_dir
        # A05: o caminho persistido no enfileiramento é a fonte de verdade;
        # só é recalculado quando a tarefa não o tem (chamada interna/teste).
        final_path = task.final_path or self._final_path_for(task, options)
        task.final_path = final_path
        try:
            validate_input(
                video_id=task.video_id,
                audio_format=options.audio_format,
                final_path=final_path,
                download_dir=download_dir,
            )
        except InvalidInputError as e:
            logger.warning("Entrada inválida para %s: %s", task.id, e)
            self._set_failed(task, f"Entrada inválida: {e}", error_code="INVALID_INPUT")
            return task

        # Q2(a): destino já existe → não baixa; valida e registra o existente.
        if final_path.exists():
            if not task.try_transition(DownloadStatus.VALIDATING):
                return task
            self._persist_status(task)
            return self._adopt_existing_file(task, final_path, options)

        # §8.4/T06: dependências ausentes (yt-dlp/ffmpeg) → erro claro ANTES
        # de reservar; nada de crash no meio nem temporário órfão.
        missing = _missing_dependency()
        if missing is not None:
            logger.warning("Dependência ausente para %s: %s", task.id, missing)
            self._set_failed(task, missing, error_code="DEPENDENCY_MISSING")
            return task

        try:
            reservation = reserve(final_path, download_dir)
        except DestinationBusy as e:
            logger.info("Destino já reservado por outra tarefa (%s): %s", task.id, e)
            self._set_failed(
                task,
                "Outra tarefa já está baixando para este arquivo",
                error_code="DESTINATION_BUSY",
            )
            return task
        except ConfigError as e:
            # §8.3/T06: download_dir indisponível/pasta de trabalho recusada
            # (permissão etc.) → erro claro na tarefa, nunca crash no worker.
            logger.warning("Não foi possível preparar o trabalho de %s: %s", task.id, e)
            self._set_failed(
                task,
                f"Não foi possível preparar o diretório de trabalho: {e}",
                error_code="WORKDIR_UNAVAILABLE",
            )
            return task

        try:
            yt_dlp = _load_yt_dlp()
            url = WATCH_URL.format(video_id=task.video_id)

            try:
                with yt_dlp.YoutubeDL(
                    self._get_ydl_options(task, reservation.temp_template, options)
                ) as ydl:
                    ydl.download([url])
            except Exception as e:
                if task.abort_event.is_set():
                    # Cancelamento (usuário) ou encerramento (shutdown): quem
                    # sinalizou marca o estado; aqui só fechamos se ninguém fechou.
                    self._finish_cancelled(task)
                elif (extraction := _extraction_error(e, yt_dlp)) is not None:
                    # T11-R4/Q-D: falha de extração ganha o rótulo da raiz
                    # (ExtractionError) com a próxima ação; o log recebe o
                    # detalhe cru (mascarado pelo formatter desde o T13).
                    logger.warning("Extração falhou para %s: %s", task.id, e)
                    self._set_failed(task, str(extraction), error_code="EXTRACTION_FAILED")
                elif isinstance(e, yt_dlp.utils.DownloadError):
                    self._set_failed(task, str(e), error_code="DOWNLOAD_ERROR")
                else:
                    logger.exception(
                        "Erro inesperado no download %s (vídeo %s)", task.id, task.video_id
                    )
                    self._set_failed(task, f"Erro inesperado: {e}", error_code="UNEXPECTED")
                return task

            # Corrida conhecida: cancelamento/encerramento entre o fim do
            # yt-dlp e a validação já definiu o estado final — não sobrescrever.
            if not task.try_transition(DownloadStatus.VALIDATING):
                return task
            self._persist_status(task)

            try:
                temp = validate_media_file(task.file_path, options.audio_format)
                # §8.1/T06: revalida a contenção imediatamente antes de
                # publicar — um diretório trocado por symlink DURANTE o
                # download não publica nada fora de download_dir.
                resolve_within(final_path, download_dir)
                publish_file(temp, final_path)
            except (PipelineError, OSError) as e:
                logger.warning("Publicação falhou para %s: %s", task.id, e)
                self._set_failed(task, f"Falha na publicação: {e}", error_code="PUBLISH_FAILED")
                return task
            except Exception as e:
                logger.exception("Erro inesperado na publicação de %s", task.id)
                self._set_failed(
                    task, f"Erro inesperado na publicação: {e}", error_code="UNEXPECTED"
                )
                return task

            # Publicado: o ``file_path`` da tarefa passa a ser o destino final.
            task.file_path = final_path

            try:
                self._register_track_file(
                    task,
                    final_path,
                    bitrate=self._configured_bitrate(options),
                    options=options,
                )
            except Exception as e:
                # Arquivo publicado sem registro (risco §1.3): falha explícita,
                # nunca COMPLETED; a reconciliação (§5.3/T03) importa o órfão.
                logger.exception("Falha ao registrar %s na biblioteca", task.id)
                self._set_failed(
                    task,
                    f"Falha ao registrar na biblioteca: {e}",
                    error_code="LIBRARY_REGISTER_FAILED",
                )
                return task

            if not task.try_transition(DownloadStatus.COMPLETED):
                return task
            log_event("download", "download_finished", "Download concluído", operation_id=task.id)
            return task
        finally:
            release(reservation)

    async def download(self, task: DownloadTask) -> DownloadTask:
        """Inicia download assíncrono."""
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, self._download_task, task)
        task._future = future
        self._active_downloads[task.video_id] = task

        try:
            return await future
        except asyncio.CancelledError:
            # O cancelamento da corrotina é o gatilho real de "encerramento no
            # meio do trabalho" (T12-R4): o Textual cancela os workers ao sair
            # e só depois dispara o Unmount — o ``shutdown`` do on_unmount
            # chega tarde e veria ``_active_downloads`` vazio. Marcar aqui
            # (INTERRUPTED + abort + linha persistida) restaura o contrato do
            # T02-R4 em qualquer ordem e acorda o thread do executor.
            self._mark_interrupted(task)
            raise
        finally:
            self._active_downloads.pop(task.video_id, None)
            # Terminais da sessão não deixam linha: o resultado vive na
            # memória/UI; a linha só existe para a próxima execução saber que
            # o trabalho ficou pela metade (INTERRUPTED — essa fica).
            if task.status in (
                DownloadStatus.COMPLETED,
                DownloadStatus.FAILED,
                DownloadStatus.CANCELLED,
            ):
                self._persist(f"remoção da linha de {task.id}", lambda: self._store.delete(task.id))

    def queue_download(
        self,
        video_id: str,
        title: str = "",
        artist: str = "",
        album: str = "",
        thumbnail_url: str = "",
        track_id: int = 0,
        track_number: int | None = None,
        disc_number: int | None = None,
        album_track_count: int | None = None,
    ) -> DownloadTask:
        """Adiciona à fila de downloads com um snapshot da configuração (A05).

        O formato, a qualidade, o diretório e ``embed_metadata`` são
        congelados aqui: o worker usa exatamente estes valores, mesmo que a
        aba Config mude antes de a tarefa terminar. O caminho final também é
        calculado agora e persistido (não é recalculado no worker).
        """
        options = _snapshot_options()
        task = DownloadTask(
            video_id=video_id,
            title=title,
            artist=artist,
            album=album,
            thumbnail_url=thumbnail_url,
            track_id=track_id,
            track_number=track_number,
            disc_number=disc_number,
            album_track_count=album_track_count,
            options=options,
        )
        final_path = self._final_path_for(task, options)
        task.final_path = final_path
        self._queue.append(task)
        # §8.1/T06: destino que resolve fora de download_dir (symlink no
        # caminho, por exemplo) NÃO é persistido — nada há para recuperar, e
        # a validação de entrada do worker reprova a tarefa com a mesma
        # causa (o erro aparece na fila; nunca se grava uma linha armadilha).
        try:
            resolve_within(final_path, options.download_dir)
        except PathEscapeError as e:
            logger.warning("Destino fora de download_dir para %s (%s)", task.id, e)
            return task
        # Persistir a intenção de baixar ANTES de começar (T04-R1): se o app
        # morrer entre enfileirar e terminar, a linha vira a pista da próxima
        # execução. O caminho final é derivado agora (o template não muda).
        self._persist(
            f"tarefa na fila ({task.id})",
            lambda: self._store.save(
                task,
                download_dir=options.download_dir,
                final_path=final_path,
            ),
        )
        return task

    def cancel_download(self, video_id: str) -> bool:
        """Cancela um download pendente ou em execução (§4.2).

        Contrato: só ``PENDING``/``RUNNING`` são canceláveis — em
        ``VALIDATING`` a janela é curta e cancelar naquele ponto deixaria o
        arquivo publicado sem registro (INV-2); estados terminais não têm o
        que cancelar. Nesses casos retorna ``False`` sem alterar nada.

        O ``abort_event`` é observado pelo yt-dlp **no próximo
        ``progress_hook``** (T02-R3): a latência máxima até o processo parar
        de fato depende do intervalo entre chunks do yt-dlp (sem limite
        formal garantido por ele; o teste com fake determinístico fixa o
        contrato em ~1 tick).
        """
        task = self._active_downloads.get(video_id)
        if task is None:
            for queued in self._queue:
                if queued.video_id == video_id:
                    task = queued
                    break
        if task is None or not task.try_transition(DownloadStatus.CANCELLED):
            return False

        task.abort_event.set()
        task.error = "Cancelado"
        if task in self._queue and task not in self._active_downloads.values():
            self._queue.remove(task)
        # Terminal: a linha persistida some (se o worker ainda estiver em
        # voo, o `finally` de `download()` repete a remoção — idempotente).
        self._persist(f"remoção da linha de {task.id}", lambda: self._store.delete(task.id))
        return True

    def get_queue(self) -> list[DownloadTask]:
        """Retorna fila de downloads (ativos + concluídos da sessão)."""
        return self._queue.copy()

    def get_active_downloads(self) -> list[DownloadTask]:
        """Retorna downloads ativos."""
        return list(self._active_downloads.values())

    def is_active_or_pending(
        self,
        video_id: str,
        *,
        title: str = "",
        artist: str = "",
        album: str = "",
    ) -> bool:
        """True se o vídeo já está em voo — ou colidiria no arquivo final.

        Evita enfileirar o mesmo vídeo duas vezes: dois downloads simultâneos
        disputariam o mesmo arquivo de saída (e o mesmo ``.part``). Com
        título/artista/álbum informados, bloqueia também **colisões de
        caminho final** (faixas diferentes com o mesmo nome sanitizado —
        T02-R5); a reserva definitiva de caminho entra com o pipeline de T03.
        """
        if video_id in self._active_downloads:
            return True
        if any(t.video_id == video_id and t.status in ACTIVE_STATUSES for t in self._queue):
            return True

        if not (title or artist or album):
            return False
        candidate_options = _snapshot_options()
        candidate = DownloadTask(
            video_id=video_id,
            title=title,
            artist=artist,
            album=album,
            options=candidate_options,
        )
        template = self._get_output_template(candidate, candidate_options)
        return any(
            t.status in ACTIVE_STATUSES
            and self._get_output_template(t, self._options_for(t)) == template
            for t in self._queue
        )

    def clear_completed(self) -> None:
        """Remove da lista as tarefas em estado final (§4.2).

        Inclui ``INTERRUPTED``. Nenhum arquivo é tocado aqui: limpeza de
        ``.part``/temporários exige política própria e é T04-R2 (§5.3) —
        este método só encolhe a fila em memória.
        """
        self._queue = [t for t in self._queue if not t.is_terminal]

    # ===== Recuperação de tarefas interrompidas (T04-R2/R3) =====

    def recover_interrupted(self) -> int:
        """Recupera tarefas deixadas por uma execução anterior (T04-R2).

        Chamado na inicialização, depois da migração do esquema (T05-R1):
        converte as linhas em voo em ``INTERRUPTED`` (a partir daí a decisão
        é do usuário) e **hidrata** cada linha de volta em um ``DownloadTask``
        na fila, para a UI listar e oferecer retomar (r) / descartar (d).

        Hidratação, não transição: a máquina da T02 governa transições *em
        voo*; a linha persistida é o passado da tarefa — reconstruí-la já no
        estado ``INTERRUPTED`` (construtor) é a forma de não inventar uma
        transição que não existe (PENDING→INTERRUPTED, por exemplo, não está
        na §4.2 e não precisa estar: nada avançou desde o encerramento).
        Devolve quantas tarefas foram recuperadas; falha de leitura fica no
        log e não impede o app de abrir.
        """
        try:
            self._store.mark_active_as_interrupted()
            rows = self._store.load_interrupted()
        except Exception:
            logger.warning(
                "Falha ao recuperar tarefas interrompidas (downloads novos seguem)",
                exc_info=True,
            )
            return 0

        known = {task.id for task in self._queue}
        recovered: list[DownloadTask] = []
        for row in rows:
            if row.task_id in known:
                continue
            recovered.append(hydrate_interrupted(row))
        self._queue.extend(recovered)
        # §13.1/T13: contagem de retomadas — com 0, o registro diz
        # explicitamente que nada ficou pela metade.
        log_event(
            "download",
            "interrupted_recovered",
            f"Recuperação: {len(recovered)} download(s) interrompido(s) aguardando decisão (r/d)",
        )
        return len(recovered)

    def _interrupted_in_queue(self, task_id: str) -> DownloadTask | None:
        """Tarefa ``INTERRUPTED`` na fila em memória — única fonte de r/d.

        Se a linha existe no banco mas a tarefa não está na fila, algo saiu
        do previsto: r/d não inventam estado; a inicialização seguinte
        reoferece a decisão.
        """
        for task in self._queue:
            if task.id == task_id and task.status is DownloadStatus.INTERRUPTED:
                return task
        return None

    def _task_files(self, task: DownloadTask) -> tuple[Path, Path]:
        """``(temp_dir, final_path)`` da tarefa, preferindo o persistido.

        O pai do final **não** é o ``download_dir`` (template gera
        ``download_dir/Artista[/Álbum]/…``): por isso a linha guarda os dois
        caminhos. Sem a linha (falha de escrita do espelho), cai para o
        template atual — que é determinístico em (título, artista, álbum,
        formato).

        §8.1/T06: os caminhos da linha só são usados se resolverem DENTRO do
        ``download_dir`` corrente — linha adulterada (ou download_dir antigo)
        vira log + fallback, nunca operação de disco fora da árvore de
        downloads (retomar/descartar não podem apagar arquivo de terceiros).
        """
        download_dir = settings.download_dir
        try:
            row = self._store.find(task.id)
        except Exception:
            logger.warning("Falha ao ler a linha persistida de %s", task.id, exc_info=True)
            row = None
        if row is not None and row.final_path and row.download_dir:
            persisted_temp = Path(row.download_dir) / TMP_DIR_NAME
            persisted_final = Path(row.final_path)
            try:
                resolve_within(persisted_temp, download_dir)
                resolve_within(persisted_final, download_dir)
            except PathEscapeError as e:
                logger.warning(
                    "Linha persistida de %s fora de download_dir (%s); usando o template atual",
                    task.id,
                    e,
                )
            else:
                return persisted_temp, persisted_final
        return download_dir / TMP_DIR_NAME, self._final_path_for(task)

    def retry_interrupted(self, task_id: str) -> DownloadTask | None:
        """Cria a nova tentativa de uma tarefa INTERRUPTED (T04-R2/R3).

        Retomar **nunca** reaproveita o ``.part``: a consistência de um
        parcial do yt-dlp/ffmpeg não é verificável de forma barata, então a
        retomada é uma **nova tarefa** (``retry_of``) e os temporários da
        tentativa antiga são descartados com log — inclusive o lock
        ``<chave>.reserva``, que órfão bloquearia a nova reserva. O arquivo
        final nunca é tocado. Devolve a nova tarefa, ou ``None`` quando o id
        não corresponde a uma INTERRUPTED na fila.
        """
        task = self._interrupted_in_queue(task_id)
        if task is None:
            return None

        temp_dir, final_path = self._task_files(task)
        discarded = discard_task_files(temp_dir=temp_dir, final_path=final_path)

        new_task = task.make_retry()
        # A05: a retomada usa a configuração ATUAL (não a do enfileiramento
        # antigo) e um caminho final novo.
        retry_options = _snapshot_options()
        new_task.options = retry_options
        new_final_path = self._final_path_for(new_task, retry_options)
        new_task.final_path = new_final_path
        # A entrada antiga sai da fila em memória: sem isso a INTERRUPTED
        # ficaria listada junto com a nova PENDING do mesmo vídeo (T04-R2).
        self._queue.remove(task)
        self._queue.append(new_task)
        # Persistir a nova ANTES de remover a antiga: um crash no meio deixa
        # as duas linhas (recuperável) em vez de nenhuma (trabalho perdido).
        self._persist(
            f"nova tentativa de {task.id}",
            lambda: self._store.save(
                new_task,
                download_dir=retry_options.download_dir,
                final_path=new_final_path,
            ),
        )
        self._persist(f"remoção da linha antiga de {task.id}", lambda: self._store.delete(task.id))
        logger.info(
            "Retomando download %s como %s (%d temporário(s) antigo(s) descartado(s))",
            task.id,
            new_task.id,
            len(discarded),
        )
        return new_task

    def retry_failed(self, task_id: str) -> DownloadTask | None:
        """Cria nova tentativa de uma tarefa FAILED desta sessão (A09).

        Falhas só vivem na fila em memória (a linha persistida some no fim);
        a nova tarefa usa a configuração ATUAL (snapshot novo), como a
        retomada de INTERRUPTED. Nenhum arquivo final é apagado; temporários
        do yt-dlp são reaproveitados/sobrescritos pela nova reserva.
        """
        task = next(
            (
                item
                for item in self._queue
                if item.id == task_id and item.status is DownloadStatus.FAILED
            ),
            None,
        )
        if task is None:
            return None

        new_task = task.make_retry()
        options = _snapshot_options()
        new_task.options = options
        new_final_path = self._final_path_for(new_task, options)
        new_task.final_path = new_final_path
        # A FAILED antiga sai da fila: a tabela mostra só a nova tentativa.
        self._queue.remove(task)
        self._queue.append(new_task)
        self._persist(
            f"nova tentativa de {task.id}",
            lambda: self._store.save(
                new_task,
                download_dir=options.download_dir,
                final_path=new_final_path,
            ),
        )
        self._persist(f"remoção da linha antiga de {task.id}", lambda: self._store.delete(task.id))
        logger.info("Retentando download %s como %s", task.id, new_task.id)
        return new_task

    def discard_interrupted(self, task_id: str) -> list[Path] | None:
        """Descarta uma tarefa INTERRUPTED: limpa os temporários próprios e a linha.

        Devolve os caminhos removidos do disco (a UI mostra a contagem) ou
        ``None`` quando o id não corresponde a uma INTERRUPTED na fila. O
        arquivo final, se existir, **nunca** é removido (§5.3) — só
        temporários e locks da própria tarefa, com log.
        """
        task = self._interrupted_in_queue(task_id)
        if task is None:
            return None

        temp_dir, final_path = self._task_files(task)
        discarded = discard_task_files(temp_dir=temp_dir, final_path=final_path)
        self._queue.remove(task)
        self._persist(f"descarte de {task.id}", lambda: self._store.delete(task.id))
        logger.info(
            "Tarefa interrompida descartada: %s (%d temporário(s) removido(s))",
            task.id,
            len(discarded),
        )
        return discarded

    def _mark_interrupted(self, task: DownloadTask) -> None:
        """Fim no meio do trabalho: INTERRUPTED + abort + linha persistida.

        Idempotente (T02-R4): tarefa já terminal não é tocada — corridas com
        ``shutdown``/cancelamento preservam o estado final existente. Usado
        pelo ``shutdown`` e pelo cancelamento da corrotina de ``download``
        (T12-R4, ordem real do Textual ao fechar o app).
        """
        if task.is_terminal:
            return
        task.abort_event.set()
        if task.can_transition_to(DownloadStatus.INTERRUPTED):
            task.transition_to(DownloadStatus.INTERRUPTED)
            # A linha fica: é ela que a próxima execução vai listar como
            # "aguardando decisão" (T04-R2) — nunca limpar aqui.
            self._persist_status(task)

    def shutdown(self) -> None:
        """Encerra o gerenciador sem bloquear a saída do app (T02-R4).

        Tarefas em voo viram ``INTERRUPTED`` (o gatilho da §4.2): o worker
        coopera com o ``abort_event`` e sai sem sobrescrever o estado. O
        executor não é aguardado — o teste com fake cooperativo verifica que
        nenhuma thread continua viva (a latência de saída é a mesma do
        cancelamento: o próximo ``progress_hook``).
        """
        for task in self._active_downloads.values():
            self._mark_interrupted(task)
        self._executor.shutdown(wait=False, cancel_futures=True)


# Instância global
download_manager = DownloadManager()
