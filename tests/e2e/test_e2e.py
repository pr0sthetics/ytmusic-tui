"""Ponta a ponta controlado (L4, T09) — opt-in e LOCAL, nunca no CI.

O que o teste prova: o wheel deste checkout instala num venv limpo e o
artefato instalado funciona no fluxo real — ``doctor`` (exit 0), busca,
álbum, player (mpv) e download (yt-dlp + ffmpeg) —, inteiramente isolado em
diretórios temporários (``YTMUSIC_*`` + ``XDG_STATE_HOME``); nada toca os
dados, o log ou os downloads reais do usuário.

Rodar (na máquina do autor; usa rede de verdade):

    YTMUSIC_E2E=1 .venv/bin/python -m pytest tests/e2e -v -s

A faixa do fluxo vem de ``YTMUSIC_E2E_QUERY`` (default: a query do L3 —
"my dear melancholy"); o arquivo baixado vive no tmp do teste e morre com
ele. O que é automatizado × o que é conferência manual está em
``tests/e2e/README.md`` (runbook em pt-BR, como o CHANGELOG).
"""

import contextlib
import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest
from platformdirs import user_config_dir

from ytmusic_tui.download.pipeline import QUARANTINE_DIR_NAME, TMP_DIR_NAME

pytestmark = pytest.mark.skipif(
    os.environ.get("YTMUSIC_E2E") != "1",
    reason="e2e: defina YTMUSIC_E2E=1 para rodar (build + venv limpo + rede real)",
)

#: Raiz do checkout (tests/e2e/test_e2e.py → ytmusic-tui/).
RAIZ = Path(__file__).resolve().parents[2]
QUERY_PADRAO = "my dear melancholy"

TIMEOUT_BUILD = 300
TIMEOUT_VENV = 120
TIMEOUT_PIP = 600
TIMEOUT_DOCTOR = 120
TIMEOUT_FLUXO = 900

#: A cópia para o build não leva lixo nem o que não entra no wheel.
_IGNORAR = (
    ".git",
    ".venv",
    "tests",
    "docs",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    ".github",
)


def _ambiente_isolado(tmp: Path) -> dict[str, str]:
    """env do fluxo: TODOS os caminhos do app em tmp (jamais dados reais)."""
    return {
        **os.environ,
        "YTMUSIC_CONFIG_DIR": str(tmp / "config"),
        "YTMUSIC_DATA_DIR": str(tmp / "data"),
        "YTMUSIC_CACHE_DIR": str(tmp / "cache"),
        "YTMUSIC_DOWNLOAD_DIR": str(tmp / "music"),
        "YTMUSIC_E2E_QUERY": os.environ.get("YTMUSIC_E2E_QUERY", QUERY_PADRAO),
        # o log segue o XDG: com o estado isolado, nem o venv limpo nem a TUI
        # escrevem no log real do usuário
        "XDG_STATE_HOME": str(tmp / "state"),
        # pty/Textual precisam de um TERM conhecido mesmo fora do terminal
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }


