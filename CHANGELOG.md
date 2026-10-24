# Changelog

Todas as mudanças relevantes deste projeto são registradas aqui.
O formato segue livremente o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto usa [versionamento semântico](https://semver.org/lang/pt-BR/).

## [Não publicado]

### Adicionado

- **Rádio automática (P05):** ao fim da fila (repeat `off`), o app continua
  com uma faixa parecida e popular da rádio do YouTube Music. A busca roda
  em background, ignora a semente/indisponíveis/últimas 50 tocadas e cada
  faixa vira a semente da próxima. A opção `auto_similar` (padrão `true`)
  fica na aba Config; falha de recomendação só mostra um toast e mantém a
  fila. Não há home personalizada nem playlist gerada.

## [0.3.2] - 2026-10-22

### Corrigido

- **Downloads (A01):** a tabela não perde mais a seleção a cada 0,5 s — as
  colunas e as linhas usam chaves estáveis (`task.id`), a seleção é
  restaurada pela linha e as células são atualizadas no lugar. `r`/`d`/`c`
  passam a agir sempre na linha que está sob o cursor.
- **Configuração (A02):** `mpv_audio_quality`, `download_format`,
  `download_quality` e `library_db_name` são validados; TOML/ambiente
  inválido vira mensagem curta com campo/valor esperado e **exit 1**, nunca
  traceback. O `doctor` reporta a configuração inválida numa linha própria e
  a aba Config usa fallback defensivo no `Select`.
- **Player modal (A03):** eventos do player (auto-next, barra, erro) são
  processados mesmo com Ajuda/Confirmação/picker aberto — a `MainScreen` é
  resolvida pela pilha, não apenas pela tela ativa.
- **TOML (A04):** gravação atômica (temporário + `fsync` + `os.replace`,
  modo 0600) e round-trip com **tomlkit**: comentários, ordem e tabelas
  aninhadas são preservados; TOML inválido não é mais zerado em silêncio.
- **Downloads em voo (A05):** cada tarefa usa um `DownloadOptions` imutável
  (formato, qualidade, diretório, metadados) tirado no enfileiramento e o
  caminho final persistido — mudar a Config no meio não mistura extensão,
  pós-processador e destino.
- **Escritas da TUI (A06):** `LibraryError`/`IntegrityError` em playlists,
  curtidas e registro de download viram log + toast em 3 partes; a
  duplicata em playlist é idempotente. Exceção inesperada do worker de
  download vira `FAILED`, não traceback.
- **Biblioteca honesta (A07):** faixa enfileirada/falha/só stream aparece
  com a marca `☁` e o álbum mostra `✓ N/M baixadas` — nada mais finge que
  um arquivo ausente foi baixado. Decisão: listar (com rótulo) em vez de
  filtrar, para não esconder faixas curtidas/adicionadas a playlists.
- **Baixar álbum (A08):** só pula a faixa se `is_downloaded` **e** o
  arquivo existir no disco; arquivo sumido volta a ser enfileirado para
  reparação.
- **Downloads na UI (A09):** `c` cancela a tarefa selecionada, `C` limpa as
  concluídas/falhas, `r` também retenta `FAILED` e `i` abre o detalhe
  completo do erro. Cancelar não apaga arquivo final.
- **Seleção (A10):** refresh de Biblioteca/Playlists restaura a seleção
  pelo id (álbum, faixa avulsa, faixa do álbum ou playlist); item removido
  cai para o vizinho.
- **Testes do `doctor` (A11):** diretório de log isolado em tmp — a suíte
  deixa de ler/criar o log real do usuário.
- **Dependências (A12):** pisos alinhados ao testado (`textual>=8.2,<9`,
  `ytmusicapi>=1.12,<2`, `python-mpv>=1.0,<2`, `yt-dlp>=2026.8.19`), `rich`
  removido das dependências diretas e `tomlkit` declarado; `uv.lock`
  atualizado.
- **Launcher da raiz (A13):** `python main.py` delega ao CLI do pacote —
  `--version`, `doctor` e a mensagem de config inválida funcionam do fonte.
- **Busca concorrente (A14):** cliente/sessão do ytmusicapi é **por
  thread** (a UI e os três workers não compartilham cookies/visitor id);
  uma falha não espera as outras chamadas lentas.
- **Transições (A15):** `try_transition` faz check + set sob lock por
  tarefa; worker, cancelamento e `shutdown` não sobrescrevem o vencedor nem
  vazam `InvalidTransitionError`.

### Adicionado

- **Barra do player (A16):** volume inicial vem da configuração e a barra
  mostra "Nada tocando" (sem `" — "` órfão); título/artista só de um lado
  não ganha separador.
- **Modais (A17):** CSS próprio para nome/picker (centralizados, com
  scroll) e botões de mouse em Confirmação (Confirmar/Cancelar), Nome
  (Criar/Salvar + Cancelar) e Picker (Cancelar), testados em 80×24.
- **Limite da Biblioteca (A18):** quando a lista chega a 500, o total real
  é consultado e a legenda avisa "Mostrando N de M" — sem truncamento
  silencioso.

### Documentado

- **Site oficial:** página estática em `site/` redesenhada como manual
  impresso (papel, azul de impressão e tipografia própria) e publicada via
  GitHub Pages com workflow dedicado. Sem mudanças no app.
- README: contagem de testes e limitações de temas corrigidas (tema vale
  por sessão; "custom themes" segue no roadmap).
- Governança interna do ciclo: o plano de engenharia anterior vira
  **documento histórico** e o backlog passa a seguir a análise de código
  de 2026-10-19 — documentos de trabalho do mantenedor, fora do repo.
- A19 (histórico/stats/seek/speed) permanece como dívida planejada para
  trabalho futuro — nenhuma API foi removida.

## [0.3.1] - 2026-10-19

### Adicionado

- Busca: a tecla `p` adiciona à playlist a faixa **destacada** nos resultados (músicas e vídeos) — já na biblioteca, entra direto pelo picker; ainda não baixada, o aviso mostra o caminho (`Enter` para tocar, `Ctrl+D` para baixar); sem lista de resultados focada, vale a faixa em reprodução, como antes (F9)

## [0.3.0] - 2026-10-18

### Adicionado

- Busca: nova seção **🎬 Vídeos** (até 20) — clipes e lives aparecem junto das músicas e tocam/baixam como **áudio** pelo pipeline normal (mp3/flac), entrando na biblioteca como faixa (F10)

## [0.2.3] - 2026-10-18

### Corrigido

- Escolher outra faixa com o player **pausado** agora começa a tocar direto: o `pause` do mpv é zerado no `play()` — antes a faixa nova entrava pausada e a barra ainda exibia "tocando" (relato de campo, BUG-02)

### Documentado

- README: a troca de tema pelo **command palette** (`Ctrl+P` ou o botão ⭘ do cabeçalho → **Theme**) entra nos destaques, na tabela de atalhos e numa nota da configuração — sem persistência: vale para a sessão

## [0.2.2] - 2026-10-18

### Corrigido

- Ajuda (`F1`): a tecla do álbum → playlist aparece como **`Shift+P`** —
  antes `p` e `P` renderizavam como dois "P" idênticos na lista de atalhos
  (o `format_key` agora reconhece maiúscula como Shift, a semântica do
  Textual)

## [0.2.1] - 2026-10-18

### Corrigido

- `Shift+P` (álbum → playlist) agora funciona também com o álbum apenas
  **destacado** na Biblioteca — antes só valia com o álbum aberto (Enter)
  e, fora do modo álbum, a tecla inativa era um silêncio total; sem álbum
  alvo o app explica o gesto em vez de silenciar (ex.: faixa avulsa
  destacada)

## [0.2.0] - 2026-10-18

### Adicionado

- Playlists locais (T17.1): a aba 🎵 **Playlists** substitui a antiga 🏠
  Início — o app agora abre na 🔍 Busca. A lista mostra `nome · N faixas`,
  `Enter` abre a playlist (faixas na ordem de posição) e `Enter` numa faixa
  toca com a fila na ordem da playlist; `N` cria (modal de nome), `E`
  renomeia, `Delete` exclui com confirmação, `Shift+↑`/`Shift+↓` movem a
  faixa e `Delete` no detalhe a remove — a reordenação usa uma transação só.
  Falha de leitura tem mensagem própria (nunca parece "nenhuma playlist") e
  os estados vazio/carregando/erro passam por worker + época
- Adicionar à playlist (T17.1): `P` adiciona a faixa selecionada na
  Biblioteca (avulsa ou dentro do álbum) — ou, fora dela, a faixa em
  reprodução, espelho do `L` — por um seletor com "➕ Nova playlist…" que
  cria e já adiciona; `Shift+P` adiciona o álbum aberto inteiro. Repetida
  não duplica ("já estava") e o recado do álbum é o que aconteceu de fato:
  "N adicionadas · M já estavam · K fora da biblioteca (baixe para
  incluí-las)", com K apenas quando o `track_count` do álbum é conhecido;
  nada a adicionar tem mensagem própria, e faixa fora da biblioteca local
  pede o download primeiro
