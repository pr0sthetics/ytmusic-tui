"""Ações de download na TUI (mixin de ``MainScreen``)."""

from typing import TYPE_CHECKING, Any

from textual.widgets import DataTable, Static
from textual.worker import Worker

from ytmusic_tui.api.models import AlbumDetail
from ytmusic_tui.download import DownloadStatus, DownloadTask, download_manager
from ytmusic_tui.library import library
from ytmusic_tui.player import TrackInfo, player
from ytmusic_tui.tui.dialogs import ConfirmScreen
from ytmusic_tui.tui.messages import (
    ALBUM_NO_TRACKS,
    ALBUM_REQUIRED,
    CONFIRM_DISCARD_CANCELLED,
    DISCARD_FAILED,
    DISCARD_REQUIRES_INTERRUPTED,
    NO_TRACK_PLAYING,
    RETRY_FAILED,
    RETRY_REQUIRES_INTERRUPTED,
    album_already_running,
    album_queue_summary,
    album_queued,
    album_skipped,
    already_queued,
    confirm_discard_message,
    discarded_download,
    download_cancelled,
    download_completed,
    download_failed,
    download_starting,
    interrupted_downloads,
    retrying_download,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class DownloadController:
    """Mixin da aba 📥 Downloads e dos atalhos Ctrl+D / A.

    A classe host (``MainScreen``) oferece ``_toast`` e mantém
    ``_current_album`` (usado pelo "a"); ``_track_info_from_song``
    (PlaybackController) e ``refresh_library`` (LibraryController) também
    vêm dos mixins do mesmo host.
    """

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
        """Atualiza tabela de downloads.

        T12-R3 (inventário): fila/ativos são listas **em memória** (cópias,
        sem I/O); o custo é montar as linhas do ``DataTable`` — pequeno e
        limitado. Segue no loop de propósito (timer de 0,5 s + eventos).
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
        table.clear(columns=True)
        table.add_columns("Status", "Título", "Artista", "Progresso", "Velocidade", "ETA")

        for task in rows:
            status_icon = {
                DownloadStatus.PENDING: "⏳",
                DownloadStatus.RUNNING: "⬇️",
                DownloadStatus.VALIDATING: "🔎",
                DownloadStatus.COMPLETED: "✅",
                DownloadStatus.FAILED: "❌",
                DownloadStatus.CANCELLED: "🚫",
                DownloadStatus.INTERRUPTED: "⏸️",
            }.get(task.status, "?")
            table.add_row(
                status_icon,
                task.title[:40],
                task.artist[:30],
                f"{task.progress:.1f}%" if task.progress else "—",
                task.speed,
                task.eta,
            )

    def _selected_download_task(self) -> DownloadTask | None:
        """Tarefa da linha selecionada na tabela de downloads (ou ``None``)."""
        try:
            table = self.query_one("#downloads-table", DataTable)
        except Exception:
            return None
        rows = self._download_rows()
        index: int = table.cursor_row
        if 0 <= index < len(rows):
            return rows[index]
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
        """Retoma a tarefa INTERRUPTED selecionada (tecla "r", T04-R2/R3).

        Retomar é uma **nova** tarefa (novo id + ``retry_of``): o ``.part``
        antigo é descartado com log (consistência não é verificável de forma
        barata) e o fluxo completo roda de novo.
        """
        task = self._selected_download_task()
        if task is None or task.status is not DownloadStatus.INTERRUPTED:
            self._toast(RETRY_REQUIRES_INTERRUPTED)
            return
        new_task = download_manager.retry_interrupted(task.id)
        if new_task is None:
            self._toast(RETRY_FAILED)
            return
        self.update_downloads_table()
        self.run_worker(self._run_download(new_task), group="download")
        self._toast(retrying_download(task.title))

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
            if existing and existing.is_downloaded:
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

        track_id = library.ensure_track(
            source_id=track.video_id,
            title=track.title,
            artist=track.artist,
            album=track.album,
            thumbnail_url=track.thumbnail_url,
            duration_seconds=int(track.duration or 0),
            track_number=track_number,
            disc_number=disc_number,
            album_track_count=album_track_count,
        )

        task = download_manager.queue_download(
            video_id=track.video_id,
            title=track.title,
            artist=track.artist,
            album=track.album or "",
            thumbnail_url=track.thumbnail_url,
            track_id=track_id or 0,
            track_number=track_number,
            disc_number=disc_number,
            album_track_count=album_track_count,
        )
        # Downloads podem ser simultâneos (settings.download_concurrent);
        # sem exclusive=True para não cancelar o worker de outro download.
        self.run_worker(self._run_download(task), group="download")
        if notify:
            self._toast(download_starting(track.title))
        return True

    async def _run_download(self, task: DownloadTask) -> None:
        """Executa download em background."""
        await download_manager.download(task)
        self.update_downloads_table()
        if task.status == DownloadStatus.COMPLETED:
            self._toast(download_completed(task.title))
            self.refresh_library()
        elif task.status == DownloadStatus.FAILED:
            self._toast(download_failed(task.error or "sem detalhe"))
        elif task.status == DownloadStatus.CANCELLED:
            self._toast(download_cancelled(task.title))
        # INTERRUPTED não gera toast: só acontece no encerramento do app,
        # com a TUI já saindo — e a janela some antes de o toast aparecer.
