"""Testes do catálogo de temas e da prévia da aba 🎨."""

from pathlib import Path

from textual.theme import Theme

from ytmusic_tui.config import DEFAULT_THEME
from ytmusic_tui.tui.themes import (
    CUSTOM_THEMES,
    THEME_BY_KEY,
    THEME_CATALOG,
    register_custom_themes,
    render_theme_preview,
    resolve_theme,
    theme_definition,
    theme_label,
)


def test_catalogo_cobre_temas_variados_e_sem_duplicatas():
    """O catálogo inclui os temas pedidos e não repete chaves."""
    keys = [info.key for info in THEME_CATALOG]
    assert len(keys) == len(set(keys))
    assert len(keys) >= 20
    assert {
        "catppuccin-mocha",
        "nord",
        "monokai",
        "solarized-dark",
        "solarized-light",
    } <= set(keys)
    assert sum(info.custom for info in THEME_CATALOG) >= 6


def test_temas_personalizados_tem_cores_completas():
    """Temas do projeto declaram todas as cores que o CSS usa."""
    assert DEFAULT_THEME in CUSTOM_THEMES
    for key, theme in CUSTOM_THEMES.items():
        assert theme.name == key
        for field in (
            "primary",
            "secondary",
            "accent",
            "foreground",
            "background",
            "surface",
            "panel",
            "success",
            "warning",
            "error",
        ):
            assert getattr(theme, field), f"{key}.{field} vazio"
        assert theme.dark is not None


def test_catalogo_resolve_para_definicao_textual():
    """Toda entrada do catálogo existe como tema do Textual."""
    for info in THEME_CATALOG:
        theme = theme_definition(info.key)
        assert theme is not None
        assert theme.name == info.key
        assert theme.dark == info.dark
        assert info.custom == (info.key in CUSTOM_THEMES)


def test_resolve_theme_cai_no_padrao_para_nome_desconhecido():
    assert resolve_theme(None) == DEFAULT_THEME
    assert resolve_theme("") == DEFAULT_THEME
    assert resolve_theme("  ") == DEFAULT_THEME
    assert resolve_theme("nao-existe") == DEFAULT_THEME
    # Embutido fora da galeria também cai no padrão: todo tema aplicado
    # precisa aparecer na lista (com ✓) para a aba não "pular" de tema.
    assert resolve_theme("ansi-dark") == DEFAULT_THEME
    assert resolve_theme("nord") == "nord"
    assert resolve_theme("  catppuccin-mocha  ") == "catppuccin-mocha"


def test_register_custom_themes_registra_todos():
    class FakeApp:
        def __init__(self) -> None:
            self.themes: list[str] = []

        def register_theme(self, theme: Theme) -> None:
            self.themes.append(theme.name)

    app = FakeApp()
    register_custom_themes(app)  # type: ignore[arg-type]
    assert set(app.themes) == set(CUSTOM_THEMES)


def test_theme_label_mostra_amostras_e_marcador_do_ativo():
    info = THEME_BY_KEY["nord"]
    label = theme_label(info)
    assert "Nord" in label
    assert "🌙" in label
    assert label.count("●") == 3
    assert "✓" not in label
    assert "✓" in theme_label(info, active=True)

    light = THEME_BY_KEY["solarized-light"]
    assert "☀️" in theme_label(light)


def test_preview_de_cada_tema_renderiza_nome_e_descricao():
    for info in THEME_CATALOG:
        preview = render_theme_preview(info)
        assert info.label in preview.plain
        assert info.description in preview.plain
        assert "▶ Título da faixa" in preview.plain
        assert "Salvar" in preview.plain


def test_app_tcss_nao_fixa_cores_do_tema():
    """A interface inteira precisa seguir o tema ativo (regressão do T18)."""
    css = (
        Path(__file__).resolve().parents[1] / "src" / "ytmusic_tui" / "tui" / "app.tcss"
    ).read_text(encoding="utf-8")
    for variable in (
        "background",
        "surface",
        "primary",
        "secondary",
        "text",
        "text-muted",
        "error",
        "success",
        "warning",
    ):
        assert f"${variable}:" not in css, f"app.tcss fixa ${variable}"
