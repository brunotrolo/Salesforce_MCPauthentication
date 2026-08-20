# Salesforce MCP Authentication Kit

Kit de autenticação **homologado em produção** e **pronto para replicar** em
qualquer projeto que consuma os **Salesforce Hosted MCP servers**
(`api.salesforce.com/platform/mcp/v1/...`) via OAuth2 com **Refresh Token
Rotation automática** em GitHub Actions.

Este kit nasceu de um projeto real (monitoramento preditivo Salesforce) e
contém **os códigos exatos que funcionaram em produção**, sem nenhum secret —
você só precisa dos valores da sua própria External Client App. Evidência de
homologação em [`docs/VALIDACAO.md`](docs/VALIDACAO.md).

---

## O problema resolvido

1. Os Salesforce Hosted MCP servers **não aceitam** Connected App legada nem
   redirect URI em HTTP puro. Só funcionam com **External Client App** +
   PKCE + JWT-based access tokens.
2. O org pode impor **Refresh Token Rotation obrigatória** (não desligável) —
   a cada refresh, o Salesforce **mata o refresh token anterior** e entrega um
   novo. Qualquer pipeline agendado que guarda o token fixo em secret **quebra
   no segundo run** com `invalid_grant`.
3. Solução: o próprio pipeline **devolve o token rotacionado** para o secret do
   GitHub em cada execução (token mint de 1h da GitHub App, sem PAT). Loop
   auto-sustentável.

```
 ┌─────────────────────────  GitHub Actions (cron */5)  ─────────────────────────┐
 │                                                                                │
 │  1. secrets.SF_REFRESH_TOKEN ──► SalesforceClient.__init__                     │
 │  2. 401/refresh ──► POST token_endpoint (grant_type=refresh_token)             │
 │        └─► Salesforce ROTACIONA o token: devolve access + NOVO refresh          │
 │  3. cliente atualiza self.refresh_token = novo                                  │
 │  4. pipeline executa (SOQL/MCP tools)                                           │
 │  5. finally: --auth-state-out grava {"refresh_token": novo,                    │
 │       "refresh_token_initial": token que a run COMEÇOU}                         │
 │  6. finalmente: passo "Rotate auth secret" lê o arquivo e roda            │
 │        gh secret set SF_REFRESH_TOKEN --body "$NOVO"                            │
 │        └─► próximo run já começa com o token atual                              │
 │        Autenticado via GitHub App (token mint de 1h, sem PAT longa-duração)  │
 └─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Estrutura do repositório

| Arquivo | Papel |
|---|---|
| `src/sf_mcp_auth/` | Pacote instalável: `client.py` (client MCP), `auth_state.py` (estado de rotação), `bootstrap.py` (CLI PKCE), `github_app_token.py` (mint de token de instalação da GitHub App, PAT-free) |
| `scripts/get_sf_mcp_tokens.py` | Bootstrap OAuth2 PKCE (gera o 1º refresh token) — wrapper fino do pacote |
| `workflow/collect.yml` | Workflow real de referência (rotação do secret + persistência de snapshot) |
| `pipeline/run_with_rotation.py` | Padrão try/finally + `--auth-state-out` (usa o pacote) |
| `docs/TROUBLESHOOTING.md` | Todos os erros reais encontrados e as causas |
| `docs/VALIDACAO.md` | Evidência de homologação em produção (testes + cadeia viva de rotação) |
| `tests/` | Testes unitários do pacote (`pytest`) |

Instalação (core stdlib-only, sem dependências externas):

```bash
pip install git+https://github.com/brunotrolo/Salesforce_MCPauthentication.git
# ou localmente: pip install .
```

Extras opcionais (só se precisar):

```bash
pip install "sf-mcp-auth[github-app]"   # mint de token GitHub App fora de CI (pyjwt[crypto])
pip install "sf-mcp-auth[test]"         # dependências da suíte de testes
```

A CLI do bootstrap vira o comando `sf-mcp-auth-bootstrap`; o client e o
`auth_state` são importáveis como `from sf_mcp_auth import ...`. Requer
Python ≥ 3.10.

---

## Pré-requisitos (setup na Salesforce, 5 min)

1. **Setup → App Manager → New Connected App → External Client App**
   (o tipo certo é *External Client App*, não o legado).
2. Configuração obrigatória:
   - **OAuth scopes**: `api`, `sfap_api`, `refresh_token` (e `mcp_api` se o
     server MCP pedir — o bootstrap já envia os 4)
   - **Require Proof Key for Code Exchange (PKCE)**: ✅ ativado
   - **Issue JSON Web Token (JWT)-based access tokens for named users**: ✅
   - **Refresh Token Policy**: prefira *Expire refresh token if not used for
     specific time* (padrão)
   - Anote **Consumer Key** e **Consumer Secret** (ficam na página do app).
3. **Refresh Token Rotation**: se o org marcar como obrigatório (campo
   bloqueado: *"To change this required setting, contact Support"*), **não
   lute contra** — este kit já foi desenhado para esse cenário.

> External Client Apps **não aceitam redirect URI em HTTP puro**. O script usa
> o callback oficial `https://login.salesforce.com/services/oauth2/success` e
> você cola o `code` manualmente — sem precisar de servidor local.

