# data: providers, tabelas e réplicas no n8n

- **Status:** Aprendizado
- **Atualizado em:** 2026-11-04

Vários providers têm um workflow no n8n que faz a mesma carga. Mudou tabela,
campo, chave ou query de um lado, mude o outro. Fonte da tabela de destino:
`providers` em `FinancialDataService.get_data`
(`persevera_tools/data/financial_data_service.py`).

## Tabelas e chaves do upsert

| Tabela | Chave | Observação |
|---|---|---|
| `renda_fixa_historico` | code, date, field, **source** | antes `credito_privado_historico` (renomeada em 2026-11-04, PR #24); vários providers gravam nela |
| `anbima_titulos_publicos_historico` | code, date, **maturity**, field | mesmo título com preços diferentes por vencimento |
| `credito_privado_referencia` | code, date | vencimento da NTN-B de referência de debêntures e CRI/CRA |
| `indicadores` | code, date, field | padrão |
| demais | code, date, field | padrão de `get_data` |

## Providers × tabela × n8n

| Source (`get_data`) | Tabela | Workflow n8n (id) | Ativo |
|---|---|---|---|
| `anbima_feed_titulos_publicos_mercado_secundario` | `anbima_titulos_publicos_historico` | ANBIMA Feed → Postgres \| Mercado Secundário (`wJU1cF110bRfljGZ`) | sim |
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

## Limites deste levantamento
Feito pelos nomes e descrições dos workflows e, nos de renda fixa, pelo código
dos nós. 26 workflows não estão liberados para MCP (Recon Agent, `pnl-*`,
`carrying-*`, `btg-*` e outros) e não foram abertos.
