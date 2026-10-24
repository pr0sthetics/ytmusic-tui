"""Testes da rádio automática (Fase 2/P05).

Cobrem a escolha pura (ordem, dedupe, semente), o contrato da fila e o
controller no fim da fila. Nada aqui toca a rede, o mpv ou o Textual: a
rádio chega como uma sequência de modelos e o player é substituído.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ytmusic_tui.api import ytmusic
from ytmusic_tui.api.models import Track
from ytmusic_tui.config import settings
from ytmusic_tui.player import PlayerState, TrackInfo, player
from ytmusic_tui.tui.messages import SIMILAR_SEARCHING, SIMILAR_UNAVAILABLE
from ytmusic_tui.tui.playback_controller import PlaybackController
from ytmusic_tui.tui.queue import PlaybackQueue, QueueItem, RepeatMode
from ytmusic_tui.tui.similar import RECENT_LIMIT, SimilarPicker


def make_track(video_id: str, *, available: bool = True) -> Track:
    return Track(video_id=video_id, title=video_id, is_available=available)


def make_item(video_id: str, *, source: str = "search") -> QueueItem:
    return QueueItem(info=TrackInfo(video_id=video_id, title=video_id), source=source)


# ===== SimilarPicker =====


def test_pick_returns_first_eligible_in_radio_order() -> None:
    """A ordem da rádio é o sinal: a primeira elegível vence, sem sorteio."""
    picker = SimilarPicker()
    candidates = [
        make_track(""),  # sem id: não tocável
        make_track("seed"),  # a própria semente
        make_track("off", available=False),  # indisponível
        make_track("first"),
        make_track("second"),
    ]

    assert picker.pick(candidates, "seed") == candidates[3]


def test_pick_skips_recent_tracks() -> None:
    picker = SimilarPicker()
    picker.remember("played")

    picked = picker.pick([make_track("played"), make_track("new")], "seed")

    assert picked is not None
    assert picked.video_id == "new"


def test_pick_returns_none_when_everything_is_excluded() -> None:
    picker = SimilarPicker()
    picker.remember("played")

    assert picker.pick([make_track("seed"), make_track("played")], "seed") is None


def test_remember_keeps_only_the_last_limit_and_moves_duplicates() -> None:
    picker = SimilarPicker()
    for number in range(RECENT_LIMIT + 5):
        picker.remember(f"v{number}")

    assert len(picker.recent) == RECENT_LIMIT
    assert "v0" not in picker.recent
    assert f"v{RECENT_LIMIT + 4}" in picker.recent

    picker.remember("v5")
    assert picker.recent[-1] == "v5"
    assert list(picker.recent).count("v5") == 1


def test_remember_ignores_empty_ids() -> None:
    picker = SimilarPicker()

    picker.remember("")

    assert list(picker.recent) == []


# ===== Fila =====


def test_queue_append_preserves_current_and_returns_index() -> None:
    queue = PlaybackQueue()
    first, second = make_item("a"), make_item("b")
    queue.set_items([first], start=0)

    index = queue.append(second)

    assert index == 1
    assert queue.current is first
    assert queue.items == (first, second)


def test_queue_append_rejects_duplicate_uid() -> None:
    queue = PlaybackQueue()
    item = make_item("a")
    queue.set_items([item], start=0)

    with pytest.raises(ValueError, match="uid repetido"):
        queue.append(item)


# ===== Controller: gatilho no fim da fila =====


class _Host(PlaybackController):
    """Host mínimo do mixin: fila real, player e worker falsos."""

    def __init__(self) -> None:
        self.queue: PlaybackQueue = PlaybackQueue()
        self.similar: SimilarPicker = SimilarPicker()
        self._last_auto_next: float = 0.0
        self._similar_token: int = 0
        self.app: Any = SimpleNamespace()
        self.toasts: list[str] = []
        self.workers: list[tuple[Any, dict[str, Any]]] = []
        self.played: list[TrackInfo] = []

    def _toast(self, message: str) -> None:
        self.toasts.append(message)

    def run_worker(self, *args: Any, **kwargs: Any) -> None:
        self.workers.append((args[0], kwargs))

    def _start_playback(self, info: TrackInfo) -> bool:
        self.played.append(info)
        self.similar.remember(info.video_id)
        return True

    def close_workers(self) -> None:
        """Fecha corrotinas agendadas que o teste não chegou a rodar."""
        for awaitable, _ in self.workers:
            awaitable.close()
        self.workers.clear()


@pytest.fixture
def controlled_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rádio ligada, master switch ligado e player parado (fim de faixa)."""
    monkeypatch.setattr(settings, "auto_play_next", True)
    monkeypatch.setattr(settings, "auto_similar", True)
    monkeypatch.setattr(player, "_state", PlayerState.STOPPED)


def test_auto_next_plays_the_next_queue_item_without_radio(
    controlled_settings: None,
) -> None:
    host = _Host()
    host.queue.set_items([make_item("a"), make_item("b")], start=0)

    assert host._maybe_auto_next() is True
    assert [info.video_id for info in host.played] == ["b"]
    assert host.workers == []


