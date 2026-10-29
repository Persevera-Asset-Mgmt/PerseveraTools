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

## Índice

| Arquivo | Status | Assunto |
|---|---|---|
| [factor_investing/estilos-longo-prazo.md](factor_investing/estilos-longo-prazo.md) | Aprendizado | Backtests de Value, Quality, Momentum e Risk; origem das quebras de performance |
| [factor_investing/ls-beta-neutro.md](factor_investing/ls-beta-neutro.md) | Implementado (PR #14) | Long/short beta-neutro |
| [factor_investing/universo-por-indice.md](factor_investing/universo-por-indice.md) | Implementado | Universo pelos membros de índices B3 em vez de ADTV fixo |
| [factor_zoo/problemas-conhecidos.md](factor_zoo/problemas-conhecidos.md) | Aprendizado | Preços errados, composição de índices desatualizada, tickers antigos |
