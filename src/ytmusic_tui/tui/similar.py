"""Escolha da próxima faixa da rádio (P05) — sem UI, rede ou Textual.

O YouTube Music já devolve a rádio de uma música ordenada por relevância e
popularidade; este módulo não inventa um score. Ele só aplica as regras de
produto que faltam: pular a própria semente, faixas indisponíveis e o que
acabou de tocar — e devolver a primeira candidata elegível.

A ausência de aleatoriedade é deliberada: a mesma rádio produz a mesma
continuação, o que mantém o comportamento previsível, testável e fácil de
explicar. A variedade vem da própria rádio e da troca de semente a cada
faixa, não de um sorteio.
"""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from ytmusic_tui.api.models import Track

#: Quantas faixas recentes a sessão lembra para não repetir (P05: dedupe).
RECENT_LIMIT = 50


@dataclass
class SimilarPicker:
    """Escolhe uma faixa parecida, em ordem de relevância da rádio."""

    recent: deque[str] = field(default_factory=lambda: deque(maxlen=RECENT_LIMIT))

    def remember(self, video_id: str) -> None:
        """Registra uma faixa tocada; ids vazios são ignorados.

        Recolocar um id já visto move-o para o fim: o dedupe é "as últimas
        N", não "alguma vez na sessão".
        """
        if not video_id:
            return
        with contextlib.suppress(ValueError):
            self.recent.remove(video_id)
        self.recent.append(video_id)

    def pick(self, candidates: Iterable[Track], seed_video_id: str) -> Track | None:
        """Primeira candidata elegível, na ordem em que a rádio devolveu.

        A ordem da rádio é o sinal de popularidade/relevância; não há
        filtro por views porque um gênero de nicho teria números baixos e
        seria injustamente descartado. Se todas as candidatas do topo forem
        repetidas, a busca desce na mesma lista — nunca repete.
        """
        for track in candidates:
            if not track.video_id or not track.is_available:
                continue
            if track.video_id == seed_video_id:
                continue
            if track.video_id in self.recent:
                continue
            return track
        return None
