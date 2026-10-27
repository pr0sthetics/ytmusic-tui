"""Testes da fila pura (Fase 2 — P01/P03)."""

import random

import pytest

from ytmusic_tui.player import TrackInfo
from ytmusic_tui.tui.queue import PlaybackQueue, QueueItem, RepeatMode


def make_item(number: int, *, source: str = "search", track_id: int | None = None) -> QueueItem:
    """Item de teste com identidade previsível."""
    return QueueItem(
        info=TrackInfo(video_id=f"v{number}", title=f"T{number}"),
        source=source,
        track_id=track_id,
    )


def uids(queue: PlaybackQueue) -> list[int]:
    return [item.uid for item in queue.items]


def test_empty_queue_has_no_current() -> None:
    queue = PlaybackQueue()

    assert len(queue) == 0
    assert queue.current is None
    assert queue.index == -1
    assert queue.at_start is True
    assert queue.next() is None
    assert queue.previous() is None
    assert queue.play_at(0) is None


def test_set_items_clamps_start_and_preserves_payload() -> None:
    items = [
        make_item(0, source="library", track_id=10),
        make_item(1, source="playlist:3"),
        make_item(2),
    ]
    queue = PlaybackQueue()

    queue.set_items(items, start=1)

    assert queue.current is items[1]
    assert queue.index == 1
    assert queue.items == tuple(items)
    assert queue.current is not None
    assert queue.current.track_id is None
    assert queue.current.source == "playlist:3"

    queue.set_items(items, start=99)
    assert queue.index == 2

    queue.set_items([], start=0)
    assert queue.index == -1
    assert queue.current is None


def test_manual_next_and_previous() -> None:
    items = [make_item(number) for number in range(3)]
    queue = PlaybackQueue()
    queue.set_items(items)

    assert queue.next(auto=False) is items[1]
    assert queue.next(auto=False) is items[2]
    assert queue.next(auto=False) is None
    assert queue.index == 2
    assert queue.previous() is items[1]
    assert queue.previous() is items[0]


def test_previous_at_start_signals_restart() -> None:
    items = [make_item(number) for number in range(2)]
    queue = PlaybackQueue()
    queue.set_items(items)

    assert queue.previous() is items[0]
    assert queue.index == 0


def test_previous_without_current_is_noop() -> None:
    queue = PlaybackQueue()
    items = [make_item(0), make_item(1)]
    queue.set_items(items, start=1)
    assert queue.remove(items[1].uid) is True

    assert queue.current is None
    assert queue.previous() is None


def test_repeat_one_repeats_only_on_auto() -> None:
    items = [make_item(number) for number in range(3)]
    queue = PlaybackQueue()
    queue.set_items(items)
    queue.set_repeat(RepeatMode.ONE)

    assert queue.next() is items[0]
    assert queue.next(auto=False) is items[1]
    assert queue.next() is items[1]

    queue.play_at(2)
    assert queue.next() is items[2]


def test_repeat_all_wraps_on_auto_and_manual() -> None:
    items = [make_item(number) for number in range(3)]
    queue = PlaybackQueue()
    queue.set_items(items, start=2)
    queue.set_repeat(RepeatMode.ALL)

    assert queue.next() is items[0]
    queue.play_at(2)
    assert queue.next(auto=False) is items[0]


def test_repeat_off_stops_at_end() -> None:
    items = [make_item(number) for number in range(2)]
    queue = PlaybackQueue()
    queue.set_items(items, start=1)

    assert queue.next() is None
    assert queue.index == 1
    assert queue.current is items[1]


def test_cycle_repeat_order() -> None:
    queue = PlaybackQueue()

    assert queue.repeat is RepeatMode.OFF
    assert queue.cycle_repeat() is RepeatMode.ALL
    assert queue.cycle_repeat() is RepeatMode.ONE
    assert queue.cycle_repeat() is RepeatMode.OFF


def test_remove_before_current_keeps_playing_item() -> None:
    items = [make_item(number) for number in range(4)]
    queue = PlaybackQueue()
    queue.set_items(items, start=1)

    assert queue.remove(items[0].uid) is True
    assert queue.current is items[1]
    assert queue.index == 0
    assert uids(queue) == [items[1].uid, items[2].uid, items[3].uid]


def test_remove_current_moves_to_next() -> None:
    items = [make_item(number) for number in range(4)]
    queue = PlaybackQueue()
    queue.set_items(items, start=1)

    assert queue.remove(items[1].uid) is True
    assert queue.current is items[2]
    assert queue.index == 1


def test_remove_last_current_stops_with_items_remaining() -> None:
    items = [make_item(number) for number in range(3)]
    queue = PlaybackQueue()
    queue.set_items(items, start=2)

    assert queue.remove(items[2].uid) is True
    assert queue.current is None
    assert queue.index == -1
    assert uids(queue) == [items[0].uid, items[1].uid]


def test_remove_unknown_uid_is_noop() -> None:
    queue = PlaybackQueue()
    queue.set_items([make_item(0)])

    assert queue.remove(999999) is False
    assert len(queue) == 1


