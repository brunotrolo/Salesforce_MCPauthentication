# Troubleshooting — erros reais encontrados e as causas

Todos os itens abaixo aconteceram de verdade no projeto original. São as
armadilhas que você vai evitar (ou resolver rápido) em qualquer projeto novo.

---

## 1. `403 {"error":"invalid_scope","error_description":"OAuth invalid scope: ... unknown_error %3D ..."}`

**Quando:** durante o bootstrap ou no primeiro refresh.

**Causa:** o scope `sfap_api` não foi concedido — a External Client App não
tem ele habilitado, ou o consentimento foi dado sem ele (apenas `api` +
`refresh_token`).

**Fix:**
1. App Manager → External Client App → editar → **OAuth scopes**: garantir
   `api`, `sfap_api`, `refresh_token` (e `mcp_api` se o server MCP pedir).
2. Refazer o bootstrap (o consentimento antigo não "ganha" o scope novo).
3. Se o erro persistir com o scope correto: revogar o consentimento anterior
   em Salesforce → Configurações pessoais → **Segurança → Aplicativos
   conectados** (revoke) e autorizar de novo.

---

## 2. `invalid_grant` no 2º run do cron (Refresh Token Rotation)

**Quando:** o pipeline funcionava manualmente e quebrou sozinho depois.

**Causa:** o org tem **Refresh Token Rotation obrigatória**. O refresh que o
pipeline fez no 1º run **matou o refresh token do secret**. O 2º run tenta
refresh com um token morto → `invalid_grant`.

**Como confirmar:** Setup → App Manager → sua External Client App → ver se
*"Enable Refresh Token Rotation"* está marcado e **bloqueado** com a mensagem
*"To change this required setting, contact Support"*. Se bloqueado, é
obrigatório — não há workaround.

**Fix:** usar o loop de rotação deste kit (client captura o token novo +
`finally` grava `out/auth_state.json` + passo do workflow atualiza o secret).
Nunca rodar mais de 1 vez com o mesmo token fixo no secret.

---

## 3. `FileNotFoundError: [Errno 2] No such file or directory: 'out/previous.json'`

**Quando:** o pipeline de CI roda depois do primeiro snapshot já existir.

**Causa:** o passo fazia `cd monitoring && python orchestrate.py ...` com o
`--history-file out/previous.json` **relativo à raiz do repo**. O `cd`
mudou o CWD para `monitoring/`, então `out/previous.json` resolveu para
`monitoring/out/previous.json` (inexistente).

**Fix:** rodar o script **da raiz** (`python monitoring/orchestrate.py`) com
caminhos `out/...` — e criar `out/` antes (`mkdir -p out`). Imports do
módulo continuam funcionando porque `sys.path[0]` é o diretório do script
(`monitoring/`), não o CWD.

---

## 4. `error: src refspec data does not match any` (persistência em branch)

**Quando:** o passo que persiste snapshot na branch `data` falha no push.

**Causa:** `git worktree add data-branch origin/data` cria a worktree em
**detached HEAD** (não em um branch local). O `git commit` grava num HEAD
desanexado e `git push origin data` não acha branch local `data` → refspec
inválido. O 1º run funcionou porque usou `git worktree add data-branch -b
data` (criando o branch); nos seguintes, o branch já existia e o caminho
mudou para detached.

**Fix:** push pelo HEAD: `git push origin HEAD:data` — o GitHub interpreta a
ref `HEAD` da worktree e grava na branch remota `data`.

---

## 5. Cron `*/15 * * * *` não dispara no minuto exato (nem sempre dispara)

**Quando:** os runs de schedule aparecem em horários irregulares (ex.: 21:01,
21:38, 21:58, 22:26... e gaps de ~87 min).

**Causa:** comportamento **do GitHub**, não do seu workflow. Runs de
`schedule` são enfileirados com atraso variável (1-15 min) e podem ser
pulados em janelas de carga alta. O workflow em si está correto.

**Fix:** nada a fazer no código. Para validar com precisão, use
`gh workflow run collect.yml` (`workflow_dispatch`) — dispara na hora. Em
produção usamos uma entrada de cron **offset** (`2-59/5` além do `*/5`):
quando o scheduler funde uma janela, a segunda entrada dobra a chance de uma
das duas disparar. Se um dia precisar de pontualidade absoluta, mova o cron
para fora (ex.: GitHub Action de outro repo chamando `workflow_dispatch`
com token de outra GitHub App, ou cron externo).

---

## 6. `GITHUB_TOKEN` não atualiza o secret (rotação não roda)

**Quando:** o passo "Rotate auth secret" falha com 403/permissão.

**Causa:** o `GITHUB_TOKEN` padrão de Actions **não tem permissão para
escrever secrets** — por design. A API exige um token com permissão explícita.

**Fix:** configure a GitHub App (passo 3 do README) e os 2 secrets
`APP_ID` / `APP_PRIVATE_KEY` (a ação resolve a instalação sozinha via
`owner`, não precisa de installation_id). O workflow mints um installation
token de 1h a cada run (`actions/create-github-app-token@v1`) e o usa no
`gh secret set` — sem nenhum segredo longa-duração.

