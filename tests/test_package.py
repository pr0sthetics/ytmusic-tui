"""Consistência dos metadados do pacote."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import ytmusic_tui

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"


def _env_isolado(tmp_path: Path) -> dict[str, str]:
    """Ambiente do subprocesso sem tocar config/dados reais do usuário."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("YTMUSIC_")}
    env.update(
        {
            "YTMUSIC_CONFIG_DIR": str(tmp_path / "cfg"),
            "YTMUSIC_DATA_DIR": str(tmp_path / "data"),
            "YTMUSIC_CACHE_DIR": str(tmp_path / "cache"),
            "YTMUSIC_DOWNLOAD_DIR": str(tmp_path / "music"),
            "PYTHONPATH": str(REPO / "src"),
        }
    )
    return env


def test_launcher_da_raiz_respeita_version(tmp_path) -> None:
    """A13: ``python main.py --version`` imprime a versão, não abre a TUI."""
    result = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "--version"],
        cwd=REPO,
        env=_env_isolado(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"ytmusic-tui {ytmusic_tui.__version__}"


def test_launcher_da_raiz_roda_doctor(tmp_path) -> None:
    """A13: ``python main.py doctor`` roda o diagnóstico, não a TUI."""
    result = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "doctor"],
        cwd=REPO,
        env=_env_isolado(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )

    # O exit code depende do ambiente (libmpv/binários); o contrato aqui é
    # que o subcomando foi executado em vez de abrir a interface.
    assert "ytmusic-tui doctor" in result.stdout
    assert "Resultado:" in result.stdout


@pytest.mark.parametrize("campo", ["textual", "ytmusicapi", "python-mpv", "yt-dlp", "tomlkit"])
def test_pisos_de_dependencia_declarados(campo: str) -> None:
    """A12: os pisos correspondem à faixa realmente suportada/testada."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencias = data["project"]["dependencies"]
    assert any(item.startswith(f"{campo}>=") for item in dependencias), campo
    # rich não é importado pelo src/ (vem transitivamente do Textual).
    assert not any(item.startswith("rich") for item in dependencias)


def test_version_do_pacote_e_do_pyproject_coincidem() -> None:
    """``__version__`` deve ser igual ao ``version`` do pyproject.toml.

    Guarda contra a divergência encontrada na auditoria T01 (metadados
    desatualizados em relação ao código publicado). A comparação é com o
    pyproject do repositório, não com o metadado do editable install — que
    só é regenerado no ``pip install``.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert ytmusic_tui.__version__ == data["project"]["version"]


def test_css_do_app_acompanha_o_modulo() -> None:
    """``app.tcss`` precisa existir ao lado de ``app.py`` (T06.1).

    O Textual resolve ``CSS_PATH`` relativo ao módulo do app: num pacote
    instalado sem o arquivo, o startup morre com ``StylesheetError``. Esta
    guarda pega o descuido no próprio fonte; o job "pacote" do CI confere o
    artefato construído (wheel e sdist) antes de instalar.
    """
    # Import local: mantém o collect leve (o `app` puxa textual/player).
    from ytmusic_tui.tui import app as app_module

    module_file = app_module.__file__
    assert module_file is not None
    tui_dir = Path(module_file).resolve().parent

    css_path = app_module.YTMusicTUI.CSS_PATH
    assert isinstance(css_path, str)
    assert (tui_dir / "app.tcss").is_file()
    assert (tui_dir / css_path).is_file()