- Biblioteca por álbum (T17.1): o nível 1 lista álbuns
  (`💿 Título — Artista  (N faixas)`, com `⚠ N ausentes` quando há) e a
  seção "— Faixas avulsas —"; `Enter` expande o álbum dentro da própria
  aba (cabeçalho com título/artista/ano/contagem, faixas em ordem de
  disco/número, `t` toca o álbum com a fila em ordem e `Esc`/`←` voltam ao
  nível 1). Arquivo não encontrado na última reconciliação aparece marcado
  (⚠) por faixa — na biblioteca e nas playlists — com legenda, e tocar uma
  faixa ausente avisa sem travar
- Downloads (T17.1/Q13b): quando o arquivo já baixado é encontrado no
  disco, o desfecho aparece na tabela ("Já existia") e o toast é próprio
  ("validado e registrado sem baixar") — o arquivo existente nunca é
  sobrescrito, e agora isso fica visível
- Testes: 585 no total (era 554) — novos cenários de playlists, biblioteca
  por álbum, ausências, desfecho de download e regressão do encerramento

### Alterado

- README: destaque de leveza ganhou números — com o app recém-iniciado na
  máquina de referência, ~90 MB de memória (PSS, cota proporcional) e
  ~125 MB residentes (RSS); a linha de apresentação também menciona
  "lightweight"
