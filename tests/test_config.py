"""Testes para configuração."""

import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from ytmusic_tui import config as config_mod
from ytmusic_tui.config import Settings
from ytmusic_tui.errors import ConfigError


def isolated_settings(toml_file: Path):
    """Cria uma subclasse de Settings apontando para um TOML específico.

    Evita que o config.toml real do usuário interfira nos testes.
    """

    class IsolatedSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="YTMUSIC_",
            case_sensitive=False,
            extra="ignore",
            toml_file=toml_file,
        )

    return IsolatedSettings


def test_settings_defaults(tmp_path, monkeypatch):
    """Testa valores padrão das configurações (sem config.toml)."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    settings = isolated_settings(tmp_path / "ausente.toml")()

    assert settings.config_dir.exists()
    assert settings.data_dir.exists()
    assert settings.cache_dir.exists()
    assert settings.mpv_volume == 50
    assert settings.download_format == "mp3"
    assert settings.download_quality == "320"
    assert settings.theme == "github-dark"


def test_theme_persiste_e_e_relido(tmp_path, monkeypatch):
    """A escolha da aba 🎨 Temas sobrevive a uma nova instância."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml = tmp_path / "config.toml"
    settings = isolated_settings(toml)()
    settings.update_and_save(theme="nord")

    assert 'theme = "nord"' in toml.read_text(encoding="utf-8")
    assert isolated_settings(toml)().theme == "nord"


def test_theme_vazio_usa_o_padrao(tmp_path, monkeypatch):
    """TOML com tema em branco não vira nome inválido na TUI."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml = tmp_path / "config.toml"
    toml.write_text('theme = "   "\n', encoding="utf-8")
    assert isolated_settings(toml)().theme == "github-dark"


def test_theme_desconhecido_e_aceito_na_config(tmp_path, monkeypatch):
    """O catálogo vive na TUI; a config não deve falhar por nome antigo."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml = tmp_path / "config.toml"
    toml.write_text('theme = "tema-que-nao-existe"\n', encoding="utf-8")
    assert isolated_settings(toml)().theme == "tema-que-nao-existe"


def test_settings_validation(tmp_path, monkeypatch):
    """Testa que variáveis de ambiente são validadas e aplicadas."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("YTMUSIC_MPV_VOLUME", "75")
    monkeypatch.setenv("YTMUSIC_DOWNLOAD_CONCURRENT", "5")

    settings = isolated_settings(tmp_path / "ausente.toml")()

    assert settings.mpv_volume == 75
    assert settings.download_concurrent == 5


def test_download_concurrent_is_validated_1_to_10(tmp_path, monkeypatch):
    """T06-R4: a faixa de downloads simultâneos é validada, nunca clampada.

    A mesma faixa que o gerenciador aplica (``set_concurrent``) é barrada
    aqui, na fonte (TOML/ambiente) — um ``config.toml`` errado nunca chega
    em memória como um limite diferente do escrito.
    """
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    monkeypatch.setenv("YTMUSIC_DOWNLOAD_CONCURRENT", "11")
    with pytest.raises(ValidationError):
        isolated_settings(tmp_path / "ausente.toml")()

    monkeypatch.setenv("YTMUSIC_DOWNLOAD_CONCURRENT", "0")
    with pytest.raises(ValidationError):
        isolated_settings(tmp_path / "ausente.toml")()


def test_library_db_path(tmp_path, monkeypatch):
    """Testa propriedade do caminho do banco."""
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    settings = isolated_settings(tmp_path / "ausente.toml")()
    db_path = settings.library_db_path
    assert db_path.parent == settings.data_dir
    assert db_path.name == "library.db"


def test_settings_reads_toml_file(tmp_path, monkeypatch):
    """O arquivo config.toml deve ser lido (regressão: era ignorado)."""
    toml = tmp_path / "config.toml"
    toml.write_text(
        # Campos legados de OAuth são ignorados silenciosamente (extra="ignore")
        'google_client_id = "abc.apps.googleusercontent.com"\n'
        'google_client_secret = "s3cr3t"\n'
        'download_format = "opus"\n'
        "mpv_volume = 33\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    s = isolated_settings(toml)()

    assert s.mpv_volume == 33
    assert s.download_format == "opus"


def test_env_overrides_toml(tmp_path, monkeypatch):
    """Variáveis de ambiente têm precedência sobre o arquivo TOML."""
    toml = tmp_path / "config.toml"
    toml.write_text("mpv_volume = 33\n", encoding="utf-8")

    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("YTMUSIC_MPV_VOLUME", "77")

    assert isolated_settings(toml)().mpv_volume == 77


def test_tilde_expansion(tmp_path, monkeypatch):
    """'~' em caminhos do TOML deve ser expandido para o HOME."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("YTMUSIC_DOWNLOAD_DIR", "~/minhas-musicas")

    s = isolated_settings(tmp_path / "ausente.toml")()

    assert s.download_dir == fake_home / "minhas-musicas"
    assert s.download_dir.exists()


