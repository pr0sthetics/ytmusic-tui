"""Catálogo de temas da TUI e temas personalizados.

O Textual já traz temas embutidos (Catppuccin, Nord, Monokai, Solarized,
Dracula, Tokyo Night, Rosé Pine, Gruvbox, ...). Aqui ficam os temas criados
pelo projeto — com identidade própria e usados como padrão — e o catálogo
único que a aba 🎨 Temas exibe.

Nada de cor espalhada pelo ``app.tcss``: o CSS usa as variáveis do tema
(``$background``, ``$primary``, ``$text``, ...), então trocar o tema muda a
interface inteira. Os temas personalizados são registrados no app em
``YTMusicTUI.on_mount`` antes de a tela principal ser montada.
"""

from dataclasses import dataclass

from rich.text import Text
from textual.app import App
from textual.theme import BUILTIN_THEMES, Theme

from ytmusic_tui.config import DEFAULT_THEME
from ytmusic_tui.tui.messages import THEME_BUILTIN, THEME_CUSTOM, THEME_DARK, THEME_LIGHT


@dataclass(frozen=True)
class ThemeInfo:
    """Metadados de um tema para a lista da aba 🎨 Temas."""

    key: str
    label: str
    description: str
    dark: bool
    custom: bool
    swatches: tuple[str, str, str]


def _variables(background: str, primary: str, accent: str) -> dict[str, str]:
    """Variáveis de contraste que acompanham cada tema personalizado.

    ``button-color-foreground`` é o texto sobre botões/realces na cor
    primária; sem ele, um tema claro ficaria com texto claro sobre fundo
    claro. O cursor e a seleção também seguem a cor de destaque.
    """
    return {
        "button-color-foreground": background,
        "block-cursor-background": primary,
        "block-cursor-foreground": background,
        "block-cursor-text-style": "none",
        "footer-key-foreground": primary,
        "input-selection-background": f"{primary} 35%",
        "input-selection-foreground": background,
        "screen-selection-background": f"{accent} 35%",
        "screen-selection-foreground": background,
    }


#: Temas criados pelo projeto. A chave é o nome registrado no Textual e o
#: mesmo valor gravado em ``theme`` no config.toml.
CUSTOM_THEMES: dict[str, Theme] = {
    "github-dark": Theme(
        name="github-dark",
        primary="#58a6ff",
        secondary="#30363d",
        accent="#bc8cff",
        foreground="#e6edf3",
        background="#0d1117",
        surface="#161b22",
        panel="#21262d",
        success="#3fb950",
        warning="#d29922",
        error="#f85149",
        dark=True,
        variables={
            **_variables("#0d1117", "#58a6ff", "#bc8cff"),
            # Preserva o texto exato do visual GitHub que o app já usava.
            "text": "#e6edf3",
            "text-muted": "#8b949e",
        },
    ),
    "github-light": Theme(
        name="github-light",
        primary="#0969da",
        secondary="#d0d7de",
        accent="#8250df",
        foreground="#1f2328",
        background="#ffffff",
        surface="#f6f8fa",
        panel="#eaeef2",
        success="#1a7f37",
        warning="#9a6700",
        error="#cf222e",
        dark=False,
        variables={
            **_variables("#ffffff", "#0969da", "#8250df"),
            "text": "#1f2328",
            "text-muted": "#656d76",
        },
    ),
    "kanagawa": Theme(
        name="kanagawa",
        primary="#7e9cd8",
        secondary="#957fb8",
        accent="#ffa066",
        foreground="#dcd7ba",
        background="#1f1f28",
        surface="#2a2a37",
        panel="#363646",
        success="#98bb6c",
        warning="#e6c384",
        error="#e82424",
        dark=True,
        variables=_variables("#1f1f28", "#7e9cd8", "#ffa066"),
    ),
    "everforest": Theme(
        name="everforest",
        primary="#a7c080",
        secondary="#7fbbb3",
        accent="#d699b6",
        foreground="#d3c6aa",
        background="#2d353b",
        surface="#343f44",
        panel="#3d484d",
        success="#a7c080",
        warning="#dbbc7f",
        error="#e67e80",
        dark=True,
        variables=_variables("#2d353b", "#a7c080", "#d699b6"),
    ),
    "ayu-dark": Theme(
        name="ayu-dark",
        primary="#59c2ff",
        secondary="#ffb454",
        accent="#d2a6ff",
        foreground="#b3b1ad",
        background="#0a0e14",
        surface="#0d1017",
        panel="#131721",
        success="#aad94c",
        warning="#e6b450",
        error="#f07178",
        dark=True,
        variables=_variables("#0a0e14", "#59c2ff", "#d2a6ff"),
    ),
    "nightfox": Theme(
        name="nightfox",
        primary="#719cd6",
        secondary="#9d79d6",
        accent="#63cdcf",
        foreground="#cdcecf",
        background="#192330",
        surface="#212e3f",
        panel="#29394f",
        success="#81b29a",
        warning="#dbc074",
        error="#c94f6d",
        dark=True,
        variables=_variables("#192330", "#719cd6", "#63cdcf"),
    ),
    "monokai-pro": Theme(
        name="monokai-pro",
        primary="#78dce8",
        secondary="#ab9df2",
        accent="#ffd866",
        foreground="#fcfcfa",
        background="#2d2a2e",
        surface="#353236",
        panel="#3e3b40",
        success="#a9dc76",
        warning="#ffd866",
        error="#ff6188",
        dark=True,
        variables=_variables("#2d2a2e", "#78dce8", "#ffd866"),
    ),
    "gruvbox-light": Theme(
        name="gruvbox-light",
        primary="#458588",
        secondary="#b16286",
        accent="#d79921",
        foreground="#3c3836",
        background="#fbf1c7",
        surface="#f2e5bc",
        panel="#ebdbb2",
        success="#79740e",
        warning="#b57614",
        error="#cc241d",
        dark=False,
        variables=_variables("#fbf1c7", "#458588", "#d79921"),
    ),
}


