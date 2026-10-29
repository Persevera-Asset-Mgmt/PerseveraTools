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
  Resolvido pela padronização de tickers (abaixo): fica a linha do sucessor.
- **Duas convenções de ticker:** as linhas da carga antiga (até 18/06/2026)
  guardam os códigos da época (ELET3, EMBR3, MRFG3, NTCO3, ODPV3, GUAR3,
  AZUL4…), que o factor_zoo já trocou pelos sucessores (AXIA3, EMBJ3, MBRF3,
  NATU3, SAUD3, RIAA3, AZUL54…). O Bloomberg devolve o histórico com o ticker
  atual na data da consulta (IBRA de 2013 traz MOTV3, AXIA3, GOLL54), então
  cada troca futura deixa linhas antigas para trás.
- **Padronização de tickers:** como no factor_zoo, um ativo, um código. A carga
  traduz pelo `factor_zoo_alias` (respeitando `effective_date`) antes de
  gravar, e `factor_zoo.aliases --apply` (passo 2 do `.bat` diário) renomeia
  as linhas já gravadas quando surge um alias novo, arquivando o original em
  `factor_zoo_removed`. Primeira aplicação: 21 aliases, 143 mil linhas.
- **Cobertura (28/10/2026):** em 94,2% dos membros-dia o código existe no
  `price_close` do factor_zoo, 5,1% casam via alias e 0,7% não casam (AMBV4,
  AMBV3 e TIET11 sem alias, cadastrados em 28/10/2026; PRGA4, ARCE4, BSUL5,
  SUBA3, PLAS4 e dois ids do Bloomberg deslistados).
- **Aliases aplicados (28/10/2026):** 24 aliases na composição; nenhum código
  antigo sobrou e a correspondência com o `price_close` do factor_zoo subiu
  para 99,7% dos membros-dia (sobram PRGA4, ARCE4, SUBA3, BSUL5, PLAS4,
  BRDT4-old e dois ids do Bloomberg). No factor_zoo, AMBV3 (141.979 linhas) e
  TIET11 desde 2016 (39.189) foram arquivados em `factor_zoo_removed`.
- **Classes que convivem no índice:** AMBV3/AMBV4 eram listadas junto com a
  série ABEV3 até 2013. A regra "fica a linha do sucessor" (certa no dia de
  uma troca de ticker) descartou o peso da outra classe: faltam ~4,7% no
  IBX100 (2.163 dias), ~4,2% no MLCX (1.338) e ~1,2% no IBRA (324). A carga
  passou a somar os pesos quando código antigo e sucessor convivem por mais
  de `TICKER_CHANGE_OVERLAP` (5) pregões; o `aliases --apply` (SQL) mantém
  "sucessor vence", então caso parecido descoberto no futuro pede recarga do
  período.
- **Somas de peso da carga antiga:** 820 combinações índice-dia fora de
  100% ± 0,5 antes de qualquer alias (IBX100 de mar/2000 em ~67%; SMLL com 703
  dias entre 99,4% e 101,1%). Motivo da recarga integral pelo Bloomberg. A
  carga só substitui a partir do primeiro dia com membros no Bloomberg; o que
  houver antes fica como está.
- **Ticker atual antes da vigência:** o Bloomberg devolve o ticker atual mesmo
  antes do `effective_date` de um alias (AXIA6 no IBOV de 2002), mas o
  factor_zoo guarda esse trecho no código antigo (ELET6 até 2019). Nesses
  dias o backtest não acha preço: no IBOV recarregado, 98,4% dos membros-dia
  tinham preço no factor_zoo, e todo o resto era AXIA6 e GOLL54 antes de
  2020. A carga e o `aliases --apply` passaram a devolver o código antigo
  antes da vigência (10.381 linhas pendentes em 29/10/2026: AXIA6, GOLL54,
  AESB3). Medir cobertura por data, não só pela existência do código.
- **Recarga pelo Bloomberg (29/10/2026):** IBOV de 2002 em diante. Entre
  2000 e abril de 2001 o Bloomberg tem o IBOV incompleto (17 membros em
  janeiro de 2001, contra 55 na tabela), então esse trecho ficou com a carga
  antiga. IBX100 tem histórico no Bloomberg desde 2002 e IBX50 desde 2003.
- **Alias fora do Fibery:** quando o sucessor já saiu de Ações Ativas (AESB3,
  incorporada pela Auren), o par vai para o `SEED` do `aliases.py`; recolocar o
  sucessor em Ações Ativas faria o factor_zoo baixar um papel extinto. A carga alerta membros do último pregão sem
  `price_close` recente no factor_zoo: CBAV3 e MILS3 (em convergência por OPA,
  tirados de Ações Ativas e retomados); AXIA6 sem negociação desde 05/06/2026.
- **Deslistados sem ticker:** vêm do Bloomberg como ids (`0927042D`,
  `1844932D`) e não casam com o factor_zoo; pequeno viés de sobrevivência no
  histórico do IBRA.
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
- Pregões: dias úteis ANBIMA com fechamento do Ibovespa. A B3 fecha em dias
  que a ANBIMA não lista (24/12, 31/12 ou último dia útil do ano, 25/01 e
  09/07 até 2021, 20/11 até 2019); nesses dias o Bloomberg às vezes já
  devolve a carteira seguinte (IBRA em 31/12/2015).
- Primeira carga em 2026-10-28 (PC do Bloomberg): 5 índices de 19/06 a 27/10
  e IBRA desde o lançamento (23/07/2012, 860 consultas, ~15 min). Emenda com a
  carga antiga sem troca de membros; pesos somam 100% ± 0,5 em todo pregão.
  GOLL54 sai do IBRA em 30/01/2024 e AZUL54 em 29/05/2025, antes dos preços
  errados; GOLL4 sai do IBX100 da carga antiga um pregão antes (29/01/2024),
  então as fontes podem divergir em ±1 pregão nas datas de troca.
- xbbg: o PC do Bloomberg usa 0.12 (`bds` aceita `format`, fixado em
  `wide`); o `.venv` tem 1.4, cujo `bds` não tem `format`.
