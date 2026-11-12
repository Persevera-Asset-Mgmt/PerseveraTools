# PerseveraTools

Pacote interno da Persevera: leitura e carga de dados (providers → Postgres),
renda fixa, pesquisa quant (factor investing, risk budgeting) e integrações
(Fibery, custodiantes). Visão dos módulos no [README](README.md).

## Regras de trabalho
- Antes de alterar código, explicar o diagnóstico e propor a mudança.
- Não escrever nem apagar nada no Fibery; exclusões de campos são feitas manualmente.
- Commit e push só quando pedido; mudanças vão por branch + PR, nunca direto no `main`.

## Ambiente
- Testes e scripts: `.venv/Scripts/python.exe` (Python 3.12). O `python` do PATH
  é outro (3.15) e não tem numpy/pandas/pytest.
- Testes: `.venv/Scripts/python.exe -m pytest -q tests/`. São offline (sem
  banco, Fibery ou Bloomberg); um teste novo entra no Git normalmente (`tests/`
  saiu do `.gitignore` no PR #27).
- CI: `.github/workflows/tests.yml` roda os testes em todo PR e push no `main`,
  em dois jobs: `dev` (Ubuntu, Python 3.12, `uv.lock`) e `prod-bloomberg`
  (Windows, Python 3.11, pandas 2.2.2, numpy 1.26.4).
- Produção: os jobs agendados (`run_*.bat`) rodam no PC Bloomberg, com
  Python 3.11, **pandas 2.2.2 e numpy 1.26**. Código com datas ou tipos numpy
  deve funcionar também nessas versões (o job `prod-bloomberg` do CI cobre
  isso). Uma mudança só chega lá depois de o pacote ser reinstalado nessa
  máquina.

## Banco e n8n
- Vários providers de `persevera_tools/data/providers` têm uma réplica em
  workflow do n8n que grava nas mesmas tabelas. Renomear tabela, mudar campo ou
  chave exige atualizar código **e** workflow. Mapa em
  [docs/data/providers-tabelas-n8n.md](docs/data/providers-tabelas-n8n.md).
- `renda_fixa_historico` (antes `credito_privado_historico`) é compartilhada por
  vários providers; a chave do upsert inclui `source`.
- Desde 2026-11-11 ela guarda também títulos públicos, com o vencimento no code
  (`NTN-B_2035-05-15`; `persevera_tools/data/tpf_codes.py`). Query que lista
  códigos dessa tabela (ex.: debêntures para a Mais Retorno) precisa excluí-los;
  query de TPF por data deve repetir o predicado do índice parcial
  `renda_fixa_historico_tpf_field_date_idx` (a tabela tem ~28 mi de linhas e
  nenhum índice por data). `anbima_titulos_publicos_historico` está em
  desativação. Detalhes na seção TPF do mapa acima.

## Documentação
- `docs/` guarda o *porquê*: aprendizados, diagnósticos e propostas, com
  `Status` e `Atualizado em`. Convenções em [docs/README.md](docs/README.md).
- Contexto específico de um módulo fica num `CLAUDE.md` dentro da pasta dele
  (ex.: `persevera_tools/quant_research/risk_budgeting_spectrum/CLAUDE.md`).
