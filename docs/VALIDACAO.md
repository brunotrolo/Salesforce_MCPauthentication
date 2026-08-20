# Homologação (19–20/08/2026)

Este kit foi homologado **em produção** no projeto real de monitoramento
preditivo Salesforce (repo `Salesforce_PredictiveMonitoring`). Nada aqui é
protótipo: cada componente abaixo rodou de verdade. A migração **PAT-free**
(GitHub App) foi validada em 20/08/2026 — evidência na §5.

## 1. Testes unitários do pacote

```bash
python -m pytest tests -q
# 39 passed in 0.70s
```

Cobre: client MCP (refresh, rotação, retry, rate limit), `auth_state`
(initial/rotated), bootstrap PKCE (code extraction, validação de state,
troca por tokens) e `github_app_token` (JWT RS256 + mint de installation
token).

## 2. Testes de integração do pipeline consumidor

Suite completa do projeto real:

```bash
# 100 passed, 12 failed (12 falhas PRÉ-EXISTENTES, sem regressão)
```

As edições deste kit (guard `refresh_token_initial` no `auth_state`) foram
validadas com 0 regressões: a única falha nova introduzida foi corrigida na
hora (assertiva de teste atualizada para o novo shape do arquivo).

## 3. Validação viva da cadeia de rotação (prova definitiva)

Fluxo executado no GitHub Actions real, workflow `collect.yml`:

| Run | Token usado | Resultado | Secret `SF_REFRESH_TOKEN` |
|---|---|---|---|
| dispatch 15:57 | token novo do usuário | ✅ green | 15:58:14 (rotacionado) |
| dispatch 15:58:54 | token **rotacionado** da run anterior | ✅ green | 15:59:40 (rotacionado de novo) |

O log da run 1 confirmou o passo de rotação executando e gravando o secret
(`SF_REFRESH_TOKEN rotated`). A run 2 — que **antes do fix sempre quebrava**
com `invalid_grant` porque o secret havia sido sobrescrito com o token morto —
rodou verde com o token rotacionado.

### A falha que motivou o guard (reproduzida em produção)

- **15:15 (pré-fix)**: pipeline falhou com
  `invalid_grant: expired access/refresh token`; o passo `Rotate auth secret`
  (com `if: always()`) **regravou o token morto no secret**.
- **15:30 (pós-fix)**: mesma falha de autenticação, mas o guard
  `refresh_token == refresh_token_initial` **pulou a escrita** — o secret foi
  preservado (timestamp inalterado). Comportamento correto: run que nunca
  refrescou não pode clobberar um token válido.

## 4. Requisitos para replicar (validados)

1. External Client App com PKCE + JWT-based access tokens (ver README §
   Pré-requisitos).
2. 4 secrets no repo: `SF_CLIENT_ID`, `SF_CLIENT_SECRET`, `SF_REFRESH_TOKEN`
   (gerado via bootstrap), `SALESFORCE_MCP_URL`.
3. **GitHub App** com `Actions → Read and write` + `Secrets → Read and write`
   e os 2 secrets `APP_ID`, `APP_PRIVATE_KEY` (a ação resolve a instalação
   via `owner`; não precisa de `INSTALLATION_ID`) — ver README § 3. A
   rotação do secret só roda com o token da App mintado pelo próprio
   workflow (1h de vida) — sem nenhum segredo longa-duração.
4. `concurrency` group no workflow (uma run por vez) + guard no passo rotate.

## 5. Critério de aceite da migração GitHub App (PAT-free) — VALIDADO em 20/08/2026

1. Workflow com o passo "Mint GitHub App token" usando
   `actions/create-github-app-token@v1`. ✅
2. Uma run com `workflow_dispatch` passa em todos os steps (mint + pipeline +
   rotate) e o timestamp de `SF_REFRESH_TOKEN` (`gh secret list`) muda.
   ✅ Run `32319893752` verde; `SF_REFRESH_TOKEN` rotacionado às 01:08:42Z.
3. `GH_PAT` **removido** do repo e a run seguinte continua rodando — prova de
   que nenhum PAT está em repouso na conta. ✅ Run `32320562707` verde (exit
   0, 100% PAT-free); `SF_REFRESH_TOKEN` rotacionado às 01:19:45Z.
4. `APP_PRIVATE_KEY` nunca aparece em logs, chat ou arquivos. ✅

**Estado final dos secrets do repo de produção (20/08/2026):**

```
APP_ID, APP_PRIVATE_KEY, SALESFORCE_MCP_URL, SF_CLIENT_ID,
SF_CLIENT_SECRET, SF_REFRESH_TOKEN
```

Sem `GH_PAT`, sem `INSTALLATION_ID` (a ação resolve a instalação via
`owner`).

### 3 armadilhas descobertas na migração (todas corrigidas e documentadas)

1. **Secrets com prefixo `GITHUB_` são recusados**: a API do GitHub reserva
   o prefixo para os secrets próprios — `gh secret set GITHUB_APP_ID`
   responde `HTTP 422` ("Secret names must not start with GITHUB_"). Usar
   `APP_ID` / `APP_PRIVATE_KEY`.
2. **`secrets` context não é permitido em `if:` de step** — erro de parse do
   workflow ("Unrecognized named-value: 'secrets'"). Workaround padrão:
   expor via `env` do job (`GH_APP_ID`/`GH_APP_PRIVATE_KEY`) e usar
   `if: ${{ env.GH_APP_ID != '' && env.GH_APP_PRIVATE_KEY != '' }}`.
3. **`actions/create-github-app-token@v1` não tem input `installation-id`**
   (aviso "Unexpected input(s) 'installation-id'") — a ação resolve a
   instalação sozinha via `owner` + `repositories`. Sem secret extra.

## 6. Cobertura de casos de falha (documentada)

Todos os erros reais encontrados estão em `docs/TROUBLESHOOTING.md`, incluindo
a armadilha nº 9 (run falho regravando token morto) que motivou o guard, e os
itens 10–12 (migração GitHub App: prefixo `GITHUB_` reservado, `secrets` em
`if:`, input `installation-id` inexistente).
