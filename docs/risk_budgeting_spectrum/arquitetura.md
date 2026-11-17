# RBS: arquitetura ponta a ponta (Fibery → motor → Hub)

- **Status:** Aprendizado (leitura do código e do Fibery, sem alteração de comportamento)
- **Atualizado em:** 2026-11-17
- **Base lida:** PerseveraTools `main` @ `3781760`; PerseveraHub `main` (pin do
  Tools em `982c227`, v0.21.0, 2026-11-09); Fibery `perseveraasset.fibery.io`,
  space `Inv-Rsrch-Quant` (somente leitura).

Este documento descreve como a página **Alocação · RBS** do PerseveraHub produz
os números que mostra. Para o *porquê* do offshore, ver [offshore.md](offshore.md);
para problemas de calibração, [calibracao.md](calibracao.md) (ver a seção
"Divergências com os outros docs" no fim deste arquivo: parte dele está
desatualizada).

## 1. Visão geral

```
Fibery (Inv-Rsrch-Quant)                PerseveraTools                         PerseveraHub
┌──────────────────────────┐   read_fibery   ┌───────────────────────────┐   ┌───────────────────────────────┐
│ Calibração de Mercado    │ ──────────────► │ loaders/fibery.py         │   │ frontend AlocacaoRbs.tsx      │
│ Classes de Ativo         │                 │  → SpectrumConfig         │   │  GET /carteiras-administradas │
│ Parâmetros de Classe     │                 │ core.build_spectrum       │◄──│      /alocacao-rbs            │
│ RC Target por Bucket     │                 │ core.compute_feasible_... │   │ backend api.py → tarefas.py   │
│ Correlação Cross-Classe  │                 └───────────────────────────┘   │  (thread, cache 1 h)          │
└──────────────────────────┘                                                 │ apps/carteiras/rbs.py         │
                                                                              │  calcular() + montar() + excel│
                                                                              └───────────────────────────────┘
```

Três camadas, com responsabilidades separadas:

| Camada | Onde | Faz | Não faz |
|---|---|---|---|
| Dados | Fibery, space `Inv-Rsrch-Quant` | Guarda a calibração versionada (σ, vols, limites, RC targets, correlações) | Não calcula nada |
| Motor | `persevera_tools/quant_research/risk_budgeting_spectrum/` | Valida a calibração e otimiza os perfis | Não conhece o Hub nem gráficos |
| Apresentação | `PerseveraHub/backend/apps/carteiras/{api,rbs}.py` + `frontend/.../AlocacaoRbs.tsx` | Dispara o cálculo em segundo plano, monta tabelas/gráficos, exporta Excel | Não reimplementa a matemática |

## 2. Modelo de dados no Fibery (space `Inv-Rsrch-Quant`)

Cinco databases alimentam o motor. Relações:

```
Calibração de Mercado (1) ──< Parâmetros de Classe >── (1) Classes de Ativo
        │                                                     │  ├─ Classificação Layer 1 → Inv-Taxonomia (bucket)
        ├──< RC Target por Bucket >── Classificação Layer 1   │  ├─ Conjunto            → Inv-Taxonomia
        └──< Correlação Cross-Classe >── Classe A / Classe B ─┘  ├─ Proxy               → Inv-Rsrch-Quant/Indicadores
                                                                 └─ Fonte de Estimativas (Histórico | Premissa)
```

| Database | Campos que o motor lê | Observações |
|---|---|---|
| **Calibração de Mercado** | `Data Calibração`, `workflow/state` (Rascunho → Em Comitê → Aprovada → Substituída), `Livro` (Onshore/Offshore), `Sigma Min`, `Sigma Max`, `N Profiles`, `Min Weight Threshold` | `Name` é fórmula: `"Calibração " + Livro + " - " + data`. É a chave de junção das tabelas filhas (o loader casa pelo **nome** da calibração, não pelo id). |
| **Classes de Ativo** | `Name`, `Classificação Layer 1` (= bucket), `Proxy`, `Livro`; o Hub lê também `Conjunto` | Catálogo único, sem versão. Ordem do catálogo define a ordem das classes dentro do bucket. `Livro` vazio = Onshore. |
| **Parâmetros de Classe** | `Calibração`, `Classe`, `Default Vol`, `Max Weight`, `Max RC` (opcional), `RC Intra-Bucket` (opcional) | Uma linha por (calibração, classe). Todas as classes do livro precisam ter linha. `Livro` aqui é fórmula herdada da calibração. |
| **RC Target por Bucket** | `Calibração`, `Classificação Layer 1`, `RC P1`, `RC P10`, `Curvatura RC` (opcional) | Exatamente uma linha por bucket por calibração; `RC P1` e `RC P10` somam 1 cada. `RC P10` é o RC do **último** perfil, qualquer que seja `N Profiles`. |
| **Correlação Cross-Classe** | `Calibração`, `Classe A`, `Classe B`, `Rho` | Matriz **completa**, todos os pares i<j, inclusive intra-bucket (apesar do nome "Cross"): 78 linhas Onshore (13 classes) e 91 Offshore (14), conferido no Fibery. Classe A no bucket de menor índice (RF < Alt < RV). |

