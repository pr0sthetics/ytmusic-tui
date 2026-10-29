# Release — runbook do processo (§14.3 · T16)

Este documento é o processo **completo** de release: as 10 etapas
obrigatórias, o versionamento, o rollback e o portão GQ-01..GQ-20 como
checklist operacional. Foi escrito no T16 (a automação + este runbook); a
**execução** de uma release é uma ação autorizada à parte, conduzida pela
auditoria — este texto é o que ela segue.

## Papéis: o que cada um faz

| Papel | O que faz | O que NÃO faz |
|---|---|---|
| Execução do T16 (esta entrega) | Runbook, workflow `release.yml`, correções do escopo (Q16/Q19) e `--version`; validações locais | Não cria tag, não faz bump de versão, não publica nada |
| Execução da release (autorizada) | Bump + CHANGELOG, suíte + L3/L4, tag anotada, acompanha o workflow, marca o portão GQ, monitora | Não reescreve tag publicada; não publica sem o workflow passar |

O workflow **reage** à tag (`v*`): nada nele cria tag ou decide versão. A
tag é o ato autorizado.

## Versionamento semântico (`0.x.y` até 1.0.0)

Enquanto o major for `0`, **interfaces, dados persistidos e comportamento
documentado ainda podem mudar** — o número comunica o tamanho do impacto:

- **patch** (`0.x.Y`): correções compatíveis; sem mudança de comportamento
  documentado;
- **minor** (`0.X.0`): funcionalidade nova; pré-1.0 pode incluir ajuste de
  comportamento documentado — anote com clareza no CHANGELOG;
- **1.0.0**: declara estáveis as interfaces, os dados persistidos e o
  comportamento documentado.

Regras fixas: a versão precisa ser **idêntica** em `pyproject.toml`,
`src/ytmusic_tui/__init__.py` (`__version__`), `uv.lock` e na tag (o
workflow falha se divergir); uma tag publicada **nunca** é movida/apagada
(quem pinou quebraria) — correção é release nova.

## Pré-requisitos (antes de começar)

- `main` sem pendências de CI (a guarda do workflow confere o commit da tag);
- L3/L4 locais rodados nesta janela (§ etapa 3) com as saídas registradas;
- nenhuma issue crítica/alta aberta sem mitigação (GQ-02/GQ-11).

## As 10 etapas obrigatórias (§14.3)

### 1. Atualizar versão + CHANGELOG

```bash
# a versão aparece em 3 lugares e precisa ser idêntica:
#   pyproject.toml        → [project] version = "X.Y.Z"
#   src/ytmusic_tui/__init__.py → __version__ = "X.Y.Z"
uv lock           # atualiza a entrada ytmusic-tui do lock
uv lock --check   # confirma a sincronia

# CHANGELOG: mantenha "## [Não publicado]" no topo (vazio) e insira
# "## [X.Y.Z] - AAAA-MM-DD" logo abaixo, com o conteúdo acumulado
# (sem reescrever entradas antigas).
```

Commite (`release: vX.Y.Z (versão + CHANGELOG)`), faça push (autorizado) e
confirme o CI verde do commit (`gh run list --workflow=ci.yml`). Se o
README citar exemplo de versão/tag, confira que a âncora continua válida.

**Critério:** `uv lock --check` limpo; seção da versão no CHANGELOG; CI
verde no commit.

