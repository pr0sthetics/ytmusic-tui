"""Biblioteca com SQLite inacessível de verdade (T08 · §10.1) — ``run_test``.

A L1 cobre a tradução dos erros (unit) e a UI com load mockado; aqui o
sqlite falha REALMENTE — há um ARQUIVO comum onde deveria estar o
diretório do banco, então nem abrir nem criar funciona (independe de
permissão: vale para root). A aba Biblioteca precisa mostrar o erro
tipado — nunca "biblioteca vazia".
"""

import pytest
from textual.widgets import Static

from ytmusic_tui.library import db_manager
from ytmusic_tui.library.errors import DatabaseInaccessibleError
from ytmusic_tui.tui.app import YTMusicTUI
from ytmusic_tui.tui.widgets import LibraryListView


@pytest.mark.asyncio
async def test_inaccessible_sqlite_shows_error_not_empty_library(tmp_path, monkeypatch):
    blocker = tmp_path / "bloqueado"
    blocker.write_text("sou um arquivo comum", encoding="utf-8")

    db_manager.close()
    monkeypatch.setattr(db_manager, "db_path", blocker / "library.db")

    app = YTMusicTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        # Erro tipado na abertura (T05) — o app não morre.
        assert isinstance(app.db_error, DatabaseInaccessibleError)

        await screen.refresh_library().wait()
        message = screen.query_one("#library-message", Static)
        assert message.display is True
        rendered = str(message.render())
        assert "Não foi possível carregar a biblioteca local" in rendered
        assert "inacessível" in rendered  # ação específica do caso (T05-R4)
        # O caso "biblioteca vazia" NÃO pode aparecer no lugar do erro.
        assert "Nenhuma faixa na biblioteca local ainda" not in rendered
        assert screen.query_one("#library-list", LibraryListView).display is False
