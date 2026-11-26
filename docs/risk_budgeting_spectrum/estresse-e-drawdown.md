# RBS: estresse e drawdown histórico

- **Status:** Implementado em `risk_budgeting_spectrum/stress.py` (funções puras, testes offline). O Hub o usa na
  página Alocação · RBS (PR separado no PerseveraHub).
- **Atualizado em:** 2026-11-26
- **Por quê:** o σ-alvo mede o risco em tempos normais. O estudo de 2026-11-25 mostrou que o P1 perde
  de 4,4% a 4,6% num mês (Onshore, março de 2020) e 8,6% em 2021-22 (Offshore), e que nenhum teto de
  peso resolve isso ([backtest.md](backtest.md)). A saída adotada foi **mostrar essa perda ao lado do σ**.

## O que calcula

| Função | Resultado |
|---|---|
| `retornos_janelas(precos)` | retorno de cada série em cada janela de `JANELAS_ESTRESSE` |
| `estresse_carteira(pesos, retornos)` | retorno da carteira em cada janela; NaN se uma classe com peso não tem dado |
| `drawdown_historico(pesos, retornos_mensais)` | pior mês e máximo drawdown da carteira **atual** no histórico mensal |
| `choque_vol_correlacao(pesos, cov)` | σ base e σ sob choque (vols × 1,5; ρ → ρ + 0,5·(1 − ρ)) |

## Janelas

Retorno entre dois fechamentos (`P(fim)/P(base) − 1`; sem pregão na data vale o último fechamento, até 7 dias antes):

| Janela | Base | Fim |
|---|---|---|
| Dia 26/10/2026 | 2026-10-23 | 2026-10-26 |
| Semana 26-30/10/2026 | 2026-10-23 | 2026-10-30 |
| BR mai-jun/2018 | 2018-05-17 | 2018-06-29 |
| COVID fev-mar/2020 | 2020-02-19 | 2020-03-23 |
| Juros 2022 (jan-out) | 2021-12-30 | 2022-10-14 |

As datas foram recuperadas comparando com os retornos publicados em
[avaliacao-modelo.md](avaliacao-modelo.md) (seção 13), com preços de `indicadores`: COVID, mai-jun/2018 e
os dois dias de outubro reproduzem o Ibovespa, o S&P 500 e o IMA-B 5+ ao quinto decimal. A janela de 2022
reproduz o Ibovespa e fica a 0,2 p.p. no S&P 500 (−25,0% contra −24,8%) e 0,05 p.p. no IMA-B 5+: a data exata da
análise original não ficou registrada.

## O que o drawdown não é

- É a **carteira de hoje aplicada ao passado** (pesos fixos, rebalanceados todo mês, dado mensal), não o
  backtest walk-forward ([backtest.md](backtest.md)), que re-estima os pesos a cada trimestre. Os dois números
  diferem (ex.: P1 Onshore −4,4% no backtest).
- A amostra começa no primeiro mês em que todas as classes com peso têm retorno (`desde`): perfis com Bitcoin ou
  Systematic Trend têm histórico mais curto que os conservadores. Compare perfis olhando também o `desde`.
- Dado mensal subestima a perda dentro do mês.
- Classes sem série (fundos de crédito Onshore) não têm retorno próprio: quem chama as trata como IDA-DI
  (otimista) e IDA-IPCA (pior caso), e mostra a faixa.
- Caixa não entra (decisão de liquidez); quem exibe aplica o colchão, perda × (1 − caixa).
