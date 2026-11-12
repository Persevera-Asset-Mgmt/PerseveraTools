# data: providers, tabelas e réplicas no n8n

- **Status:** Aprendizado
- **Atualizado em:** 2026-11-12

Vários providers têm um workflow no n8n que faz a mesma carga. Mudou tabela,
campo, chave ou query de um lado, mude o outro. Fonte da tabela de destino:
`providers` em `FinancialDataService.get_data`
(`persevera_tools/data/financial_data_service.py`).

## Tabelas e chaves do upsert

| Tabela | Chave | Observação |
|---|---|---|
| `renda_fixa_historico` | code, date, field, **source** | antes `credito_privado_historico` (renomeada em 2026-11-04, PR #24); vários providers gravam nela. Desde 2026-11-11 guarda também TPF, com o vencimento no code (ver abaixo) |
| `anbima_titulos_publicos_historico` | code, date, **maturity**, field | mesmo título com preços diferentes por vencimento. Em desativação: nenhum leitor conhecido usa mais (desde 2026-11-12), mas continua recebendo as cargas até a fase final (ver abaixo) |
| `credito_privado_referencia` | code, date | vencimento da NTN-B de referência de debêntures e CRI/CRA |
| `indicadores` | code, date, field | padrão |
| demais | code, date, field | padrão de `get_data` |

## Providers × tabela × n8n

| Source (`get_data`) | Tabela | Workflow n8n (id) | Ativo |
|---|---|---|---|
| `anbima_feed_titulos_publicos_mercado_secundario` | `anbima_titulos_publicos_historico` + `renda_fixa_historico` (gravação dupla) | ANBIMA Feed → Postgres \| Mercado Secundário (`wJU1cF110bRfljGZ`) | sim |
| `anbima_feed_debentures_mais_mercado_secundario` | `renda_fixa_historico` + `credito_privado_referencia` | idem | sim |
| `anbima_feed_cri_cra_mercado_secundario` | `renda_fixa_historico` + `credito_privado_referencia` | idem | sim |
| `anbima_feed_indices_resultados_*` | `indicadores` | ANBIMA API → Postgres \| Índices (`plfmt05nXImIo4Rv`) | sim |
| `mais_retorno_debentures` | `renda_fixa_historico` | Mais Retorno → Postgres \| Debêntures (`KR9EDNi6nnWTtNfK`) | sim |
| `b3_bdi` | `renda_fixa_historico` | B3 → Postgres \| BDI ConsolidatedRecords (`sMYTeM65QKl6QrmK`) | não (ainda com o nome antigo da tabela) |
| `sgs` | `indicadores` | SGS (BCB) → Postgres (`YacutazBh8n8SXQj`) | sim |
| `sidra` | `indicadores` | SIDRA (IBGE) → Postgres (`dRUhfD41aa2OWzew`) | sim |
| `fred` | `indicadores` | FRED → Postgres (`YGHZDLtyus1T0DrN`) | sim |
| `bcb_focus` | `indicadores` | BCB Focus → Postgres (`RejbZOPA96fEpfwV`) | sim |
| `investfy_investor_flow` | `indicadores` | Investfy → Postgres (`EPqGynjHCChAOU2k`) | sim |
| `mdic` | `indicadores` | MDIC → Postgres (`MXAmmi96PjebII2n`) | sim |

Cargas só no n8n, sem provider equivalente:
- ANBIMA → Postgres \| Série Histórica de Fundos (diário) → `fundos_cvm`
  (`3QNvMyh8iNUsRsKO`, ativo; há duas variantes manuais inativas). O provider
  `anbima_fundos_lote_serie_historica` grava em outra tabela (`fundos_anbima`),
  então não é réplica direta.
- Referências NTN-B (carga histórica) → `credito_privado_referencia`
  (`gLLtysNd5YFJKRcq`, inativo, via webhook).
- Factor Zoo - Atualizar Latest → `factor_zoo_latest` (`DKpyBsSdz0Hi7LA9`).

Providers sem réplica conhecida no n8n: `anbima_debentures`, `anbima_cri_cra`,
`anbima_titulos_publicos`, `anbima_indices`, as demais categorias do
`anbima_feed`, `debentures_com`, `simplify`, `invesco`, `kraneshares`,
`b3_investor_flow`, `mais_retorno_fundos`.

## TPF em `renda_fixa_historico` (desde 2026-11-11)

- Formato do code: `<tipo_titulo>_<YYYY-MM-DD>`, ex. `NTN-B_2035-05-15`
  (`persevera_tools/data/tpf_codes.py`). A chave de `renda_fixa_historico` não
  tem vencimento; uma coluna `maturity` nullable não poderia entrar na PK e
  quebraria os `ON CONFLICT` de todos os escritores.
- Histórico copiado de `anbima_titulos_publicos_historico` em 2026-11-11
  (893.113 linhas, 2017-06-01 → 2026-11-10; source NULL virou `anbima`).
- Gravação dupla durante a transição: no n8n (`wJU1cF110bRfljGZ`) e em
  `FinancialDataService.get_data` para `anbima_feed_titulos_publicos_mercado_secundario`
  e `anbima_titulos_publicos`.
- Índice parcial `renda_fixa_historico_tpf_field_date_idx` em `(field, date)`
  com `WHERE code ~ '^(LTN|LFT|NTN-[A-Z])_\d{4}-\d{2}-\d{2}$'`. Consultas de TPF
  por data devem repetir esse predicado para usá-lo (a PK começa por `code`).
- A lista de códigos da Mais Retorno (provider e `KR9EDNi6nnWTtNfK`) exclui
  `^(LTN|LFT|NTN|TESOURO)`.
- Leitores:
  - `get_series(category='titulos_publicos')` (`persevera_tools/fixed_income/data.py`)
    lê `renda_fixa_historico` desde 2026-11-12 e devolve o mesmo formato de
    antes (`date, code, maturity, value`, com `code` = tipo, ex. `NTN-B`).
    Usado pelo spread IPCA (`calculate_spread`), inclusive no Dashboard (PR #38).
  - PerseveraHub: `curva_ntnb` (Spreads por Emissor) e `taxas_titulos_publicos`
    (duration dos títulos públicos), em `backend/apps/dados/renda_fixa.py`, leem
    `renda_fixa_historico` desde 2026-11-12 (Hub PR #36, em produção). Conferido
    no banco: mesmas 34.473 linhas da curva NTN-B e mesmas 50 taxas de
    2026-11-11. Única diferença: o ponto NTN-B 2025-05-15 de 2020-01-08, que
    tinha source NULL e agora entra na curva do Hub (registrado no
    `divergencias.md` do Hub).
- Fase final, pendente: renomear `anbima_titulos_publicos_historico` (ex.:
  `_old`), criar uma view de compatibilidade com o mesmo nome (SQL em
  `split_part`, como nos leitores) e tirar a gravação dupla do n8n
  (`legacyTable` no nó "Normalizar + Montar Lotes SQL") e de
  `FinancialDataService.get_data` (`tpf_dual_write_sources`). Pré-condições:
  pacote reinstalado no PC Bloomberg (para os jobs Python já gravarem nas
  duas tabelas) e alguns dias de cargas conferidas nas duas tabelas.

## Limites deste levantamento
Feito pelos nomes e descrições dos workflows e, nos de renda fixa, pelo código
dos nós. 26 workflows não estão liberados para MCP (Recon Agent, `pnl-*`,
`carrying-*`, `btg-*` e outros) e não foram abertos.