---

## Passo a passo

### 1. Gerar o refresh token inicial

```bash
python scripts/get_sf_mcp_tokens.py "<CONSUMER_KEY>" "<CONSUMER_SECRET>"
# ou, com o pacote instalado:
sf-mcp-auth-bootstrap "<CONSUMER_KEY>" "<CONSUMER_SECRET>"
```

- Abre o navegador com a URL de autorização → aprova → cai na página de
  sucesso do Salesforce.
- Cole a URL final (ou só o `?code=...`) no terminal.
- O script valida `state` (anti-CSRF) e imprime o **refresh token** — nunca o
  access token completo.

### 2. Gravar os secrets no GitHub

```bash
gh secret set SF_CLIENT_ID <<< '<CONSUMER_KEY>'
gh secret set SF_CLIENT_SECRET <<< '<CONSUMER_SECRET>'
gh secret set SF_REFRESH_TOKEN <<< '<refresh token impresso no passo 1>'
gh secret set SALESFORCE_MCP_URL <<< 'https://api.salesforce.com/platform/mcp/v1/platform/sobject-reads'
```

O `SALESFORCE_MCP_URL` aponta para o server MCP desejado
(`sobject-all`, `sobject-reads`, `lens-explorer`, ...). Se já usa em outro
projeto, reaproveite a URL.

### 3. Criar a GitHub App (PAT-free rotation — a única forma sustentável)

O `GITHUB_TOKEN` de Actions **não consegue** atualizar secrets, e um PAT
fine-grained nunca expira — qualquer vazamento vira uma porta escancarada.
A solução canônica é uma **GitHub App**: você cria uma vez, instala no repo
e o workflow minta um token efêmero de 1h a cada run. Nada fica exposto,
nada precisa ser rotacionado manualmente nunca mais.

**Setup único (~5 min, você faz só isto no browser):**

1. **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**
   - **GitHub App name**: qualquer nome (ex.: `mcp-secret-rotator`)
   - **Homepage URL**: qualquer uma (ex.: a do repo)
   - **Webhook → Active**: ❌ desmarque (não precisa)
   - **Repository permissions**:
     - **Actions → Read and write** ⚠️ *(isto cobre `gh secret set`);*
       **Secrets** também precisa de **Read and write** (a GitHub moveu essa
       permissão para "Secrets" separada de "Actions" em algumas contas —
       marque ambas como Read-write para garantir)
     - **Metadata → Read-only** (adicionada automaticamente, obrigatória)
   - Clique em **Create GitHub App**
2. Na página da App recém-criada, role até **Private keys** →
   **Generate a private key** → baixa um arquivo `.pem`. **Guarde este
   arquivo** — ele é a única credencial que realmente merece proteção, e só
   vive na sua máquina e no secret do GitHub.
3. **Install the App** (link na mesma página) → escolha o repo de
   monitoramento. *(A ação `create-github-app-token` resolve a instalação
   sozinha via `owner` — você não precisa anotar o installation_id.)*
4. **Grave 2 secrets no repo de monitoramento:**
   ```bash
   # na raiz do repo de monitoramento:
   # 1. App ID (número visível no topo da página da App)
   gh secret set APP_ID <<< '<APP_ID_NUMÉRICO>'
   # 2. Conteúdo do arquivo .pem baixado (multiline vai direto no stdin:
   #    `cat app.private-key.pem | gh secret set APP_PRIVATE_KEY`)
   cat app.private-key.pem | gh secret set APP_PRIVATE_KEY
   ```