---

## 7. Refresh Token Policy de 30 dias no org

**Quando:** docs/config do app mostram *"Expire refresh token if not used for
specific time (30 days)"*.

**Causa:** o org revoga refresh tokens ociosos após o período configurado.

**Fix:** irrelevante com o loop de rotação — o token é usado a cada 5 min
(cron) e rotacionado a cada uso; nunca fica ocioso. O secret sempre carrega
um token recém-usado.

---

## 8. Access token dura ~2h — e o pipeline não deve tratar disso

O access token expira em ~2h. Não programe renovação por tempo: o client
renova **sob demanda** no primeiro 401 (ver `call_tool` →
`_refresh_token`). Isso simplifica: o token vive o mínimo necessário e a
rotação acontece apenas quando o MCP realmente recusa o atual.

---

## 9. Run falho com `if: always()` regrava um token MORTO no secret

**Quando:** o workflow quebra no meio do pipeline, e mesmo assim o passo
"Rotate auth secret" executa e sobrescreve o secret com um token velho.

**Causa (bug real, encontrado em produção em 2026-08-19):** o `if: always()`
no rotate é necessário — se o pipeline quebrar DEPOIS de um refresh, o token
novo já nasceu e o antigo já morreu, e o finally ainda não rodou para gravá-lo
em `out/auth_state.json`. Mas quando a run falha ANTES de qualquer refresh,
`client.refresh_token` ainda é o token velho (vindo do secret). O finally grava
ele em `auth_state.json` e o rotate escreve esse token morto por cima do
secret — matando o token que outra run rotacionou.

**Como acontece na prática:** a primeira run da manhã roda com o token novo,
refresca e rotaciona. A segunda run (agendada logo depois) começa com o token
já rotacionado, e uma falha sem refresh dela regrava o token VELHO no secret.
Na terceira run, `invalid_grant` — e a cadeia de rotação morre.

**Fix (já aplicado no template):** o rotate compara o token atual com o token
inicial da run e só escreve quando houve rotação de verdade:

```yaml
OLD_TOKEN=$(python -c "import json;print(json.load(open('out/auth_state.json')).get('refresh_token_initial',''))")
if [ -z "$OLD_TOKEN" ]; then
  echo "No refresh_token_initial - cannot verify rotation, skipping"; exit 0
fi
if [ "$NEW_TOKEN" = "$OLD_TOKEN" ]; then
  echo "Token was NOT rotated this run - skipping secret write"; exit 0
fi
```

**Complemento obrigatório:** `concurrency: group: collect,
cancel-in-progress: false` no workflow — duas runs sobrepostas refrescam o
token ao mesmo tempo e a perdedora escreve um token morto sobre o do ganhador,
mesmo com o guard de comparação.

---

## 10. `HTTP 422: Secret names must not start with GITHUB_` ao gravar secrets

**Quando:** `gh secret set GITHUB_APP_ID ...` (ou qualquer nome com o
prefixo `GITHUB_`).

**Causa:** o GitHub **reserva o prefixo `GITHUB_`** para os secrets que ele
mesmo cria (também são proibidos em `env` de workflow). A API recusa o nome
com 422 — não é erro de autenticação nem de permissão.

**Fix:** usar nomes sem o prefixo: `APP_ID`, `APP_PRIVATE_KEY`. (Descoberto
na migração PAT-free em 2026-08-19.)

---

## 11. Workflow não carrega: `Unrecognized named-value: 'secrets'` no `if:`

**Quando:** o passo "Mint GitHub App token" usa
`if: ${{ secrets.APP_ID != '' }}` e o GitHub se recusa a rodar o workflow.

**Causa:** o context `secrets` **não é permitido em `if:` de step** (só em
`env`, `with` e `run`); o parser do workflow rejeita o arquivo inteiro.

**Fix (workaround padrão):** expor os secrets via `env` do job e usar o env
no `if:`:

```yaml
jobs:
  collect:
    env:
      GH_APP_ID: ${{ secrets.APP_ID }}
      GH_APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
    steps:
      - name: Mint GitHub App token (PAT-free rotation)
        id: app_token
        if: ${{ env.GH_APP_ID != '' && env.GH_APP_PRIVATE_KEY != '' }}
        uses: actions/create-github-app-token@v1
```

---

## 12. `Warning: Unexpected input(s) 'installation-id'` no passo de mint

**Quando:** o passo `actions/create-github-app-token@v1` roda com o aviso de
input desconhecido e/ou o secret `INSTALLATION_ID` nunca é lido.

**Causa:** a ação **não tem input `installation-id`** — ela resolve a
instalação sozinha via `owner` (e `repositories`, se informado). Passar o
input não quebra a run (só avisa), mas polui o template com algo que não
existe.

**Fix:** remover `installation-id` do passo e o secret `INSTALLATION_ID` do
repo — só `APP_ID` + `APP_PRIVATE_KEY` são necessários.