- README: a nota "~80% vibe-coded" virou a seção **Development methodology
  & AI stack** — projeto apresentado como AI-native (fluxo prompt-driven
  multi-modelo; papéis humano/IA; camadas de qualidade), com selo, citação
  de topo e limitações atualizados
- README: a seção *Releases and updates* deixou de tratar automação e
  hashes como "future step" — o fluxo do T16 já publica `SHA256SUMS` e os
  hashes no corpo da Release

### Corrigido

- Encerramento (Q20): eventos de player enfileirados durante o teardown
  podiam derrubar o app com `NoMatches` na barra "tocando agora" (o flake
  do T16); agora são descartados assim que o app começa a parar e a barra
  usa busca tolerante — com teste de regressão do caso
- Workflow de release: a conferência do conteúdo dos artefatos não usa mais
  `… | grep -q` (o `grep` sai no primeiro match e o produtor — `unzip`/`tar`
  — morria com SIGPIPE; sob `pipefail`, o step caía). Pego no primeiro uso
  real do processo, em 2026-10-18, com a Release ainda em rascunho — nada
  quebrado ficou público; o `ci.yml` usava o mesmo padrão (mascarado pela
  ausência de `pipefail`) e recebeu a mesma correção

## [0.1.5] - 2026-10-17

### Adicionado

- Logs estruturados em JSON Lines (T13): cada linha do log é um objeto
  JSON com campos fixos (`ts`, `level`, `component`, `operation_id`,
  `event`, `error_code`, `message`; `detail` com o traceback em erros) e
  eventos para subida/parada do app (com as versões), download
  iniciado/concluído/falhou (código de saída do ffmpeg quando presente),
  retentativas da API, migrações do banco, erros de banco, quarentena e
  recuperação de downloads interrompidos — dá para filtrar com `jq`
- Privacidade no log (T13): o mascaramento é aplicado na gravação — HOME
  vira `~`, URLs perdem query/token e cabeçalhos de credencial
  (`Authorization`, `Cookie`, `Set-Cookie`) viram `[REDACTED]` — e a
  consulta digitada na busca deixou de ser registrada
- Novo diagnóstico `ytmusic-tui doctor` (T13): checa versões, libmpv,
  `mpv`/`ffmpeg`, diretórios (existência, escrita, modo), banco da
  biblioteca em somente leitura (`integrity_check` + versão do esquema ×
  app) e resíduos de manutenção, com saída mascarada pronta para colar
  numa issue; exit code 0 (avisos) ou 1 (algo essencial quebrado), sem
  abrir a TUI
- Monitor de contrato agendado (T09): o workflow `contract.yml` roda o L3
  (integração real) toda segunda às 06:00 UTC e sob demanda
  (`workflow_dispatch`), com regra de ruído zero — inconclusivo (sem
  rede, serviço fora, runner bloqueado) vira skip com motivo e job verde;
  mudança real de contrato falha com o relatório por campo/item
