"""Modelo da tarefa de download e sua máquina de estados (T02).

Estados, transições permitidas e validação ficam centralizados aqui: o
gerenciador e a TUI só observam o estado e pedem transições via
``DownloadTask.transition_to`` — nenhum outro componente escreve ``status``
(T02-R6). Transição fora da máquina levanta ``InvalidTransitionError``;
quem tem corrida conhecida (ex.: ``shutdown()`` concorrente com o worker)
consulta ``can_transition_to`` antes de transicionar.

Transições permitidas (§4.2 do plano):

    PENDING    → RUNNING, CANCELLED
    RUNNING    → VALIDATING, FAILED, CANCELLED, INTERRUPTED
    VALIDATING → COMPLETED, FAILED, INTERRUPTED
    terminais  → (nenhuma; retomar = nova tarefa com ``retry_of``)

``INTERRUPTED`` é terminal por decisão: encerrar o app no meio de um
download fecha a tarefa, e a retomada é uma **nova** tarefa (T04-R2; ver
Q7 registrada no plano de engenharia). O status usa ``StrEnum`` para
serializar como string simples (persistência futura — T04).
"""

import asyncio
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

__all__ = [
    "ACTIVE_STATUSES",
    "DownloadOptions",
    "DownloadOutcome",
    "DownloadStatus",
    "DownloadTask",
    "InvalidTransitionError",
    "TERMINAL_STATUSES",
]


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    """Configuração imutável de uma tarefa de download (A05).

    O snapshot é tirado no **enfileiramento** e usado até o fim da tarefa
    (worker, pós-processamento, caminho final e registro na biblioteca).
    Mudar ``download_format``/``download_dir``/``embed_metadata`` com um
    download em voo não pode mais gerar extensão, pós-processador e caminho
    inconsistentes. Retomar cria um snapshot novo com a configuração atual.
    """

    audio_format: str
    quality: str
    download_dir: Path
    embed_metadata: bool


class DownloadOutcome(StrEnum):
    """Desfecho qualificado da tarefa (T17.1/R8 — complemento visível da Q2(a)).

    A política não muda (nunca sobrescreve): isto só torna visível **como**
    a tarefa terminou. ``EXISTING_ADOPTED`` = o arquivo de destino já existia;
    foi validado e registrado sem baixar.
    """

    NONE = ""
    EXISTING_ADOPTED = "existing_adopted"


class DownloadStatus(StrEnum):
    """Estados possíveis de uma tarefa de download (§4.1 do plano)."""

    PENDING = "pending"  # na fila, ainda não iniciada
    RUNNING = "running"  # baixando (equivalente ao antigo DOWNLOADING)
    VALIDATING = "validating"  # yt-dlp terminou; validando o arquivo final
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # encerramento do app no meio do trabalho


#: Estados "em voo": a tarefa ainda pode evoluir (bloqueiam retentativa).
ACTIVE_STATUSES = frozenset(
    {DownloadStatus.PENDING, DownloadStatus.RUNNING, DownloadStatus.VALIDATING}
)

#: Estados finais: a tarefa não transiciona mais (nova tentativa = nova tarefa).
TERMINAL_STATUSES = frozenset(
    {
        DownloadStatus.COMPLETED,
        DownloadStatus.FAILED,
        DownloadStatus.CANCELLED,
        DownloadStatus.INTERRUPTED,
    }
)

#: Tabela de transições permitidas — fonte de verdade da máquina (§4.2).
_ALLOWED: dict[DownloadStatus, frozenset[DownloadStatus]] = {
    DownloadStatus.PENDING: frozenset({DownloadStatus.RUNNING, DownloadStatus.CANCELLED}),
    DownloadStatus.RUNNING: frozenset(
        {
            DownloadStatus.VALIDATING,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
            DownloadStatus.INTERRUPTED,
        }
    ),
    DownloadStatus.VALIDATING: frozenset(
        {
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.INTERRUPTED,
        }
    ),
    DownloadStatus.COMPLETED: frozenset(),
    DownloadStatus.FAILED: frozenset(),
    DownloadStatus.CANCELLED: frozenset(),
    DownloadStatus.INTERRUPTED: frozenset(),
}