### Estado em 2026-11-17 (lido do Fibery)

Duas calibrações, ambas em **Rascunho**; não há nenhuma "Aprovada".

| Calibração | Livro | σ min–max | N | Peso mín. | Classes | Correlações |
|---|---|---|---|---|---|---|
| Calibração Onshore - 2026-10-14 (id 1) | Onshore | 1,9% – 11,5% | 10 | 0,5% | 13 | 78 pares (completo: C(13,2)) |
| Calibração Offshore - 2026-11-09 (id 2) | Offshore | 2,0% – 14,0% | 10 | 2,0% | 14 | 91 pares (completo: C(14,2)) |

Onshore, parâmetros (vol a.a. / Max Weight / Max RC):

| Bucket | Classe | Proxy | Vol | Max W | Max RC |
|---|---|---|---|---|---|
| RF | RF Crédito DI | `anbima_ida_di` | 1,90% | 100% | – |
| RF | RF Fundo Crédito GI | (sem proxy, *Premissa*) | 2,50% | 100% | – |
| RF | RF Fundo Crédito HY | (sem proxy, *Premissa*) | 4,00% | 100% | – |
| RF | RF Pré Longa | `anbima_irf_m1+` | 5,14% | 100% | – |
| RF | RF Inflação Curta | `anbima_ima_b5` | 2,15% | 100% | – |
| RF | RF Inflação Longa | `anbima_ima_b5+` | 7,15% | 100% | – |
| RF | RF Crédito Inflação | `anbima_ida_ipca` | 4,85% | 100% | – |
| Alt | Multimercado | `anbima_ihfa` | 4,48% | 20% | – |
| Alt | Long & Short | `persevera_anbima_ihfa_long_short` | 4,98% | 100% | – |
| Alt | Bitcoin | `bitcoin_usd` | 34,95% | 8% | 20% |
| Alt | Ouro | `gold_100oz_futures` | 23,91% | 25% | – |
| RV | RV Brasil | `br_ibovespa` | 17,63% | 35% | – |
| RV | RV EUA | `us_sp500` | 14,75% | 35% | – |

RC targets Onshore: RF 100% → 5% (γ 0,5); Alternativos 0% → 25% (γ 1,2); RV 0% → 70% (γ 1,2).
Offshore: RF 100% → 5% (γ 0,5); Alternativos 0% → 15% (γ 1,2); RV 0% → 80% (γ 1,2).
`RC Intra-Bucket` está vazio em todas as classes Onshore (divisão uniforme).

## 3. Motor (`risk_budgeting_spectrum`)

Arquivos: `config.py` (dataclasses + validação), `loaders/fibery.py` (Fibery →
`SpectrumConfig`), `core.py` (otimização), `estimation.py` (sugestão de
vols/correlações; **não é usado pelo Hub**, só por `scripts/rbs_offshore_calibracao.py`).

### 3.1 Loader (`load_from_fibery(status="Aprovada", livro="Onshore")`)

1. Lê `Calibração de Mercado`, filtra por livro e status, ordena por `Data Calibração` desc e pega a mais recente.
2. Lê o catálogo de classes do **mesmo livro**; junta com Parâmetros pela calibração (inner join por nome). Falta de parâmetro de qualquer classe → erro.
3. Agrupa as classes por bucket, na ordem de aparição; monta `RCTarget` e `rc_curvature` por bucket (curvatura vazia ⇒ γ = 1).
4. Monta `full_corr` n×n: exige **todos** os pares, rejeita duplicata conflitante e diagonal ≠ 1.
5. **Validação de viabilidade**: calcula σ mínimo (min-variance long-only) e σ máximo (concentração máxima permitida pelos Max Weight) e rejeita calibração com `Sigma Min` abaixo do mínimo viável ou `Sigma Max` acima do máximo viável.
6. `SpectrumConfig.validate()`: soma dos RC P1/P10 = 1, correlação simétrica/PSD, γ > 0 etc.