- Suíte ponta a ponta controlada (T09): `tests/e2e/` (opt-in com
  `YTMUSIC_E2E=1`, nunca no CI) constrói o wheel, instala num venv limpo
  e roda doctor + busca + álbum + player + download reais, tudo isolado
  em tmp e com limpeza conferida; runbook em `tests/e2e/README.md`
- Pacote publicado é instalado e executado de verdade no CI (T14): o job
  `pacote` instala **wheel e sdist** em ambientes limpos e roda o
  `doctor` (exit 0) pelos dois entry points suportados — console script
  e `python -m ytmusic_tui`; o sdist ganha cobertura de instalação pela
  primeira vez
- Ciclo de vida da instalação documentado e testado (T14): atualizar,
  downgrade com pin de versão exata, desinstalar (a biblioteca e os
  downloads **não** são removidos) e remoção de dados locais, com o caso
  real do pipx em container (distrobox/toolbox) e receita de isolamento
  por `PIPX_HOME`/`PIPX_BIN_DIR`
- Matriz de suporte publicada (T14): LMDE 7/Mint = referência;
  Ubuntu/Debian compatíveis = distribuição; outra distro Linux =
  compatibilidade; terminal 80×24 = interface — macOS/Windows nativos
  não são oficialmente suportados; Python 3.11–3.13 (3.12 validado com a
  suíte completa)
- Processo de release documentado (T16): runbook com as 10 etapas
  obrigatórias (§14.3), versionamento semântico, rollback (versão, banco e
  publicação) e o portão GQ-01..GQ-20 como checklist operacional — mais o
  workflow `release.yml`, que reage a uma tag autorizada: correspondência
  tag ↔ commit ↔ versão, guarda de CI verde, build limpo, `SHA256SUMS`,
  notas extraídas do CHANGELOG e verificação do publicado (download,
  hashes, `--version` e `doctor` em venvs limpos) antes de sair do
  rascunho
- Ruído benigno do mpv/PipeWire fora da UI (T16/Q19): avisos de
  configuração do libpipewire (`pw.conf`/`conf.c:*`/`client.conf`) não
  aparecem mais sobre a TUI; o mpv fala pelo log interno (ruído conhecido
  filtrado) e erros reais seguem visíveis na interface
- `ytmusic-tui --version` (T16): imprime a versão instalada — útil em
  relatos de bug e na verificação do artefato publicado

### Alterado

- Teste de integração real (L3) separa inconclusivo de contrato (T09):
  falha de rede/serviço/rate limit vira skip com motivo explícito
  ("inconclusivo: … — sem veredito de contrato"); contrato quebrado (ou
  resposta fora do contrato do ytmusicapi) falha como sempre — a mesma
  regra vale para o job agendado
- README em inglês (T09): o arquivo inteiro passa para o inglês (código,
  strings da interface, comentários e o CHANGELOG seguem em pt-BR)
- CI falha rápido com diagnóstico (T14): `timeout-minutes` por job
  (testes 45, segurança 15, pacote 20) e no passo informativo de
  cobertura (20), mais `faulthandler` do pytest (10 min por teste, com
  dump de todas as threads e saída imediata) — resposta ao incidente de
  2026-10-17, quando um passo travado consumiu ~5h48m até o limite de 6 h
  do GitHub sem nenhum stack
- Mensagem de primeira inicialização orienta a instalação do mpv por
  família de distro (T14) e os requisitos do README marcam o que é
  obrigatório × opcional

### Corrigido

- Monitor de contrato não cobra mais o `browseId` do detalhe do álbum
  (T09): o `get_album` do ytmusicapi não ecoa o campo (é a entrada da
  chamada, e o app já o tem) — a primeira rodada real do L3 flagrou a
  exigência impossível herdada do T10-R6
- Encerramento do app sob carga (T13): o registro de parada (`app_stop`)
  sai depois do teardown dos componentes — registrá-lo antes alargava a
  janela de uma corrida rara do encerramento do player (herdada do T11)
  que travava a suíte de integração local; a ordem ficou travada por
  teste de regressão
- README: `pip install --user` ganha aviso de que distros com Python
  "externamente gerenciado" (PEP 668 — Debian/Ubuntu/Mint/LMDE) recusam
  esse caminho (T14), comprovado numa rodada real em Ubuntu 24.04; lá, o
  caminho é pipx ou um venv dedicado
- Deadlock raro do encerramento do player (T11.1/Q14): um evento do
  libmpv em voo durante o teardown prendia a thread de eventos em
  `call_from_thread` — esperando o loop, que por sua vez esperava o
  `join()` do `terminate()` — o travamento capturado pelos dumps do CI em
  2026-10-17; a entrega player→UI vinda de outra thread passou a usar
  `post_message` (thread-safe, não bloqueante) e um teste de regressão
  força a janela com o handle real