def test_auto_next_at_queue_end_requests_radio(controlled_settings: None) -> None:
    host = _Host()
    host.queue.set_items([make_item("seed")], start=0)

    assert host._maybe_auto_next() is True
    assert host.toasts == [SIMILAR_SEARCHING]
    assert len(host.workers) == 1
    assert host.workers[0][1]["group"] == "similar"
    host.close_workers()


def test_auto_next_respects_auto_similar_off(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auto_similar", False)
    host = _Host()
    host.queue.set_items([make_item("seed")], start=0)

    assert host._maybe_auto_next() is False
    assert host.toasts == []
    assert host.workers == []


def test_auto_next_respects_master_switch_off(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auto_play_next", False)
    host = _Host()
    host.queue.set_items([make_item("seed")], start=0)

    assert host._maybe_auto_next() is False
    assert host.workers == []


def test_repeat_all_keeps_wrapping_instead_of_radio(controlled_settings: None) -> None:
    host = _Host()
    host.queue.set_items([make_item("a"), make_item("b")], start=1)
    assert host.queue.cycle_repeat() is RepeatMode.ALL

    assert host._maybe_auto_next() is True
    assert [info.video_id for info in host.played] == ["a"]
    assert host.workers == []


# ===== Controller: busca em background =====


async def test_fetch_similar_appends_radio_item_and_plays(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed = make_item("seed")
    host.queue.set_items([seed], start=0)
    monkeypatch.setattr(
        ytmusic,
        "get_radio_tracks",
        lambda video_id, limit=25: (make_track("radio"),),
    )

    await host._fetch_similar(seed, host._similar_token)

    assert [item.info.video_id for item in host.queue.items] == ["seed", "radio"]
    assert host.queue.current is host.queue.items[1]
    assert host.queue.current.source == "radio"
    assert [info.video_id for info in host.played] == ["radio"]
    assert "radio" in host.similar.recent


async def test_fetch_similar_failure_toasts_and_keeps_queue(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed = make_item("seed")
    host.queue.set_items([seed], start=0)

    def boom(video_id: str, limit: int = 25) -> tuple[Track, ...]:
        raise RuntimeError("sem rede")

    monkeypatch.setattr(ytmusic, "get_radio_tracks", boom)

    await host._fetch_similar(seed, host._similar_token)

    assert host.queue.items == (seed,)
    assert host.queue.current is seed
    assert host.toasts == [SIMILAR_UNAVAILABLE]
    assert host.played == []


async def test_fetch_similar_without_candidates_toasts_and_keeps_queue(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed = make_item("seed")
    host.queue.set_items([seed], start=0)
    monkeypatch.setattr(ytmusic, "get_radio_tracks", lambda video_id, limit=25: ())

    await host._fetch_similar(seed, host._similar_token)

    assert host.queue.items == (seed,)
    assert host.toasts == [SIMILAR_UNAVAILABLE]


async def test_fetch_similar_discards_stale_token(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed = make_item("seed")
    host.queue.set_items([seed], start=0)
    monkeypatch.setattr(
        ytmusic,
        "get_radio_tracks",
        lambda video_id, limit=25: (make_track("radio"),),
    )

    await host._fetch_similar(seed, host._similar_token - 1)

    assert host.queue.items == (seed,)
    assert host.toasts == []
    assert host.played == []


async def test_fetch_similar_respects_setting_toggled_off_mid_flight(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed = make_item("seed")
    host.queue.set_items([seed], start=0)
    monkeypatch.setattr(
        ytmusic,
        "get_radio_tracks",
        lambda video_id, limit=25: (make_track("radio"),),
    )
    monkeypatch.setattr(settings, "auto_similar", False)

    await host._fetch_similar(seed, host._similar_token)

    assert host.queue.items == (seed,)
    assert host.played == []


async def test_fetch_similar_discards_if_user_changed_track(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed, other = make_item("seed"), make_item("other")
    host.queue.set_items([seed, other], start=0)
    host.queue.play_at(1)
    monkeypatch.setattr(
        ytmusic,
        "get_radio_tracks",
        lambda video_id, limit=25: (make_track("radio"),),
    )

    await host._fetch_similar(seed, host._similar_token)

    assert len(host.queue) == 2
    assert host.played == []


async def test_fetch_similar_discards_if_playback_resumed(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed = make_item("seed")
    host.queue.set_items([seed], start=0)
    monkeypatch.setattr(
        ytmusic,
        "get_radio_tracks",
        lambda video_id, limit=25: (make_track("radio"),),
    )
    monkeypatch.setattr(player, "_state", PlayerState.PLAYING)

    await host._fetch_similar(seed, host._similar_token)

    assert host.queue.items == (seed,)
    assert host.played == []


async def test_fetch_similar_dedupes_candidates_against_recent(
    controlled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host()
    seed = make_item("seed")
    host.queue.set_items([seed], start=0)
    host.similar.remember("radio-played")
    monkeypatch.setattr(
        ytmusic,
        "get_radio_tracks",
        lambda video_id, limit=25: (
            make_track("radio-played"),
            make_track("radio-new"),
        ),
    )

    await host._fetch_similar(seed, host._similar_token)

    assert host.queue.current is not None
    assert host.queue.current.info.video_id == "radio-new"
