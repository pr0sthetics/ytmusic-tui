"""Guardas de escrita da TUI (A06).

Toda escrita na biblioteca feita a partir de um callback/atalho passa por
aqui: erro de banco (lock, disco cheio, esquema) não pode virar exceção no
handler da tela nem fazer a ação sumir em silêncio. A guarda:

1. registra ``log_event`` com ``exc_info`` e ``error_code`` (R0.5);
2. converte o erro tipado em mensagem de domínio (``describe_library_error``)
   ou usa o texto da ``IntegrityError``;
3. mostra um toast em 3 partes (o que falhou · o que foi preservado · o que
   fazer agora).

A operação devolve ``None`` quando falha e o valor dela quando sucede; os
chamadores comparam com ``None`` (ou usam o valor) — nunca deixam a exceção
escapar.
"""

import logging
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError

from ytmusic_tui.library.errors import LibraryError, describe_library_error
from ytmusic_tui.log import log_event
from ytmusic_tui.tui.messages import library_write_error

__all__ = ["guard_write"]

T = TypeVar("T")


def guard_write(
    operation: Callable[[], T],
    *,
    toast: Callable[[str], None],
    component: str,
    event: str,
    what: str,
) -> T | None:
    """Executa ``operation``; falha de escrita vira toast + log, não exceção.

    ``what`` descreve a ação no infinitivo (ex.: "criar a playlist") e entra
    na mensagem do log — o toast já tem o texto de domínio de
    :func:`library_write_error`.
    """
    try:
        return operation()
    except LibraryError as error:
        detail = describe_library_error(error)
        error_code = type(error).__name__
    except IntegrityError as error:
        # Restrição de domínio (ex.: faixa repetida numa corrida): não é
        # falha do banco, mas também não pode virar traceback na TUI.
        detail = str(error.orig or error)
        error_code = "IntegrityError"
    except OSError as error:
        # Disco cheio/permissão ao salvar; o SQLite costuma traduzir, mas a
        # rede de segurança cobre o que escapar.
        detail = str(error)
        error_code = type(error).__name__
    log_event(
        component,
        event,
        f"Falha ao {what}: {detail}",
        level=logging.ERROR,
        error_code=error_code,
        exc_info=True,
    )
    toast(library_write_error(detail))
    return None