def test_update_and_save_persists_to_toml(tmp_path, monkeypatch):
    """update_and_save deve gravar no TOML e ser relido por nova instância."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml_path = tmp_path / "config.toml"
    settings = isolated_settings(toml_path)()
    settings.update_and_save(
        mpv_volume=33,
        download_format="flac",
        show_notifications=False,
        download_dir=tmp_path / "musicas",
    )

    # Em memória
    assert settings.mpv_volume == 33
    assert settings.download_format == "flac"

    # Persistido (preserva tipos TOML)
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert data["mpv_volume"] == 33
    assert data["download_format"] == "flac"
    assert data["show_notifications"] is False

    # Relido por uma nova instância
    again = isolated_settings(toml_path)()
    assert again.mpv_volume == 33
    assert again.download_format == "flac"
    assert again.show_notifications is False
    assert again.download_dir == tmp_path / "musicas"


def test_update_and_save_preserves_unknown_keys(tmp_path, monkeypatch):
    """Chaves desconhecidas (ex.: restos de config antiga) não são perdidas."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml_path = tmp_path / "config.toml"
    toml_path.write_text('google_client_id = "abc"\nmpv_volume = 10\n', encoding="utf-8")

    settings = isolated_settings(toml_path)()
    settings.update_and_save(mpv_volume=20)

    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert data["google_client_id"] == "abc"
    assert data["mpv_volume"] == 20


def _isolar_diretorios(tmp_path: Path, monkeypatch) -> None:
    """Aponta TODOS os diretórios da instância para o tmp (nada do usuário)."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("YTMUSIC_DOWNLOAD_DIR", str(tmp_path / "musica"))


def test_config_dir_env_relocates_the_toml_reading(tmp_path, monkeypatch):
    """T16/Q16: com ``YTMUSIC_CONFIG_DIR``, o TOML lido vive DENTRO dele.

    Antes, a variável só trocava o campo ``config_dir`` e a leitura ainda
    caía no arquivo real do usuário — o L4 chegou a ler
    ``mpv_audio_quality`` de um config real (Q16).
    """
    env_dir = tmp_path / "config"
    env_dir.mkdir()
    (env_dir / "config.toml").write_text("mpv_volume = 17\n", encoding="utf-8")
    _isolar_diretorios(tmp_path, monkeypatch)

    # Classe BASE (semântica de produção), não a subclasse isolada:
    # é exatamente o caminho que o L4 e o usuário final usam.
    s = Settings()

    assert s.mpv_volume == 17


def test_config_dir_env_relocates_the_toml_writing_and_locks_the_default(tmp_path, monkeypatch):
    """T16/Q16: a gravação fica no diretório esperado — nunca no arquivo real.

    ``DEFAULT_CONFIG_FILE`` é apontado para um caminho sentinela: se qualquer
    leitura/gravação sair do ``YTMUSIC_CONFIG_DIR``, o teste falha.
    """
    real = tmp_path / "real" / "config.toml"  # "arquivo real do usuário"
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_FILE", real)
    _isolar_diretorios(tmp_path, monkeypatch)

    s = Settings()
    s.update_and_save(mpv_volume=33)

    gravado = tmp_path / "config" / "config.toml"
    assert tomllib.loads(gravado.read_text(encoding="utf-8"))["mpv_volume"] == 33
    assert not real.exists(), "a gravação saiu do diretório de config esperado"


def test_resolve_toml_file_keeps_the_default_without_env(tmp_path, monkeypatch):
    """Sem ``YTMUSIC_CONFIG_DIR``, o caminho padrão continua valendo (T16/Q16)."""
    monkeypatch.delenv("YTMUSIC_CONFIG_DIR", raising=False)
    real = tmp_path / "real" / "config.toml"
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_FILE", real)

    assert config_mod._resolve_toml_file(Settings) == real


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("mpv_audio_quality", "ultra"),
        ("download_format", "ogg"),
        ("download_quality", "999"),
        ("download_quality", "abc"),
        ("library_db_name", "../evil.db"),
        ("library_db_name", "/tmp/evil.db"),
    ],
)
def test_valores_fora_do_enum_sao_recusados(tmp_path, monkeypatch, campo, valor):
    """A02: typo no TOML vira erro de validação com campo e esperado.

    ``mpv_audio_quality="ultra"`` antes chegava no ``Select`` e derrubava o
    mount com ``InvalidSelectValueError``; ``download_quality``/nome do banco
    nem eram validados.
    """
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml = tmp_path / "config.toml"
    toml.write_text(f'{campo} = "{valor}"\n', encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        isolated_settings(toml)()

    assert campo in str(excinfo.value)


def test_download_quality_best_e_faixa_valida(tmp_path, monkeypatch):
    """A02: ``best`` e a faixa 64–320 são aceitos e normalizados."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml = tmp_path / "config.toml"
    toml.write_text('download_quality = "best"\n', encoding="utf-8")
    assert isolated_settings(toml)().download_quality == "best"

    toml.write_text("download_quality = 128\n", encoding="utf-8")
    assert isolated_settings(toml)().download_quality == "128"


