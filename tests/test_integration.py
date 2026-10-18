"""Integração real (L3) com monitor de contrato (T10-R6) — opt-in.

Para rodar (ex.: para conferir se o YouTube Music mudou algo que quebre o app):

    YTMUSIC_INTEGRATION=1 .venv/bin/python -m pytest tests/test_integration.py -v -s

O ``-s`` mostra o relatório do contrato (ele também vai na mensagem quando um
teste falha). Cada teste de contrato compara a resposta CRUA do serviço com o
que os adaptadores consomem (``tests/contract.py``) e depois adapta de verdade
(``search_result_from_raws``/``album_detail_from_raw``): mudou o formato? O
relatório diz o quê, por campo e por item — atualize as fixtures do L2 antes
de publicar a correção.

Regra de ruído zero (T09/Q12 — a mesma do workflow ``contract.yml``):

- **inconclusivo** (sem rede, serviço fora, rate limit, runner bloqueado) →
  o teste **skipa** com motivo explícito ("inconclusivo: <classe> — sem
  veredito de contrato"); o job agendado fica verde e repetir é o remédio;
- **incompatibilidade de contrato** (o formato mudou de verdade) → **falha**
  com o relatório por campo/item: é o sinal útil, job vermelho;
- demais falhas (``InvalidResponseError`` — resposta fora do contrato do
  ytmusicapi —, 404, bug nosso) também **falham**: um skip verde escondendo
  quebra real seria pior que o ruído.

O agendamento roda este arquivo toda segunda (06:00 UTC) no GitHub
(``contract.yml``); localmente, ele continua opt-in.
"""

import os

import pytest

from tests import contract
from ytmusic_tui.api import ytmusic
from ytmusic_tui.api.adapt import album_detail_from_raw, search_result_from_raws
from ytmusic_tui.api.classify import classify_exception
from ytmusic_tui.errors import NetworkError, RateLimitError, ServiceUnavailableError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("YTMUSIC_INTEGRATION") != "1",
        reason="integração: defina YTMUSIC_INTEGRATION=1 para rodar (usa a rede)",
    ),
]

QUERY = "my dear melancholy"

#: Classes INCONCLUSIVAS (T09/Q12): a falha está no caminho até o serviço, não
#: no formato — não há veredito de contrato a dar (e repetir é o remédio).
_INCONCLUSIVAS = (NetworkError, RateLimitError, ServiceUnavailableError)


def _rede(operacao, *, context: str):
    """Chama a rede real separando inconclusivo × contrato (T09/Q12).

    Falha inconclusiva vira ``pytest.skip`` com o motivo padrão do job
    agendado; o restante sobe **já classificado** (``APIError``) para o pytest
    nomear a classe — contrato quebrado (ou resposta fora do contrato do
    ytmusicapi) não pode virar skip verde.
    """
    try:
        return operacao()
    except Exception as exc:
        error = classify_exception(exc, context=context)
        if isinstance(error, _INCONCLUSIVAS):
            pytest.skip(
                f"inconclusivo: {type(error).__name__} — sem veredito de contrato "
                f"(repita mais tarde; detalhe: {error})"
            )
        raise error from exc


def _initialize() -> None:
    """Garante o cliente anônimo; sem cliente não há contrato a checar."""
    if not _rede(ytmusic.initialize, context="inicialização do cliente"):
        pytest.skip(
            "inconclusivo: cliente ytmusicapi não inicializou — sem veredito de "
            f"contrato (repita mais tarde; detalhe: {ytmusic.last_error or 'sem detalhe'})"
        )


def _raw_search() -> tuple[list, list, list]:
    """Resposta crua da busca (sem adaptadores) para o monitor de contrato.

    Usa o cliente do transporte de propósito: o contrato é entre o SERVIÇO e
    os adaptadores — o caminho público (``search_catalog``) já os aplica.
    """
    _initialize()
    client = ytmusic._require_client()
    songs = _rede(
        lambda: client.search(QUERY, filter="songs", limit=10),
        context=f'busca "{QUERY}" (músicas)',
    )
    albums = _rede(
        lambda: client.search(QUERY, filter="albums", limit=10),
        context=f'busca "{QUERY}" (álbuns)',
    )
    videos = _rede(
        lambda: client.search(QUERY, filter="videos", limit=10),
        context=f'busca "{QUERY}" (vídeos)',
    )
    return songs, albums, videos


def _assert_contract(scope, items, consumed, essential) -> None:
    """Falha com o relatório completo se houver problema de contrato."""
    problems, report = contract.inspect(scope, items, consumed=consumed, essential=essential)
    print("\n" + report)
    assert not problems, f"formato mudou ({scope}) — relatório:\n{report}"


def test_contrato_e_adaptacao_da_busca_real():
    """A busca real devolve o que os adaptadores consomem — e eles adaptam."""
    songs, albums, videos = _raw_search()
    _assert_contract("faixa de busca", songs, contract.CONSUMED_TRACK, contract.ESSENTIAL_TRACK)
    _assert_contract("álbum de busca", albums, contract.CONSUMED_ALBUM, contract.ESSENTIAL_ALBUM)
    # F10: vídeos adaptam por ``track_from_raw`` — o mesmo contrato da faixa.
    _assert_contract("vídeo de busca", videos, contract.CONSUMED_TRACK, contract.ESSENTIAL_TRACK)

    result = search_result_from_raws(songs, albums, videos)
    assert result.tracks, "esperava músicas no resultado"
    assert result.albums, "esperava álbuns no resultado"
    assert result.videos, "esperava vídeos no resultado (F10)"
    assert all(track.video_id for track in result.tracks)
    assert all(album.browse_id for album in result.albums)
    assert all(video.video_id for video in result.videos)


def test_contrato_e_adaptacao_do_album_real():
    """Abrir um álbum real devolve faixas tocáveis (como na aba 💿 Álbum)."""
    songs, albums, videos = _raw_search()
    result = search_result_from_raws(songs, albums, videos)
    album = next(
        (item for item in result.albums if "Dear Melancholy" in item.title),
        result.albums[0],
    )

    raw = _rede(
        lambda: ytmusic._require_client().get_album(album.browse_id),
        context=f"álbum {album.browse_id}",
    )
    _assert_contract(
        "álbum (detalhe)",
        [raw],
        contract.CONSUMED_ALBUM_DETAIL,
        contract.ESSENTIAL_ALBUM_DETAIL,
    )
    _assert_contract(
        "faixa do álbum",
        raw.get("tracks") or [],
        contract.CONSUMED_TRACK,
        contract.ESSENTIAL_TRACK,
    )

    details = album_detail_from_raw(raw)
    assert details.title
    playable = [track for track in details.tracks if track.video_id and track.is_available]
    assert playable, "esperava faixas disponíveis no álbum"


def test_fluxo_publico_real():
    """Smoke dos caminhos públicos (retry + cache + modelos) com a rede real."""
    _initialize()
    result = _rede(lambda: ytmusic.search_catalog(QUERY), context=f'busca "{QUERY}"')

    assert result.tracks, "esperava músicas no resultado"
    assert result.albums, "esperava álbuns no resultado"
    assert all(track.video_id for track in result.tracks)
    assert all(album.browse_id for album in result.albums)

    album = next(
        (item for item in result.albums if "Dear Melancholy" in item.title),
        result.albums[0],
    )
    details = _rede(
        lambda: ytmusic.get_album(album.browse_id),
        context=f"álbum {album.browse_id}",
    )

    assert details.title
    playable = [track for track in details.tracks if track.video_id and track.is_available]
    assert playable, "esperava faixas disponíveis no álbum"