def _theme_for(key: str) -> Theme:
    """Definição do tema: personalizada quando existe, senão embutida."""
    return CUSTOM_THEMES.get(key) or BUILTIN_THEMES[key]


def _info(key: str, label: str, description: str) -> ThemeInfo:
    theme = _theme_for(key)
    return ThemeInfo(
        key=key,
        label=label,
        description=description,
        dark=theme.dark,
        custom=key in CUSTOM_THEMES,
        swatches=(
            theme.primary,
            theme.secondary or theme.primary,
            theme.accent or theme.primary,
        ),
    )


#: Ordem exibida na aba: primeiro os temas do projeto, depois os escuros e
#: por fim os claros. A lista é a fonte única para a UI e para os testes.
THEME_CATALOG: tuple[ThemeInfo, ...] = (
    # Temas personalizados
    _info(
        "github-dark",
        "GitHub Escuro",
        "O visual original do app: fundo profundo, azul de destaque e verde de sucesso.",
    ),
    _info(
        "github-light",
        "GitHub Claro",
        "Limpo e neutro, com azul forte para links, seleção e botões.",
    ),
    _info(
        "kanagawa",
        "Kanagawa",
        "Inspirado nas gravuras de Hokusai: azul, violeta e laranja suaves.",
    ),
    _info(
        "everforest",
        "Everforest",
        "Verdes naturais e contraste baixo, confortável para sessões longas.",
    ),
    _info(
        "ayu-dark",
        "Ayu Escuro",
        "Fundo quase preto com azul, laranja e roxo vibrantes.",
    ),
    _info(
        "nightfox",
        "Nightfox",
        "Azul-noite profundo com destaques em ciano e violeta.",
    ),
    _info(
        "monokai-pro",
        "Monokai Pro",
        "A paleta clássica do Monokai em uma versão moderna e suave.",
    ),
    _info(
        "gruvbox-light",
        "Gruvbox Claro",
        "Tons quentes de papel com azul e roxo de destaque.",
    ),
    # Embutidos escuros
    _info(
        "catppuccin-mocha",
        "Catppuccin Mocha",
        "O catppuccin mais escuro: rosa, lilás e pêssego sobre fundo noturno.",
    ),
    _info(
        "catppuccin-macchiato",
        "Catppuccin Macchiato",
        "Meio-termo do Catppuccin, com contraste um pouco maior.",
    ),
    _info(
        "catppuccin-frappe",
        "Catppuccin Frappé",
        "Catppuccin em tons mais claros e aconchegantes.",
    ),
    _info(
        "nord",
        "Nord",
        "Azuis árticos e cinzas frios, calmo e uniforme.",
    ),
    _info(
        "monokai",
        "Monokai",
        "O clássico roxo, rosa e ciano sobre cinza-esverdeado.",
    ),
    _info(
        "solarized-dark",
        "Solarized Escuro",
        "Paleta solarizada de baixo contraste, precisa e confortável.",
    ),
    _info(
        "dracula",
        "Dracula",
        "Roxo, rosa e ciano sobre um cinza-escuro arroxeado.",
    ),
    _info(
        "tokyo-night",
        "Tokyo Night",
        "Noite urbana: azul profundo com violeta e laranja.",
    ),
    _info(
        "gruvbox",
        "Gruvbox Escuro",
        "Tons terrosos e quentes com amarelo de destaque.",
    ),
    _info(
        "rose-pine",
        "Rosé Pine",
        "Rosa, lilás e azul suaves em um fundo escuro elegante.",
    ),
    _info(
        "rose-pine-moon",
        "Rosé Pine Moon",
        "Variante mais escura e fria do Rosé Pine.",
    ),
    _info(
        "atom-one-dark",
        "Atom One Dark",
        "O visual familiar do editor Atom, com azul e violeta.",
    ),
    _info(
        "flexoki",
        "Flexoki",
        "Paleta quente de tinta e papel, com contraste equilibrado.",
    ),
    _info(
        "textual-dark",
        "Textual Escuro",
        "Tema padrão do Textual, usando as cores do terminal.",
    ),
    # Embutidos claros
    _info(
        "catppuccin-latte",
        "Catppuccin Latte",
        "A versão clara do Catppuccin, com roxo e pêssego.",
    ),
    _info(
        "solarized-light",
        "Solarized Claro",
        "Papel solarizado, azul e turquesa de baixo contraste.",
    ),
    _info(
        "rose-pine-dawn",
        "Rosé Pine Dawn",
        "Amanhecer: creme, rosa e azul suaves.",
    ),
    _info(
        "atom-one-light",
        "Atom One Light",
        "Claro e direto, com azul e magenta do editor Atom.",
    ),
    _info(
        "textual-light",
        "Textual Claro",
        "Tema claro padrão do Textual, usando as cores do terminal.",
    ),
)

