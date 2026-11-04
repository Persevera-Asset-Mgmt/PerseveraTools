# Diagnóstico do engine de backtest

- **Status:** Proposta
- **Atualizado em:** 2026-11-04

Leitura do engine (`persevera_tools/quant_research/factor_investing`) feita em
2026-10-23 e reconferida no código do `main` em 2026-11-04 (depois dos PRs
#14, #22, #26 e #27). Os itens abaixo ainda estão no código. Nenhum foi medido
em backtest, exceto onde indicado.

## O que está certo

- Fundamentos trimestrais datados pela divulgação (com +90 dias como
  alternativa): sem olhar para frente nos balanços.
- `price_close` ajustado por proventos.
- O P&L do dia D usa os pesos de D−1.
- Diagnóstico por rebalanceamento (universo, cobertura por componente,
  turnover, custos, valores descartados por idade).

## Problemas

### 1. Os pesos voltam ao alvo todo dia, sem custo

`daily_weights = weights_df.reindex(trading_index).ffill()` em
`_simulate_holdings_pnl` repete o peso alvo em todos os dias até o próximo
rebalanceamento. O NAV é o de uma carteira rebalanceada todo dia para peso
igual, e não o de uma carteira que só negocia na data de rebalanceamento.

- O rebalanceamento diário embute um efeito de reversão à média que a
  estratégia real não captura.
- No short, a perna vendida não cresce quando o papel sobe, ao contrário de
  uma posição real. No L/S beta-neutro, o beta líquido real também se afasta
  de zero entre rebalanceamentos, e o backtest não mostra isso.
- `_traded_notional` compara peso alvo com peso alvo, e não com os pesos que
  a carteira teria depois de flutuar até a data: turnover e custo de
  negociação ficam subestimados.

**Correção:** deixar os pesos flutuarem com os retornos entre
rebalanceamentos (buy-and-hold) e calcular P&L, turnover e custo a partir dos
pesos flutuados. É o item que mais deve mudar os números.

### 2. Sharpe do long/short com taxa livre de risco

No L/S a garantia não rende nada, mas `build_summary` subtrai
`risk_free_rate` do retorno anualizado. O padrão é 0, então hoje não aparece;
com o CDI (~14%), o Sharpe do L/S fica muito negativo sem motivo. Duas saídas:
somar o CDI sobre a garantia no retorno do L/S, ou não subtrair a taxa livre
de risco no L/S.

### 3. Nomes com poucos componentes ganham força no score

`z_scores.mean(axis=1, skipna=True)` em `calculate_factor_exposure` tira a
média só dos componentes disponíveis. Um papel com 1 de 5 componentes recebe
um score que varia mais e cai com mais frequência nos extremos (topo e fundo).
Em jun/2020, no estilo Value (3 componentes), o universo líquido tinha 123
nomes com 3 de 3, 11 com 2 e 17 com 1; no Quality, 10% a 20% dos nomes têm
cobertura incompleta. O limite de idade (PR #26) aumenta esses casos: a BRAP4
passou a ficar mais tempo na carteira Value (ver
[idade-dos-componentes.md](idade-dos-componentes.md)).

**Correção:** exigir cobertura mínima (por exemplo, pelo menos metade dos
componentes) ou reescalar o score composto pelo número de componentes.

### 4. Sem defasagem de execução

No rebalanceamento de D, o score usa dados até o fechamento de D, e a
carteira nova é negociada no próprio fechamento de D (o retorno de D+1 já usa
os pesos de D). Na prática o fechamento de D só é conhecido depois que o
pregão fecha; o mais cedo possível é a abertura ou o fechamento de D+1.

- Pesa mais em sinais de curto prazo (`momentum_1m`, `rsi_*`, reversão), em
  que parte do retorno acontece no dia seguinte. Fatores trimestrais e de
  consenso quase não sentem.
- Caso parecido nos fundamentos: datados pelo dia da divulgação, que muitas
  vezes sai depois do fechamento.

**Correção:** parâmetro `execution_lag` (pregões entre o sinal e a
negociação, com custo cobrado no dia da negociação). Rodar com 0 e 1 mostra
quanto do resultado depende dessa hipótese.

### 5. Papel que some do pregão

Quando um nome em carteira fica sem preço depois do `price_ffill_limit`,
`_daily_portfolio_return` tira ele do dia e redistribui o peso entre os outros
da mesma perna (evento `renorm`). É como vender pelo último preço. O
movimento durante uma suspensão de mais de 5 pregões também se perde (o
retorno na volta fica NaN). Aceitável para fusões e OPAs; otimista para
recuperação judicial com suspensão longa.

### 6. Idade do ADTV contada em linhas

O filtro de liquidez usa `snapshot_series(max_age=price_ffill_limit)`, que
conta a idade em linhas do painel (a união das datas do `factor_zoo`), não em
dias corridos como os componentes desde o PR #26. Com datas extras no painel
(fundamentos divulgados em fim de semana, por exemplo), o limite efetivo pode
ser menor que 5 pregões.

## Já tratado

- **Limite de idade dos componentes e do beta:** PR #26
  ([idade-dos-componentes.md](idade-dos-componentes.md)).
- **ADTV mínimo nominal fixo** (R$8 mi em 2010 não é R$8 mi em 2026; o
  universo líquido ia de 76 nomes em 2010 a 154 em 2020): o universo por
  índice é a alternativa (PR #22, [universo-por-indice.md](universo-por-indice.md)).
- **Sem testes:** testes offline do limite de idade (PR #26) e CI em dois
  ambientes (PR #27). Ainda faltam testes de `portfolio`, do scoring e da
  simulação de P&L.

## Ordem proposta

1. Pesos com drift (item 1), com testes offline da simulação.
2. Cobertura mínima de componentes (item 3).
3. CDI no long/short (item 2).
4. `execution_lag` (item 4).
5. Agrupar por emissor em vez da raiz do ticker (Eletrobras ON × PNB, ver
   [problemas-conhecidos.md](../factor_zoo/problemas-conhecidos.md)).