- Isolamento de configuração do L4 (T16/Q16): `YTMUSIC_CONFIG_DIR` agora
  move o `config.toml` junto (leitura e gravação) — antes o fluxo de
  ponta a ponta lia (e poderia gravar) o config real do usuário; a
  regressão é coberta por testes e por guarda no próprio L4

## [0.1.4] - 2026-10-16

### Adicionado

- Falha de stream no player agora aparece na interface (T11): quando o mpv
  não consegue carregar a URL ou o arquivo (indisponível, inexistente), a
  barra de reprodução mostra "falha" e um aviso "Erro no player: …" com o
  motivo — em vez de silêncio, sem traceback
- Falha de extração do yt-dlp no download tem mensagem própria (T11): diz o
  que falhou, que **nada foi baixado nem publicado** e a próxima ação
  ("atualize o yt-dlp e tente de novo"), preservando o detalhe original —
  antes caía no genérico "Erro inesperado"
- Encerramento do player é auditável (T11): fechar o app encerra o `yt-dlp`
  usado pelo hook do mpv (nenhum processo órfão ou zumbi) e eventos
  atrasados do player não alcançam uma interface já desmontada
- Ajuda completa na tecla F1 (T12): o modal lista os **atalhos reais**
  (extraídos dos bindings — sem cópia que desatualiza), com Esc para fechar,
  e as teclas de download só disparam na aba certa
- Estados de tela visíveis (T12): busca, biblioteca, álbum, player e
  downloads mostram carregando/vazio/sem resultados/falha em vez de tela
  ambígua — inclusive "carregando…" na barra de reprodução e uma dica de
  como começar quando a fila de downloads está vazia
- Descarte de download interrompido pede confirmação (T12): `d` pergunta
  (y/Enter confirma, n/Esc cancela); onde nada é destruído, não pergunta

### Alterado

- A URL de watch do YouTube Music (`music.youtube.com/watch?v=…`) passa a
  existir em um único módulo, com teste que falha se o literal reaparecer
  em qualquer outro arquivo (T11)
- Mensagens de erro em 3 partes (T12): o que falhou, se os dados do usuário
  estão preservados e a próxima ação — sem traceback e com detalhes
  mascarados (§8.3)
- Strings pt-BR da interface (avisos, erros, ajuda, dicas) em um ponto único
  (`tui/messages.py`) (T12)
- Configuração salva a cada alteração real, sem estado pendente (T12):
  abrir a aba não regrava o arquivo nem avisa à toa; falha de escrita vira
  aviso mascarado e nada mais se perde
- Barra de reprodução se adapta ao terminal (T12): em telas estreitas o
  volume e o progresso saem de cena e a faixa + botões continuam visíveis;
  títulos com emoji/CJK/colchetes aparecem corretos (escape próprio de
  markup — `[LIVE]` não some mais)

### Corrigido

- Com áudio real, o fim de faixa não avançava para a próxima (T11): o
  evento do mpv era comparado num formato que nunca casava (os campos
  chegam como bytes) — o avanço automático volta a funcionar e a falha de
  stream passa a virar erro na interface
- Resposta antiga de busca/álbum não sobrescreve mais a mais recente (T12):
  cada operação tem época de render
- Sair com download em andamento agora aparece como `INTERRUPTED` na
  próxima sessão (T12): a interface não fica mentindo "baixando" até a
  próxima recarga
- Leitura da biblioteca não congela mais a interface (T12): a consulta roda
  fora do loop de eventos

## [0.1.3] - 2026-10-16

### Adicionado

- Publicação atômica de downloads (T03): o arquivo vai para um temporário em
  `download_dir/.tmp/` (mesmo filesystem) e é publicado com `os.replace` +
  `fsync`, com reserva exclusiva do nome final — nada é sobrescrito em
  silêncio e duas tarefas não disputam o mesmo arquivo
- Se o arquivo de destino já existir, o download não acontece: o existente é
  validado, registrado na biblioteca (bitrate desconhecido) e a faixa é
  concluída; se for inválido, a tarefa falha sem apagar nada
- Reconciliação na inicialização (T03): arquivos válidos sem registro são
  importados para a biblioteca, arquivos inválidos vão para
  `download_dir/.quarantine/` (modo 0700), registros sem arquivo são
  relatados no log e temporários/`.part` com mais de 24 h e prefixo do
  próprio app são removidos — nunca arquivos finais
