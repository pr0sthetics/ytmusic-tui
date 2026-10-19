"""Ponto de entrada do pacote ``ytmusic_tui``.

Permite iniciar a aplicação via console script (``ytmusic-tui``), com
``python -m ytmusic_tui`` ou ``python main.py``. Sem argumentos abre a TUI
(``ytmusic_tui.tui.app.main``); o subcomando ``doctor`` roda o diagnóstico
do ambiente (``ytmusic_tui.doctor``) sem abrir a interface e ``--version``
imprime a versão instalada (§13.3/T13; T16).
"""

import argparse
import sys

from ytmusic_tui import __version__

__all__ = ["main"]


def _report_invalid_configuration(message: str) -> int:
    """Imprime configuração inválida em 3 partes e devolve exit 1 (A02).

    Nunca traceback: o usuário vê o arquivo, o campo e o valor esperado.
    """
    print(f"ytmusic-tui não pode iniciar:\n{message}", file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    """CLI mínima: sem comando abre a TUI; ``doctor`` e ``--version`` informam."""
    parser = argparse.ArgumentParser(
        prog="ytmusic-tui",
        description=(
            "YouTube Music no terminal. Sem argumentos abre a TUI; "
            "`doctor` verifica o ambiente sem abrir a interface."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("doctor",),
        help="comando opcional (padrão: abrir a TUI)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="mostra a versão instalada e sai",
    )
    return parser


def _doctor_exit_code() -> int:
    """Roda o ``doctor``; falha ao carregar a configuração vira diagnóstico."""
    from ytmusic_tui.errors import ConfigError

    try:
        from ytmusic_tui.doctor import run_doctor
    except ConfigError as exc:  # pragma: no cover - depende do sistema
        print(
            f"ytmusic-tui doctor não conseguiu preparar os diretórios do usuário: {exc}\n"
            "Verifique as permissões dos diretórios de dados/configuração "
            "(`YTMUSIC_DATA_DIR`, `YTMUSIC_CONFIG_DIR`).",
            file=sys.stderr,
        )
        return 1
    return run_doctor()


def main(argv: list[str] | None = None) -> None:
    """Inicia a TUI (padrão) ou roda o ``doctor``; sem mpv, mensagem amigável.

    O import de ``mpv`` (python-mpv) carrega a libmpv do sistema e falha com
    ``OSError`` se ela não existir — o melhor primeiro contato possível com
    quem acabou de instalar o app é uma mensagem clara, não um traceback.
    ``ConfigError`` (diretório de dados inacessível etc.) segue o mesmo
    princípio: mensagem curta e saída limpa (§8.3/T06).
    """
    args = _build_parser().parse_args(argv)
    if args.command == "doctor":
        raise SystemExit(_doctor_exit_code())

    # A02: configuração inválida (TOML/ambiente) é recusada ANTES de importar
    # Textual/mpv — mensagem clara, exit 1, nenhum traceback. O ``settings``
    # global carregou um fallback para o import não explodir; nada roda com
    # ele sem passar por esta checagem. O import em si pode falhar com
    # ``ConfigError`` (diretório de dados sem permissão, por exemplo): esse
    # caso também vira mensagem amigável, nunca traceback.
    from ytmusic_tui.errors import ConfigError

    try:
        from ytmusic_tui.config import configuration_error
    except ConfigError as exc:  # pragma: no cover - depende do sistema
        print(
            f"ytmusic-tui não conseguiu preparar os diretórios do usuário: {exc}\n"
            "Verifique as permissões dos diretórios de dados/configuração "
            "(`YTMUSIC_DATA_DIR`, `YTMUSIC_CONFIG_DIR`).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    if (config_error := configuration_error()) is not None:
        raise SystemExit(_report_invalid_configuration(str(config_error)))

    try:
        from ytmusic_tui.tui.app import main as run
    except OSError as exc:  # pragma: no cover - depende do sistema
        print(
            "ytmusic-tui precisa do player mpv instalado (o pacote `mpv` da sua "
            "distro traz a libmpv):\n"
            "  Debian/Ubuntu/Mint/LMDE:  sudo apt install mpv libmpv-dev\n"
            "  Fedora:                   sudo dnf install mpv\n"
            "  Arch/Manjaro/EndeavourOS: sudo pacman -S mpv\n"
            f"Detalhe técnico: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except ConfigError as exc:  # pragma: no cover - depende do sistema
        print(
            f"ytmusic-tui não conseguiu preparar os diretórios do usuário: {exc}\n"
            "Verifique as permissões dos diretórios de dados/configuração "
            "(`YTMUSIC_DATA_DIR`, `YTMUSIC_CONFIG_DIR`).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    run()


if __name__ == "__main__":
    main()
