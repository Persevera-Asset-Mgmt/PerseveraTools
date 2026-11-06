# docs

Aprendizados, diagnósticos e propostas de pesquisa. Código e docstrings dizem
*como* algo funciona; aqui fica *por que* foi feito assim, o que os testes
mostraram e o que ainda está em aberto.

## Convenções

- Um arquivo por assunto, dentro da pasta do módulo (`factor_investing/`,
  `factor_zoo/`, …).
- Cabeçalho com `Status` (Aprendizado, Proposta, Implementado, Descartado) e
  `Atualizado em` (data absoluta).
- Números de backtest sempre com a configuração que os gerou (período,
  seleção, rebalanceamento, custos).
- Quando uma proposta vira código, atualize o status e aponte o PR.
- **Backtests de factor investing usam o universo dos índices B3 de histórico
  longo:** `universe_index=("IBOV", "IBX100", "SMLL")` (Ibovespa, IBrX 100 e
  Small Caps) com `adtv_min=0`. Início em 2010-01-01 ou depois (SMLL começa em
  19/06/2008). As metodologias da B3 tiram empresas em recuperação judicial
  e outras situações especiais, que no universo por ADTV distorciam os
  resultados (Gol, Azul). Outro universo só como teste de sensibilidade,
  declarado na configuração. Detalhes em
  [factor_investing/universo-por-indice.md](factor_investing/universo-por-indice.md).

## Índice

| Arquivo | Status | Assunto |
|---|---|---|
| [factor_investing/estilos-longo-prazo.md](factor_investing/estilos-longo-prazo.md) | Aprendizado | Backtests de Value, Quality, Momentum e Risk; origem das quebras de performance |
| [factor_investing/ls-beta-neutro.md](factor_investing/ls-beta-neutro.md) | Implementado (PR #14) | Long/short beta-neutro |
| [factor_investing/universo-por-indice.md](factor_investing/universo-por-indice.md) | Implementado (PR #22; Hub #17) | Universo pelos membros de índices B3 em vez de ADTV fixo |
| [factor_investing/analise-do-sinal.md](factor_investing/analise-do-sinal.md) | Implementado (PR #31) | Sharpe sobre CDI, alfa vs Ibovespa, cobertura mínima de componentes e `analyze_signal` (IC, quintis, Fama-MacBeth) |
| [factor_investing/idade-dos-componentes.md](factor_investing/idade-dos-componentes.md) | Implementado (PR #26) | Limite de idade dos componentes por `Frequência` (Diário, Consenso, Trimestral) |
| [factor_investing/diagnostico-engine.md](factor_investing/diagnostico-engine.md) | Proposta (itens 1–3 implementados, PRs #29 e #31) | Problemas do engine e medição de drift e lag: pesos com drift, CDI no L/S e cobertura mínima (feitos), defasagem de execução |
| [factor_zoo/problemas-conhecidos.md](factor_zoo/problemas-conhecidos.md) | Aprendizado | Saltos de papéis em recuperação judicial (preços corretos), carga da composição de índices (PRs #15–#18, #21, #22), tickers antigos, mesmo emissor em raízes diferentes, BDRs no universo |
| [monitoring/validacao-diaria.md](monitoring/validacao-diaria.md) | Implementado (este PR) | Validação diária das rotinas agendadas pelo banco: checagens, limites e o que a primeira execução encontrou |
| [data/providers-tabelas-n8n.md](data/providers-tabelas-n8n.md) | Aprendizado | Tabela de destino de cada provider, chaves do upsert e workflows do n8n que replicam a carga |
| [risk_budgeting_spectrum/calibracao.md](risk_budgeting_spectrum/calibracao.md) | Aprendizado | Calibração do Motor RBS no Fibery e problemas conhecidos |
