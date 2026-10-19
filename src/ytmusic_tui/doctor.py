"""Diagnóstico do ambiente — comando ``ytmusic-tui doctor`` (§13.3/T13).

Verifica **somente leitura** (nada de probe em disco, banco sem escrita,
nenhum download): versões (app, Python, ytmusicapi, yt-dlp), libmpv e
binários ``mpv``/``ffmpeg``, diretórios de dados (existência, escrita —
aferida sem criar, no ancestral existente mais próximo quando ausente — e
modo), banco da biblioteca em modo ``ro`` (abre? ``PRAGMA
integrity_check``? esquema × versão do app) e os resíduos de manutenção
(quarentena/backups/log: contagem e bytes). Nota: o import da configuração
prepara os diretórios padrão, como qualquer inicialização do app (T06) —
o compromisso "não criar" vale para as verificações em si.

A saída é texto simples (funciona sem TTY), **mascarada** (HOME → ``~``,
URLs sem query/token, credenciais redigidas) e pensada para colar numa
issue — ainda assim, revise antes de compartilhar.

Não importa Textual/TUI de propósito (§13.3): o diagnóstico roda mesmo
quando a interface não sobe.

Exit code: ``0`` = nada essencial quebrado (avisos: binário ausente,
versões ``older``/``newer``, banco ausente, esquema mais antigo);
``1`` = essencial (libmpv, ytmusicapi ou yt-dlp ausentes; diretório
existente sem escrita; banco ilegível, ``integrity_check`` ≠ ``ok`` ou
esquema mais novo que o app).
"""

import importlib
import locale
import os
import shutil
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path

from ytmusic_tui.config import configuration_error, settings
from ytmusic_tui.diagnostics import mask_diagnostics, mask_secrets
from ytmusic_tui.download.pipeline import QUARANTINE_DIR_NAME
from ytmusic_tui.library.migrations import BACKUP_DIR_NAME, SCHEMA_VERSION
from ytmusic_tui.log import log_file_path
from ytmusic_tui.versions import check_dependencies, versions_summary

__all__ = ["run_doctor"]


@dataclass(frozen=True, slots=True)
class _Line:
    """Uma linha do relatório; ``critical`` = motivo para exit 1."""

    text: str
    critical: bool = False


def _which(binary: str) -> str | None:
    """Indireção de ``shutil.which`` — os testes fixam o resultado."""
    return shutil.which(binary)


def _libmpv_error() -> str | None:
    """Importa o python-mpv (que carrega a libmpv do sistema).

    ``None`` = disponível; ``OSError`` vira a mensagem do relatório (é o
    erro real que o app mostraria ao subir sem o pacote do sistema).
    """
    try:
        importlib.import_module("mpv")
    except OSError as exc:
        return str(exc)
    return None


def _check_directory(label: str, path: Path) -> _Line:
    """Existência, escrita (sem criar) e modo de um diretório do app."""
    display = str(path)
    if path.exists():
        if not path.is_dir():
            return _Line(f"{label}: {display} — NÃO é um diretório", critical=True)
        mode = stat.S_IMODE(path.stat().st_mode)
        if not os.access(path, os.W_OK | os.X_OK):
            return _Line(f"{label}: {display} — SEM ESCRITA (modo {mode:04o})", critical=True)
        return _Line(f"{label}: {display} — ok (modo {mode:04o})")

    # Ausente: nada é criado — a escrita é aferida no ancestral mais próximo
    # (criar o diretório para "testar" já seria alterar o sistema).
    ancestor = next((parent for parent in path.parents if parent.exists()), None)
    if ancestor is not None and not os.access(ancestor, os.W_OK | os.X_OK):
        return _Line(
            f"{label}: {display} — não existe e o ancestral mais próximo "
            f"({ancestor}) não tem escrita",
            critical=True,
        )
    return _Line(f"{label}: {display} — não existe ainda (criado na 1ª abertura)")


def _check_database(path: Path) -> list[_Line]:
    """Banco em somente leitura: abre, ``integrity_check`` e esquema × app."""
    display = str(path)
    if not path.exists():
        return [_Line(f"banco: {display} — não existe (criado na 1ª abertura)")]
    try:
        # URI mode=ro: abrir não cria e nada é gravado (nem WAL).
        connection = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            has_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            version = (
                connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                if has_table is not None
                else None
            )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        # Corrompido/inacessível não vira traceback: é o diagnóstico.
        return [_Line(f"banco: {display} — ILEGÍVEL: {exc}", critical=True)]

    result = integrity[0] if integrity else "sem resultado"
    if result != "ok":
        return [_Line(f"banco: {display} — integrity_check REPROVADO: {result}", critical=True)]

    lines = [_Line(f"banco: {display} — integrity ok")]
    if version is None:
        lines.append(_Line("  esquema: sem controle de migração (migra na 1ª abertura)"))
    elif version < SCHEMA_VERSION:
        lines.append(
            _Line(
                f"  esquema: v{version} é mais antigo que o app (v{SCHEMA_VERSION}) "
                "— migra na 1ª abertura, com backup"
            )
        )
    elif version > SCHEMA_VERSION:
        lines.append(
            _Line(
                f"  esquema: v{version} é MAIS NOVO que o app (v{SCHEMA_VERSION}) "
                "— atualize o ytmusic-tui",
                critical=True,
            )
        )
    else:
        lines.append(_Line(f"  esquema: v{version} (atual)"))
    return lines


