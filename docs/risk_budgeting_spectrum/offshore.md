# RBS offshore: espectro de alocação em USD

- **Status:** Proposta (loader por livro e gerador da calibração implementados neste PR; calibração ainda não cadastrada no Fibery)
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

| Bucket | Classe | Proxy | Max Weight | RC Intra |
|---|---|---|---|---|
| RF | Off Pós-fixado | USFR | 40% | |
| RF | Off Treasury Curto | SHY | 100% | |
| RF | Off Treasury 7-10 | IEF | 100% | |
| RF | Off TIPS | STIP | 100% | |
| RF | Off Crédito IG | SLQD | 100% | |
| RF | Off Crédito HY | ANGL | 15% | |
| RF | Off MBS | MBB | 20% | |
| Alt | Off Trend | DBMF | 20% | |
| Alt | Off Ouro | ouro | 15% | |
| Alt | Off Bitcoin | bitcoin | 5% (Max RC 20%) | |
| RV | Off Ações EUA | SPYM | 60% | 60% |
| RV | Off Ações DM ex-EUA | IEFA | 30% | 22% |
| RV | Off Ações EM | FRDM | 20% | 12% |
| RV | Off Imobiliário | REET | 10% | 6% |

O prefixo "Off" evita nomes repetidos entre livros: Parâmetros e Correlações
se ligam às classes pelo nome. Os Max Weight são chute inicial, a validar no
comitê. RC Intra na RV segue aproximadamente o peso de mercado; sem ele, o
motor divide o risco igualmente e EM fica com mais peso que EUA.

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

## Em aberto

- Janela mais longa com índices como proxy antes de cada ETF existir.
- Piso de 2% gera saltos (ouro zerado do P3 ao P5, 4,6% no P6).
- Walk-forward fora da amostra.
- Campos de ticker (US, UCITS, XP) no catálogo para gerar a cesta da IBKR.
