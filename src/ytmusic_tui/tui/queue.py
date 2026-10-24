"""Fila de reprodução independente da UI (Fase 2 — P01/P03).

O modelo não conhece Textual: recebe o ``TrackInfo`` do player e devolve o
próximo item. Assim a política de repeat/shuffle e as edições da fila ficam
testáveis sem montar a tela. A UI é dona de *quando* tocar; a fila é dona de
*qual* item vem depois.
"""

from __future__ import annotations

import contextlib
import itertools
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from ytmusic_tui.player import TrackInfo

#: Uid monotônico por processo: a identidade de um item sobrevive a
#: reordenações e remoções (a seleção da lista é restaurada por ele).
_UID = itertools.count(1)


class RepeatMode(StrEnum):
    """Modo de repetição da fila."""

    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass(frozen=True, slots=True, eq=False)
class QueueItem:
    """Uma faixa na fila.

    ``track_id`` é o id na biblioteca local quando a faixa veio dela; faixas
    só de stream ficam com ``None``. ``source`` alimenta o histórico
    (``search``, ``library``, ``playlist:1``, ``album:2``, ``history``).
    """

    info: TrackInfo
    track_id: int | None = None
    source: str = "search"
    uid: int = field(default_factory=lambda: next(_UID))


class PlaybackQueue:
    """Fila com índice, repeat e shuffle; sem dependência de UI.

    Invariantes: ``-1 <= index < len(items)``; ``index == -1`` significa
    "sem faixa atual" (fila vazia ou parada). Remover a faixa atual deixa a
    próxima no lugar dela; se era a última, a fila para (``index == -1``),
    mesmo que ainda existam faixas anteriores.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._items: list[QueueItem] = []
        self._index = -1
        self._repeat = RepeatMode.OFF
        self._shuffle = False
        #: Ordem original (uids) para desfazer o shuffle; None = não embaralhada.
        self._base_order: list[int] | None = None
        self._rng = rng or random.Random()
        self._listeners: list[Callable[[], None]] = []

    # ===== Leitura =====

    @property
    def items(self) -> tuple[QueueItem, ...]:
        """Itens na ordem de reprodução atual."""
        return tuple(self._items)

    @property
    def index(self) -> int:
        """Posição da faixa atual (-1 = nenhuma)."""
        return self._index

    @property
    def current(self) -> QueueItem | None:
        """Faixa atual, se houver."""
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return None

    @property
    def repeat(self) -> RepeatMode:
        """Modo de repetição em vigor."""
        return self._repeat

    @property
    def shuffle(self) -> bool:
        """True quando a fila está embaralhada."""
        return self._shuffle

    @property
    def at_start(self) -> bool:
        """True na primeira faixa (o ``←`` reinicia em vez de voltar)."""
        return self._index <= 0

    def __len__(self) -> int:
        return len(self._items)

    # ===== Listeners =====

    def add_listener(self, listener: Callable[[], None]) -> None:
        """Registra callback chamado a cada mudança (modal/barra)."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[], None]) -> None:
        """Remove callback registrado (idempotente)."""
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def _notify(self) -> None:
        """Avisa os observadores; falha de UI não corrompe a fila."""
        for listener in tuple(self._listeners):
            with contextlib.suppress(Exception):
                listener()

    # ===== Construção =====

    def set_items(self, items: Iterable[QueueItem], start: int = 0) -> None:
        """Define a fila e a faixa inicial.

        Com shuffle ligado, a faixa escolhida vira a primeira e o restante é
        embaralhado — o gesto "toquei esta" continua valendo.
        """
        queue_items = list(items)
        if len({item.uid for item in queue_items}) != len(queue_items):
            raise ValueError("a fila não pode ter uid repetido")
        self._items = queue_items
        if not queue_items:
            self._index = -1
            self._base_order = None
            self._notify()
            return

        start = max(0, min(start, len(queue_items) - 1))
        if self._shuffle:
            selected = queue_items[start]
            rest = [*queue_items[:start], *queue_items[start + 1 :]]
            self._rng.shuffle(rest)
            self._items = [selected, *rest]
            self._index = 0
            self._base_order = [item.uid for item in queue_items]
        else:
            self._index = start
            self._base_order = None
        self._notify()

    # ===== Navegação =====

    def play_at(self, index: int) -> QueueItem | None:
        """Toca o item na posição (``None`` se fora da fila)."""
        if not 0 <= index < len(self._items):
            return None
        self._index = index
        self._notify()
        return self.current

    def next(self, *, auto: bool = True) -> QueueItem | None:
        """Avança um item.

        ``auto`` é o fim natural da faixa: aí ``repeat one`` repete a atual e
        ``repeat off`` para no fim. O avanço manual ignora ``repeat one`` e
        só dá a volta com ``repeat all``.
        """
        if not self._items:
            return None
        if auto and self._repeat is RepeatMode.ONE:
            return self.current
        if self._index < 0:
            if auto:
                return None
            self._index = 0
            self._notify()
            return self.current

        next_index = self._index + 1
        if next_index < len(self._items):
            self._index = next_index
            self._notify()
            return self.current
        if self._repeat is RepeatMode.ALL:
            self._index = 0
            self._notify()
            return self.current
        return None

    def previous(self) -> QueueItem | None:
        """Volta um item; na primeira, devolve a atual (o ``←`` reinicia)."""
        if not self._items or self._index <= 0:
            return self.current
        self._index -= 1
        self._notify()
        return self.current

    # ===== Edição =====

    def remove(self, uid: int) -> bool:
        """Remove o item; devolve False se o uid não está na fila."""
        index = self._find(uid)
        if index is None:
            return False

        was_current = index == self._index
        del self._items[index]
        if self._base_order is not None:
            with contextlib.suppress(ValueError):
                self._base_order.remove(uid)

        if not self._items:
            self._index = -1
        elif was_current:
            # A próxima ocupa o lugar; se era a última, a fila para.
            self._index = index if index < len(self._items) else -1
        elif index < self._index:
            self._index -= 1
        self._notify()
        return True

    def move(self, uid: int, delta: int) -> bool:
        """Move o item ``delta`` posições; a faixa atual acompanha."""
        index = self._find(uid)
        if index is None or delta == 0:
            return False
        target = index + delta
        if not 0 <= target < len(self._items):
            return False
        current_uid = self.current.uid if self.current is not None else None
        item = self._items.pop(index)
        self._items.insert(target, item)
        if current_uid is not None:
            self._index = next(
                (
                    position
                    for position, entry in enumerate(self._items)
                    if entry.uid == current_uid
                ),
                self._index,
            )
        self._notify()
        return True

    def clear(self) -> None:
        """Esvazia a fila (stop explícito)."""
        self._items.clear()
        self._index = -1
        self._base_order = None
        self._notify()

    # ===== Modos =====

    def set_repeat(self, mode: RepeatMode) -> RepeatMode:
        """Define o repeat; devolve o modo aplicado."""
        self._repeat = mode
        self._notify()
        return self._repeat

    def cycle_repeat(self) -> RepeatMode:
        """Cicla ``off -> all -> one -> off`` (atalho ``R``)."""
        modes = (RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE)
        self._repeat = modes[(modes.index(self._repeat) + 1) % len(modes)]
        self._notify()
        return self._repeat

    def toggle_shuffle(self) -> bool:
        """Liga/desliga o shuffle sem perder a faixa atual.

        Ligar embaralha só o que ainda vai tocar; desligar restaura a ordem
        original e mantém a faixa atual no comando. Reordenações manuais
        feitas durante o shuffle não sobrevivem ao desligamento.
        """
        self._shuffle = not self._shuffle
        if self._shuffle:
            self._base_order = [item.uid for item in self._items]
            self._shuffle_after_current()
        else:
            self._restore_base_order()
        self._notify()
        return self._shuffle

    # ===== Internos =====

    def _find(self, uid: int) -> int | None:
        for index, item in enumerate(self._items):
            if item.uid == uid:
                return index
        return None

    def _shuffle_after_current(self) -> None:
        """Embaralha a cauda; o que já tocou e a faixa atual ficam parados."""
        if self._index < 0:
            self._rng.shuffle(self._items)
            return
        head = self._items[: self._index + 1]
        tail = self._items[self._index + 1 :]
        self._rng.shuffle(tail)
        self._items = [*head, *tail]

    def _restore_base_order(self) -> None:
        """Volta à ordem original, mantendo a faixa atual como atual."""
        if self._base_order is None:
            return
        current = self.current
        by_uid = {item.uid: item for item in self._items}
        base_set = set(self._base_order)
        ordered = [by_uid[uid] for uid in self._base_order if uid in by_uid]
        ordered.extend(item for item in self._items if item.uid not in base_set)
        self._items = ordered
        self._base_order = None
        if current is None:
            self._index = -1 if not self._items else min(self._index, len(self._items) - 1)
            return
        self._index = next(
            (index for index, item in enumerate(self._items) if item.uid == current.uid),
            -1,
        )