#: Índice por chave para validar configuração e localizar itens da lista.
THEME_BY_KEY: dict[str, ThemeInfo] = {info.key: info for info in THEME_CATALOG}


def register_custom_themes(app: App) -> None:
    """Registra os temas do projeto no app (idempotente)."""
    for theme in CUSTOM_THEMES.values():
        app.register_theme(theme)


def resolve_theme(name: str | None) -> str:
    """Nome de tema aplicável; desconhecido/vazio cai no padrão.

    A configuração é editável à mão, então um nome antigo ou com typo não
    pode impedir a TUI de abrir. O catálogo é a fonte única: todo tema
    aplicável tem uma entrada na aba 🎨 Temas (e um marcador ✓), então a
    lista nunca abre destacando um tema diferente do que está em uso.
    """
    candidate = (name or "").strip()
    if candidate in THEME_BY_KEY:
        return candidate
    return DEFAULT_THEME


def theme_definition(key: str) -> Theme | None:
    """Definição Textual do tema, ou ``None`` se a chave não existir."""
    return CUSTOM_THEMES.get(key) or BUILTIN_THEMES.get(key)


def theme_label(info: ThemeInfo, *, active: bool = False) -> str:
    """Rótulo da lista: três amostras de cor, ícone e marcador do salvo."""
    swatches = "".join(f"[{color}]●[/]" for color in info.swatches)
    icon = "🌙" if info.dark else "☀️"
    marker = "  ✓" if active else ""
    return f"{swatches}  {icon} {info.label}{marker}"


def render_theme_preview(info: ThemeInfo) -> Text:
    """Prévia em Rich Text com as cores reais do tema selecionado.

    A prévia não depende do tema ativo: ela mostra como *aquele* tema
    pinta textos, botões, progresso e estados — inclusive antes de o
    usuário confirmar a escolha.
    """
    theme = theme_definition(info.key)
    if theme is None:  # pragma: no cover — catálogo sempre resolve
        return Text(f"{info.label}\n{info.description}")

    colors = theme.to_color_system()
    primary = colors.primary.hex
    foreground = (
        colors.foreground.hex if colors.foreground else ("#ffffff" if info.dark else "#000000")
    )
    background = (
        colors.background.hex if colors.background else ("#000000" if info.dark else "#ffffff")
    )
    surface = colors.surface.hex if colors.surface else background
    muted = colors.foreground.with_alpha(0.65).hex6 if colors.foreground else foreground
    secondary = colors.secondary.hex if colors.secondary else primary
    accent = colors.accent.hex if colors.accent else primary
    success = colors.success.hex if colors.success else primary
    warning = colors.warning.hex if colors.warning else primary
    error = colors.error.hex if colors.error else primary
    mode = THEME_DARK if info.dark else THEME_LIGHT
    kind = THEME_CUSTOM if info.custom else THEME_BUILTIN

    text = Text()
    text.append("🎨  ", style="")
    text.append(info.label, style=f"bold {primary}")
    text.append("\n")
    text.append(info.description, style=muted)
    text.append("\n\n")
    text.append(f"{mode} · tema {kind}", style=f"italic {muted}")
    text.append("\n\n")

    for color in (primary, secondary, accent, success, warning, error):
        text.append("   ", style=f"on {color}")
    text.append("   ", style=f"on {foreground}")
    text.append("   ", style=f"on {background}")

    text.append("\n\n")
    text.append("▶ Título da faixa", style=f"bold {primary}")
    text.append("\n")
    text.append("Artista · Álbum", style=muted)
    text.append("\n")
    text.append("▰▰▰▰▰▰▱▱▱▱", style=primary)
    text.append("  2:31 / 4:12", style=muted)
    text.append("\n\n")
    text.append("  Salvar  ", style=f"bold {background} on {primary}")
    text.append("  Cancelar  ", style=f"{foreground} on {surface}")
    text.append("\n\n")
    text.append("✓ ", style=success)
    text.append("Download concluído", style=foreground)
    text.append("   ⚠ ", style=warning)
    text.append("Aviso", style=foreground)
    text.append("   ✕ ", style=error)
    text.append("Erro", style=foreground)
    return text
