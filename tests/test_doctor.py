"""Testes do ``doctor`` (§13.3/T13).

O contrato: nada essencial quebrado → exit 0; essencial quebrado → exit 1
com linha ``[crítico]``; avisos (ffmpeg ausente, banco inexistente) NÃO
mudam o exit; a saída não vaza HOME nem credencial; argumento desconhecido
é erro de uso (exit 2). Tudo com seams determinísticos (``_which`` /
``_libmpv_error``) e caminhos em tmp — o ambiente real não entra.
"""

import os
from pathlib import Path

import pytest

from ytmusic_tui import doctor
from ytmusic_tui.config import settings
from ytmusic_tui.errors import ConfigValidationError
from ytmusic_tui.library.migrations import SCHEMA_VERSION


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Caminhos em tmp + seams fixos: nada do ambiente real entra no teste."""
    monkeypatch.setattr(settings, "config_dir", tmp_path / "cfg")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "download_dir", tmp_path / "music")
    # A11: o diretório de log também é isolado. Antes o doctor consultava o
    # log real do usuário e a suíte falhava em sandbox/HOME restrito.
    monkeypatch.setattr(doctor, "log_file_path", lambda: tmp_path / "log" / "ytmusic-tui.log")
    monkeypatch.setattr(doctor, "_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor, "_libmpv_error", lambda: None)
    return tmp_path


def test_libmpv_ausente_e_critico(ambiente, monkeypatch, capsys):
    """Sem libmpv o app não abre player: essencial, exit 1."""
    monkeypatch.setattr(doctor, "_libmpv_error", lambda: "libmpv.so.2: cannot open shared object")

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 1
    assert "[crítico] libmpv: AUSENTE" in out
    assert "exit 1" in out


def test_banco_ausente_e_aviso(ambiente, capsys):
    """Banco que ainda não existe é normal na primeira execução (exit 0)."""
    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 0
    assert "banco:" in out
    assert "não existe (criado na 1ª abertura)" in out
    assert "exit 0" in out


def test_config_invalida_tem_linha_propria(ambiente, monkeypatch, capsys):
    """A02: o doctor reporta config inválida em vez de morrer no import."""
    erro = ConfigValidationError(
        "configuração inválida (TOML em /tmp/config.toml ou variáveis YTMUSIC_*):\n"
        "  - mpv_audio_quality='ultra': Input should be 'low', 'medium', 'high' or 'best'"
    )
    monkeypatch.setattr(doctor, "configuration_error", lambda: erro)

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 1
    assert "configuração: INVÁLIDA" in out
    assert "mpv_audio_quality='ultra'" in out
    assert "Traceback" not in out


def test_doctor_nao_cria_nada(ambiente, capsys):
    """Rodar o doctor não pode criar diretórios (checks são somente leitura)."""
    doctor.run_doctor()
    capsys.readouterr()

    assert not (ambiente / "cfg").exists()
    assert not (ambiente / "data").exists()
    assert not (ambiente / "cache").exists()
    assert not (ambiente / "music").exists()
    # A11: nada do log real é criado/lido pela verificação.
    assert not (ambiente / "log").exists()


def test_banco_corrompido_critico_sem_traceback(ambiente, capsys):
    """Arquivo que não é SQLite vira ``[crítico]`` legível, nunca traceback."""
    data = ambiente / "data"
    data.mkdir()
    (data / "library.db").write_bytes(b"isto nao e um banco sqlite")

    code = doctor.run_doctor()
    captured = capsys.readouterr()

    assert code == 1
    assert "ILEGÍVEL" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_banco_integra_e_esquema_atual(ambiente, capsys):
    """Banco saudável no esquema do app: integrity ok, exit 0."""
    from ytmusic_tui.library.database import DatabaseManager

    data = ambiente / "data"
    data.mkdir()
    db = DatabaseManager(data / "library.db")
    db.migrate()
    db.close()

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 0
    assert "integrity ok" in out
    assert f"esquema: v{SCHEMA_VERSION} (atual)" in out


def test_esquema_mais_novo_e_critico(ambiente, capsys):
    """Banco de um app mais novo: migrar às cegas estragaria — exit 1."""
    import sqlite3

    data = ambiente / "data"
    data.mkdir()
    connection = sqlite3.connect(data / "library.db")
    try:
        connection.execute("CREATE TABLE schema_migrations (version INT)")
        connection.execute("INSERT INTO schema_migrations VALUES (?)", (SCHEMA_VERSION + 1,))
        connection.commit()
    finally:
        connection.close()

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 1
    assert "MAIS NOVO" in out


def test_diretorio_sem_escrita_e_critico(ambiente, capsys):
    """Diretório existente sem escrita quebra o app: essencial, exit 1."""
    if os.geteuid() == 0:  # pragma: no cover - CI roda como usuário
        pytest.skip("root ignora permissões de escrita")

    data = ambiente / "data"
    data.mkdir()
    os.chmod(data, 0o500)
    try:
        code = doctor.run_doctor()
    finally:
        os.chmod(data, 0o700)
    out = capsys.readouterr().out

    assert code == 1
    assert "SEM ESCRITA" in out


def test_ffmpeg_ausente_e_aviso(ambiente, monkeypatch, capsys):
    """Sem ffmpeg há conversão de áudio limitada, mas o app abre (exit 0)."""
    monkeypatch.setattr(
        doctor,
        "_which",
        lambda name: None if name == "ffmpeg" else f"/usr/bin/{name}",
    )

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 0
    assert "binário ffmpeg: ausente no PATH" in out


def test_relatorio_mascarado(ambiente, monkeypatch, capsys):
    """HOME, URL com token e credencial em texto livre nunca saem crus."""
    home = Path.home()
    monkeypatch.setattr(settings, "config_dir", home / ".ytmusic-doctor-teste-nao-existe")
    monkeypatch.setattr(
        doctor,
        "_libmpv_error",
        lambda: (
            "falha ao carregar libmpv em https://exemplo.com/x?sig=SEGREDO "
            "Authorization: Bearer abc-secret"
        ),
    )

    code = doctor.run_doctor()
    out = capsys.readouterr().out

    assert code == 1  # libmpv ausente é crítico
    assert str(home) not in out
    assert "~/.ytmusic-doctor-teste-nao-existe" in out
    assert "SEGREDO" not in out
    assert "abc-secret" not in out
    assert "Authorization: [REDACTED]" in out
    assert "Revise o conteúdo antes de compartilhar" in out


def test_main_doctor_ponta_a_ponta(ambiente, capsys):
    """``main(["doctor"])`` roda o diagnóstico e sai com o código dele."""
    from ytmusic_tui.main import main

    with pytest.raises(SystemExit) as excinfo:
        main(["doctor"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "ytmusic-tui doctor" in out
    assert "Resultado:" in out


def test_main_argumento_desconhecido_e_erro_de_uso(capsys):
    """Argumento fora dos comandos é erro de uso: exit 2 (padrão argparse)."""
    from ytmusic_tui.main import main

    with pytest.raises(SystemExit) as excinfo:
        main(["banana"])

    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_main_version_imprime_e_sai(capsys):
    """``--version`` imprime a versão instalada e sai com 0 (GQ-14/T16)."""
    import ytmusic_tui
    from ytmusic_tui.main import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"ytmusic-tui {ytmusic_tui.__version__}"
