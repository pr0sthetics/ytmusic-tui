"""Configuração centralizada da aplicação."""

import json
import os
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir, user_config_dir, user_data_dir
from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from ytmusic_tui.errors import ConfigError
from ytmusic_tui.permissions import secure_directory, secure_file

# Caminho padrão do arquivo de configuração TOML.
# É calculado independentemente da instância para evitar dependência circular
# (config_dir é uma das configurações carregadas do próprio arquivo).
DEFAULT_CONFIG_FILE = Path(user_config_dir("ytmusic-tui")) / "config.toml"


def _resolve_toml_file(settings_cls: type[BaseSettings]) -> Path:
    """Arquivo TOML em vigor para LER e GRAVAR a configuração.

    Prioridade (T16/Q16):

    1. um ``toml_file`` fixado pela própria classe (subclasses de teste);
    2. ``YTMUSIC_CONFIG_DIR`` do ambiente — o TOML acompanha o diretório
       redirecionado, então o arquivo real do usuário nunca é lido nem
       gravado fora do diretório de configuração esperado;
    3. o caminho padrão do usuário (:data:`DEFAULT_CONFIG_FILE`).
    """
    configured = settings_cls.model_config.get("toml_file")
    if configured is not None:
        return Path(str(configured))
    env_dir = os.environ.get("YTMUSIC_CONFIG_DIR")
    if env_dir:
        return Path(os.path.expanduser(env_dir)) / "config.toml"
    return DEFAULT_CONFIG_FILE


# Faixa aceita de downloads simultâneos (T06-R4). A validação vive aqui
# (pydantic, para TOML/ambiente) e o gerenciador recusa valores fora dela —
# nenhum ponto ajusta em silêncio. Quem normaliza para o usuário é a UI.
MIN_DOWNLOAD_CONCURRENT = 1
MAX_DOWNLOAD_CONCURRENT = 10


def _toml_literal(value: object) -> str:
    """Serializa um valor simples para TOML (str, bool, int, float)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))


class Settings(BaseSettings):
    """Configurações da aplicação carregadas de arquivo e variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_prefix="YTMUSIC_",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Define a precedência das fontes de configuração.

        Ordem (maior prioridade primeiro):
        1. Valores passados no construtor
        2. Variáveis de ambiente (YTMUSIC_*)
        3. Arquivo TOML em vigor — ``_resolve_toml_file``: ``YTMUSIC_CONFIG_DIR``
           quando definido, senão ``~/.config/ytmusic-tui/config.toml``
        4. Secrets de arquivos
        """
        toml_source = TomlConfigSettingsSource(
            settings_cls, toml_file=_resolve_toml_file(settings_cls)
        )
        return (
            init_settings,
            env_settings,
            toml_source,
            file_secret_settings,
        )

    @field_validator(
        "config_dir",
        "data_dir",
        "cache_dir",
        "download_dir",
        mode="before",
    )
    @classmethod
    def _expand_user_path(cls, value: object) -> object:
        """Expande '~' em caminhos vindos do arquivo de configuração."""
        if isinstance(value, (str, os.PathLike)):
            return Path(os.path.expanduser(str(value)))
        return value

    # Diretórios base
    config_dir: Path = Field(
        default_factory=lambda: Path(user_config_dir("ytmusic-tui")),
        description="Diretório de configuração",
    )
    data_dir: Path = Field(
        default_factory=lambda: Path(user_data_dir("ytmusic-tui")),
        description="Diretório de dados (banco de dados, downloads)",
    )
    cache_dir: Path = Field(
        default_factory=lambda: Path(user_cache_dir("ytmusic-tui")),
        description="Diretório de cache",
    )

    # Player (mpv)
    mpv_volume: int = Field(default=50, ge=0, le=100, description="Volume inicial (0-100)")
    mpv_audio_quality: str = Field(
        default="high",
        description="Qualidade de áudio: low, medium, high, best",
    )

    # Download
    download_dir: Path = Field(
        default_factory=lambda: Path.home() / "Music" / "YouTube Music",
        description="Diretório padrão para downloads",
    )
    download_format: str = Field(
        default="mp3",
        description="Formato de download: mp3, opus, m4a, flac",
    )
    download_quality: str = Field(
        default="320",
        description="Qualidade do áudio em kbps (para mp3) ou 'best'",
    )
    download_concurrent: int = Field(
        default=3,
        ge=MIN_DOWNLOAD_CONCURRENT,
        le=MAX_DOWNLOAD_CONCURRENT,
        description="Downloads simultâneos",
    )
    embed_metadata: bool = Field(
        default=True,
        description="Inserir metadados (tags ID3, capa) nos arquivos",
    )

    # Biblioteca local
    library_db_name: str = Field(
        default="library.db",
        description="Nome do arquivo do banco de dados SQLite",
    )

    # Interface TUI
    show_notifications: bool = Field(
        default=True,
        description="Mostrar notificações toast",
    )
    auto_play_next: bool = Field(
        default=True,
        description="Reproduzir próxima música automaticamente",
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Diretórios internos da aplicação (§8.3/T06): 0700 estrito (chmod
        # pós-mkdir, umask-agnóstico); existente só aperta, com log.
        secure_directory(self.config_dir, label="diretório de configuração")
        secure_directory(self.data_dir, label="diretório de dados")
        secure_directory(self.cache_dir, label="diretório de cache")
        # download_dir é MÍDIA DO USUÁRIO: criado como sempre (modo padrão do
        # sistema), nunca chmodado — quem decide como compartilhar é o usuário.
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"não foi possível criar o diretório de downloads {self.download_dir}: {exc}"
            ) from exc

    @property
    def library_db_path(self) -> Path:
        """Caminho completo do banco de dados da biblioteca."""
        return self.data_dir / self.library_db_name

    def update_and_save(self, **updates: Any) -> None:
        """Atualiza valores em memória e persiste no arquivo TOML.

        Preserva chaves já existentes no arquivo (inclusive desconhecidas).
        """
        for key, value in updates.items():
            if key not in type(self).model_fields:
                raise KeyError(f"Configuração desconhecida: {key}")
            setattr(self, key, value)
        self._save_toml(updates)

    def _save_toml(self, updates: dict[str, Any]) -> None:
        """Reescreve o TOML aplicando as atualizações."""
        import tomllib

        # O caminho NUNCA sai do diretório de configuração esperado
        # (T16/Q16): classe fixou um ``toml_file`` (testes) >
        # ``YTMUSIC_CONFIG_DIR`` > caminho padrão do usuário.
        path = _resolve_toml_file(type(self))
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                data = {}
        for key, value in updates.items():
            data[key] = str(value) if isinstance(value, Path) else value
        lines = [f"{key} = {_toml_literal(value)}" for key, value in data.items()]
        secure_directory(path.parent, label="diretório de configuração")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # §8.3/T06: o TOML é dado do usuário — 0600 (aperta existente).
        secure_file(path, label="arquivo de configuração")


# Instância global de configurações
settings = Settings()
