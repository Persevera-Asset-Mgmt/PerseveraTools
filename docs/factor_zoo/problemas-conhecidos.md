# factor_zoo: problemas conhecidos

- **Status:** Aprendizado
- **Atualizado em:** 2026-10-28

## Preços errados após reestruturação

| Código | Datas | `price_close` | Provável causa |
|---|---|---|---|
| GOLL54 | 11–16/06/2025 | 10,27 → 200,01 → 701,00 → 210,30 | grupamento não ajustado ou emenda do alias GOLL4→GOLL54 |
| AZUL54 | 05–09/01/2026 | 680 → 558,9 → 255 → 25 → 75 | idem, alias AZUL4→AZUL54 |

`price_close_split_adj` tem os mesmos valores, então o erro não está no ajuste
por dividendo. Os dois tickers estão no `SEED` de
`persevera_tools/data/factor_zoo/aliases.py`.

**Mitigação proposta:** alerta no pipeline diário para retorno diário acima de
±50% em `price_close`, para pegar esses casos e emendas de alias malfeitas.
Mudança separada, ainda não implementada.

## Composição de índices B3 (`b3_index_composition`)

- **Desatualizada:** última data em 19/06/2026 (IBOV, IBX100, IBX50, MLCX,
  SMLL); perdeu a revisão de setembro. A carga antiga saiu na migração.
  Substituída por `persevera_tools/data/index_composition_update.py`
  (Bloomberg `INDX_MWEIGHT_HIST`, passo 4 do `run_factor_zoo_daily.bat`);
  ver "Carga da composição" abaixo.
- **Código antigo e novo no mesmo dia:** na troca de ticker a B3 lista os dois
  com o mesmo peso (ELET3 e AXIA3, EMBR3 e EMBJ3 em 31/10/2025; soma 108%).
  Quem lê precisa colapsar após traduzir pelos aliases (não somar).
- **Tickers antigos:** a tabela guarda os códigos da época (ELET3, EMBR3,
  MRFG3, NTCO3, ODPV3, GUAR3, AZUL4…), que o factor_zoo já trocou pelos
  sucessores (AXIA3, EMBJ3, MBRF3, NATU3, SAUD3, RIAA3, AZUL54…). Sem
  tradução via `factor_zoo_alias` esses nomes somem do universo.
- **Cobertura:** IBOV e IBX100 desde 2000, IBX50 desde 2002, MLCX e SMLL desde
  2008; diária; `value` é o peso no índice.

## Carga da composição (`index_composition_update`)

- Campo Bloomberg `INDX_MWEIGHT_HIST` no ticker do índice com
  `END_DATE_OVERRIDE`: devolve todos os membros na data, inclusive deslistados.
  `PERCENT_WEIGHT` + `REL_INDEX` é por ativo e exigiria conhecer de antemão os
  membros possíveis (viés de sobrevivência com a lista de ativos atual).
- Mapeamento: IBOV Index → IBOV, IBX Index → IBX100, IBX50 Index → IBX50,
  IBRA Index → IBRA (novo), SMLLBV Index → SMLL, MLCXBV Index → MLCX.
- Rebalanceamento do backtest pode ser diário, então a tabela tem uma linha
  por membro por pregão. No histórico, busca fins de mês e faz bisseção onde
  os membros mudam: membros exatos em todo pregão, pesos exatos nos dias
  buscados e repetidos entre eles. Ensaio com o IBOV de 2025 (tabela antiga
  como fonte): 251 pregões reproduzidos com 70 consultas, zero divergências.
- Falha (exit 1) em erro de consulta, composição vazia após o início do
  índice ou índice mais de 5 pregões atrasado. Soma de pesos ≠ 100% e troca de
  mais de 10% dos membros só geram alerta.
- Ainda não rodou contra o Bloomberg: primeira execução deve ser manual com
  `--dry-run` no PC do terminal.