- Downloads que ficaram pela metade sobrevivem ao encerramento (T04): o
  ciclo de vida das tarefas é persistido em tabela própria
  (`download_tasks`) e, na execução seguinte, aparecem como interrompidos na
  aba 📥, onde `r` retoma e `d` descarta
- Retomar nunca reaproveita o `.part`: é uma nova tarefa (com `retry_of`) e
  os temporários da tentativa antiga — inclusive o lock `.reserva` órfão de
  um encerramento abrupto — são descartados antes de baixar de novo; o
  arquivo final do usuário nunca é tocado
- Temporários/`.part` de download aguardando decisão não são mais removidos
  pela limpeza de 24 h: com dono, a decisão (retomar/descartar) é do
  usuário; sem dono, a política de idade continua valendo
- O banco da biblioteca agora tem migrações versionadas (T05): a versão do
  esquema fica gravada no próprio banco e um banco criado por versões
  anteriores é migrado sem perder dados; banco novo já nasce no esquema
  atual, estampado — e esquema mais novo que o app vira erro claro, nunca
  `create_all` silencioso
- Backup automático do banco antes de qualquer migração (via `VACUUM INTO`,
  nunca cópia de arquivo em uso): fica na pasta `backups/`, ao lado do
  banco, com retenção dos 5 mais recentes — backup falhou, não migra
- Diagnóstico de integridade do banco (`PRAGMA integrity_check`) disponível
  por API para o futuro comando de diagnóstico (T13)
- Faixas cujo arquivo registrado não está mais no disco são marcadas como
  ausentes (`is_missing`, migração v2 do banco): a reconciliação marca na
  primeira ocorrência e desmarca quando o arquivo volta; registrar o arquivo
  de novo limpa a marca — "registro sem arquivo" deixa de viver só no log
- Mensagens da aba Biblioteca específicas por tipo de falha (permissão,
  banco corrompido, esquema incompatível, backup/migração falhados, banco em
  uso): cada caso explica o que houve e o que fazer, em vez de um erro
  genérico — inclusive quando a falha acontece na abertura do banco
- Aviso ao tocar uma faixa cujo arquivo registrado não está mais no disco
  (em vez de mandar o player abrir um caminho inexistente); a reconciliação
  marca a faixa como ausente na passagem seguinte
- README: seção **"Versões conhecidas boas"** (Python 3.11–3.13, mpv/libmpv,
  ffmpeg e yt-dlp validados, com comandos de checagem) e subseção
  **"Releases e atualizações"** (release = commit + versão + CI verde, manual;
  fluxo Dependabot → PR → CI → merge) (T07)
- Camada L2 de testes (T08): integração local **sem rede** (bloqueio autouse
  de `socket`, com mensagem clara) e falsos reais — yt-dlp stubado no import
  tardio e **ffmpeg como script executável de verdade no PATH** — cobrindo
  download completo, conversão falhando (exit ≠ 0), ffmpeg ausente com o que
  instalar, conversão lenta + cancelamento do usuário, extração com erro,
  disco cheio (ENOSPC real do kernel), busca offline e com resposta
  malformada, SQLite inacessível (nunca "biblioteca vazia") e app morto com
  `SIGKILL` no meio do download — recuperado na sessão seguinte pelo caminho
  real do worker
- Busca valida a forma da resposta da API (T08): lista malformada vira erro
  tipado (`InvalidResponseError`) com mensagem "esperado × recebido", em vez
  de um `AttributeError` genérico — músicas e álbuns são validados antes do
  uso, e a UI mostra o caso sem mudanças
- Integração com o YouTube Music endurecida (T10): a camada `api/` devolve
  **modelos internos** (título, artistas, duração, álbum, thumbnails) —
  nenhuma camada acima vê objetos do `ytmusicapi`; campos ausentes × inválidos
  são explícitos e o formato duplo de alguns campos (álbum como objeto ou
  texto) é aceito
- Erros de rede/serviço classificados (T10): sem rede, tempo esgotado,
  serviço fora, não encontrado, resposta inválida e limite de requisições
  viram mensagens compreensíveis na busca, com o erro cru preservado como
  causa para o log
- Tentativas automáticas só para falhas transitórias (T10): até 3 tentativas
  com espera crescente (0,5 s → 1 s → 2 s); HTTP 429 re-tenta, 4xx definitivo
  não; uma busca nova abandona as tentativas da anterior — a primeira
  tentativa de cada busca sempre roda
