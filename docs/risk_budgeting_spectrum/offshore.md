# RBS offshore: espectro de alocação em USD

- **Status:** Proposta (loader por livro em PR #35, estimação par a par neste PR; calibração em Rascunho no Fibery)
- **Atualizado em:** 2026-11-09

## Ponto de partida

O modelo offshore atual é a planilha "Model Portfolios" (Google Sheets). Tem 5
perfis (Preservation → Growth) com uma divisão fixa RF/RV/Alternativos por
perfil e uma divisão fixa dentro de cada classe, igual em todos os perfis
(pesos 1/n ou 2:1). Não usa vol nem correlação. Abas: Atual (ETFs US), Atual
(UCITS) com tickers equivalentes e da XP, De-risked (sem MTUM) e Weights, que
gera a cesta da IBKR.

Problemas encontrados na planilha (2026-11-06):
- linha sem ticker na aba Weights, entre FRDM e IEFA, com 1/3 da parcela de
  ações ex-EUA;
- `#VALUE!` em ANGL e MBB no Preservation (o "-" é texto);
- equivalentes UCITS que mudam a exposição: USFR e FLOT viram o mesmo FLOA
  (floater corporativo), STIP 0–5 vira IDTP (curva inteira), ANGL vira IHYA (HY
  amplo); MBB e MTUM ficaram com o ticker US.

Vol realizada (USD, retornos semanais) dos perfis atuais, com proxies para
IGIB (IEF), CTA/IALT (DBMF), GLDM (ouro) e FBTC (bitcoin):

| Perfil | jun/19–nov/26 | nov/23–nov/26 |
|---|---|---|
| Preservation | 3,5% | 2,6% |
| Conservative | 7,2% | 5,4% |
| Mod_Conservative | 10,1% | 7,9% |
| Moderate | 13,2% | 10,6% |
| Growth | 16,4% | 13,3% |

## Decisões de desenho

1. **Mesmo motor e mesma nomenclatura do onshore.** Perfis P1…P10 numa curva
   exponencial de vol, orçamento de risco por bucket (RF, Alternativos, RV).
2. **Faixa própria: σ de 2% a 14%, em USD.** A curva onshore (1,9%–11,5%)
   cortaria o Growth atual. Medir em BRL não serve: o câmbio põe até a carteira
   de T-bills em ~15% de vol, e quanto mandar para fora é decisão de alocação
   do cliente, fora do modelo (como o caixa no onshore).
3. **T-bills dentro do otimizador, como Pós-fixado.** Diferente do onshore,
   onde o caixa fica fora. Com vol de ~0,4%, a classe precisa de Max Weight
   (40%) para o risk budgeting não concentrar nela.
4. **Um representante por classe.** UCITS quando existir; senão o ETF US. Pares
   que eram escolha de duration ou estilo saem (SCHP, IGIB, FLOT, QQQ, MTUM,
   CTA, IALT).
5. **Sem visões táticas nem regimes** nesta etapa, como no RBS onshore.
6. **Alternativos com 15% do risco no P10** (onshore: 25%). Com 25%, o P10
   ficava com 37% em alternativos e ~55% em ações.
7. **Parâmetros no Fibery, nas mesmas tabelas do onshore**, com o campo Livro
   (ver abaixo). Vol e correlação são estimadas por script e congeladas na
   calibração, para o espectro ser reproduzível.

## Classes (14)

Nomes e proxies como estão no Fibery (2026-11-09). A Proxy é um índice de
histórico longo (retorno total, USD); o ETF de implementação é separado.

| Bucket | Classe | Proxy | Ticker | Diário desde | Max Weight | RC Intra |
|---|---|---|---|---|---|---|
| RF | RF Pós-fixado | `us_bloomberg_tbill_index` | LD20TRUU | 2000 | 40% | |
| RF | RF Treasury Curta | `us_bloomberg_treasury_1_3y_tr` | LT01TRUU | 2000 | 100% | |
| RF | RF Treasury Longa | `us_treasuries_7_10y` | LT09TRUU | 1997 | 100% | |
| RF | RF Inflação | `us_bloomberg_tips_0_5y_tr` | LTP5TRUU | 2010* | 100% | |
| RF | RF Crédito IG | `us_bloomberg_corporate_ig_bond_index` | LUACTRUU | 1989 | 100% | |
| RF | RF Crédito HY | `us_bloomberg_corporate_hy_bond_index` | LF98TRUU | 1999 | 15% | |
| RF | MBS | `us_bloomberg_mbs_tr` | LUMSTRUU | 2000 | 20% | |
| Alt | Systematic Trend | `DBMF` | DBMF US Equity | 2019 | 20% | |
| Alt | Ouro | `gold` | XAU Comdty | 1980 | 15% | |
| Alt | Bitcoin | `bitcoin_usd` | XBTUSD Curncy | 2011 | 5% (Max RC 20%) | |
| RV | Ações EUA | `us_sp500_tr` | SPXT | 2000 | 60% | 60% |
| RV | Ações DM ex-EUA | `msci_eafe_net_tr` | NDDUEAFE | 2000 | 30% | 22% |
| RV | Ações EM | `msci_em_net_tr` | NDUEEGF | 2000 | 20% | 12% |
| RV | Imobiliário | `ftse_epra_nareit_developed_tr` | TRNGLU | 2005 | 10% | 6% |

\* LTP5TRUU tem dados desde 2002, mas com lacunas: os retornos semanais
contínuos começam em jun/2010.

Ouro e Bitcoin têm o mesmo nome nos dois livros; o loader filtra por livro, então
isso funciona. Os Max Weight são chute inicial, a validar no comitê. RC Intra na
RV segue aproximadamente o peso de mercado; sem ele, o motor divide o risco
igualmente e EM fica com mais peso que EUA.

Escolhas de proxy:
- **SG Trend (NEIXCTAT) tem acesso restrito** no terminal; o ETF CTA (Simplify)
  foi testado e descartado (correlação 0,42 com o DBMF, vol 17,8% contra 11,1%).
  O trend fica com o DBMF, desde 2019. Alternativa futura: índice próprio de
  time-series momentum com os ~60 futuros da base.
- IG e HY usam índices amplos (LUACTRUU, LF98TRUU); os ETFs SLQD (0–5 anos) e
  ANGL (fallen angels) medem um risco diferente. O UCITS mapeado (VDPA, IHYA)
  já é amplo.
- A relação `Classes de Ativo.Proxy` ↔ `Indicadores` passou a 1 indicador → N
  classes, para Bitcoin compartilhar `bitcoin_usd` com o onshore.

Tickers UCITS ainda a confirmar: T-bill 0–3 meses (ex.: IB01) no lugar de FLOA,
TIPS 0–5, fallen angels ou HY, MBS, DM ex-EUA, imobiliário e DBMF.

## Protótipo (2026-11-09)

Vols e correlações de jun/19 a nov/26 (388 semanas), σ 2%–14%, 10 perfis,
peso mínimo 2%, RC por bucket RF 100%→5% (curvatura 0,5), Alt 0%→15% (1,2),
RV 0%→80% (1,2). Os 10 perfis convergiram.

| | P1 | P3 | P5 | P7 | P9 | P10 |
|---|---|---|---|---|---|---|
| Vol-alvo | 2,0% | 3,1% | 4,7% | 7,3% | 11,3% | 14,0% |
| RF | 100% | 89% | 75% | 57% | 31% | 13% |
| Alternativos | 0% | 7% | 12% | 16% | 21% | 23% |
| RV | 0% | 5% | 13% | 27% | 48% | 64% |
| Ações EUA | 0% | 5% | 9% | 17% | 29% | 38,5% |

Pesos fixos, jun/19–nov/26, in-sample, sem custos:

| | Vol | Retorno a.a. | Max DD |
|---|---|---|---|
| Atual Conservative | 6,9% | 6,5% | −14,6% |
| Novo P7 | 7,3% | 6,7% | −13,5% |
| Atual Moderate | 12,9% | 10,9% | −25,3% |
| Novo P10 | 14,0% | 12,8% | −26,9% |

A eficiência por unidade de risco fica parecida com a do modelo atual. O ganho
é de estrutura: risco declarado por perfil, mesma régua do onshore, menos
posições e pesos que respondem a vol e correlação.

**Janela curta não serve.** Com vols de nov/23–nov/26 (ações EUA a 14%), o
P9 e o P10 ficam inviáveis com o RC por bucket e o motor cai no modo de
penalidade (P10 com 33% em T-bills). A calibração tem de usar janela longa.

## Fibery: campo Livro

`Calibração de Mercado` e `Classes de Ativo` ganham o campo **Livro** (seleção
única: Onshore, Offshore). O loader filtra a calibração e o universo pelo
livro; ausente ou vazio conta como Onshore. `Parâmetros de Classe` ganha o
campo opcional **RC Intra-Bucket** (número).

Ordem obrigatória:
1. merge do PR do loader;
2. subir a versão do persevera-tools fixada no Hub (`backend/pyproject.toml`)
   e reinstalar o pacote onde roda o Dashboard ("Alocação · RBS");
3. só então criar os campos e cadastrar classes e calibração offshore.

O loader antigo usa todas as classes do catálogo. Com classes offshore
cadastradas antes do passo 2, o RBS onshore quebra no Hub e no Dashboard.

Os CSVs para importação saem de `scripts/rbs_offshore_calibracao.py`
(classes, parâmetros, 91 correlações, RC targets e os dados da calibração).

### Estado em 2026-11-09
- "Calibração Offshore - 2026-11-09" cadastrada em Rascunho, com as 14
  classes "Off …", 14 parâmetros, 3 RC targets e 91 correlações. O espectro
  calculado a partir do Fibery bate com o protótipo.
- Name da calibração inclui o livro: "Calibração Onshore - …" /
  "Calibração Offshore - …". Livro preenchido em todas as calibrações e
  classes. Parâmetros, RC targets e correlações têm um campo calculado
  `Livro` (da calibração) para filtro.
- Visões no space Inv-Rsrch-Quant: "RBS Onshore - Calibrações",
  "RBS Offshore - Calibrações", "RBS Onshore - Classes", "RBS Offshore - Classes".
- Relações 1:1 a cuidar: `Classes de Ativo.Proxy` ↔ `Indicadores` (por isso
  o Off Bitcoin usa FBTC como proxy; `bitcoin_usd` é do Bitcoin onshore) e, até
  2026-11-09, `RC Target por Bucket.Classificação Layer 1`, convertida para
  N RC targets por bucket depois de criar os RC targets offshore ter desligado
  os buckets dos onshore.
- `read_fibery` devolve `pd.NA` nos vazios de uma coluna mista; o loader trata
  como Onshore (PR #36).

## Reestimação com proxies-índice (2026-11-09)

`scripts/rbs_offshore_calibracao.py --inicio 2005-01-01 --inicio-classe "Bitcoin=2014-01-01" --espectro`:
vol de cada classe no próprio histórico, correlação par a par no período em
comum (`estimation.estimate_class_stats(method="pairwise")`); a matriz já saiu
PSD (ajuste 0). Bitcoin começa em 2014 porque 2010–2013 levava a vol a 92%.
Demais parâmetros = calibração offshore de 2026-11-09.

| Vol (%) | Ref. (ETFs 2019–26) | Nova |
|---|---|---|
| Ações EUA | 18,4 | 17,5 |
| Ações EM | 23,3 | 20,9 |
| Imobiliário | 24,1 | 21,0 |
| RF Crédito IG | 4,5 | 6,1 |
| RF Crédito HY | 11,7 | 7,6 |
| MBS | 5,5 | 3,9 |
| Bitcoin | 61,0 | 64,7 |

Correlações que mais mudaram: ações EUA × Treasury 7–10 de 0,01 para −0,21
(entra 2008); ações × ouro de 0,24 para 0,07; IG × Treasury 7–10 de 0,40 para
0,73 (índice amplo tem duration).

| | P1 | P3 | P5 | P7 | P9 | P10 |
|---|---|---|---|---|---|---|
| RF | 100% | 88% | 75% | 59% | 33% | 13% |
| Alternativos | 0% | 7% | 9% | 11% | 15% | 18% |
| RV | 0% | 6% | 16% | 30% | 52% | 69% |
| Ações EUA | 0% | 6% | 11% | 17% | 32% | 41% |

Os 10 perfis convergem. Com a janela longa o ouro passa a diversificar melhor
e toma espaço do trend, que zera em P6, P7, P9 e P10. Parte disso vem de o trend
ser estimado só desde 2019 (DBMF) enquanto as outras classes usam 2005–2026.

## Em aberto

- Trend com histórico curto (DBMF desde 2019); índice próprio de TSMOM com os
  futuros da base resolveria.
- Piso de 2% gera saltos entre perfis (TIPS zera do P7 em diante, trend entra
  no P8 e sai no P9).
- Walk-forward fora da amostra.
- Campos de ticker (US, UCITS, XP) no catálogo para gerar a cesta da IBKR.
