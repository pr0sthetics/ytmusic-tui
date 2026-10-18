"""Testes da máquina de estados da tarefa de download (T02, §4 do plano).

Cobrem: transições permitidas da §4.2 (positivo), proibidas (negativo,
incluindo "CANCELLED nunca vira COMPLETED"), terminais sem saída,
identidade estável e retentativa (T02-R1) e a serialização como string
(StrEnum — persistência futura de T04).
"""

import pytest

from ytmusic_tui.download.task import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    DownloadStatus,
    DownloadTask,
    InvalidTransitionError,
)

ALL_STATUSES = tuple(DownloadStatus)


def _task_in(status: DownloadStatus) -> DownloadTask:
    """Constrói uma tarefa no estado pedido, só por transições válidas."""
    path = {
        DownloadStatus.PENDING: [],
        DownloadStatus.RUNNING: [DownloadStatus.RUNNING],
        DownloadStatus.VALIDATING: [DownloadStatus.RUNNING, DownloadStatus.VALIDATING],
        DownloadStatus.COMPLETED: [
            DownloadStatus.RUNNING,
            DownloadStatus.VALIDATING,
            DownloadStatus.COMPLETED,
        ],
        DownloadStatus.FAILED: [DownloadStatus.RUNNING, DownloadStatus.FAILED],
        DownloadStatus.CANCELLED: [DownloadStatus.RUNNING, DownloadStatus.CANCELLED],
        DownloadStatus.INTERRUPTED: [DownloadStatus.RUNNING, DownloadStatus.INTERRUPTED],
    }[status]

    task = DownloadTask(video_id="abc")
    for step in path:
        task.transition_to(step)
    return task


ALLOWED_TRANSITIONS = [
    (DownloadStatus.PENDING, DownloadStatus.RUNNING),
    (DownloadStatus.PENDING, DownloadStatus.CANCELLED),
    (DownloadStatus.RUNNING, DownloadStatus.VALIDATING),
    (DownloadStatus.RUNNING, DownloadStatus.FAILED),
    (DownloadStatus.RUNNING, DownloadStatus.CANCELLED),
    (DownloadStatus.RUNNING, DownloadStatus.INTERRUPTED),
    (DownloadStatus.VALIDATING, DownloadStatus.COMPLETED),
    (DownloadStatus.VALIDATING, DownloadStatus.FAILED),
    (DownloadStatus.VALIDATING, DownloadStatus.INTERRUPTED),
]


@pytest.mark.parametrize(("origin", "target"), ALLOWED_TRANSITIONS)
def test_allowed_transitions(origin, target):
    """Todas as transições da §4.2 funcionam (§4.4)."""
    task = _task_in(origin)

    assert task.can_transition_to(target) is True
    task.transition_to(target)

    assert task.status is target


INVALID_TRANSITIONS = [
    (DownloadStatus.PENDING, DownloadStatus.VALIDATING),
    (DownloadStatus.PENDING, DownloadStatus.COMPLETED),
    (DownloadStatus.PENDING, DownloadStatus.FAILED),
    (DownloadStatus.PENDING, DownloadStatus.INTERRUPTED),
    (DownloadStatus.RUNNING, DownloadStatus.PENDING),
    (DownloadStatus.RUNNING, DownloadStatus.COMPLETED),  # exige VALIDATING
    (DownloadStatus.VALIDATING, DownloadStatus.RUNNING),
    (DownloadStatus.VALIDATING, DownloadStatus.CANCELLED),  # janela curta
    (DownloadStatus.CANCELLED, DownloadStatus.COMPLETED),  # corrida: nunca
    (DownloadStatus.INTERRUPTED, DownloadStatus.COMPLETED),
    *[(terminal, DownloadStatus.RUNNING) for terminal in TERMINAL_STATUSES],
]


@pytest.mark.parametrize(("origin", "target"), INVALID_TRANSITIONS)
def test_invalid_transitions_raise(origin, target):
    """Transição fora da máquina levanta InvalidTransitionError (§4.4)."""
    task = _task_in(origin)

    assert task.can_transition_to(target) is False
    with pytest.raises(InvalidTransitionError) as excinfo:
        task.transition_to(target)

    assert excinfo.value.current is origin
    assert excinfo.value.target is target
    assert task.status is origin  # estado preservado na recusa


def test_terminal_states_have_no_exits():
    """Terminais não saem para nenhum estado (retry = nova tarefa)."""
    for terminal in TERMINAL_STATUSES:
        task = _task_in(terminal)
        assert task.is_terminal is True
        for target in ALL_STATUSES:
            assert task.can_transition_to(target) is False


def test_active_and_terminal_partition_all_statuses():
    """Todo estado é ativo ou terminal, nunca ambos (§4.2)."""
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)
    assert set(DownloadStatus) == ACTIVE_STATUSES | TERMINAL_STATUSES


def test_task_ids_are_unique_and_stable():
    """Cada tarefa tem id próprio; o id não muda com o ciclo de vida (T02-R1)."""
    a, b = DownloadTask(video_id="a"), DownloadTask(video_id="a")

    assert a.id != b.id
    assert a.id.startswith("task-")

    before = a.id
    a.transition_to(DownloadStatus.RUNNING)
    assert a.id == before


def test_make_retry_creates_new_task_referencing_previous():
    """Retentativa: id novo, ``retry_of`` apontando para a tentativa anterior."""
    task = _task_in(DownloadStatus.FAILED)
    task.title, task.artist, task.album, task.track_id = "T", "A", "Alb", 7
    task.track_number, task.disc_number, task.album_track_count = 3, 1, 9

    retry = task.make_retry()

    assert retry.id != task.id
    assert retry.retry_of == task.id
    assert retry.status is DownloadStatus.PENDING
    assert (retry.video_id, retry.title, retry.artist, retry.album, retry.track_id) == (
        "abc",
        "T",
        "A",
        "Alb",
        7,
    )
    # A ordem no álbum sobrevive à retentativa (T17.1/R2).
    assert (retry.track_number, retry.disc_number, retry.album_track_count) == (3, 1, 9)
    # Abortos independentes: cancelar a retentativa não afeta a original.
    retry.abort_event.set()
    assert not task.abort_event.is_set()


def test_status_serializes_as_string():
    """StrEnum: grava como string simples (persistência futura de T04)."""
    assert DownloadStatus.RUNNING == "running"
    assert f"{DownloadStatus.INTERRUPTED}" == "interrupted"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
