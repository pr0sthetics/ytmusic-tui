"""Erros transversais da aplicação (T06 · §8.3; T10 · §11-R2).

Vive fora de ``config`` para que ``permissions`` e ``config`` possam usá-lo
sem ciclo de import. A entrada do app (``main``) traduz ``ConfigError`` em
mensagem amigável — falha de ambiente nunca vira traceback para o usuário.

A taxonomia da API (T10-R2) dá à UI e ao retry o que cada falha pede:

- ``NetworkError``            — falha de rede/caminho até o serviço (retry);
- ``RateLimitError``          — HTTP 429 (retry);
- ``ServiceUnavailableError`` — o serviço respondeu e recusou (sem retry);
- ``NotFoundError``           — recurso inexistente (HTTP 404, sem retry);
- ``InvalidResponseError``    — resposta fora do contrato (T08);
- ``ExtractionError``         — resposta válida sem dados reproduzíveis; no
  download é a falha do yt-dlp ao extrair a URL (T11-R4 — ex.: "Unable to
  extract", "Sign in to confirm you're not a bot").

``retryable`` como atributo de classe é a política do T10-R3: só falha
transitória (``NetworkError``, ``RateLimitError``, HTTP 5xx) é re-tentada.
O classificador puro (exceção crua → erro interno) vive em
``ytmusic_tui.api.classify``; a tradução da falha de extração do yt-dlp
vive em ``download._extraction_error`` (T11-R4).
"""

__all__ = [
    "APIError",
    "ConfigError",
    "ConfigValidationError",
    "ExtractionError",
    "InvalidResponseError",
    "NetworkError",
    "NotFoundError",
    "RateLimitError",
    "ServiceUnavailableError",
]


class ConfigError(Exception):
    """Falha de configuração/ambiente (diretório, permissão, layout)."""


class ConfigValidationError(ConfigError):
    """Configuração inválida (TOML ou ``YTMUSIC_*``) — A02.

    A mensagem já vem formatada para o usuário final (arquivo, campo, valor
    e o que era esperado). O entry point a imprime e sai com código 1; o
    ``doctor`` a reporta numa linha própria. Nunca vira traceback.
    """


class APIError(Exception):
    """Base dos erros internos da API do YouTube Music (T10-R2).

    Quem chama ``ytmusic_tui.api`` só precisa conhecer esta base para tratar
    uma falha de integração; o subtipo diz o que fazer (re-tentar, avisar,
    desistir) sem expor ``requests``/``ytmusicapi`` a quem está acima.

    ``retryable`` pode ser sobrescrito por instância quando o mesmo subtipo
    cobre casos com política diferente (ex.: ``ServiceUnavailableError`` de
    HTTP 5xx é transitório, mas de HTTP 403 não): ``Erro(msg, retryable=True)``.
    """

    #: A operação pode ser re-tentada com backoff? (política do T10-R3)
    retryable: bool = False

    def __init__(self, *args: object, retryable: bool | None = None) -> None:
        super().__init__(*args)
        if retryable is not None:
            self.retryable = retryable


class NetworkError(APIError):
    """Falha de rede/caminho até o serviço — transitória, re-tentável."""

    retryable = True


class RateLimitError(APIError):
    """O serviço pediu desaceleração (HTTP 429) — re-tentável."""

    retryable = True


class ServiceUnavailableError(APIError):
    """O serviço respondeu, mas não pode atender (5xx, 403, gate, etc.).

    Não re-tentável por padrão: insistir não muda a resposta e pode piorar
    um bloqueio temporário. Casos transitórios vêm como ``NetworkError``
    ou ``RateLimitError`` (HTTP 429).
    """


class NotFoundError(APIError):
    """O recurso pedido não existe no YouTube Music (HTTP 404)."""


class InvalidResponseError(APIError):
    """Resposta da API com formato inesperado (T08 · §10.1; T10-R2 expande).

    A validação de forma (``api._ensure_result_list`` e os adaptadores)
    converte qualquer resposta fora do contrato em erro tipado com
    "esperado × recebido" — a UI mostra o caso, nunca um ``AttributeError``
    genérico. Parsing quebrado do próprio ytmusicapi (``KeyError`` etc. por
    mudança de formato) também cai aqui, via classificador.
    """


class ExtractionError(APIError):
    """Resposta válida, mas sem dados reproduzíveis (produtor: T11-R4).

    No download é a falha do yt-dlp ao EXTRAIR a URL de watch — a primeira
    etapa: nada foi baixado nem publicado (INV-1). Sinais: o tipo
    ``ExtractorError`` ou mensagens estáveis ("Unable to extract", "no
    video formats", "Unsupported URL", "Sign in to confirm"). O player não
    passa por aqui: a mensagem do mpv vai como veio (classificar texto de
    terceiro não é confiável — decisão do T11-R4).
    """