def _rodar(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: int,
    o_que: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Executa e exige exit 0; em falha, mostra a saída capturada."""
    processo = subprocess.run(
        argv, env=env, cwd=cwd, timeout=timeout, capture_output=True, text=True
    )
    if processo.returncode != 0:
        print(processo.stdout)
        print(processo.stderr)
    assert processo.returncode == 0, f"{o_que} falhou (exit {processo.returncode})"
    return processo


def _build_wheel(tmp: Path) -> Path:
    """Constrói o wheel a partir de uma CÓPIA do checkout (não suja o repo)."""
    fonte = tmp / "fonte"
    shutil.copytree(RAIZ, fonte, ignore=shutil.ignore_patterns(*_IGNORAR))
    dist = tmp / "dist"
    _rodar(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        env=os.environ.copy(),
        cwd=fonte,
        timeout=TIMEOUT_BUILD,
        o_que="build do wheel",
    )
    wheels = sorted(dist.glob("*.whl"))
    assert wheels, "o build não produziu wheel"
    return wheels[0]


def _criar_venv(tmp: Path) -> Path:
    venv = tmp / "venv"
    _rodar(
        [sys.executable, "-m", "venv", str(venv)],
        env=os.environ.copy(),
        timeout=TIMEOUT_VENV,
        o_que="criação do venv",
    )
    return venv


def _ler_resumo(saida: str) -> dict:
    linhas = [linha for linha in saida.splitlines() if linha.startswith("[e2e-json] ")]
    assert linhas, "o fluxo não emitiu o resumo [e2e-json]"
    return json.loads(linhas[-1].removeprefix("[e2e-json] "))


def _conferir_resultado(tmp: Path, resumo: dict) -> None:
    """O fluxo deixou arquivo válido no tmp e a linha na biblioteca."""
    arquivo = Path(resumo["download"]["arquivo"])
    assert arquivo.is_file(), f"arquivo baixado ausente: {arquivo}"
    assert arquivo.stat().st_size > 0, f"arquivo baixado vazio: {arquivo}"
    assert arquivo.is_relative_to(tmp / "music"), "o download saiu do tmp"
    assert resumo["biblioteca"]["track_id"] > 0, "faixa sem id na biblioteca"
    assert resumo["player"]["estado_final"] in {"playing", "paused"}, (
        f"o player não ficou tocando: {resumo['player']}"
    )


def _smoke_da_tui_no_pty(venv: Path, env: dict[str, str]) -> None:
    """Abre a TUI de verdade num pty, espera a tela montar e sai com Ctrl+Q.

    Isolada como o resto (tmp), sem rede no caminho (o ``on_mount`` só faz
    migração + reconciliação local). Se isto ficar flaky no ambiente, o
    roteiro manual do README cobre a conferência da TUI.
    """
    console = str(venv / "bin" / "ytmusic-tui")
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover — o filho vira a TUI
        os.execve(console, [console], env)

    saiu = False
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        saida = b""
        limite = time.monotonic() + 30
        while time.monotonic() < limite and b"YouTube Music TUI" not in saida:
            pronto, _, _ = select.select([fd], [], [], 0.5)
            if pronto:
                pedaco = os.read(fd, 8192)
                if not pedaco:
                    break
                saida += pedaco
        assert b"YouTube Music TUI" in saida, (
            f"a TUI não desenhou a moldura em 30s (últimos bytes: {saida[-200:]!r})"
        )

        os.write(fd, b"\x11")  # Ctrl+Q (binding de sair)
        limite = time.monotonic() + 30
        while time.monotonic() < limite:
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                saiu = True
                assert os.waitstatus_to_exitcode(status) == 0, "a TUI saiu com erro"
                return
            select.select([fd], [], [], 0.2)
        raise AssertionError("a TUI não saiu após Ctrl+Q em 30s")
    finally:
        os.close(fd)
        if not saiu:  # não deixar o app órfão pendurado no pty
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)


def _estado_do_config_real() -> tuple[int, int] | None:
    """Snapshot leve (tamanho + mtime) do ``config.toml`` REAL do usuário.

    Q16/T16: o L4 roda com ``YTMUSIC_CONFIG_DIR`` no tmp — o arquivo real do
    usuário não pode ser lido nem gravado. Guarda: só metadados, nunca o
    conteúdo; ``None`` = arquivo ausente (e ausente deve continuar).
    """
    caminho = Path(user_config_dir("ytmusic-tui")) / "config.toml"
    if not caminho.exists():
        return None
    st = caminho.stat()
    return (st.st_size, st.st_mtime_ns)


def _conferir_limpeza(tmp: Path) -> None:
    """Nada de resíduo: sem .part, .tmp e quarentena vazios, sem órfãos."""
    music = tmp / "music"
    assert not list(music.rglob("*.part")), "sobrou arquivo .part"
    temporarios = music / TMP_DIR_NAME
    assert not temporarios.exists() or not any(temporarios.iterdir()), (
        f"{TMP_DIR_NAME} ficou com resíduo"
    )
    quarentena = music / QUARANTINE_DIR_NAME
    assert not quarentena.exists() or not any(quarentena.iterdir()), "quarentena com resíduo"

    # nenhum processo (yt-dlp/mpv) continua referenciando o tmp
    try:
        pgrep = subprocess.run(["pgrep", "-f", str(tmp)], capture_output=True, text=True)
    except FileNotFoundError:  # sem procps no host: conferência manual cobre
        return
    assert pgrep.returncode == 1, f"processo órfão apontando para o tmp:\n{pgrep.stdout}"


def test_fluxo_ponta_a_ponta_com_instalacao_limpa(tmp_path):
    """Build → venv limpo → doctor → fluxo real → TUI no pty → limpeza."""
    env = _ambiente_isolado(tmp_path)
    config_real_antes = _estado_do_config_real()
    wheel = _build_wheel(tmp_path)
    venv = _criar_venv(tmp_path)
    _rodar(
        [
            str(venv / "bin" / "pip"),
            "install",
            "--quiet",
            "--disable-pip-version-check",
            str(wheel),
        ],
        env=os.environ.copy(),
        timeout=TIMEOUT_PIP,
        o_que="instalação do wheel no venv limpo",
    )
    console = venv / "bin" / "ytmusic-tui"
    assert console.is_file(), "console script ausente após instalar o wheel"

    doctor = _rodar(
        [str(console), "doctor"],
        env=env,
        timeout=TIMEOUT_DOCTOR,
        o_que="doctor no venv limpo",
    )
    print(doctor.stdout)  # evidência (o teste roda com -s)

    fluxo = _rodar(
        [str(venv / "bin" / "python"), str(RAIZ / "tests" / "e2e" / "e2e_flow.py")],
        env=env,
        cwd=tmp_path,
        timeout=TIMEOUT_FLUXO,
        o_que="fluxo ponta a ponta",
    )
    print(fluxo.stdout)  # o relatório passo a passo fica na saída do pytest
    resumo = _ler_resumo(fluxo.stdout)

    _conferir_resultado(tmp_path, resumo)
    _smoke_da_tui_no_pty(venv, env)
    _conferir_limpeza(tmp_path)

    assert _estado_do_config_real() == config_real_antes, (
        "Q16: o config.toml real do usuário mudou durante o L4 — "
        "o isolamento por YTMUSIC_CONFIG_DIR vazou"
    )
