"""Configuração centralizada da aplicação."""

import contextlib
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Literal

import tomlkit
from platformdirs import user_cache_dir, user_config_dir, user_data_dir
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)
from tomlkit.exceptions import TOMLKitError

from ytmusic_tui.errors import ConfigError, ConfigValidationError
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

#: Bitrate aceito para mp3/opus (A02). Abaixo disso a conversão perde
#: qualidade; acima do teto os codificadores recusam ou saturam. ``best``
#: continua valendo para os formatos que não usam bitrate (m4a/flac).
MIN_DOWNLOAD_QUALITY = 64
MAX_DOWNLOAD_QUALITY = 320

#: Opções válidas da aba Config (A02) — a UI usa estas listas para o fallback
#: defensivo quando o valor em memória não estiver entre as opções.
AUDIO_QUALITY_VALUES: tuple[str, ...] = ("low", "medium", "high", "best")
DOWNLOAD_FORMAT_VALUES: tuple[str, ...] = ("mp3", "opus", "m4a", "flac")

#: Tema aplicado quando o config.toml não define ``theme`` ou traz um nome
#: desconhecido. É um tema personalizado registrado pela TUI; o valor é
#: compartilhado com ``tui.themes`` sem importar Textual no carregamento da
#: configuração (o CLI ``doctor`` não precisa da biblioteca gráfica).
DEFAULT_THEME = "github-dark"


