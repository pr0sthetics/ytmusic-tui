"""Ações de download na TUI (mixin de ``MainScreen``)."""

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.widgets import DataTable, Static
from textual.worker import Worker

from ytmusic_tui.api.models import AlbumDetail
from ytmusic_tui.diagnostics import mask_diagnostics
from ytmusic_tui.download import (
    DownloadOutcome,
    DownloadStatus,
    DownloadTask,
    download_manager,
)
from ytmusic_tui.library import library
from ytmusic_tui.log import log_event
from ytmusic_tui.player import TrackInfo, player
from ytmusic_tui.tui.dialogs import ConfirmScreen, ErrorDetailScreen
from ytmusic_tui.tui.guards import guard_write
from ytmusic_tui.tui.messages import (
    ALBUM_NO_TRACKS,
    ALBUM_REQUIRED,
    CANCEL_FAILED,
    CANCEL_REQUIRES_ACTIVE,
    CONFIRM_DISCARD_CANCELLED,
    DISCARD_FAILED,
    DISCARD_REQUIRES_INTERRUPTED,
    DOWNLOAD_EXISTING_ADOPTED,
    NO_DOWNLOAD_ERROR,
    NO_TRACK_PLAYING,
    RETRY_FAILED,
    RETRY_REQUIRES_INTERRUPTED_OR_FAILED,
    album_already_running,
    album_queue_summary,
    album_queued,
    album_skipped,
    already_queued,
    confirm_discard_message,
    discarded_download,
    download_cancelled,
    download_completed,
    download_error_detail,
    download_existing_adopted,
    download_failed,
    download_starting,
    downloads_cleared,
    interrupted_downloads,
    retrying_download,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class DownloadController:
    """Mixin da aba 📥 Downloads e dos atalhos Ctrl+D / A.

    A classe host (``MainScreen``) oferece ``_toast`` e mantém
    ``_current_album`` (usado pelo "a"); ``_track_info_from_song``
    (PlaybackController) e ``refresh_library`` (LibraryController) também
    vêm dos mixins do mesmo host.
    """

    #: Colunas da tabela, na ordem, com chave estável (A01): criadas UMA vez.
    #: A chave explícita é o que permite atualizar a célula no lugar e
    #: restaurar a seleção sem reconstruir a tabela a cada 0,5 s.
    _DOWNLOAD_COLUMNS: tuple[tuple[str, str], ...] = (
        ("Status", "status"),
        ("Desfecho", "outcome"),
        ("Título", "title"),
        ("Artista", "artist"),
        ("Progresso", "progress"),
        ("Velocidade", "speed"),
        ("ETA", "eta"),
        ("Erro", "error"),
    )

    # Estado e membros fornecidos pela tela host (o mypy analisa cada mixin
    # isoladamente; as declarações abaixo existem só na checagem de tipos).
    _current_album: AlbumDetail | None
    if TYPE_CHECKING:
        app: Any

        def query_one(self, selector: Any, expect_type: Any = None) -> Any: ...
        def run_worker(self, *args: Any, **kwargs: Any) -> Any: ...
        def _toast(self, message: str) -> None: ...
        def refresh_library(self) -> Worker[None]: ...

        _track_info_from_song: Callable[..., Any]

    # ===== Tabela de downloads =====

    def _download_rows(self) -> list[DownloadTask]:
        """Linhas da tabela: fila + ativas, sem duplicatas (T04-R2).

        A fila em memória ainda contém a tarefa RUNNING (ela só sai em
        cancel/clear): sem o dedup por ``id``, cada download ativo apareceria
        duas vezes — e o mapeamento linha ↔ tarefa de retomar/descartar
        ficaria ambíguo.
        """
        rows: list[DownloadTask] = []
        seen: set[str] = set()
        for task in download_manager.get_queue() + download_manager.get_active_downloads():
            if task.id in seen:
                continue
            seen.add(task.id)
            rows.append(task)
        return rows

    def update_downloads_table(self) -> None:
        """Atualiza tabela de downloads preservando seleção e colunas (A01).

        Antes, cada atualização (timer de 0,5 s) chamava ``clear(columns=True)``:
        o Textual devolvia o cursor para ``(0, 0)`` e as colunas eram
        recriadas — o usuário perdia a linha selecionada e ``r``/``d`` podiam
        agir na tarefa errada. Agora as colunas nascem uma vez, as linhas usam
        ``key=task.id`` e a seleção é restaurada pela chave (fallback: posição
        anterior, limitada ao novo total). Se a lista não mudou, só as células
        são atualizadas no lugar — sem flicker.

        T12-R3 (inventário): fila/ativos são listas **em memória** (cópias,
        sem I/O); o custo é montar/atualizar as linhas do ``DataTable`` —
        pequeno e limitado. Segue no loop de propósito (timer de 0,5 s +
        eventos).
        """
        try:
            table = self.query_one("#downloads-table", DataTable)
            message = self.query_one("#downloads-message", Static)
        except Exception:
            # A aba de downloads não está montada no momento.
            return
        rows = self._download_rows()
        # §12.2 (T12): fila vazia é um estado visível — a tabela sem linhas
        # some e a dica de como começar aparece no lugar; com linhas, some a
        # dica e volta a tabela.
        table.display = bool(rows)
        message.display = not rows

        selected_id = self._selected_download_id(table)
        previous_index = table.cursor_row

        # A01: colunas criadas uma única vez (não no caminho periódico).
        if not table.columns:
            table.add_columns(*self._DOWNLOAD_COLUMNS)

        wanted = [task.id for task in rows]
        current = [row_key.value or "" for row_key in table.rows]
        if wanted != current:
            # Só reconstrói quando a lista mudou (inclusão/remoção/ordem).
            # ``clear()`` sem ``columns=True`` mantém as colunas e as chaves.
            table.clear()
            for task in rows:
                table.add_row(*self._download_cells(task), key=task.id)
        else:
            # Mesma lista: atualiza as células no lugar (progresso/status).
            for task in rows:
                for (_, column_key), value in zip(
                    self._DOWNLOAD_COLUMNS, self._download_cells(task), strict=True
                ):
                    table.update_cell(task.id, column_key, value)

        if rows:
            index = min(previous_index, len(rows) - 1)
            if selected_id is not None:
                # A linha selecionada pode ter sumido: cai para o vizinho.
                with contextlib.suppress(Exception):
                    index = table.get_row_index(selected_id)
            if table.cursor_row != index:
                table.move_cursor(row=index, scroll=False)

    @staticmethod
    def _download_cells(task: DownloadTask) -> tuple[str, ...]:
        """Células de uma linha da tabela (ordem = ``_DOWNLOAD_COLUMNS``)."""
        status_icon = {
            DownloadStatus.PENDING: "⏳",
            DownloadStatus.RUNNING: "⬇️",
            DownloadStatus.VALIDATING: "🔎",
            DownloadStatus.COMPLETED: "✅",
            DownloadStatus.FAILED: "❌",
            DownloadStatus.CANCELLED: "🚫",
            DownloadStatus.INTERRUPTED: "⏸️",
        }.get(task.status, "?")
        # Q2(a)/R8: o desfecho qualificado tem coluna própria — o que
        # veio do disco (arquivo já existente) não se confunde com o
        # que foi baixado agora.
        outcome = (
            DOWNLOAD_EXISTING_ADOPTED if task.outcome is DownloadOutcome.EXISTING_ADOPTED else ""
        )
        return (
            status_icon,
            outcome,
            task.title[:40],
            task.artist[:30],
            f"{task.progress:.1f}%" if task.progress else "—",
            task.speed,
            task.eta,
            task.error[:40],
        )

    def _selected_download_id(self, table: DataTable) -> str | None:
        """Chave (``task.id``) da linha sob o cursor, se houver (A01)."""
        if not table.rows:
            return None
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        return cell_key.row_key.value

    def _selected_download_task(self) -> DownloadTask | None:
        """Tarefa da linha selecionada na tabela de downloads (ou ``None``).

        A01: usa a **chave** da linha, não o índice — a mesma linha continua
        sendo o alvo de ``r``/``d`` mesmo se a tabela for reconstruída.
        """
        try:
            table = self.query_one("#downloads-table", DataTable)
        except Exception:
            return None
        selected_id = self._selected_download_id(table)
        if selected_id is None:
            return None
        for task in self._download_rows():
            if task.id == selected_id:
                return task
        return None

    # ===== Ações =====

    def action_download(self) -> None:
        """Baixa faixa atual."""
        # Sem guard aqui: `download_current_track` já valida e avisa o usuário
        # ("Nenhuma faixa em reprodução") — antes o Ctrl+D era ignorado em
        # silêncio quando não havia faixa tocando.
        self.download_current_track()

    def action_download_album(self) -> None:
        """Baixa todas as faixas do álbum aberto na aba 💿 Álbum (tecla "a")."""
        if not self._current_album:
            self._toast(ALBUM_REQUIRED)
            return
        self.download_album(self._current_album)

    def action_retry_download(self) -> None:
        """Retoma a tarefa selecionada (tecla "r", T04-R2/R3; A09).

        ``INTERRUPTED`` (sessão anterior) e ``FAILED`` (falha desta sessão)
        viram uma **nova** tarefa (novo id + ``retry_of``): o ``.part``
        antigo não é reaproveitado e o fluxo completo roda de novo.
        """
        task = self._selected_download_task()
        if task is None or task.status not in (
            DownloadStatus.INTERRUPTED,
            DownloadStatus.FAILED,
        ):
            self._toast(RETRY_REQUIRES_INTERRUPTED_OR_FAILED)
            return
        if task.status is DownloadStatus.FAILED:
            new_task = download_manager.retry_failed(task.id)
        else:
            new_task = download_manager.retry_interrupted(task.id)
        if new_task is None:
            self._toast(RETRY_FAILED)
            return
        self.update_downloads_table()
        self.run_worker(self._run_download(new_task), group="download")
        self._toast(retrying_download(task.title))

    def action_cancel_download(self) -> None:
        """Cancela o download selecionado (tecla "c"; A09).

        Só ``PENDING``/``RUNNING`` são canceláveis (a janela de
        ``VALIDATING`` deixaria arquivo publicado sem registro). O arquivo
        final nunca é apagado — o cancelamento só sinaliza o abort e remove
        a linha persistida.
        """
        task = self._selected_download_task()
        if task is None or task.status not in (
            DownloadStatus.PENDING,
            DownloadStatus.RUNNING,
        ):
            self._toast(CANCEL_REQUIRES_ACTIVE)
            return
        if not download_manager.cancel_download(task.video_id):
            self._toast(CANCEL_FAILED)
            return
        self.update_downloads_table()
        self._toast(download_cancelled(task.title))

    def action_clear_downloads(self) -> None:
        """Remove da lista as tarefas concluídas/falhas (tecla "C"; A09)."""
        before = len(download_manager.get_queue())
        download_manager.clear_completed()
        removed = before - len(download_manager.get_queue())
        self.update_downloads_table()
        self._toast(downloads_cleared(removed))

    def action_show_download_error(self) -> None:
        """Abre o detalhe do erro da tarefa selecionada (tecla "i"; A09)."""
        task = self._selected_download_task()
        if task is None or not task.error:
            self._toast(NO_DOWNLOAD_ERROR)
            return
        self.app.push_screen(
            ErrorDetailScreen(task.title, download_error_detail(task.title, task.error))
        )

    def action_discard_download(self) -> None:
        """Descarta a tarefa INTERRUPTED selecionada (tecla "d", T04-R2).

        Ação **destrutiva** (limpa os temporários da tarefa e a linha
        persistida): desde o T12-R4 a tecla abre uma confirmação modal — o
        "sim" chama ``_discard_download_confirmed``. Retomar (r) não apaga
        nada e segue sem modal.
        """
        task = self._selected_download_task()
        if task is None or task.status is not DownloadStatus.INTERRUPTED:
            self._toast(DISCARD_REQUIRES_INTERRUPTED)
            return
        self.app.push_screen(
            ConfirmScreen(confirm_discard_message(task.title)),
            lambda confirmed: self._discard_download_confirmed(task, confirmed),
        )

    def _discard_download_confirmed(self, task: DownloadTask, confirmed: bool | None) -> None:
        """Callback do modal de descarte (T12-R4): só o "sim" remove algo."""
        if not confirmed:
            self._toast(CONFIRM_DISCARD_CANCELLED)
            return
        discarded = download_manager.discard_interrupted(task.id)
        if discarded is None:
            self._toast(DISCARD_FAILED)
            return
        self.update_downloads_table()
        self._toast(discarded_download(task.title, len(discarded)))

    def announce_interrupted(self) -> None:
        """Avisa sobre downloads interrompidos recuperados (T04-R2).

        Roda após a recuperação da inicialização: o toast só ensina as
        teclas — r/d exigem selecionar a linha na aba 📥 Downloads.
        """
        interrupted = sum(
            1 for task in download_manager.get_queue() if task.status is DownloadStatus.INTERRUPTED
        )
        if interrupted:
            self._toast(interrupted_downloads(interrupted))

    def download_current_track(self) -> None:
        """Baixa a faixa em reprodução."""
        track = player.current_track
        if not track or not track.video_id:
            self._toast(NO_TRACK_PLAYING)
            return
        self._enqueue_download(track)

    def download_album(self, album: AlbumDetail) -> None:
        """Enfileira o download de todas as faixas do álbum.

        Faixas já baixadas (na biblioteca local) são puladas; um único toast
        resume o resultado em vez de um por faixa.

        T12-R3 (inventário): as consultas de ``library``/``ensure_track`` por
        faixa são SQLite local, limitadas ao tamanho do álbum (unidades de
        ms no total) — seguem no loop; o download em si roda em worker.

        Ordem de álbum (T17.1/R2): o número 1-based vem da listagem completa
        do álbum (faixa indisponível pulada não desloca o número das demais)
        e o total conhecido viaja junto para virar ``Album.track_count``.
        """
        numbered = [
            (number, track)
            for number, track in enumerate(album.tracks, start=1)
            if track.video_id and track.is_available
        ]
        if not numbered:
            self._toast(ALBUM_NO_TRACKS)
            return

        album_title = album.title
        album_total = album.track_count or len(album.tracks) or None
        queued = 0
        skipped = 0
        in_progress = 0
        for number, track in numbered:
            info = self._track_info_from_song(track)
            if not info.video_id:
                continue
            if not info.album:
                info.album = album_title
            existing = library.get_track_by_source_id(info.video_id)
            if existing is not None and self._downloaded_file_is_present(existing):
                skipped += 1
                continue
            if self._enqueue_download(
                info,
                notify=False,
                track_number=number,
                album_track_count=album_total,
            ):
                queued += 1
            else:
                in_progress += 1

        parts = []
        if queued:
            parts.append(album_queued(queued))
        if in_progress:
            parts.append(album_already_running(in_progress))
        if skipped:
            parts.append(album_skipped(skipped))
        self._toast(album_queue_summary(album_title, parts))

    @staticmethod
    def _downloaded_file_is_present(track: Any) -> bool:
        """True só quando a faixa está baixada **e** o arquivo existe (A08).

        ``is_downloaded`` pode estar ``True`` com o arquivo apagado depois da
        última reconciliação (ou com ``is_missing`` marcado). Pular essa faixa
        em "Baixar álbum" impedia a reparação pelo fluxo natural; aqui ela
        volta a ser enfileirada. A checagem de disco é barata (um ``stat``)
        e limitada ao tamanho do álbum.
        """
        if not getattr(track, "is_downloaded", False):
            return False
        if getattr(track, "is_missing", False):
            return False
        local_path = getattr(track, "local_path", None)
        return bool(local_path) and Path(str(local_path)).is_file()

    def _enqueue_download(
        self,
        track: TrackInfo,
        notify: bool = True,
        *,
        track_number: int | None = None,
        disc_number: int | None = None,
        album_track_count: int | None = None,
    ) -> bool:
        """Registra a faixa na biblioteca e enfileira o download.

        O registro acontece ANTES do download para que o arquivo final já
        fique vinculado (aparece na aba Biblioteca); a ordem do álbum
        (T17.1/R2), quando informada, vai junto no registro e na tarefa.
        """
        if not track.video_id:
            return False

        # Título/artista/álbum entram na checagem para bloquear também
        # colisões de caminho final (T02-R5): faixas diferentes que gerariam
        # o mesmo arquivo não podem baixar em paralelo.
        if download_manager.is_active_or_pending(
            track.video_id,
            title=track.title,
            artist=track.artist,
            album=track.album or "",
        ):
            if notify:
                self._toast(already_queued(track.title))
            return False

        track_id = guard_write(
            lambda: library.ensure_track(
                source_id=track.video_id,
                title=track.title,
                artist=track.artist,
                album=track.album,
                thumbnail_url=track.thumbnail_url,
                duration_seconds=int(track.duration or 0),
                track_number=track_number,
                disc_number=disc_number,
                album_track_count=album_track_count,
            ),
            toast=self._toast,
            component="tui.downloads",
            event="track_register_failed",
            what="registrar a faixa na biblioteca",
        )
        if track_id is None:
            return False

        task = guard_write(
            lambda: download_manager.queue_download(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                album=track.album or "",
                thumbnail_url=track.thumbnail_url,
                track_id=track_id or 0,
                track_number=track_number,
                disc_number=disc_number,
                album_track_count=album_track_count,
            ),
            toast=self._toast,
            component="tui.downloads",
            event="download_enqueue_failed",
            what="enfileirar o download",
        )
        if task is None:
            return False
        # Downloads podem ser simultâneos (settings.download_concurrent);
        # sem exclusive=True para não cancelar o worker de outro download.
        self.run_worker(self._run_download(task), group="download")
        if notify:
            self._toast(download_starting(track.title))
        return True

    async def _run_download(self, task: DownloadTask) -> None:
        """Executa download em background."""
        try:
            await download_manager.download(task)
        except Exception as error:
            # A06: falha inesperada do worker não pode sumir como traceback
            # no handler da TUI — vira estado FAILED + toast em 3 partes.
            logger.exception("Erro inesperado no download %s", task.id)
            log_event(
                "tui.downloads",
                "download_worker_failed",
                f"Erro inesperado no download: {error}",
                operation_id=task.id,
                error_code=type(error).__name__,
                level=logging.ERROR,
                exc_info=True,
            )
            if (
                not task.try_transition(DownloadStatus.FAILED)
                and task.status is DownloadStatus.PENDING
                and task.try_transition(DownloadStatus.RUNNING)
            ):
                # Exceção antes de o worker sair de PENDING (falha ao agendar
                # o executor): consome a tentativa até FAILED para não deixar
                # a linha eternamente "pendente".
                task.try_transition(DownloadStatus.FAILED)
            if task.status is DownloadStatus.FAILED:
                task.error = mask_diagnostics(f"Erro inesperado: {error}")
            self.update_downloads_table()
            self._toast(download_failed(task.error or "erro inesperado"))
            return
        self.update_downloads_table()
        if task.status == DownloadStatus.COMPLETED:
            # R8: desfecho próprio quando o arquivo do destino já existia
            # (Q2(a) — validado e registrado sem baixar; nada foi baixado).
            if task.outcome is DownloadOutcome.EXISTING_ADOPTED:
                self._toast(download_existing_adopted(task.title))
            else:
                self._toast(download_completed(task.title))
            self.refresh_library()
        elif task.status == DownloadStatus.FAILED:
            self._toast(download_failed(task.error or "sem detalhe"))
        elif task.status == DownloadStatus.CANCELLED:
            self._toast(download_cancelled(task.title))
        # INTERRUPTED não gera toast: só acontece no encerramento do app,
        # com a TUI já saindo — e a janela some antes de o toast aparecer.