- Timeouts separados de conexão (5 s) e leitura (15 s) (T10): conexão que
  morre não fica pendurada esperando o servidor; pior caso ~60 s documentado
  (3 tentativas por requisição, 2 requisições em paralelo)
- Cache em disco de busca (5 min) e álbum (24 h) (T10): resposta recente sai
  do cache sem tocar a rede; envelope versionado, escrita atômica, teto de
  20 MiB com poda dos mais antigos e diretório `cache_dir/api` 0700 — URL de
  stream **nunca** é cacheada, e falha de cache nunca quebra o app
- Aba ⚙️ Config informa a versão instalada × testada de `ytmusicapi` e
  `yt-dlp` (T10): quando a instalada é mais velha que a do `uv.lock`, orienta
  atualizar; um teste amarra as duas pontas (lock novo sem confirmar a versão
  testada = CI vermelho)
- Monitor de contrato do serviço real (T10): o teste de integração opt-in
  compara a resposta crua com o que os adaptadores consomem e gera um
  relatório por campo e por item quando o formato mudar — o anexo da correção

### Alterado

- Downloads usam uma máquina de estados explícita (T02): novos estados
  "validando" e "interrompido"; cancelar não afeta as outras tarefas e nunca
  sobrescreve um resultado já finalizado; encerrar o app marca downloads em
  voo como interrompidos (retomada e descarte na aba 📥)
- Faixas diferentes que gerariam o mesmo arquivo final não são mais
  enfileiradas em paralelo (evita disputa pelo mesmo caminho de saída)
- A tabela de downloads não duplica mais a linha da tarefa em execução
  (fila + ativos eram somados) e a linha selecionada é a referência das
  ações de retomar/descartar
- Falhas do banco da biblioteca têm erros tipados (inacessível, sem
  permissão, esquema incompatível, corrompido): a mensagem mostrada diz o
  caso real e a inicialização do app nunca cria/derruba tabelas em
  silêncio — e falha de banco não derruba o app
- Desenvolvimento/CI agora usam lockfile (`uv.lock`, uv): as versões exatas
  de runtime + dev ficam travadas num resolve único que cobre Python
  3.11–3.13, e o CI instala dele — falha se `pyproject.toml` e lock
  divergirem; contribuir sem uv continua possível via
  `pip install -e ".[dev]"` (sem lockfile) (T07)
- Auditar dependências passou a ser sobre o **conjunto travado** do
  lockfile (`uv export` → `pip-audit -r`) em vez do ambiente flutuante (T07)
- Dependabot migra para o ecossistema `uv`: atualiza `uv.lock` e
  `pyproject.toml` juntos (grupos dev/runtime mantidos) (T07)

### Corrigido

- Instalação limpa do wheel/sdist não incluía `tui/app.tcss` (único arquivo
  não-`.py` de runtime do pacote): o app morria no startup com
  `StylesheetError` — o CSS passa a ser empacotado explicitamente, o job
  "pacote" do CI confere o conteúdo do artefato (wheel e sdist) antes de
  instalar e uma guarda de fonte garante o arquivo ao lado de `app.py`
  (T06.1)
- O timer que atualiza a tabela de downloads podia derrubar o app durante o
  encerramento (acessava a tela sem verificar se ainda havia telas na
  pilha): agora tolera o instante — como os eventos do player — e um teste
  de regressão reproduz o caso exato (T08)

### Segurança

- Endurecimento geral (T06): o caminho de cada download é normalizado uma
  única vez e o destino é contido dentro de `download_dir` **depois** de
  resolver symlinks e `..` — nada é criado, baixado ou publicado fora da
  pasta de mídia, nem com entrada hostil, banco adulterado ou colisão entre
  tarefas
- yt-dlp com limites de rede (T06): timeout de socket de 30 s e 3
  retentativas (3 para fragmentos) — stream travado vira falha clara em vez
  de download pendurado para sempre
- yt-dlp ou ffmpeg ausente vira falha da tarefa com o que instalar (T06); o
  app avisa uma vez por execução quando só o yt-dlp falta, e o ffmpeg só é
  exigido na hora do download
- Ciclo de vida do mpv explícito (T06): `start`/`shutdown` idempotentes,
  falha ao iniciar vira estado de erro na TUI (nunca exceção solta) e
  `LC_NUMERIC=C` é fixado ao criar cada instância
- Permissões privadas para dados e trabalho (T06): diretórios internos
  (config, dados, cache, log, backups, `.tmp`, `.quarantine`) nascem 0700 e
  arquivos (banco + sidecars, backups, log rotacionado, `config.toml`) 0600
  **mesmo com `umask 0`**; o que já existe só é apertado (nunca afrouxado),
  com registro no log — e `download_dir` é mídia do usuário, nunca é
  chmodado
