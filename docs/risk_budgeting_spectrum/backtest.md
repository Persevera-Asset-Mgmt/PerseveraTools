# RBS: backtest walk-forward trimestral

- **Status:** Implementado (`backtest.py`, `scripts/rbs_backtest.py`); resultados abaixo são do
  estudo de 2026-11-23, com dados mensais extraídos do banco por consulta de leitura.
- **Atualizado em:** 2026-11-23
- **Pergunta:** o σ-alvo de cada perfil se confirma quando vols e correlações são re-estimadas
  a cada trimestre só com dados até a data, e a carteira fica parada até o próximo rebalanceamento?

## Configuração (vale para todos os números)

- **Rebalanceamento:** trimestral (último mês de cada trimestre), carteira deriva entre as datas.
- **Estimação em cada data:** só dados até a data, janela de 15 anos, mínimo de 3 anos por
  classe (classe sem histórico fica de fora: `max_weight` = 1e-6, o piso de peso zera). Vol =
  50% EWMA + 50% desvio da janela (Bitcoin só EWMA); correlação par a par, sem PSD no dado,
  `nearest_correlation` na matriz final.
- **Frequência:** **mensal** (fechamento do mês). O λ do EWMA mantém a meia-vida do λ = 0,995
  diário. Vol e correlação mensais aproximam, mas não replicam, as da página Capital Market
  Assumptions (diária e semanal). Vol realizada e drawdown em dado mensal **subestimam** o risco
  intra-mês.
- **Política:** a da calibração 2026-11-17 em todas as datas (σ máx 11%, RC targets, curvaturas,
  piso de peso; Onshore com tetos de 15% no Fundo Crédito GI e 10% no HY, comparado com a versão
  sem tetos). Não há versão histórica da calibração: testa o **método**, não a política da época.
- **Moedas:** Onshore em BRL; Bitcoin, Ouro e S&P 500 convertidos por `brl_usd` (R$ por US$).
  Offshore em USD. O S&P 500 do Onshore é o índice de preço (sem dividendos), o que subestima o
  retorno do RV EUA.
- **Fundos de crédito (Onshore, sem série):** rendem como IDA-DI ou como IDA-IPCA (dois cenários,
  que são limites e não a série dos fundos).
- **Não inclui:** custos, impostos, colchão de caixa. Caixa de referência do Onshore: CDI.
- **Períodos:** Onshore 2012-03 a 2026-10 (60 rebalanceamentos, 175 meses); Offshore 2008-03 a
  2026-10 (76 rebalanceamentos, 223 meses). Bitcoin só entra depois de 3 anos de dado,
  Systematic Trend (DBMF) e Long & Short idem.

## Resultados

σ realizado = desvio dos retornos mensais × √12; "real/alvo" = σ realizado ÷ σ-alvo médio.

### Onshore (com tetos de crédito; fundos = IDA-DI / IDA-IPCA)

| Perfil | σ alvo % | σ real % (DI / IPCA) | real/alvo (DI / IPCA) | Retorno a.a. % (DI) | Exc. CDI a.a. % (DI) | Pior mês % | Máx. DD % |
|---|---|---|---|---|---|---|---|
| P1 | 1,90 | 2,23 / 3,11 | 1,17 / 1,63 | 10,6 | 0,7 | −4,4 | −4,4 |
| P2 | 2,31 | 2,21 / 2,98 | 0,96 / 1,29 | 10,9 | 1,0 | −4,5 | −4,5 |
| P3 | 2,81 | 2,43 / 3,15 | 0,87 / 1,12 | 11,5 | 1,6 | −4,4 | −4,4 |
| P5 | 4,15 | 3,35 / 3,75 | 0,81 / 0,90 | 12,6 | 2,6 | −4,3 | −4,9 |
| P7 | 6,13 | 4,99 / 5,03 | 0,82 / 0,82 | 14,2 | 4,0 | −4,5 | −5,8 |
| P10 | 11,00 | 9,50 / 9,50 | 0,86 / 0,86 | 18,7 | 8,3 | −4,5 | −11,4 |

Índice de Sharpe sobre CDI (cenário DI): 0,36 (P1), 0,67 (P3), 0,75 (P5), 0,79 (P7), 0,86 (P10).

- **P4 a P10 realizam 80% a 86% do σ-alvo** e P1 a P3 ficam mais perto ou acima. O desvio é
  conservador para os perfis de risco, mas **P1 a P3 dependem do que os fundos de crédito
  entregam**: no cenário IDA-IPCA o P1 realiza 1,6× o alvo.
- **Pior mês: março de 2020, −4,4% em todos os perfis** (P1 a P10). A pernada de renda fixa
  doméstica domina o risco de cauda até do P10, e o P1 não protege dela (já visto no estresse,
  [avaliacao-modelo.md](avaliacao-modelo.md) seção 13).
