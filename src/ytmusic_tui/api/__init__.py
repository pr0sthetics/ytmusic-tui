"""Wrapper para API do YouTube Music usando ytmusicapi (T10-R1/R3).

Cliente anônimo (sem login): apenas os métodos que o app usa são expostos —
``search_catalog``, ``get_album``, ``get_radio_tracks`` e
``get_stream_url``. Os métodos sem consumidor (``search`` genérico,
get_song/artist/playlist, home e moods) foram removidos no T10-R1 (Q-A): se
um recurso precisar deles, eles voltam tipados, dirigidos pelo uso real.
``get_watch_playlist`` voltou como ``get_radio_tracks`` para a rádio da fila
(P05).

Nenhuma camada acima de ``api/`` vê objeto do ytmusicapi: as respostas cruas
viram modelos internos (``api.models``) nos adaptadores (``api.adapt``) —
um teste de fonte garante a fronteira. Funções de conta (curtidas,
biblioteca remota, playlists remotas) seguem omitidas — com o cliente
anônimo o ytmusicapi responde ``YTMusicUserError: Please provide
authentication``; a biblioteca do app é local (SQLite).

Resiliência (T10-R3): a sessão própria aplica timeouts separados de
conexão × leitura (o default do ytmusicapi é um timeout único de 30s) e
cada chamada passa pela política de retry em ``api.retry`` — só falha
transitória re-tenta, com backoff e teto; a busca aceita ``should_abort``
para o usuário abandonar as tentativas (a primeira sempre roda, A1).

Cache (T10-R4): busca (TTL 5 min) e álbum (TTL 24h) são servidos do disco
quando recentes — o arquivo guarda o payload dos modelos (``api.models``),
nunca URL de stream; falha de cache nunca quebra a operação (best-effort).

``get_stream_url`` é o único ponto público de URL de watch (T11-R1): ele
delega para o módulo-folha ``urls``, o único lugar do ``src/`` onde o
literal da URL existe — o player e o download também pegam de lá.
"""

import functools
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar, cast

import requests
from ytmusicapi import YTMusic

from ytmusic_tui.api.adapt import (
    album_detail_from_raw,
    radio_tracks_from_raw,
    search_result_from_raws,
)
from ytmusic_tui.api.cache import ResponseCache, cache_key
from ytmusic_tui.api.models import AlbumDetail, SearchResult, Track
from ytmusic_tui.api.retry import RetryPolicy, call_with_retry
from ytmusic_tui.config import settings
from ytmusic_tui.errors import InvalidResponseError
from ytmusic_tui.urls import watch_url

__all__ = ["AlbumDetail", "SearchResult", "YTMusicAPI", "ytmusic"]

logger = logging.getLogger("ytmusic_tui.api")

T = TypeVar("T")

#: Timeouts separados (T10-R3), em segundos: conexão × leitura.
#:
#: Pior caso documentado (Q-C): cada tentativa pode gastar 5s conectando +
#: 15s lendo = 20s; 3 tentativas + backoff de 1,5s ≈ **61,5s** até desistir
#: de uma busca travada — a busca típica leva ~2s, e o usuário abandona
#: antes via ``should_abort`` (nova busca).
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 15.0


def _new_session() -> requests.Session:
    """Sessão requests com timeouts separados — seam do ytmusicapi.

    O ``YTMusic`` aceita ``requests_session`` (API pública documentada pelo
    próprio ytmusicapi); a sessão própria substitui o timeout único de 30s
    do default. O ``partial`` existe porque o ytmusicapi chama
    ``session.post/get`` sem passar ``timeout``.
    """
    session = requests.Session()
    session.request = functools.partial(  # type: ignore[method-assign]
        session.request, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
    )
    return session