- Mensagens de erro (yt-dlp, mpv, banco) exibidas na TUI são mascaradas
  (T06): HOME vira `~` e URLs perdem query/credenciais — o log preserva o
  detalhe cru
- `set_concurrent` valida o valor recebido (tipo e faixa 1–10) em vez de
  ajustar em silêncio (T06); a aba Config normaliza antes de aplicar e avisa
  em caso de valor inválido

## [0.1.2] - 2026-10-15

### Adicionado

- Auditoria de vulnerabilidades conhecidas (`pip-audit`) nas dependências:
  no CI e nas ferramentas de desenvolvimento
- Dependabot também para as GitHub Actions do CI (com `checkout` e
  `setup-python` atualizados para a v7, sem os avisos de depreciação do Node)
- Gates de formatação (`ruff format --check`) e tipagem (`mypy`) no CI, além
  de um job que constrói o pacote (sdist + wheel) e testa a instalação dele
  em ambiente limpo (import do app + console script)
- Teste que garante que `__version__` e o `version` do pyproject não divergem

### Alterado

- `ruff format` aplicado em todo o `src/` e `tests/` (sem mudança de
  comportamento)
- Tipagem: os 116 erros do `mypy` foram zerados — anotações completas nos
  módulos, `Base` declarativa no estilo SQLAlchemy 2.0 e overrides
  documentados para as bibliotecas sem stubs (`mpv`, `yt_dlp`) e para a
  dívida conhecida de `Column[...]` em `library.service`
- Metadados de licença no formato SPDX (`license = "MIT"` + `license-files`),
  eliminando os avisos de depreciação do setuptools (prazo: fev/2027)

### Corrigido

- Selo de testes no README (e as demais menções): dizia 55, mas a suíte tem
  62 testes passando (valor conferido com `pytest` em ambiente limpo, como
  manda o manual)
- Nomes de arquivo com muitos caracteres multibyte (acentos, CJK, emoji)
  agora são cortados também por bytes (limite de 255 bytes por componente do
  filesystem), evitando falha do download por nome longo

## [0.1.1] - 2026-10-15

### Adicionado

- Logging em arquivo (com rotação, em `~/.local/state/ytmusic-tui/log/` no
  Linux) — nunca no stderr, que sujaria a TUI
- Dependabot semanal para as dependências Python (PRs testadas pelo CI)

### Alterado

- A TUI foi dividida em módulos menores: `tui/screens.py` (tela principal),
  `tui/widgets.py` (widgets) e controllers de reprodução, downloads e
  biblioteca — o `app.py` ficou com o ciclo de vida do app e o `main()`
  (sem mudança de comportamento)

### Corrigido

- Falha ao carregar a biblioteca local não parece mais uma "biblioteca vazia":
  o erro aparece na aba 📚 (com a mensagem padrão preservada para quando o
  carregamento voltar a funcionar) e fica registrado no log

## [0.1.0] - 2026-10-15

Primeira versão publicada. 🎉

### Adicionado

- Busca de músicas e álbuns (duas requisições em paralelo, ~2s)
- Reprodução via mpv (libmpv) com qualidade selecionável e fila contínua
- Aba 💿 Álbum: faixas, artista, ano e download do álbum inteiro (`A`), pulando
  as faixas já baixadas
- Downloads via yt-dlp + ffmpeg (MP3/Opus/M4A/FLAC, tags ID3 e capa embutida),
  com progresso em tempo real e concorrência configurável
- Biblioteca local em SQLite (faixas baixadas e curtidas locais)
- Configuração por TOML e variáveis de ambiente (`YTMUSIC_*`), editável na aba
  ⚙️ Config do app
- 55 testes automatizados (offline, com mocks) + testes de integração opcionais
  (`YTMUSIC_INTEGRATION=1`)
- CI no GitHub Actions (ruff + testes em Python 3.11 e 3.13)

### Corrigido

- Downloads duplicados: o mesmo vídeo não é mais enfileirado duas vezes
  enquanto está em andamento (dois downloads disputariam o mesmo arquivo)
- Primeiro contato sem o mpv instalado agora mostra uma mensagem clara em vez
  de um traceback do python-mpv

### Limitações conhecidas

- Opera sem conta Google (o OAuth do ytmusicapi foi descontinuado em 2025);
  curtidas e biblioteca são locais
- ~80% do código foi produzido em "vibe coding" com IA (ver README)
