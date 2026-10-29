# Universo pelos membros de índices B3

- **Status:** Proposta (bloqueada pelo fluxo da composição de índices)
- **Atualizado em:** 2026-10-28

## Motivação

O filtro atual (ADTV de 21 dias ≥ R$8 mi, valor fixo em reais nominais) deixa
entrar empresas em situação especial que giram muito, como GOLL54 e AZUL54
durante as reestruturações, e fica mais frouxo com a inflação (R$8 mi em 2010
não é R$8 mi em 2026). As metodologias dos índices B3 já filtram liquidez e
situação especial.

## Evidência

Na `b3_index_composition`:

- GOLL4 saiu de todos os índices em 29/01/2024; GOLL54 nunca entrou. O preço
  errado é de 12/06/2025.
- AZUL4 saiu em 29/05/2025; AZUL54 nunca entrou. O preço errado é de 01/2026.
- Tamanho: IBX100 ~100 nomes; IBX100 ∪ SMLL entre 125 (2010) e 163 (2025).

## Pré-requisitos (fluxo de dados)

Ver [../factor_zoo/problemas-conhecidos.md](../factor_zoo/problemas-conhecidos.md):

1. Restabelecer a carga diária da `b3_index_composition` (parou em 19/06/2026)
   e preencher o buraco desde então. Carga escrita
   (`persevera_tools/data/index_composition_update.py`); falta a primeira
   execução no PC do Bloomberg.
2. Traduzir tickers antigos para os sucessores do factor_zoo
   (`factor_zoo_alias`, respeitando `effective_date`). Feito na própria tabela
   (carga e `aliases --apply`); o motor lê códigos canônicos.
3. Monitorar a data da última composição (a carga falha com mais de 5 pregões
   de atraso) e membros sem preço recente no factor_zoo (alerta).
4. Aliases que faltavam: AMBV4 e AMBV3 → ABEV3 cadastrados no Fibery
   (Códigos Anteriores do ABEV3, 28/10/2026); TIET11 → AESB3 no `SEED` do
   `aliases.py`, porque AESB3 saiu de Ações Ativas após a incorporação pela
   Auren.

## Desenho no motor

- `universe_index: str | Sequence[str] | None = None` em `BacktestConfig`
  (ex.: `"IBX100"` ou `("IBX100", "SMLL")`); substitui o filtro de taxonomia
  por denominação.
- Em cada rebalanceamento, composição mais recente até a data (sem olhar para
  frente); os códigos da tabela já são os canônicos do factor_zoo.
- `index_max_age_days` (~10): composição mais velha que isso → erro, em vez de
  seguir com dado velho em silêncio.
- O rebalanceamento pode ser diário, semanal ou quinzenal: usar a composição
  do pregão (a tabela é diária).
- ADTV, preço recente e `one_class_per_issuer` continuam valendo
  (`adtv_min=0` desliga o ADTV).

## Validação planejada

Rodar os 4 estilos com IBX100 ∪ SMLL e comparar com o filtro de ADTV: retorno,
Sharpe, drawdown, número de nomes e se as quebras de GOLL54/AZUL54 somem.
