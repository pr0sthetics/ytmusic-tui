#!/usr/bin/env python3
"""Launcher de desenvolvimento do ytmusic-tui (rodando do código-fonte).

Delega ao CLI oficial do pacote (``ytmusic_tui.main``) para que ``--version``,
``doctor`` e a mensagem de configuração inválida se comportem exatamente como
nos console scripts ``ytmusic-tui`` e ``python -m ytmusic_tui`` (A13). Antes,
este arquivo importava direto ``ytmusic_tui.tui.app``: ``--version`` e
``doctor`` abriam a TUI em vez de fazer o que prometem.
"""

import sys
from pathlib import Path

# Adicionar src ao path para desenvolvimento.
src_path = Path(__file__).parent / "src"
if src_path.exists():
    sys.path.insert(0, str(src_path))

# Import tardio de propósito: precisa vir DEPOIS do ajuste de sys.path acima
# para funcionar rodando o app direto do código-fonte.
from ytmusic_tui.main import main  # noqa: E402

if __name__ == "__main__":
    main()