def _fsync_directory(directory: Path) -> None:
    """Sincroniza a entrada de diretório após o ``os.replace`` (best-effort).

    Sem isso, o rename pode estar no cache do FS quando faltar energia: o
    arquivo novo existe, mas o nome pode voltar ao antigo. Em sistemas sem
    ``fsync`` de diretório (ex.: Windows), é um no-op.
    """
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, text: str) -> None:
    """Grava ``text`` em ``path`` de forma atômica e privada (A04).

    Sequência: temporário no MESMO diretório (rename atômico) → ``fsync`` →
    modo 0600 → ``os.replace`` → ``fsync`` do diretório. Falha em qualquer
    passo remove o temporário e NÃO toca no arquivo antigo: disco cheio ou
    crash no meio não truncam a configuração.
    """
    secure_directory(path.parent, label="diretório de configuração")
    descriptor, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_tmp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Nasce 0600: o umask não decide a permissão de um arquivo de config.
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def format_validation_error(error: ValidationError, path: Path) -> str:
    """Mensagem curta e acionável para configuração inválida (A02).

    Lista arquivo, campo, valor recebido e o esperado — o usuário sabe
    exatamente o que corrigir, sem traceback do Pydantic. O caminho do TOML
    aparece porque é o lugar mais provável da edição; variáveis
    ``YTMUSIC_*`` também são aceitas (e citadas no cabeçalho).
    """
    lines = [f"configuração inválida (TOML em {path} ou variáveis YTMUSIC_*):"]
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "(raiz)"
        value = repr(item.get("input"))
        if len(value) > 80:  # valor gigante não polui o terminal
            value = f"{value[:77]}..."
        lines.append(f"  - {location}={value}: {item.get('msg', 'valor inválido')}")
    lines.append("Corrija o arquivo (ou remova o campo para usar o padrão) e abra o app de novo.")
    return "\n".join(lines)


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

    @field_validator("download_quality", mode="before")
    @classmethod
    def _validate_download_quality(cls, value: object) -> str:
        """Aceita ``best`` ou um inteiro na faixa suportada (A02).

        Um typo (``"abc"``, ``"999"``) não pode derrubar a TUI no import:
        vira erro de validação com o esperado explícito. O valor é
        normalizado para string porque é assim que o yt-dlp o consome.
        """
        text = str(value).strip()
        if text.lower() == "best":
            return "best"
        if text.isdigit():
            bitrate = int(text)
            if MIN_DOWNLOAD_QUALITY <= bitrate <= MAX_DOWNLOAD_QUALITY:
                return str(bitrate)
        raise ValueError(
            f"use 'best' ou um inteiro entre {MIN_DOWNLOAD_QUALITY} e {MAX_DOWNLOAD_QUALITY} kbps"
        )

    @field_validator("theme", mode="before")
    @classmethod
    def _normalize_theme(cls, value: object) -> str:
        """Tema vazio vira o padrão; nome desconhecido é resolvido na TUI.

        O catálogo de temas mora na camada TUI (importa Textual); validar
        aqui exigiria carregar a interface em comandos que só querem ler a
        configuração. Um nome desconhecido não é erro fatal: a TUI aplica o
        padrão e o usuário corrige na aba 🎨 Temas.
        """
        text = str(value).strip()
        return text or DEFAULT_THEME

    @field_validator("library_db_name")
    @classmethod
    def _validate_library_db_name(cls, value: str) -> str:
        """O banco mora em ``data_dir``: só um nome de arquivo simples (A02).

        Caminho absoluto, separador ou ``..`` permitiria escrever fora do
        diretório de dados — a configuração é recusada, nunca "consertada".
        """
        text = value.strip()
        forbidden = not text or text in {".", ".."} or "/" in text or "\\" in text
        if forbidden or Path(text).is_absolute() or "\x00" in text:
            raise ValueError(
                "use um nome de arquivo simples (sem diretórios, sem caminho absoluto)"
            )
        return text

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
    mpv_audio_quality: Literal["low", "medium", "high", "best"] = Field(
        default="high",
        description="Qualidade de áudio: low, medium, high, best",
    )

    # Download
    download_dir: Path = Field(
        default_factory=lambda: Path.home() / "Music" / "YouTube Music",
        description="Diretório padrão para downloads",
    )
    download_format: Literal["mp3", "opus", "m4a", "flac"] = Field(
        default="mp3",
        description="Formato de download: mp3, opus, m4a, flac",
    )
    download_quality: str = Field(
        default="320",
        description=f"Qualidade do áudio em kbps ({MIN_DOWNLOAD_QUALITY}-"
        f"{MAX_DOWNLOAD_QUALITY}) ou 'best'",
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
    auto_similar: bool = Field(
        default=True,
        description="Ao fim da fila, continuar com músicas parecidas (rádio)",
    )
    theme: str = Field(
        default=DEFAULT_THEME,
        description="Tema da interface (aba 🎨 Temas)",
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
        """Reescreve o TOML aplicando as atualizações (A04).

        Usa ``tomlkit`` para preservar comentários, ordem e tabelas
        aninhadas do arquivo do usuário. A gravação em si é atômica
        (:func:`_atomic_write`): um erro no meio não trunca a configuração
        antiga. TOML ilegível é preservado e vira ``ConfigError`` — nunca
        "zerado" em silêncio.
        """
        # O caminho NUNCA sai do diretório de configuração esperado
        # (T16/Q16): classe fixou um ``toml_file`` (testes) >
        # ``YTMUSIC_CONFIG_DIR`` > caminho padrão do usuário.
        path = _resolve_toml_file(type(self))
        if path.exists():
            try:
                original = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfigError(f"não foi possível ler a configuração {path}: {exc}") from exc
            try:
                document = tomlkit.parse(original)
            except TOMLKitError as exc:
                raise ConfigError(
                    f"o arquivo de configuração {path} não é um TOML válido ({exc}); "
                    "o original foi preservado — corrija-o e tente de novo"
                ) from exc
        else:
            document = tomlkit.document()

        for key, value in updates.items():
            document[key] = str(value) if isinstance(value, Path) else value

        try:
            text = tomlkit.dumps(document)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"não foi possível serializar a configuração: {exc}") from exc
        _atomic_write(path, text)
        # §8.3/T06: o TOML é dado do usuário — 0600 (aperta existente).
        secure_file(path, label="arquivo de configuração")


def _load_settings() -> Settings:
    """Carrega a configuração global sem deixar o import levantar (A02).

    Campo inválido no TOML/ambiente não pode impedir o app de abrir com uma
    mensagem útil: o erro tipado fica em :data:`_CONFIGURATION_ERROR` e
    :func:`configuration_error` o entrega ao entry point e ao ``doctor``.
    O objeto ``settings`` cai para os padrões (``model_construct``, sem
    criar diretórios) só para que os imports continuem funcionando; nenhum
    caminho de execução usa esses padrões sem antes checar o erro — o app e
    o ``doctor`` recusam/reportam explicitamente.
    """
    global _CONFIGURATION_ERROR
    try:
        return Settings()
    except ValidationError as exc:
        _CONFIGURATION_ERROR = ConfigValidationError(
            format_validation_error(exc, _resolve_toml_file(Settings))
        )
        return Settings.model_construct()
    except tomllib.TOMLDecodeError as exc:
        path = _resolve_toml_file(Settings)
        _CONFIGURATION_ERROR = ConfigValidationError(
            f"configuração inválida: o TOML em {path} não pôde ser lido ({exc}).\n"
            "Corrija a sintaxe do arquivo e abra o app de novo."
        )
        return Settings.model_construct()
    except OSError as exc:
        path = _resolve_toml_file(Settings)
        _CONFIGURATION_ERROR = ConfigValidationError(
            f"não foi possível ler a configuração {path}: {exc}.\n"
            "Verifique permissões e o caminho do arquivo."
        )
        return Settings.model_construct()


_CONFIGURATION_ERROR: ConfigValidationError | None = None


def configuration_error() -> ConfigValidationError | None:
    """Erro de configuração da instância global (``None`` = configuração ok)."""
    return _CONFIGURATION_ERROR


# Instância global de configurações
settings = _load_settings()