class InvalidTransitionError(ValueError):
    """Transição de estado fora da máquina (§4.2)."""

    def __init__(self, current: DownloadStatus, target: DownloadStatus) -> None:
        super().__init__(f"Transição inválida: {current.value} → {target.value}")
        self.current = current
        self.target = target


@dataclass
class DownloadTask:
    """Uma tentativa de download (identificada por ``id``)."""

    video_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    thumbnail_url: str = ""
    track_id: int = 0  # ID na biblioteca local
    # Ordem na listagem do álbum (T17.1/R2): 1-based; None = desconhecida.
    # Vivem só em memória — o espelho persistido do T04 não guarda ordem
    # (sem migração de esquema); a retomada os perde e recai em added_at.
    track_number: int | None = None
    disc_number: int | None = None
    # Total de faixas do álbum quando conhecido (R2/R5): vira Album.track_count.
    album_track_count: int | None = None
    # Como a tarefa terminou (T17.1/R8): COMPLETED normal vs. arquivo de
    # destino já existente importado sem baixar (Q2(a)).
    outcome: DownloadOutcome = DownloadOutcome.NONE
    # Identificador estável da tarefa (T02-R1): o log e a recuperação (T04)
    # correlacionam eventos por ele; retentativa gera novo id.
    id: str = field(default_factory=lambda: f"task-{uuid4().hex[:12]}")
    # Se esta tarefa é uma retentativa, aponta para a tentativa anterior.
    retry_of: str | None = None
    status: DownloadStatus = DownloadStatus.PENDING
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    file_path: Path | None = None
    #: Destino final calculado no enfileiramento (A05): o worker não o
    #: recalcula, então mudar ``download_dir`` com a tarefa em voo não move
    #: o arquivo nem registra um caminho diferente do publicado.
    final_path: Path | None = None
    #: Snapshot imutável da configuração (A05), tirado no enfileiramento.
    options: DownloadOptions | None = None
    error: str = ""
    # Forward reference: ``DownloadTask`` ainda está sendo definido aqui.
    _future: "asyncio.Future[DownloadTask] | None" = field(default=None, repr=False)
    abort_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # A15: check + set atômicos. ``RLock`` porque ``try_transition`` e
    # ``can_transition_to`` podem aninhar; ``compare=False`` para a tarefa
    # continuar comparável (o lock é infraestrutura, não estado).
    _transition_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False
    )

    @property
    def is_terminal(self) -> bool:
        """True quando a tarefa não transiciona mais."""
        with self._transition_lock:
            return self.status in TERMINAL_STATUSES

    def can_transition_to(self, target: DownloadStatus) -> bool:
        """True se a transição é permitida a partir do estado atual."""
        with self._transition_lock:
            return target in _ALLOWED[self.status]

    def try_transition(self, target: DownloadStatus) -> bool:
        """Tenta a transição atomicamente; ``False`` = outro estado venceu.

        A15: check + set sob o lock da tarefa. É o método preferido em
        corridas esperadas (worker × cancelamento/shutdown): o perdedor não
        levanta exceção nem sobrescreve o estado final do vencedor.
        """
        with self._transition_lock:
            if target not in _ALLOWED[self.status]:
                return False
            self.status = target
            return True

    def transition_to(self, target: DownloadStatus) -> None:
        """Muda de estado validando a máquina (§4.2).

        Levanta ``InvalidTransitionError`` quando a transição não existe —
        nunca "conserta" o estado em silêncio (INV-1). O check + set são
        atômicos (A15); em corridas esperadas, prefira ``try_transition``.
        """
        if not self.try_transition(target):
            raise InvalidTransitionError(self.status, target)

    def make_retry(self) -> "DownloadTask":
        """Cria uma nova tentativa (novo ``id``) referenciando esta tarefa."""
        return DownloadTask(
            video_id=self.video_id,
            title=self.title,
            artist=self.artist,
            album=self.album,
            thumbnail_url=self.thumbnail_url,
            track_id=self.track_id,
            retry_of=self.id,
            # A posição no álbum não muda entre tentativas (T17.1/R2).
            track_number=self.track_number,
            disc_number=self.disc_number,
            album_track_count=self.album_track_count,
        )