> **Setup sem App (não recomendado):** se a App não estiver configurada, o
> passo de mint é pulado e a rotação não roda (o workflow avisa e sai com
> sucesso). Configure a App — é o único caminho suportado para o loop
> auto-sustentável.

### 4. Adicionar o workflow

Copie `workflow/collect.yml` para `.github/workflows/` e adapte:

- `python monitoring/orchestrate.py` → o seu script (ex.: `python app/main.py`)
- `--auth-state-out out/auth_state.json` → **mantenha** (é o contrato com o
  passo de rotação)
- Instale as dependências do seu projeto no passo *Install dependencies*
  (adicione também `pip install git+https://github.com/brunotrolo/Salesforce_MCPauthentication.git`)
- Remova os passos que não usar (persistência de snapshot, artefatos)

O passo que faz o segredo virar autônomo é o "Rotate auth secret" (já no
workflow). Antes dele, o passo "Mint GitHub App token" mints um
installation token efêmero de 1h a partir da GitHub App — eliminando o PAT
longa-duração da equação. O guard compara `refresh_token` com
`refresh_token_initial` (que o pipeline grava com o token que a run COMEÇOU)
e **só escreve o secret quando realmente rotacionou** — uma run que falhou
antes de refrescar não pode sobrescrever um token válido com um token morto:

```yaml
      # job-level env: secrets context is not allowed in step `if:` - the
      # standard workaround is exposing the pair via job env:
      # env:
      #   GH_APP_ID: ${{ secrets.APP_ID }}
      #   GH_APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
      # Mint a 1-hour installation token from the GitHub App (PAT-free).
      # `if:` uses job env (GH_APP_*) because the secrets context is not
      # allowed in step if-conditions; skipped when the App is not configured
      # (the rotate step then skips too).
      - name: Mint GitHub App token (PAT-free rotation)
        id: app_token
        if: ${{ env.GH_APP_ID != '' && env.GH_APP_PRIVATE_KEY != '' }}
        uses: actions/create-github-app-token@v1
        with:
          app-id: ${{ secrets.APP_ID }}
          private-key: ${{ secrets.APP_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: ${{ github.event.repository.name }}

      - name: Rotate auth secret (refresh token rotation)
        if: always()
        env:
          # The 1-hour App token minted above - the only way in this setup.
          GH_TOKEN: ${{ steps.app_token.outputs.token }}
        run: |
          set -e
          if [ -z "$GH_TOKEN" ]; then
            echo "No GitHub App token - skipping secret rotation"
            exit 0
          fi
          if [ ! -f "out/auth_state.json" ]; then
            echo "No auth_state.json - nothing to rotate"; exit 0
          fi
          NEW_TOKEN=$(python -c "import json;print(json.load(open('out/auth_state.json'))['refresh_token'])")
          OLD_TOKEN=$(python -c "import json;print(json.load(open('out/auth_state.json')).get('refresh_token_initial',''))")
          if [ "$NEW_TOKEN" = "$OLD_TOKEN" ]; then
            echo "Token was NOT rotated this run - skipping secret write"; exit 0
          fi
          gh secret set SF_REFRESH_TOKEN --body "$NEW_TOKEN"
          echo "SF_REFRESH_TOKEN rotated"
```

O workflow inteiro ainda traz duas proteções contra corrida:

- **`concurrency: group: collect, cancel-in-progress: false`** — só uma run
  coletora roda por vez. Duas runs sobrepostas refrescam e rotacionam o token
  ao mesmo tempo; a perdedora escreveria um token morto por cima do ganhador.
- **`if: always()` no rotate** — mesmo com pipeline quebrada, o token já
  rotacionado volta para o secret e a próxima run começa viva.

### 5. Validar

```bash
gh workflow run collect.yml          # 1º run manual
gh run watch                         # sucesso em pipeline + rotate + persist
gh secret list                       # SF_REFRESH_TOKEN com timestamp NOVO
                                    #  (prova que rotacionou)
gh workflow run collect.yml          # 2º run: usa o token rotacionado
```