def test_update_and_save_preserva_comentarios_e_estrutura(tmp_path, monkeypatch):
    """A04: tomlkit preserva comentários, ordem e tabelas aninhadas."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "# comentário do topo\n"
        "mpv_volume = 10  # volume inicial\n"
        'download_format = "mp3"\n'
        "\n"
        "[extra]\n"
        'chave = "preservada"\n',
        encoding="utf-8",
    )

    settings = isolated_settings(toml_path)()
    settings.update_and_save(mpv_volume=33)

    text = toml_path.read_text(encoding="utf-8")
    assert "# comentário do topo" in text
    assert "# volume inicial" in text
    assert "[extra]" in text and 'chave = "preservada"' in text
    data = tomllib.loads(text)
    assert data["mpv_volume"] == 33
    assert data["extra"]["chave"] == "preservada"


def test_update_and_save_e_atomico_e_nao_trunca_em_falha(tmp_path, monkeypatch):
    """A04: OSError no meio da gravação deixa o arquivo antigo íntegro."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml_path = tmp_path / "config.toml"
    original = "# original\nmpv_volume = 10\n"
    toml_path.write_text(original, encoding="utf-8")
    settings = isolated_settings(toml_path)()

    def _replace_falha(src, dst):  # noqa: ANN001 - seam do os.replace
        raise OSError("disco cheio")

    monkeypatch.setattr(config_mod.os, "replace", _replace_falha)
    with pytest.raises(OSError, match="disco cheio"):
        settings.update_and_save(mpv_volume=20)

    assert toml_path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".config.toml.*.tmp")), "temporário órfão"


def test_update_and_save_grava_0600(tmp_path, monkeypatch):
    """A04: o TOML novo nasce 0600, independentemente do umask."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml_path = tmp_path / "config.toml"
    settings = isolated_settings(toml_path)()
    settings.update_and_save(mpv_volume=33)

    assert stat.S_IMODE(toml_path.stat().st_mode) == 0o600


def test_toml_invalido_e_preservado_e_reportado(tmp_path, monkeypatch):
    """A04: TOML inválido não é zerado em silêncio — erro tipado, original fica."""
    monkeypatch.setenv("YTMUSIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YTMUSIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMUSIC_CACHE_DIR", str(tmp_path / "cache"))

    toml_path = tmp_path / "config.toml"
    toml_path.write_text("mpv_volume = 10\n", encoding="utf-8")
    settings = isolated_settings(toml_path)()
    original = "isto nao e toml = [\n"
    toml_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigError, match="não é um TOML válido"):
        settings.update_and_save(mpv_volume=20)

    assert toml_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "conteudo",
    [
        'mpv_audio_quality = "ultra"\n',
        'mpv_volume = "abc"\n',
        'download_format = "ogg"\n',
        "isto nao e toml = [\n",
    ],
)
def test_cli_com_config_invalida_sai_1_sem_traceback(tmp_path, conteudo):
    """A02: ``python -m ytmusic_tui`` recusa config inválida com mensagem útil."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text(conteudo, encoding="utf-8")

    env = {chave: valor for chave, valor in os.environ.items() if not chave.startswith("YTMUSIC_")}
    env.update(
        {
            "YTMUSIC_CONFIG_DIR": str(cfg),
            "YTMUSIC_DATA_DIR": str(tmp_path / "data"),
            "YTMUSIC_CACHE_DIR": str(tmp_path / "cache"),
            "YTMUSIC_DOWNLOAD_DIR": str(tmp_path / "music"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "ytmusic_tui"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "configuração inválida" in result.stderr
    # Diz que TOML/ambiente são as fontes possíveis (o caso de sintaxe cita
    # o TOML; o de valor inválido cita ambos).
    assert "YTMUSIC" in result.stderr or "TOML" in result.stderr
    assert "Corrija o arquivo" in result.stderr or "Corrija a sintaxe" in result.stderr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
