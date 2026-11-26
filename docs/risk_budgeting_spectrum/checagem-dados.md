# RBS: checagem de dados das séries de proxy

- **Status:** Implementado em `risk_budgeting_spectrum/data_checks.py` (funções puras, testes offline) e
  `scripts/rbs_checar_dados.py` (só leitura: Fibery e banco).
- **Atualizado em:** 2026-11-26
- **Por quê:** vols e correlações do RBS saem de séries diárias de `indicadores`. Uma série atrasada, com
  valor repetido ou com um salto de rolagem muda a vol sem aviso. A checagem roda antes de recalibrar e aprovar,
  ao lado de [rbs_conferir_inputs.py](../../scripts/rbs_conferir_inputs.py) (vol da calibração contra a EWMA) e de
  `rbs_robustez.py` (estabilidade dos perfis).

## O que mede (por série, janela de 15 anos)

| Medida | Alerta quando |
|---|---|
| Frescor (`dias_sem_dado`) | mais de 5 dias corridos sem dado (`--max-atraso`) |
| Buracos (`maior_gap`) | intervalo entre observações acima de 10 dias (`--max-gap`) |
| Valores não positivos | qualquer valor ≤ 0 |
| Retornos exatamente zero | mais de 3% dos dias (valor repetido, ex.: feriado preenchido) |
| Retornos extremos (|r| > 6σ) e vol com e sem eles | a vol sem extremos difere mais de 15% da vol com todos os dias (`--tol-vol`) |

**Retorno extremo não é erro.** Em crise ele é real (os mesmos dias aparecem em séries correlacionadas), e a
vol com todos os dias é a correta; a coluna `vol_sem_extremos_%` mostra só a sensibilidade. Quem lê decide.
`top3` lista as três maiores variações com data, para conferir contra uma segunda fonte.

## Uso

    python scripts/rbs_checar_dados.py                       # Onshore e Offshore, só séries com alerta
    python scripts/rbs_checar_dados.py --livro Offshore --status "Em Comitê" --max-atraso 3
    python scripts/rbs_checar_dados.py --todas               # inclui as séries sem alerta

Sai com código 1 se houver algum alerta. Classes sem proxy (fundos de crédito Onshore) são listadas e não checadas.

## Resultado em 2026-11-26 (22 proxies dos dois livros)

Rodada com o banco de produção em leitura. Três alertas, nenhum erro de dado que distorça a vol:

- **`anbima_ihfa`** (Multimercado Onshore) terminava em 2026-11-16, 10 dias de atraso contra 1 a 2 nas demais.
  Verificar a ingestão da ANBIMA.
- **`us_bloomberg_treasury_1_3y_tr`** (RF Treasury Curta Offshore): 178 retornos zero em 3.787 dias (4,7%), o que
  sugere valor repetido em feriado. Os demais índices de renda fixa dos EUA têm de 37 a 46 (cerca de 1%).
- **`anbima_ida_di`**: vol de 0,75% com todos os dias e 0,45% sem os 22 extremos. Sensível, mas a série é de vol
  muito baixa e o peso dela na vol da carteira é pequeno.

Sem alerta, mas para conferir: `gold_100oz_futures` caiu 11,37% em 2026-01-30 (maior variação da janela; é série
de futuros, vale comparar com spot para descartar salto de rolagem). Os extremos de março de 2020, 18/05/2017 e
26/10/2026 são eventos reais: o IMA-B 5+ cai 9,8%, o IRF-M 1+ 6,4% e o IDA-IPCA 5,7% no último, nas três séries ao mesmo tempo.
Nenhum buraco acima de 10 dias, nenhum valor não positivo, e o DBMF começa em 2019-05-08 (origem do histórico
de 2019 do Offshore na página Alocação · RBS, ver [estresse-e-drawdown.md](estresse-e-drawdown.md)).

## Limites

- A vol é o desvio-padrão simples da janela, não a EWMA da calibração (essa fica em `rbs_conferir_inputs.py`).
- Para séries de vol muito baixa e com tendência (IDA-DI, IDA-IPCA), o corte de 6σ pega parte da deriva.
- Não compara com uma segunda fonte; indica onde olhar.