def test_move_reorders_and_current_follows() -> None:
    items = [make_item(number) for number in range(3)]
    queue = PlaybackQueue()
    queue.set_items(items, start=2)  # [i0, i1, i2], atual = i2

    # i0 vai para o fim: [i1, i2, i0] — a atual (i2) passa a ser índice 1.
    assert queue.move(items[0].uid, 2) is True
    assert uids(queue) == [items[1].uid, items[2].uid, items[0].uid]
    assert queue.current is items[2]
    assert queue.index == 1

    # i0 volta para o começo: [i0, i1, i2] — a atual volta ao índice 2.
    assert queue.move(items[0].uid, -2) is True
    assert uids(queue) == [items[0].uid, items[1].uid, items[2].uid]
    assert queue.current is items[2]
    assert queue.index == 2

    # Mover a própria faixa atual: o índice acompanha o item.
    assert queue.move(items[2].uid, -1) is True
    assert uids(queue) == [items[0].uid, items[2].uid, items[1].uid]
    assert queue.current is items[2]
    assert queue.index == 1


def test_move_out_of_bounds_is_noop() -> None:
    items = [make_item(number) for number in range(2)]
    queue = PlaybackQueue()
    queue.set_items(items)

    assert queue.move(items[0].uid, -1) is False
    assert queue.move(items[1].uid, 1) is False
    assert queue.move(items[0].uid, 0) is False
    assert queue.move(999999, 1) is False


def test_shuffle_keeps_current_and_all_items() -> None:
    items = [make_item(number) for number in range(6)]
    queue = PlaybackQueue(rng=random.Random(7))
    queue.set_items(items)
    original = uids(queue)

    assert queue.toggle_shuffle() is True
    assert queue.current is items[0]
    assert uids(queue) != original
    assert set(uids(queue)) == set(original)

    assert queue.toggle_shuffle() is False
    assert uids(queue) == original
    assert queue.current is items[0]


def test_shuffle_visits_each_item_once() -> None:
    items = [make_item(number) for number in range(6)]
    queue = PlaybackQueue(rng=random.Random(13))
    queue.set_items(items)
    queue.toggle_shuffle()

    seen = [queue.current.uid] if queue.current else []
    while (item := queue.next(auto=False)) is not None:
        seen.append(item.uid)

    assert len(seen) == len(items)
    assert len(set(seen)) == len(items)


def test_shuffle_after_current_keeps_played_prefix() -> None:
    items = [make_item(number) for number in range(5)]
    queue = PlaybackQueue(rng=random.Random(3))
    queue.set_items(items, start=1)
    queue.next(auto=False)  # current = item 2

    queue.toggle_shuffle()
    assert [item.uid for item in queue.items[:2]] == [items[0].uid, items[1].uid]
    assert queue.current is items[2]


def test_new_queue_with_shuffle_starts_with_selected() -> None:
    items = [make_item(number) for number in range(5)]
    queue = PlaybackQueue(rng=random.Random(5))
    queue.toggle_shuffle()

    queue.set_items(items, start=3)

    assert queue.shuffle is True
    assert queue.index == 0
    assert queue.current is items[3]
    assert set(uids(queue)) == {item.uid for item in items}


def test_listeners_fire_and_can_be_removed() -> None:
    queue = PlaybackQueue()
    calls: list[int] = []

    def listener() -> None:
        calls.append(1)

    queue.add_listener(listener)
    queue.set_items([make_item(0)])
    assert len(calls) == 1

    queue.next(auto=False)
    assert len(calls) == 1  # já está na última: sem mudança

    queue.remove_listener(listener)
    queue.clear()
    assert len(calls) == 1


def test_listener_failure_does_not_break_queue() -> None:
    queue = PlaybackQueue()
    items = [make_item(0), make_item(1)]

    def boom() -> None:
        raise RuntimeError("falha da UI")

    queue.add_listener(boom)
    queue.set_items(items)

    assert queue.next(auto=False) is items[1]


def test_duplicate_uids_are_rejected() -> None:
    queue = PlaybackQueue()
    item = make_item(0)

    with pytest.raises(ValueError, match="uid repetido"):
        queue.set_items([item, item])


def test_index_stays_valid_after_random_operations() -> None:
    rng = random.Random(17)
    queue = PlaybackQueue(rng=random.Random(19))
    queue.set_items([make_item(number) for number in range(8)], start=4)

    for _ in range(300):
        operation = rng.choice(["next", "previous", "remove", "move", "shuffle", "repeat", "play"])
        if operation == "next":
            queue.next(auto=rng.random() < 0.5)
        elif operation == "previous":
            queue.previous()
        elif operation == "remove" and queue.items:
            queue.remove(rng.choice(queue.items).uid)
        elif operation == "move" and queue.items:
            queue.move(rng.choice(queue.items).uid, rng.choice([-2, -1, 1, 2]))
        elif operation == "shuffle":
            queue.toggle_shuffle()
        elif operation == "repeat":
            queue.cycle_repeat()
        elif operation == "play" and queue.items:
            queue.play_at(rng.randrange(len(queue.items)))

        assert -1 <= queue.index < len(queue)
        if queue.index >= 0:
            assert queue.current is queue.items[queue.index]
        else:
            assert queue.current is None
