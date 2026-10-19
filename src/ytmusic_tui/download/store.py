"""Persistência mínima das tarefas de download (T04-R1).

Tabela própria (``download_tasks``) no mesmo SQLite da biblioteca: criada
pelo esquema atual em banco novo e pela migração v1 em banco anterior a
ela (T05-R1). A tabela é um **espelho de estado** das tarefas em
voo: a verdade da biblioteca continua em ``tracks`` (nada é duplicado) e
``track_id`` é um inteiro solto, não uma FK.

Estados persistidos:

- ``PENDING``/``RUNNING``/``VALIDATING``: tarefa em voo — se o app morrer
  (kill -9, queda de energia), essas linhas são a pista da próxima execução;
- ``INTERRUPTED``: aguardando decisão do usuário — retomar (novo id com
  ``retry_of``) ou descartar (T04-R2);
- ``COMPLETED``/``FAILED``/``CANCELLED``: a linha é apagada no fim da
  sessão (o resultado é da memória; terminal só fica persistido se a
  escrita de remoção falhar — a inicialização trata a sobra).

``AWAITING_DECISION_STATUSES`` é diferente de ``TERMINAL_STATUSES`` da
máquina (§4.2, ``task.py``): ``INTERRUPTED`` é terminal **para a máquina**
(não transiciona mais), mas continua "aguardando decisão" para a
persistência — é assim que o temporário associado fica retido pela §5.3
até o usuário retomar ou descartar (T04). Pela mesma razão ``PENDING`` e
``RUNNING``/``VALIDATING`` também contam como aguardando: uma linha em voo
persistida é trabalho que a próxima execução vai converter em INTERRUPTED
e, até isso acontecer, seus temporários não podem ser recolhidos pela
limpeza de idade.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from ytmusic_tui.download.pipeline import TMP_DIR_NAME, reservation_key
from ytmusic_tui.download.task import ACTIVE_STATUSES, DownloadStatus, DownloadTask
from ytmusic_tui.library.database import DatabaseManager, db_manager
from ytmusic_tui.library.models import DownloadTaskRecord, utcnow

__all__ = [
    "AWAITING_DECISION_STATUSES",
    "DownloadTaskStore",
    "PendingTaskFiles",
    "hydrate_interrupted",
]

#: Estados "aguardando decisão" (ordem fixa para serialização previsível).
#: Ver docstring do módulo para a diferença vs. ``TERMINAL_STATUSES``.
AWAITING_DECISION_STATUSES: tuple[DownloadStatus, ...] = (
    DownloadStatus.PENDING,
    DownloadStatus.RUNNING,
    DownloadStatus.VALIDATING,
    DownloadStatus.INTERRUPTED,
)
_AWAITING_VALUES = tuple(status.value for status in AWAITING_DECISION_STATUSES)


@dataclass(frozen=True)
class PendingTaskFiles:
    """Temporários associados a uma tarefa aguardando decisão (§5.3/T04).

    ``key``/``temp_dir``/``final_path`` bastam para localizar tudo que o
    pipeline pode ter deixado: ``.tmp/<chave>.*`` (reserva e download em
    andamento) e ``<final>.part``/``.ytdl`` (artefatos do yt-dlp ao lado do
    destino). O pai do final **não** é o ``download_dir`` (o template gera
    ``download_dir/Artista[/Álbum]/…``), por isso o diretório é persistido
    junto.
    """

    task_id: str
    key: str
    temp_dir: Path
    final_path: Path

    @property
    def legacy_parts(self) -> tuple[Path, Path]:
        """``.part``/``.ytdl`` ao lado do destino final (artefatos do yt-dlp)."""
        return Path(f"{self.final_path}.part"), Path(f"{self.final_path}.ytdl")


def hydrate_interrupted(row: DownloadTaskRecord) -> DownloadTask:
    """Reconstrói a tarefa a partir da linha, já em ``INTERRUPTED`` (T04-R2).

    **Hidratação, não transição**: a máquina da T02 governa transições *em
    voo*; a linha persistida é o passado da tarefa. Reconstruí-la direto no
    estado ``INTERRUPTED`` evita inventar uma transição que não existe na
    §4.2 (PENDING→INTERRUPTED, por exemplo) — nada avançou desde o
    encerramento, então não há transição a fazer.
    """
    return DownloadTask(
        video_id=row.video_id,
        title=row.title,
        artist=row.artist,
        album=row.album,
        thumbnail_url=row.thumbnail_url,
        track_id=row.track_id,
        id=row.task_id,
        retry_of=row.retry_of or None,
        status=DownloadStatus.INTERRUPTED,
        error="Interrompido no encerramento anterior",
        # A05: o caminho final persistido volta com a tarefa; a retomada
        # (:meth:`DownloadManager.retry_interrupted`) troca por um snapshot e
        # um caminho novos, conforme a configuração atual.
        final_path=Path(row.final_path) if row.final_path else None,
    )


class DownloadTaskStore:
    """Espelho persistido das tarefas de download.

    Falhas de escrita/leitura **nunca** são tratadas aqui: o chamador
    (``DownloadManager``) as registra no log e segue — perder a pista de
    recuperação é ruim, quebrar o download ou fabricar sucesso falso por
    causa dela é pior (INV-1).
    """

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or db_manager

    def save(self, task: DownloadTask, *, download_dir: Path, final_path: Path) -> None:
        """Grava (cria ou atualiza) a linha da tarefa — upsert por ``task_id``."""
        with self.db.session() as session:
            record = session.get(DownloadTaskRecord, task.id)
            if record is None:
                record = DownloadTaskRecord(task_id=task.id)
                session.add(record)
            record.video_id = task.video_id
            record.title = task.title
            record.artist = task.artist
            record.album = task.album
            record.thumbnail_url = task.thumbnail_url
            record.track_id = task.track_id
            record.final_path = str(final_path)
            record.download_dir = str(download_dir)
            record.status = task.status.value
            record.retry_of = task.retry_of or ""
            record.updated_at = utcnow()

    def update_status(self, task_id: str, status: DownloadStatus) -> None:
        """Atualiza só o status (e ``updated_at``) de uma linha existente."""
        with self.db.session() as session:
            session.execute(
                update(DownloadTaskRecord)
                .where(DownloadTaskRecord.task_id == task_id)
                .values(status=status.value, updated_at=utcnow())
            )

    def delete(self, task_id: str) -> None:
        """Remove a linha (idempotente: ausente não é erro)."""
        with self.db.session() as session:
            record = session.get(DownloadTaskRecord, task_id)
            if record is not None:
                session.delete(record)

    def find(self, task_id: str) -> DownloadTaskRecord | None:
        """Devolve a linha (desanexada da sessão) ou ``None``."""
        with self.db.session() as session:
            return session.get(DownloadTaskRecord, task_id)

    def mark_active_as_interrupted(self) -> int:
        """Converte linhas em voo (PENDING/RUNNING/VALIDATING) em INTERRUPTED.

        ``INTERRUPTED`` = "aguardando decisão" (T04-R2): o usuário retoma ou
        descarta. Nunca converte linhas já em INTERRUPTED nem terminais
        persistidas por falha de escrita. Devolve quantas linhas mudaram.
        """
        active_values = tuple(status.value for status in ACTIVE_STATUSES)
        with self.db.session() as session:
            # ``cast``: o stub do SQLAlchemy tipa ``execute(update(...))``
            # como ``Result`` genérico; em DML o retorno real é
            # ``CursorResult`` (único com ``rowcount``).
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(DownloadTaskRecord)
                    .where(DownloadTaskRecord.status.in_(active_values))
                    .values(status=DownloadStatus.INTERRUPTED.value, updated_at=utcnow())
                ),
            )
            return int(result.rowcount or 0)

    def load_interrupted(self) -> list[DownloadTaskRecord]:
        """Linhas aguardando decisão do usuário (INTERRUPTED), estáveis em ordem."""
        with self.db.session() as session:
            return list(
                session.execute(
                    select(DownloadTaskRecord)
                    .where(DownloadTaskRecord.status == DownloadStatus.INTERRUPTED.value)
                    .order_by(DownloadTaskRecord.created_at, DownloadTaskRecord.task_id)
                ).scalars()
            )

    def awaiting_decision_files(self) -> list[PendingTaskFiles]:
        """Temporários de todas as tarefas aguardando decisão (§5.3/T04).

        Inclui PENDING/RUNNING/VALIDATING (linha em voo sobrevivente a um
        encerramento brusco) e INTERRUPTED (decisão pendente na TUI). Linhas
        sem caminhos persistidos são ignoradas (não há o que localizar).
        """
        with self.db.session() as session:
            rows = list(
                session.execute(
                    select(DownloadTaskRecord).where(
                        DownloadTaskRecord.status.in_(_AWAITING_VALUES)
                    )
                ).scalars()
            )

        files: list[PendingTaskFiles] = []
        for row in rows:
            if not row.final_path or not row.download_dir:
                continue
            final_path = Path(row.final_path)
            files.append(
                PendingTaskFiles(
                    task_id=row.task_id,
                    key=reservation_key(final_path),
                    temp_dir=Path(row.download_dir) / TMP_DIR_NAME,
                    final_path=final_path,
                )
            )
        return files
