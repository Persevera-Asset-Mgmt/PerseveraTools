# Validação diária das rotinas agendadas

- **Status:** Implementado (este PR; agendamento pendente)
- **Atualizado em:** 2026-11-06

## Por que pelo banco e não pelo log

Os jobs (`run_factor_zoo_daily.bat` e o de indicadores, renda fixa e IHFA)
rodam no PC Bloomberg, e o log do pacote fica em `~/.persevera/logs` do
usuário de lá. Esse log também não registra tudo: o `company_data` mostra o
progresso com `print`, e uma falha antes do Python (`python not found`, `cd`
que falhou) não deixa nada no arquivo. O de 06/11/2026 parecia limpo, mas a B3
tinha deixado de gravar 05/11 por timeout. O banco mostra o que chegou e
pode ser lido de qualquer máquina.

`persevera_tools.monitoring.daily_checks` só lê. Sai com código 1 quando
alguma checagem falha.

```
python -m persevera_tools.monitoring.daily_checks [--date AAAA-MM-DD] [--json]
```

## Pregão esperado

O dia útil ANBIMA (`feriados_anbima`) anterior à data da checagem, menos os
dias úteis sem fechamento do Ibovespa em `indicadores` (`br_ibovespa`), que
são fechamentos só da B3, como 24/12 e 31/12. Os dias depois do último
fechamento carregado continuam esperados. A defasagem é contada em pregões.

## Checagens

| Checagem | Rotina | Falha | Aviso |
|---|---|---|---|
| `factor_zoo` `price_close` | `.bat` passo 1 (`company_data`) | último pregão ausente | códigos < 90% da mediana dos 10 pregões anteriores |
| `factor_zoo` `median_dollar_volume_traded_21d` | `.bat` passo 3 (fatores) | último pregão ausente | idem |
| Ações Ativas sem `price_close` | passo 1 / cadastro no Fibery | | algum código ativo sem histórico |
| Aliases pendentes | `.bat` passo 2 (`aliases --apply`) | | código antigo com linhas no `factor_zoo` ou na composição a partir da vigência |
| Saltos de preço | passo 1 | | retorno acima de ±50% no último pregão gravado |
| `b3_index_composition` | `.bat` passo 4 | algum índice (IBOV, IBRA, IBX100, IBX50, MLCX, SMLL) atrasado | |
| `indicadores` | spreads/duration de debêntures e IHFA long_short | série atrasada (IHFA aceita 1 pregão a mais) | |
| `renda_fixa_historico` (`source='b3'`) | B3 BDI ConsolidatedRecords | pregão esperado sem linhas | pregão sem linhas ou com < 25% da mediana nos últimos 30 |

O campo derivado da segunda linha prova que o passo 3 rodou. A maioria dos
fatores derivados não serve para isso: o modo incremental só grava linhas
novas ou alteradas, e vários campos (variabilidade, tendência, `short_interest_pct`)
têm datas esparsas por construção.

Uma checagem que dá erro de consulta vira falha só dela; as outras rodam.

## Desempenho

A execução completa leva cerca de 70 s, dos quais ~25 s são da B3:
`renda_fixa_historico` (27 milhões de linhas) não tem índice por data.

`factor_zoo` é particionada por ano e indexada por `(code, field)`. Duas
consultas tiveram de ser reescritas com `EXISTS`:

- **Códigos ativos sem histórico:** `DISTINCT ... code = ANY(...)` levava
  ~100 s, e `NOT EXISTS` por código leva ~3 s.
- **Aliases pendentes:** `count(*)` por alias levava ~3 min, e `EXISTS` leva
  menos de 1 s.

## Primeira execução (06/11/2026, pregão esperado 05/11)

- **`b3_index_composition`:** todos os índices parados em 30/10, 3 pregões de
  atraso. O passo 4 do `.bat` não está gravando. Causa ainda não investigada.
- **B3 BDI:** 05/11 sem dados (timeouts da B3 nas duas execuções de 06/11).
  Também há dias parciais: 13/10 (6.507 linhas), 15/10 (10.918), 30/10
  (5.443) e 04/11 (17.919), contra uma mediana de 134.682. O provider faz uma
  tentativa só, com timeout de 30 s.
- **RAIZ55:** em Ações Ativas sem histórico. É esperado até a próxima execução
  do `.bat` (troca RAIZ4 → RAIZ55 cadastrada no Fibery em 06/11).
- **Ok:** `factor_zoo` (preços e fatores em 05/11), aliases, saltos de preço,
  spreads, duration e IHFA.

## Em aberto

- Agendar a execução diária depois do horário dos jobs e definir onde chega
  o alerta.
- Redirecionar a saída do `.bat` para um arquivo no drive compartilhado, para
  ter também os `print` e os tracebacks.
- Investigar o passo 4 (composição de índices) e as falhas da B3.
