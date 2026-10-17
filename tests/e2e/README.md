# L4 — ponta a ponta controlado (opt-in, local)

Esta pasta guarda a camada L4 (§10 do plano): o fluxo real de ponta a ponta,
rodado **sob demanda** e **sempre isolado em tmp**. Nunca entra no CI —
depende de rede, de mpv e de yt-dlp/ffmpeg do host (o CI cobre L1/L2 + o L3
agendado). Localmente, o pré-release exige L3 + L4.

## O que é automatizado (`test_e2e.py`)

Com `YTMUSIC_E2E=1`, um teste faz, em sequência:

1. **build do wheel** a partir de uma cópia do checkout (o repositório não
   é sujo);
2. **venv limpo + instalação do wheel** (o artefato publicável com as
   dependências do PyPI — não o código-fonte);
3. **`ytmusic-tui doctor`** no venv instalado (exit 0 esperado — gancho do
   T13);
4. **fluxo real** por subprocesso no venv (`e2e_flow.py`): busca → álbum →
   player (mpv; o critério é o stream carregar, não áudio audível) →
   download (yt-dlp + ffmpeg) → arquivo válido + linha na biblioteca;
5. **TUI de verdade num pty**: aguarda a tela montar e sai com `Ctrl+Q`;
6. **limpeza**: sem `*.part`, `.tmp` e `.quarantine` vazios, sem processo
   órfão apontando para o tmp.

Tudo aponta para o **tmp do pytest** (`YTMUSIC_CONFIG_DIR`, `YTMUSIC_DATA_DIR`,
`YTMUSIC_CACHE_DIR`, `YTMUSIC_DOWNLOAD_DIR` e `XDG_STATE_HOME`): nada toca os
dados, o log ou os downloads reais. Desde o T16 (Q16), o próprio
`config.toml` acompanha o `YTMUSIC_CONFIG_DIR` — o arquivo real do usuário
nunca é lido nem gravado, e o teste ainda guarda (tamanho/mtime) do arquivo
real antes/depois como regressão. O arquivo baixado morre com o tmp.

## Como rodar

```bash
# na máquina do autor (rede real; mpv + ffmpeg instalados)
YTMUSIC_E2E=1 .venv/bin/python -m pytest tests/e2e -v -s

# apontando outra faixa autorizada (o download é removido no fim)
YTMUSIC_E2E=1 YTMUSIC_E2E_QUERY="nome da faixa" .venv/bin/python -m pytest tests/e2e -v -s
```

Dura alguns minutos (build + pip + download real). Para inspecionar o que
rodou antes do pytest apagar o tmp:

```bash
YTMUSIC_E2E=1 .venv/bin/python -m pytest tests/e2e -v -s --basetemp=/tmp/e2e-l4
ls -R /tmp/e2e-l4   # fonte copiada, dist/, venv/, music/, state/…
```

> **Instabilidade externa (Q17):** o fluxo depende do YouTube — uma rodada
> pode falhar num passo de rede (player que não carrega ou `403` no download)
> por oscilação do serviço, como o app real também sofreria no mesmo momento.
> A assinatura em rajada é `no audio or video data played` **com a extração
> do yt-dlp ok (rc=0)**. Regra para a release (docs/release.md): repetir
> **uma única vez**, preservando o primeiro resultado no relatório;
> assinatura diferente (ou sem a extração ok) é falha real — investigue,
> nunca mascare.

## Conferência manual (a TUI de ponta a ponta)

O que a automação não cobre: a interação real (mouse/teclas) e o julgamento
de quem está usando — recomenda-se repetir **antes de um release** ou quando
algo cheirar estranho. Ambiente isolado (não obrigatório, mas recomendado):

```bash
export YTMUSIC_CONFIG_DIR=/tmp/e2e-manual/config YTMUSIC_DATA_DIR=/tmp/e2e-manual/data \
       YTMUSIC_CACHE_DIR=/tmp/e2e-manual/cache YTMUSIC_DOWNLOAD_DIR=/tmp/e2e-manual/music
ytmusic-tui
```

Checklist:

1. `Ctrl+S` busca a faixa autorizada; `Enter` toca (som audível, se houver
   áudio no host);
2. `Ctrl+D` baixa a faixa em reprodução; a aba 📥 Downloads mostra o
   progresso até concluir;
3. `Ctrl+L` mostra a faixa na biblioteca; tocar toca do arquivo local;
4. abrir um álbum (`Enter` na busca) e `A` baixa o álbum inteiro;
5. sair com `Ctrl+Q` e conferir a limpeza:

```bash
pgrep -a yt-dlp                 # saída vazia: nada pendurado
find /tmp/e2e-manual -name '*.part' -o -name '.tmp' -o -name '.quarantine'
# nada parcial: sem *.part; .tmp/.quarantine vazios (ou ausentes)
```

Para colar como evidência: a saída do `ytmusic-tui doctor`, o trecho do log
(o mesmo mascarado de sempre:
`$XDG_STATE_HOME/ytmusic-tui/log/ytmusic-tui.log` — sem os `YTMUSIC_*`
isolados, o log real fica em `~/.local/state/ytmusic-tui/log/`) e os
comandos acima.