- **σ realizado por subperíodo (P1 / P5 / P10):** 2012-15 1,2 / 3,5 / 11,4; 2016-19 1,7 / 2,4 / 7,9;
  2020-22 3,5 / 4,3 / 11,0; 2023-26 2,1 / 2,9 / 7,7. O P1 só passa bem do alvo de 1,9% em 2020-22 (3,5%, 1,8×).
- **Perfil sem convergência em 38 das 60 datas** (nas dez primeiras listadas pelo script é o P1:
  2012-03 e 2015-09 a 2017-09). Causa provável: σ-alvo do P1 abaixo do piso viável com aquelas
  vols (não verificado); nesse caso o P1 entrega o piso, acima de 1,9%.
- **Tetos de crédito (15% / 10%): efeito pequeno no histórico**, porque os fundos rendem como
  IDA. P2 sobe de σ 2,12 para 2,21 e o Sharpe cai de 0,52 para 0,47; os demais perfis mudam
  menos que 0,1 p.p. Os tetos são seguro contra o risco que o histórico não mede, não resultado
  do backtest.
- **Giro médio por rebalanceamento:** 4% a 8% do patrimônio (P2 8% com tetos, 5% sem).

### Offshore (sem classes sem série; USD)

| Perfil | σ alvo % | σ real % | real/alvo | Retorno a.a. % | Pior mês % | Máx. DD % |
|---|---|---|---|---|---|---|
| P1 | 2,00 | 2,24 | 1,12 | 2,4 | −2,2 | −8,6 |
| P3 | 2,92 | 3,43 | 1,18 | 3,2 | −4,4 | −11,3 |
| P5 | 4,27 | 5,01 | 1,17 | 4,2 | −7,8 | −14,8 |
| P7 | 6,23 | 7,08 | 1,14 | 4,7 | −10,8 | −22,5 |
| P10 | 11,00 | 11,81 | 1,07 | 8,6 | −15,7 | −35,9 |

- **Todos os perfis realizam acima do σ-alvo** (1,07× a 1,18×): a vol estimada com dado até a
  data subestima o que veio depois, sobretudo em 2008 e 2020-22 (P5: 6,5 em 2008-11 e 6,7 em
  2020-22, contra 4,3 de alvo).
- **P1 perdeu 8,6% de pico a vale** (2022: duration e crédito caem juntos) e o **P10 perdeu
  35,9%** (2008-09). O σ-alvo não descreve essas perdas.
- **P10 não converge** em 10 datas (2008-03, 06 e 09; 2010-09 e 12; 2011-03 e 06; 2012-12; 2013-03;
  2025-12); P8 e P9 também em 2008-03 e 06. Hipótese (não verificada): com poucas classes de risco
  no início do histórico, o teto de vol viável fica abaixo de 11%.
- **Retornos** (P1 2,4% a.a. em USD, sem custos) descrevem o período testado; não são projeção.

## Leitura

1. **O método de estimação não gera carteiras sistematicamente mais arriscadas que o alvo no
   Onshore (P2 a P10 de 4% a 19% abaixo; P1 acima) e as gera no Offshore (7 a 18% acima).** Se o σ-alvo for apresentado
   como promessa, o Offshore precisa de margem (ex.: usar σ-alvo × 0,9 na estimação ou mostrar
   a faixa).
2. **A perda de cauda não escala com o perfil no Onshore** (março de 2020 foi igual em todos). O
   estresse e o σ precisam andar juntos nas lâminas (ver [avaliacao-modelo.md](avaliacao-modelo.md)).
3. **O P1 do Onshore depende de dois fundos de crédito sem série.** Enquanto não houver proxy,
   os tetos de 15% e 10% limitam a exposição ao que não conseguimos medir.

## Limitações

- Dado mensal, política de hoje, 15 anos de janela mesmo quando há menos dado.
- Fundos de crédito, Bitcoin e Long & Short com histórico curto ou substituído.
- Parte da diferença realizado vs alvo é ruído de amostra (175 e 223 meses; erro-padrão da vol
  realizada de uns 5%).
- O Offshore de 2008 a 2011 roda com poucas classes (sem Bitcoin nem Systematic Trend, por
  falta de histórico).

## Como rodar

    python scripts/rbs_backtest.py --livro Onshore --status Rascunho --inicio 2012-03-01 \
        --max-weight "RF Fundo Crédito GI=0.15" --max-weight "RF Fundo Crédito HY=0.10"
    python scripts/rbs_backtest.py --livro Offshore --inicio 2008-03-01

Precisa de Fibery e banco (somente leitura). A CLI não foi executada ponta a ponta nesta
máquina (sem acesso direto ao banco); os resultados acima vêm do mesmo código, com os preços
mensais extraídos por consulta de leitura e a política lida do Fibery. A lógica
está coberta por `tests/quant_research/test_rbs_backtest.py` (sem dado futuro, deriva e giro,
exclusão de classe sem histórico, consistência do EWMA com o estimador diário).