class YTMusicAPI:
    """Cliente para API do YouTube Music (fronteira tipada do T10-R1/R3)."""

    def __init__(
        self,
        *,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cache: ResponseCache | None = None,
    ) -> None:
        self._client: YTMusic | None = None
        self._initialized = False
        self.last_error: str = ""
        self._policy = policy or RetryPolicy()
        self._sleep = sleep
        self._cache = cache
        # A14: um cliente/sessão POR THREAD. ``requests.Session`` e o
        # ``YTMusic`` (visitor id/cookies) não têm garantia de thread-safety;
        # a busca dispara três chamadas em paralelo. O ``ThreadPoolExecutor``
        # é reaproveitado entre buscas, então cada worker reutiliza o próprio
        # cliente — isolamento sem criar cliente por requisição.
        self._local = threading.local()
        self._clients: list[YTMusic] = []
        self._clients_lock = threading.Lock()
        #: Cliente injetado por testes de transporte (L2): vale para todas as
        #: threads, inclusive as do pool de busca.
        self._override_client: YTMusic | None = None

    def _use_client(self, client: YTMusic) -> None:
        """Injeta um cliente de transporte para testes (L2), em todas as threads."""
        self._override_client = client
        self._local.client = client
        self._client = client
        self._initialized = True

    def initialize(self) -> bool:
        """Inicializa o cliente YTMusic da THREAD ATUAL em modo anônimo.

        A autenticação OAuth do ``ytmusicapi`` está quebrada no lado do Google
        desde ago/2025 (sigma67/ytmusicapi#813): os tokens OAuth são rejeitados
        pelos endpoints internos do YouTube Music com ``HTTP 400 - invalid
        argument``. O cliente anônimo cobre busca, reprodução e download; a
        biblioteca local continua em SQLite.

        A14: cada thread tem o próprio ``YTMusic``/``requests.Session`` — a
        thread da UI e os três workers de busca não compartilham cookies,
        visitor id nem sessão.
        """
        client = cast("YTMusic | None", getattr(self._local, "client", None))
        if client is not None:
            return True

        try:
            # YTMusic() anônimo não faz chamadas de rede no construtor; o
            # visitor id só é obtido de forma lazy na primeira requisição.
            # A sessão própria aplica os timeouts separados (T10-R3).
            client = YTMusic(requests_session=_new_session())
            self._local.client = client
            with self._clients_lock:
                self._clients.append(client)
            self._client = client
            self._initialized = True
            self.last_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self._initialized = False
            self._client = None
            self.last_error = str(e)
            return False

    def reset(self) -> None:
        """Descarta os clientes (todas as threads) para permitir reinicialização."""
        self._local.client = None
        self._override_client = None
        self._client = None
        self._initialized = False
        with self._clients_lock:
            self._clients.clear()

    def _require_client(self) -> YTMusic:
        """Cliente subjacente da thread atual — privado (não sai daqui)."""
        if self._override_client is not None:
            return self._override_client
        client = cast("YTMusic | None", getattr(self._local, "client", None))
        if client is None:
            if not self.initialize():
                raise RuntimeError("YTMusic não inicializado.")
            client = cast("YTMusic | None", getattr(self._local, "client", None))
        if not client:
            raise RuntimeError("YTMusic não inicializado.")
        return client

    def _get_cache(self) -> ResponseCache:
        """Cache em disco (lazy: o diretório sai do ``settings`` na 1ª leitura)."""
        if self._cache is None:
            self._cache = ResponseCache(settings.cache_dir / "api")
        return self._cache

    def _call(
        self,
        operation: Callable[[], T],
        *,
        context: str,
        should_abort: Callable[[], bool] | None = None,
    ) -> T:
        """Executa ``operation`` com retry/classificação da política (T10-R3)."""
        return call_with_retry(
            operation,
            policy=self._policy,
            context=context,
            sleep=self._sleep,
            should_abort=should_abort,
        )

    def search_catalog(
        self,
        query: str,
        songs_limit: int = 25,
        albums_limit: int = 15,
        videos_limit: int = 20,
        should_abort: Callable[[], bool] | None = None,
    ) -> SearchResult:
        """Busca músicas, vídeos e álbuns em paralelo (três requisições).

        A busca sem filtro capa cada categoria em ~5 itens (ruim para a lista
        de músicas) e a versão sequencial custaria o triplo da latência; em
        paralelo o tempo total é o de uma requisição só (~2s, medido).

        A saída é sempre ``SearchResult`` com modelos internos (T10-R1); a
        validação de forma do T08 roda dentro de ``search_result_from_raws``.
        ``should_abort`` corta as NOVAS tentativas quando o usuário já
        abandonou a busca (T10-R3; a primeira tentativa sempre roda, A1).
        Busca recente (TTL 5 min, T10-R4) sai do cache: zero rede.
        """
        cache = self._get_cache()
        key = cache_key(query, songs_limit, albums_limit, videos_limit)
        cached = cache.get("search", key)
        if cached is not None:
            # §13.2/T13: registra o fato, nunca a consulta do usuário.
            logger.debug("cache hit na busca")
            return SearchResult.from_payload(cached)

        # A14: NÃO resolve o cliente na thread da UI — cada worker resolve o
        # próprio dentro de ``_call``. Criar aqui um cliente extra só gastaria
        # sessão/visitor id sem uso.
        context = f'busca "{query}"'

        pool = ThreadPoolExecutor(max_workers=3)
        futures = [
            pool.submit(
                self._call,
                lambda filtro=filtro, limite=limite: self._require_client().search(
                    query, filter=filtro, limit=limite
                ),
                context=context,
                should_abort=should_abort,
            )
            for filtro, limite in (
                ("songs", songs_limit),
                ("albums", albums_limit),
                ("videos", videos_limit),
            )
        ]
        try:
            result = search_result_from_raws(*(future.result() for future in futures))
        except Exception:
            # A14: uma falha não espera as outras chamadas — as pendentes são
            # canceladas e o pool não é aguardado no encerramento (as que já
            # estão rodando terminam em background, sem bloquear a UI).
            for future in futures:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        cache.put("search", key, result.to_payload())
        return result

    def get_album(self, browse_id: str) -> AlbumDetail:
        """Obtém detalhes do álbum como modelo interno (T10-R1), com retry.

        Sem ``should_abort`` por design: a carga é disparada por worker
        exclusivo (abrir outro álbum cancela o anterior) e o teto de
        tentativas já limita o esforço; a política fina de "resultado velho
        não sobrescreve novo" é o T12-R2. Álbum recente (TTL 24h, T10-R4)
        sai do cache: zero rede.
        """
        cache = self._get_cache()
        key = cache_key(browse_id)
        cached = cache.get("album", key)
        if cached is not None:
            logger.debug("cache hit no álbum %s", browse_id)
            return AlbumDetail.from_payload(cached)

        raw = self._call(
            lambda: self._require_client().get_album(browse_id),
            context=f"álbum {browse_id}",
        )
        if not isinstance(raw, dict):
            raise InvalidResponseError(
                f"resposta inesperada da API: esperado dict, recebido {type(raw).__name__}"
            )
        detail = album_detail_from_raw(raw)
        cache.put("album", key, detail.to_payload())
        return detail

    def get_radio_tracks(self, video_id: str, limit: int = 25) -> tuple[Track, ...]:
        """Faixas da rádio da música, na ordem de relevância do serviço.

        É o "song radio" do YouTube Music: a mesma lista que o serviço usaria
        para continuar a partir desta faixa. A ordem chega pronta do serviço
        (relevância + popularidade); a camada de cima só aplica dedupe. Sem
        ``should_abort``: a chamada roda num worker de background e é curta
        (uma requisição), como ``get_album``; uma troca de faixa descarta o
        resultado por época no controller.
        """
        raw = self._call(
            lambda: self._require_client().get_watch_playlist(
                videoId=video_id,
                radio=True,
                limit=limit,
            ),
            context=f"rádio de {video_id}",
        )
        return radio_tracks_from_raw(raw)

    def get_stream_url(self, video_id: str) -> str:
        """URL de watch para reprodução/download via yt-dlp.

        O ytmusicapi não expõe uma URL de stream direta; a resolução é
        delegada ao yt-dlp, embutido no mpv, a partir da URL de watch.
        Delega ao módulo-folha ``urls`` (T11-R1) — a UI e o download
        compartilham a mesma montagem.
        """
        return watch_url(video_id)


# Instância global
ytmusic = YTMusicAPI()