Se o 2º run for verde, o ciclo está provado — o cron mantém sozinho. Este
protocolo exato foi executado em produção em 19/08/2026, e a migração
PAT-free (GitHub App, `GH_PAT` removido, run verde sem ele) foi validada
em 20/08/2026 — evidência completa em `docs/VALIDACAO.md` (§3 e §5).

**Critério de aceite da migração PAT-free** (ver `docs/VALIDACAO.md` §5):

1. Workflow com o passo "Mint GitHub App token"
   (`actions/create-github-app-token@v1`).
2. Uma run `workflow_dispatch` passa em todos os steps (mint + pipeline +
   rotate) e o timestamp de `SF_REFRESH_TOKEN` muda.
3. `GH_PAT` removido do repo e a run seguinte continua verde — prova de que
   nenhum PAT está em repouso na conta.
4. `APP_PRIVATE_KEY` nunca aparece em logs, chat ou arquivos.

---

## Como o client rotaciona (o coração do kit)

`sf_mcp_auth/client.py` (código real, self-contained):

```python
def _refresh_token(self) -> None:
    token_endpoint = self._discover_token_endpoint()
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": self.refresh_token,
        "client_id": self.client_id,
        "client_secret": self.client_secret,
    }
    ...  # POST urlencoded no token_endpoint
    self.token = body["access_token"]
    new_refresh = body.get("refresh_token")
    if new_refresh and new_refresh != self.refresh_token:
        self.refresh_token = new_refresh   # ← captura a rotação
```

E o chamador (padrão em `pipeline/run_with_rotation.py`) **sempre** devolve o
token para o secret, em sucesso E em falha. Agora usando o pacote e gravando
também o token inicial (para o guard de rotação do workflow):

```python
from sf_mcp_auth.auth_state import write_auth_state
from sf_mcp_auth.client import SalesforceClient

client = SalesforceClient()           # criado ANTES do try
try:
    run_pipeline(client=client)       # faz refresh/rotação internamente
finally:
    if args.auth_state_out and client.refresh_token:
        write_auth_state(
            args.auth_state_out,
            client.refresh_token,
            initial=os.environ.get("SF_REFRESH_TOKEN"),
        )
```

O arquivo `auth_state.json` resultante:

```json
{
  "refresh_token": "<token rotacionado nesta run>",
  "refresh_token_initial": "<token que a run COMEÇOU>"
}
```

O passo *Rotate auth secret* só escreve o secret quando os dois diferem.

---

## Armadilhas documentadas (você não vai cair duas vezes)

| Sintoma | Causa | Fix |
|---|---|---|
| `403 invalid_scope ... unknown_error %3D` | scope `sfap_api` faltando no consent | refazer bootstrap com os 4 scopes |
| `invalid_grant` no 2º run do cron | RTR obrigatória matou o token fixo | kit de rotação automática |
| `FileNotFoundError: 'out/previous.json'` | `cd pasta && script` com caminho relativo à raiz | rodar da raiz, caminhos `out/...` |
| `src refspec data does not match any` | `git worktree add ... origin/data` = detached HEAD | `git push origin HEAD:data` |
| Cron `*/5` não dispara no minuto certo | GitHub atrasa/pula slots de schedule sob carga | normal; entrada offset `2-59/5` + `workflow_dispatch` para validar |
| `GITHUB_TOKEN` não seta secret | permissão insuficiente por design | GitHub App com Secrets read/write (mint de token de 1h a cada run) |
| Run falho `if: always()` regrava token morto no secret | pipeline quebrada antes do refresh grava o token antigo por cima do rotacionado | guard `refresh_token == refresh_token_initial` no passo rotate + `concurrency` |

Detalhes completos em `docs/TROUBLESHOOTING.md`.

---

## Segurança

- **Nenhum secret neste kit** — Consumer Key/Secret/refresh token entram
  apenas como argumentos/env vars/secrets do GitHub.
- O access token nunca é impresso pelo bootstrap (só os 12 primeiros chars).
- Secrets vivem em `gh secret` / GitHub Actions — nunca em arquivos do repo.
- **PAT-free por padrão**: a rotação usa uma GitHub App cujo token de
  instalação (1h de vida) é mintado em cada run — não existe segredo
  longa-duração em repouso na conta. Os únicos 2 secrets da App são
  `APP_ID` e `APP_PRIVATE_KEY`.
