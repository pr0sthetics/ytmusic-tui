"""Testes para configuração."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from ytmusic_tui import config as config_mod
from ytmusic_tui.config import Settings


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