Política de falha: explícita (`FiberyLoaderError`), sem fallback silencioso.

### 3.2 `build_spectrum(config)`

1. **Covariância**: `Σ = D·ρ·D`, com `D = diag(vols)`.
2. **HRP de 2 níveis (só diagnóstico)**: risk parity dentro de cada bucket, covariância macro derivada dos pesos intra-bucket, risk parity entre buckets (pesos macro entre 5% e 85%). Não participa da produção; o Hub deixou de exibi-lo em 2026-11-17 (a aba "Diagnóstico HRP" saiu), mas `build_spectrum` ainda o calcula e devolve.
3. **Curva de σ-alvo**: `σ(p) = σ_min · exp(k·(p−1))`, `k = ln(σ_max/σ_min)/(N−1)`. Crescimento exponencial entre perfis.
4. **RC-target por bucket e perfil**: `f=(p−1)/(N−1)`, `g_b = f^γ_b`, `raw_b = (1−g_b)·RC_P1 + g_b·RC_P10`, renormalizado para somar 1. Os extremos são exatos; γ só afeta os intermediários. Com γ diferentes por bucket a soma bruta ≠ 1 e a renormalização muda o γ efetivo.
5. **RC-target por classe**: RC do bucket dividido uniformemente entre as classes ou por `RC Intra-Bucket`.
6. **Otimização por perfil** (`_optimize_profile_rb`, SLSQP, 3 starts determinísticos + 8 Dirichlet com semente = nº do perfil):
   - Restrições: Σw = 1; vol(w) = σ-alvo (igualdade); 0 ≤ w ≤ Max Weight; RC%ᵢ ≤ Max RC (onde definido).
   - RC **por bucket** imposto (k−1 igualdades); a divisão intra-bucket é só objetivo secundário (`_intra_rc_objective`). Razão: impor RC em todas as classes sobre-determina o problema (vol e RC colidem) e o solver sacrificava o RC de RF.
   - Estágio 1 `hard`: RC por bucket como igualdade. Se nenhum start produz solução factível, estágio 2 `penalty`: RC por bucket vira penalidade (peso 1e3), σ-alvo continua dura.
   - **Polish**: pesos < `Min Weight Threshold` são zerados e o problema reotimizado no conjunto ativo, mantendo as restrições do estágio vencedor; aceito só se continuar factível.
   - **Convergência** (`_is_converged`, avaliada na solução final): |Σw−1| ≤ 1e-6; pesos em [0, max]; |σ−σ-alvo| ≤ 5e-4 (0,05 p.p.); RC% ≤ Max RC + 1e-4; **RC por bucket dentro de 1e-3** do alvo.
7. **Saída**: pesos, vol realizada (%), RC por classe e por bucket (alvo × realizado), `converged_per_profile`, `rc_mode_per_profile` ("hard"/"penalty"), HRP, covariância, config usada.

`compute_feasible_vol_range(config)` devolve (σ mín., σ máx.) viáveis; o máximo é não-convexo e usa um start por vértice de concentração.

Premissas de modelagem (do `CLAUDE.md` do módulo): caixa/CDI curto fica fora do modelo (decisão de liquidez); exceção offshore, onde T-bills entram como "RF Pós-fixado" com Max Weight.

## 4. Hub: backend

Arquivos: `backend/apps/carteiras/api.py` (rotas) e `rbs.py` (cálculo e montagem).

### 4.1 Rotas (`/carteiras-administradas/...`)

| Rota | Faz |
|---|---|
| `GET /alocacao-rbs` | Inicia ou acompanha o cálculo; quando pronto, devolve a página montada |
| `GET /alocacao-rbs/excel` | Excel (Metadados, Pesos, RC Realizado); 400 se o cálculo ainda não terminou |

`FiltrosRbs`: `livro` (Onshore|Offshore), `status` (Rascunho|Em Comitê|Aprovada|Substituída; padrão **Aprovada**), `overrides` (bool), `sigma_min` (padrão 2,1), `sigma_max` (12,0), `n_perfis` (10), `limite_peso` (0,005), `perfil` (só apresentação).

Roda no `router` comum (não no `RouterCarteiras`): é um modelo, não lê dados de carteira, então não passa por `portfolios_visiveis`.