### 2. Rodar toda a suíte obrigatória

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv lock --check
uv run pytest -q
```

**Critério:** tudo verde. A contagem de referência atual (v0.4.0) é
**712 passed + 4 skipped** — contagem diferente na release é para
investigar, nunca para "ajustar". Atualize esta referência a cada release.

### 3. Rodar L3/L4 definidos para a release (obrigatórios, locais)

```bash
YTMUSIC_INTEGRATION=1 .venv/bin/python -m pytest tests/test_integration.py -v -s   # L3 real
YTMUSIC_E2E=1 .venv/bin/python -m pytest tests/e2e -v -s                          # L4 (build + venv limpo + fluxo + TUI no pty)
```

Referências: L3 = **3 passed** em ~6 s (T09; no T16: 6,04 s); L4 = **1
passed** (T09: 66,86 s; no T16: 36,74 s) — o teste inclui build + venv
limpo + fluxo + TUI no pty. Registre as saídas exatas no relatório da
release. O L4 é onde o isolamento de configuração é guardado (o
`config.toml` real do usuário não pode mudar — guarda do próprio teste).
Se uma rodada falhar em passo de rede, aplique a regra da **Q17**
(abaixo) — antes de qualquer repetição, confira a assinatura.

**Critério:** L3 e L4 registrados; instabilidade externa tratada pela
regra de repetição; nenhuma falha real mascarada.

### 4. Build em ambiente limpo

```bash
rm -rf dist && uv build
ls -l dist/   # ytmusic_tui-X.Y.Z-py3-none-any.whl + ytmusic_tui-X.Y.Z.tar.gz
```

"Limpo" = a partir do commit da release, sem `dist/` velho. O workflow
faz exatamente isto na tag (`uv build` num checkout limpo).

**Critério:** os dois artefatos com a versão no nome; o wheel/sdist
contêm `ytmusic_tui/tui/app.tcss` (o workflow confere).

### 5. Instalar e testar o artefato

```bash
python3 -m venv /tmp/venv-release-wheel
/tmp/venv-release-wheel/bin/pip install dist/*.whl
/tmp/venv-release-wheel/bin/ytmusic-tui --version   # == X.Y.Z
/tmp/venv-release-wheel/bin/ytmusic-tui doctor      # exit 0

python3 -m venv /tmp/venv-release-sdist
/tmp/venv-release-sdist/bin/pip install dist/*.tar.gz
/tmp/venv-release-sdist/bin/ytmusic-tui --version
/tmp/venv-release-sdist/bin/ytmusic-tui doctor
```

**Critério:** wheel **e** sdist instalam em venv limpo; `--version` mostra
a versão; `doctor` sai com 0.

### 6. Confirmar correspondência pacote ↔ commit

```bash
git rev-parse HEAD                 # commit atual
git rev-parse "vX.Y.Z^{commit}"    # commit da tag (após criá-la, se local)
grep -n '^version' pyproject.toml
grep -n '__version__' src/ytmusic_tui/__init__.py
grep -n 'name = "ytmusic-tui"' -A 2 uv.lock
```

**Critério:** tag, commit, versão do pacote (pyproject), `__version__` e
`uv.lock` batem. O workflow **falha** em divergência (não publica).

### 7. Notas de release com limitações conhecidas

As notas publicadas são a **seção do CHANGELOG da versão** + o commit +
os hashes (o workflow gera; se a seção não existir, ele falha). Antes de
taguear, garanta que a seção reflete o que mudou **e** o que não está
coberto — limitações conhecidas ficam na seção *Limitations* do README e
na matriz de plataformas (ex.: WSL2 segue "not validated"); uma limitação
nova da janela precisa aparecer no CHANGELOG antes da tag.

**Critério:** seção do CHANGELOG completa; limitações conhecidas
acessíveis a partir das notas (README).

### 8. Anexar hashes SHA-256

O workflow gera `SHA256SUMS` (`sha256sum ytmusic_tui-*.whl
ytmusic_tui-*.tar.gz`), anexa como asset e imprime os valores **no corpo
das notas** (padrão da v0.1.4). Localmente:

```bash
cd dist && sha256sum ytmusic_tui-*.whl ytmusic_tui-*.tar.gz > SHA256SUMS && cat SHA256SUMS
```

**Critério:** os dois artefatos têm hash publicado (asset + notas).

### 9. Verificar instalação do publicado

O workflow faz isto **antes** de a Release sair do rascunho: baixa de
volta do GitHub Release, confere os hashes (`sha256sum --check`) e instala
wheel e sdist em venvs limpos (`--version` + `doctor`). Manual equivalente:

```bash
mkdir -p /tmp/verificacao && cd /tmp/verificacao
gh release download vX.Y.Z --pattern '*.whl' --pattern SHA256SUMS
sha256sum --check SHA256SUMS
python3 -m venv /tmp/venv-publicado
/tmp/venv-publicado/bin/pip install *.whl
/tmp/venv-publicado/bin/ytmusic-tui --version   # == X.Y.Z
/tmp/venv-publicado/bin/ytmusic-tui doctor
```

**Critério:** o artefato **publicado** (não o local) instala e roda. Se a
verificação falhar, a Release continua em rascunho — corrija e re-execute
o workflow (`gh workflow run release.yml -f tag=vX.Y.Z`).

### 10. Monitorar issues pós-release

Nas primeiras horas/dias: acompanhe o CI do `main` (próximos commits), o
monitor de contrato semanal (`contract.yml`, L3 agendado), e trie issues
novas. Regressão na release → procedimento de rollback abaixo (rollback do
**usuário** para a versão anterior + correção para frente).

## Execução da release (autorizada) — sequência

```bash
# 1) etapas acima feitas; commit da versão+CHANGELOG empurrado e CI verde
# 2) tag anotada + push (é o ato autorizado — nada no T16 faz isso):
git tag -a vX.Y.Z -m "ytmusic-tui vX.Y.Z"
git push origin vX.Y.Z

# 3) acompanhar o workflow (publica; verificação falhou = continua rascunho):
gh run list --workflow=release.yml --limit=3
gh run watch <id>
gh release view vX.Y.Z

# 4) marcada a checklist GQ abaixo; monitoramento da etapa 10 em diante.
```

`workflow_dispatch` existe para **re-execução**: conserta um rascunho que
falhou na verificação; se a release já estiver pública, ele apenas
reverifica o que está publicado (sem tocar nos artefatos). Falha **antes**
de criar a Release (guarda de CI, build, conferência de artefatos) também
se recupera assim: corrige-se o defeito em `main`, em commit novo
(fix-forward — a tag nunca se move), e o dispatch reconstrói o rascunho do
zero a partir da tag.

## Instabilidade externa do L4 — regra de repetição (Q17)

Assinatura **externa**: o fluxo falha no passo do player com
`no audio or video data played` **e a extração do yt-dlp saiu com rc=0**
(o hook extraiu; o stream não carregou) — observado em rajadas (janela
ruim do YouTube; ~50% em uma delas). Nesse caso:

1. repita o L4 **uma única vez**; registre os **dois** resultados — o
   primeiro fica preservado no relatório (não é apagado pelo segundo);
2. se a repetição passar **com a mesma assinatura externa**, registre como
   instabilidade externa (com a nota), sem tratar como aprovação plena;
3. assinatura **diferente** (ou extração sem rc=0) é falha real: investigue
   — nunca repita para "ver se passa".

Decisão registrada no T16: **sem retry automático** no código/teste (uma
atenuação mascararia falha real). A repetição é operacional e documentada.

## Rollback

### Versão (usuário final)

Pin exato de uma tag anterior — mesmos comandos da seção *Updating and
downgrading* do README:

```bash
pipx install --force git+https://github.com/pr0sthetics/ytmusic-tui@v0.1.3
pip install --user --force-reinstall git+https://github.com/pr0sthetics/ytmusic-tui@v0.1.3
```

Um install pinado não é movido por `pipx upgrade`; para voltar ao topo,
reinstale sem a tag. Os **dados permanecem** (downgrade não apaga nada).

### Banco de dados

Backups automáticos: antes de **qualquer** DDL de migração, o app cria um
`VACUUM INTO` em `data_dir/backups/` (`~/.local/share/ytmusic-tui/backups/`
por padrão), no formato
`library-AAAAMMDD-HHMMSS-v<de>-v<para>.db`; os 5 mais recentes são
mantidos. Se o backup falhar, a migração **não roda** (dados intactos).

Para restaurar (com o app fechado):

```bash
ls -lt ~/.local/share/ytmusic-tui/backups/                    # escolha o backup
mv  ~/.local/share/ytmusic-tui/library.db \
    ~/.local/share/ytmusic-tui/library.db.antes-do-restore    # nada é apagado
cp  ~/.local/share/ytmusic-tui/backups/library-AAAAMMDD-HHMMSS-v<de>-v<para>.db \
    ~/.local/share/ytmusic-tui/library.db
# abra o app e confira: `ytmusic-tui doctor` + biblioteca (Ctrl+L)
```

O que o rollback **não** desfaz: dados gravados depois do horário do
backup se perdem (é o ponto); arquivos baixados permanecem no disco; a
biblioteca **nunca é apagada**; migração irreversível só volta com o
backup pré-migração (por isso ele é automático e obrigatório).

### Publicação (tag/Release)

- tag publicada é **imutável** — nunca mover, apagar ou re-taguear;
- release ruim (defeito confirmado): publique a correção **para frente**
  (patch), edite as notas da release afetada com o aviso e aponte a
  corrigida (`gh release edit vX.Y.Z`); opcionalmente marque como
  pré-release (`--prerelease`) enquanto o patch não sai;
- rascunho nunca vira público sem passar pela verificação da etapa 9.

## Portão §16 — GQ-01..GQ-20 como checklist da release

Marcar **na execução da release** (auditoria), com a prova do dia. "Como
verificar" é operacional: comando ou consulta.

| ID | Requisito | Como verificar | ✔ |
|---|---|---|---|
| GQ-01 | Testes obrigatórios passam em ambiente limpo | `uv run pytest -q` (etapa 2) + CI verde do commit (`gh run list --workflow=ci.yml`) | [ ] |
| GQ-02 | Nenhuma falha crítica conhecida; intermitentes investigados | `gh issue list` + histórico do CI (`gh run list --workflow=ci.yml --limit=20`) | [ ] |
| GQ-03 | Busca, reprodução, download e biblioteca com testes de integração | L2 local (`tests/integration_local/`) + L3/L4 da etapa 3 com saída registrada | [ ] |
| GQ-04 | Bugs corrigidos têm teste de regressão | revisão dos commits da janela (`git log --oneline <última-tag>..HEAD`): todo `fix:` traz teste | [ ] |
| GQ-05 | Download interrompido não aparece como concluído | `uv run pytest tests/test_download_task.py tests/test_download_recovery.py -q` | [ ] |
| GQ-06 | `.part`/tarefas órfãs tratados na inicialização | `uv run pytest tests/test_recovery.py tests/test_download_recovery.py -q` | [ ] |
| GQ-07 | Falha de banco não é exibida como biblioteca vazia | `uv run pytest tests/test_library_errors.py tests/test_transactions.py -q` | [ ] |
| GQ-08 | Backup e recuperação testados | `uv run pytest tests/test_migrations.py -q` (+ restauração documentada acima) | [ ] |
| GQ-09 | Chamadas a processos externos revisadas | job `seguranca` verde + `uv run pytest tests/security -q` | [ ] |
| GQ-10 | Nomes/caminhos validados (sem escape de `download_dir`) | `uv run pytest tests/security -q` (testes de caminhos) | [ ] |
| GQ-11 | Sem vulnerabilidades críticas/altas sem mitigação | job `seguranca` (pip-audit no lock) verde; sem exceção nova | [ ] |
| GQ-12 | Logs sem credenciais/dados pessoais | `uv run pytest tests/test_log.py -q` + revisão do `doctor` | [ ] |
| GQ-13 | Pacote instalável em ambiente limpo | job `pacote` do CI (wheel+sdist + `doctor` exit 0) | [ ] |
| GQ-14 | Versão, commit e artefatos rastreáveis | workflow: correspondência tag↔versão↔commit + `SHA256SUMS` anexado + commit/hashes nas notas (`gh release view vX.Y.Z`) | [ ] |
| GQ-15 | Dependências e plataformas documentadas | README: seções de dependências + matriz de plataformas | [ ] |
| GQ-16 | Atualização e desinstalação testadas | README *Updating and downgrading* + evidência do T14 (dados persistem) | [ ] |
| GQ-17 | Falhas de rede/player geram mensagens compreensíveis | `uv run pytest tests/test_player.py tests/test_api_errors.py -q` + L4 | [ ] |
| GQ-18 | UI responsiva em operações demoradas | `uv run pytest tests/test_tui.py -q` | [ ] |
| GQ-19 | Encerramento libera processos e recursos | L2 (`tests/integration_local/`) + teste de saída da TUI (L4, pty) | [ ] |
| GQ-20 | Documentação corresponde ao comportamento real | revisão final README/CHANGELOG/docs contra o app (inclui `--version` e `doctor`) | [ ] |

Observação: GQ-14 fecha quando a release **publica** os hashes (é o último
item dependente do dia); os demais já eram verificáveis no pré-release.

## Mapa: etapa → onde roda

| Etapa | Onde roda | Evidência |
|---|---|---|
| 1 versão + CHANGELOG | manual (autorizado) | commit + seção no CHANGELOG |
| 2 suíte | manual + CI | saída do pytest + runs verdes |
| 3 L3/L4 | manual (local) | saídas registradas (com Q17 se aplicável) |
| 4 build limpo | `release.yml` (e manual) | `dist/` com os 2 artefatos |
| 5 instalar/testar artefato | manual + `release.yml` (etapa 9) | `--version` + `doctor` exit 0 |
| 6 correspondência | `release.yml` (falha se divergir) | passo verde "batem: X.Y.Z" |
| 7 notas + limitações | `release.yml` (notas) + README (limitações) | `RELEASE_NOTES.md` = seção do CHANGELOG |
| 8 hashes SHA-256 | `release.yml` | `SHA256SUMS` + hashes nas notas |
| 9 verificar publicado | `release.yml` (antes de publicar) | venvs limpos sobre os artefatos publicados |
| 10 monitorar | manual | CI/contrato/issues pós-release |

## Manutenção do workflow

- valide localmente antes de commitar: parse do YAML e `bash -n` nos
  blocos `run` (é o que a execução do T16 fez);
- sem ações de terceiros novas: apenas `actions/checkout@v7` e
  `astral-sh/setup-uv@v10.3.0` (mesmos pins do `ci.yml`, atualizados pelo
  Dependabot no grupo `actions`);
- a consulta de CI verde usa `gh api` (validada ao vivo na execução do
  T16: retorna "ok" para commit verde, "nenhuma-execução" para commit sem
  run, "pendente-ou-vermelha" nos demais casos — falha fechando);
- `workflow_dispatch` não substitui o disparo por tag: é uma ferramenta de
  recuperação/verificação, nunca de publicação "por fora".