def _check_dependencies() -> list[_Line]:
    """ytmusicapi/yt-dlp: ausente é crítico; divergente é aviso."""
    return [
        _Line(status.describe(), critical=status.state == "missing")
        for status in check_dependencies()
    ]


def _check_locale() -> _Line:
    """Encoding preferido e variáveis de locale do processo."""
    variables = " · ".join(
        f"{name}={os.environ.get(name) or '(vazio)'}" for name in ("LANG", "LC_ALL", "LC_CTYPE")
    )
    return _Line(f"locale: encoding={locale.getpreferredencoding(False)} · {variables}")


def _folder_summary(path: Path) -> str:
    """Contagem e bytes dos arquivos da pasta (ou por que não deu para medir)."""
    if not path.exists():
        return "não existe"
    if not path.is_dir():
        return "não é um diretório"
    count = 0
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    count += 1
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError as exc:
        return f"ilegível: {exc}"
    return f"{count} arquivo(s), {total} byte(s)"


def _collect() -> list[_Line]:
    """Todas as verificações, na ordem em que aparecem no relatório."""
    lines: list[_Line] = [_Line(f"versões: {versions_summary()}")]

    libmpv_error = _libmpv_error()
    if libmpv_error is None:
        lines.append(_Line("libmpv: ok"))
    else:
        lines.append(_Line(f"libmpv: AUSENTE — {libmpv_error}", critical=True))

    for binary in ("mpv", "ffmpeg"):
        found = _which(binary)
        lines.append(
            _Line(f"binário {binary}: {found}" if found else f"binário {binary}: ausente no PATH")
        )
    lines.append(_Line(""))

    # A02: configuração inválida tem linha própria e motivo completo. As
    # verificações de diretórios/banco dependem de valores confiáveis do
    # TOML — pulá-las é mais honesto que relatar caminhos de fallback.
    config_error = configuration_error()
    if config_error is not None:
        lines.append(_Line("configuração: INVÁLIDA", critical=True))
        lines.extend(_Line(f"  {part}") for part in str(config_error).splitlines())
    else:
        lines.append(_check_directory("config_dir", settings.config_dir))
        lines.append(_check_directory("data_dir", settings.data_dir))
        lines.append(_check_directory("cache_dir", settings.cache_dir))
        lines.append(_check_directory("download_dir", settings.download_dir))
        lines.extend(_check_database(settings.library_db_path))

    log_path = log_file_path()
    lines.append(_check_directory("log (diretório)", log_path.parent))
    lines.append(_Line(""))

    lines.extend(_check_dependencies())
    lines.append(_check_locale())
    lines.append(_Line(""))

    quarantine = settings.download_dir / QUARANTINE_DIR_NAME
    backups = settings.library_db_path.parent / BACKUP_DIR_NAME
    if config_error is None:
        lines.append(_Line(f"quarentena: {quarantine} — {_folder_summary(quarantine)}"))
        lines.append(_Line(f"backups: {backups} — {_folder_summary(backups)}"))
    if log_path.exists():
        lines.append(_Line(f"log: {log_path} — {log_path.stat().st_size} byte(s)"))
    else:
        lines.append(_Line(f"log: {log_path} — não existe ainda"))
    return lines


def run_doctor() -> int:
    """Roda as verificações, imprime o relatório mascarado e devolve o exit code."""
    lines = _collect()
    critical = sum(1 for line in lines if line.critical)

    report: list[str] = ["ytmusic-tui doctor — diagnóstico do ambiente (somente leitura)"]
    for line in lines:
        if not line.text:
            report.append("")
        elif line.critical:
            report.append(f"[crítico] {line.text}")
        else:
            report.append(line.text)
    report.append("")
    if critical:
        report.append(f"Resultado: {critical} problema(s) essencial(is) — exit 1.")
    else:
        report.append("Resultado: nada essencial quebrado — exit 0.")
    report.append(
        "Revise o conteúdo antes de compartilhar: dados pessoais aparecem "
        "mascarados (HOME vira ~, URLs perdem token, credenciais ficam [REDACTED])."
    )

    print(mask_secrets(mask_diagnostics("\n".join(report))))
    return 1 if critical else 0
