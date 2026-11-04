# Diagnóstico do engine de backtest

- **Status:** Proposta (item 1 implementado, PR #29)
- **Atualizado em:** 2026-11-04

Leitura do engine (`persevera_tools/quant_research/factor_investing`) feita em
2026-10-23 e reconferida no código do `main` em 2026-11-04 (depois dos PRs
#14, #22, #26 e #27). Os itens 1 e 4 foram medidos em backtest (ver
"Medição: drift e lag"); os outros, não.

## O que está certo

- Fundamentos trimestrais datados pela divulgação (com +90 dias como
  alternativa): sem olhar para frente nos balanços.
- `price_close` ajustado por proventos.
- O P&L do dia D usa os pesos de D−1.
- Diagnóstico por rebalanceamento (universo, cobertura por componente,
  turnover, custos, valores descartados por idade).

## Problemas

### 1. Os pesos voltam ao alvo todo dia, sem custo (implementado)

**Implementado** com `BacktestConfig.weight_drift=True` (padrão): entre
rebalanceamentos os pesos flutuam com os retornos; giro, custo de negociação e
aluguel usam os pesos flutuados; um papel sem preço sai de vez da carteira e o
peso dele vai para a mesma perna. `weight_drift=False` reproduz o engine
anterior, descrito abaixo.

`daily_weights = weights_df.reindex(trading_index).ffill()` em
`_simulate_holdings_pnl` repetia o peso alvo em todos os dias até o próximo
rebalanceamento. O NAV era o de uma carteira rebalanceada todo dia para peso
igual, e não o de uma carteira que só negocia na data de rebalanceamento.

- O rebalanceamento diário embute um efeito de reversão à média que a
  estratégia real não captura.
- No short, a perna vendida não cresce quando o papel sobe, ao contrário de
  uma posição real. No L/S beta-neutro, o beta líquido real também se afasta
  de zero entre rebalanceamentos, e o backtest não mostra isso.
- `_traded_notional` compara peso alvo com peso alvo, e não com os pesos que
  a carteira teria depois de flutuar até a data: turnover e custo de
  negociação ficam subestimados.

Medido: o drift tira de 0,3 a 2,2 p.p. ao ano em todos os casos (tabela
abaixo). O engine anterior também escondia um giro de 130–200% ao ano no
long-only e 455–600% no L/S, feito de graça para voltar ao peso alvo todo dia.

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

Medido: 1 pregão de atraso muda o retorno de −0,6 a +1,1 p.p. ao ano, com
sinal que depende do estilo (tabela abaixo). Piora Value e Momentum no
long-only (que usam o mês mais recente) e melhora Quality e Risk no L/S.

**Correção:** parâmetro `execution_lag` (pregões entre o sinal e a
negociação, com custo cobrado no dia da negociação). Rodar com 0 e 1 mostra
quanto do resultado depende dessa hipótese.

### 5. Papel que some do pregão

Quando um nome em carteira fica sem preço depois do `price_ffill_limit`, ele
sai e o peso dele é redistribuído entre os outros da mesma perna (evento
`renorm`). É como vender pelo último preço. Com `weight_drift=True` ele sai de
vez (um evento por saída); no engine anterior saía só naquele dia e voltava no
seguinte. O movimento durante uma suspensão de mais de 5 pregões também se
perde (o retorno na volta fica NaN). Aceitável para fusões e OPAs; otimista
para recuperação judicial com suspensão longa.

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
- **Sem testes:** testes offline do limite de idade (PR #26), da simulação de
  P&L com e sem drift (`tests/test_factor_investing_simulation.py`) e CI em
  dois ambientes (PR #27). Ainda faltam testes de `portfolio` e do scoring.
- **Pesos com drift (item 1):** PR #29.

## Medição: drift e lag

Mesma configuração de [estilos-longo-prazo.md](estilos-longo-prazo.md)
(2010-01-01 a 2026-10-27, quintil 20%, `BME`, ADTV ≥ R$8 mi, 10 bps); L/S
beta-neutro. As carteiras são as do engine; um protótipo resimulou cada uma
(correlação diária de 0,9999 com o engine na variante base). Os retornos de
GOLL54 e AZUL54 nas janelas de preço errado foram zerados nas quatro
variantes ([problemas-conhecidos.md](../factor_zoo/problemas-conhecidos.md)).
Retorno anual em %, Sharpe entre parênteses:

| Estilo | Livro | Base (pesos fixos) | Lag 1 pregão | Drift | Drift + lag |
|---|---|---|---|---|---|
| Value | LO | 15,3 (0,59) | 14,8 (0,57) | 14,9 (0,58) | 14,4 (0,56) |
| Quality | LO | 11,9 (0,56) | 12,0 (0,57) | 11,2 (0,53) | 11,3 (0,54) |
| Momentum | LO | 15,7 (0,65) | 15,1 (0,63) | 14,7 (0,61) | 14,2 (0,59) |
| Risk | LO | 13,3 (0,86) | 13,5 (0,87) | 13,0 (0,84) | 13,2 (0,86) |
| Value | L/S β | 8,3 (0,52) | 8,2 (0,51) | 7,4 (0,45) | 6,8 (0,41) |
| Quality | L/S β | 15,2 (0,94) | 15,9 (0,98) | 13,0 (0,77) | 13,2 (0,79) |
| Momentum | L/S β | 18,6 (0,92) | 18,7 (0,93) | 16,7 (0,79) | 16,2 (0,77) |
| Risk | L/S β | 18,8 (1,12) | 19,9 (1,19) | 17,9 (0,97) | 18,5 (1,03) |

- Drift: piora todos os casos; −0,3 a −1,0 p.p. no long-only e −0,9 a
  −2,2 p.p. no L/S, com Sharpe 0,13–0,17 menor e drawdown 5–7 p.p. pior no L/S
  (a perna vendida passa a pesar mais quando os papéis sobem).
- Lag: −0,6 a +1,1 p.p., sinal variável. Drift e lag praticamente se somam.
- **Preço errado com drift:** sem zerar GOLL54 e AZUL54, o L/S de Quality com
  drift fica com valor negativo (drawdown de −106%): a posição vendida cresce
  com o salto falso em vez de voltar ao tamanho no dia seguinte. Com o drift
  no padrão, corrigir esses preços no `factor_zoo` passa a ser necessário para
  os L/S de Quality e Risk.
- Os números das outras páginas (estilos, beta-neutro) são do engine com pesos
  fixos; `weight_drift=False` reproduz.

## Ordem proposta

1. ~~Pesos com drift (item 1).~~ Feito.
2. Corrigir os preços de GOLL54 e AZUL54 no `factor_zoo` (pré-requisito para
   os L/S com drift).
3. Cobertura mínima de componentes (item 3).
4. CDI no long/short (item 2).
5. `execution_lag` (item 4).
6. Agrupar por emissor em vez da raiz do ticker (Eletrobras ON × PNB, ver
   [problemas-conhecidos.md](../factor_zoo/problemas-conhecidos.md)).