### 4.2 Execução em segundo plano (`apps/core/tarefas.py`)

- O cálculo (~100 s: Fibery ~20 s + `build_spectrum` ~80 s, medido em 2026-10-03) não cabe numa requisição. Roda numa thread do processo web (`ThreadPoolExecutor`, `MAX_TAREFAS = 2`); estado e resultado ficam no cache compartilhado (Postgres).
- Chave de cache: `chave_cache("tarefa:carteiras.rbs", {livro, status, overrides})`. Overrides são o dict `{sigma_min_pct, sigma_max_pct, n_profiles, min_weight_threshold}` (vazio se desligados). `perfil` **não** entra na chave: trocar o perfil só remonta a apresentação.
- Dedupe: `cache.add` atômico garante uma só execução por chave; as demais requisições leem o estado "rodando".
- Batida de vida a cada 10 s; sem batida por 60 s o estado "rodando" é considerado perdido e o próximo pedido recomeça (reinício/deploy perde o cálculo em curso).
- TTL do resultado: 3600 s (igual ao legado). Erro fica 60 s. Erros conhecidos (`DadosNaoEncontrados`, `FonteIndisponivel`, `ParametroInvalido`) mostram a mensagem; qualquer outro vira "Erro inesperado no cálculo." com traceback no log.

### 4.3 `rbs.calcular(livro, status, overrides)`

`load_from_fibery(status, livro)` → `config.with_overrides(**overrides)` → `build_spectrum` → `compute_feasible_vol_range` (falha vira `None`, mostra "—"). `FiberyLoaderError` (ex.: nenhuma calibração no status) vira `DadosNaoEncontrados`.

Atenção: `with_overrides` **não passa** pela validação de viabilidade do loader (só por `config.validate()` dentro de `build_spectrum`). Um σ fora da faixa viável não é recusado: aparece como perfis "sem convergência".

### 4.4 `rbs.montar(calc, classif, perfil)`

Montada a cada requisição sobre o resultado em cache (barato). `classif` vem de `Classes de Ativo` (`Classificação Layer 1`, `Conjunto`, `Livro`), filtrada pelo livro; falha de leitura vira aviso e a página segue sem Layer 1/Conjunto. Gera:

- **cabecalho**: livro, calibração, status, data, faixa de σ, σ mínima long-only.
- **visao**: KPIs, tabela de classes (vol, Max W, Max RC), tabela de RC targets (RC P1, RC P*N*, γ).
- **espectro**: 4 colunas empilhadas 100% (pesos e RC, por classe e por bucket), σ alvo × realizado, tabela de pesos por perfil.
- **detalhe** (perfil escolhido; padrão o do meio): KPIs, composição (peso, RC realizado, RC alvo, Δ RC), donuts (Classe, Layer 1, Conjunto), RC alvo × realizado por bucket e por classe.
- **inputs**: vols, correlação completa (triângulo inferior), **correlação macro** (recalculada aqui com os pesos intra-bucket do perfil escolhido; antes de 2026-11-17 eram os do HRP) e blocos intra-bucket.
- ~~**hrp**: pesos HRP por classe e por bucket, vol HRP.~~ Removido do Hub em 2026-11-17 (PerseveraHub #48).
- **avisos**: lista os perfis não convergidos.

Cores: `BUCKET_COLORS` (de `acompanhamentos/configs/capital_market_assumptions`), com tons por classe dentro do bucket.

### 4.5 Frontend (`AlocacaoRbs.tsx`)

- Filtros em query params (`useFiltrosUrl`), links compartilháveis. Livro/Status/Overrides só valem ao clicar **Calcular espectro**; o seletor de perfil muda na hora.
- `useQuery` com `refetchInterval` de 3 s enquanto `status === 'rodando'`; mostra cronômetro e "leva cerca de 2 minutos".
- 4 abas: Visão geral, Espectro de alocação, Detalhe do perfil, Inputs do modelo (a aba Diagnóstico HRP saiu em 2026-11-17). Botão "Baixar Excel" quando há resultado.
- Os campos de override só mandam valores quando `overrides=true`.

### 4.6 Versão do motor no Hub

`backend/pyproject.toml` fixa `persevera-tools` por commit (`982c227`, v0.21.0). Atualização é deliberada (regra do `CLAUDE.md` do Hub). O pin **já inclui** o loader por livro (PRs #35/#36) e **não inclui** o PR #37 (estimação par a par), irrelevante para a página.

### 4.7 Testes e rastreabilidade

Hub: `backend/tests/api/test_rbs_api.py`, `frontend/.../AlocacaoRbs.test.tsx`, `tests/unit/test_heatmap_correlacao.py`; equivalência com o Streamlit na tag `legado-final` (`tests_paginas/test_rbs.py`). Tools: `tests/quant_research/test_rbs_livro.py` e `test_rbs_estimation.py` (nenhum teste em `tests/` chama `build_spectrum` ou `_optimize_profile_rb`: o `core.py` não tem cobertura direta no Tools; no Hub ele é exercitado pelos testes de API/equivalência). Divergências registradas em `docs/migracao/divergencias.md` (2026-10-03: cálculo em segundo plano; mensagem de erro do loader; 2026-11-03: heatmaps com escala fixa −1..1).

## 5. Observações e riscos (leitura do código; confirmar antes de agir)

1. **Padrão da página não funciona hoje.** O status padrão é "Aprovada", mas as duas calibrações estão em Rascunho. Quem abre a página sem mexer vê a mensagem do loader ("Nenhuma Calibração de Mercado … em status 'Aprovada'"). Já registrado no Hub (divergencias, 2026-10-03), continua valendo.
2. **Defaults de override desalinhados da calibração.** σ 2,1%/12,0% (Hub) vs 1,9%/11,5% (Onshore) vs 2,0%/14,0% (Offshore). Ao ligar overrides sem editar, o Onshore roda com σ diferente do cadastrado e o Offshore com faixa bem menor; os campos não são preenchidos com os valores da calibração carregada.
3. **Overrides contornam a checagem de viabilidade** (4.3). O usuário só descobre pelo aviso de não convergência.
4. **Frescor.** Resultado em cache por 1 h, chave sem versão de calibração: edição no Fibery pode levar até 1 h para aparecer (ou até alguém recalcular após expirar). O cálculo roda em thread do processo web; um deploy durante os ~100 s o perde.
5. **`rc_mode_per_profile` não aparece na UI.** A tela só mostra "Convergiu Sim/Não"; distinguir `hard` de `penalty` exigiria expor o campo.
6. **Casamento por nome.** O loader junta Parâmetros/RC/Correlação à calibração pelo `Name` (fórmula com livro e data). Renomear a calibração ou duas calibrações com o mesmo nome quebram a junção.
7. **Correlação por classe depende de nomes únicos dentro do livro.** Ouro e Bitcoin existem nos dois livros; funciona porque o catálogo é filtrado por livro antes.
8. **`intra_max_weight` nunca é preenchido** (loader fixa `None`; há um TODO), então o HRP intra-bucket não tem limite por classe.
9. **Qualidade da calibração Onshore:** classes "RF Fundo Crédito GI/HY" sem proxy (Fonte = Premissa); correlações de Long & Short eram simuladas na calibração anterior (ver [calibracao.md](calibracao.md)) e não foi verificado se foram substituídas.

## 6. Divergências com os outros docs (para corrigir)

| Onde | Diz | Estado verificado em 2026-11-17 |
|---|---|---|
| `calibracao.md` | Só existe "Calibração - 2026-05-04" em Rascunho | Existem "Calibração Onshore - 2026-10-14" e "Calibração Offshore - 2026-11-09" (Rascunho) |
| `calibracao.md` | Max RC só em Bitcoin, 15% | Bitcoin Max RC 20% (e Max Weight 8%) |
| `calibracao.md` | "Erro de RC não entra no critério de convergência" | Entra: `_is_converged` checa RC por bucket (tolerância 1e-3) desde `d73ef48` |
| `calibracao.md` | Perfis P1–P10 de L&S 11%, 5%, 0… com Max Weight 100% | Max Weight de L&S segue 100%; os pesos citados são da calibração antiga, não reconferidos |
| `__init__.py` do módulo (docstring) | `from persevera_tools.quant_research.hrp import …` | O pacote é `risk_budgeting_spectrum` |

## 7. Pendências sugeridas

- Decidir o que fazer com o padrão "Aprovada" enquanto não houver calibração aprovada (p.ex. cair no status mais avançado disponível, ou avisar de forma explícita).
- Preencher os campos de override com os valores da calibração carregada.
- Testes unitários do `core.py` (convergência, interpolação com γ, modo penalty).
- Atualizar `calibracao.md` e a docstring do `__init__.py`.
