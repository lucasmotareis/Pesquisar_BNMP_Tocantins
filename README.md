# Painel PMTO de Mandados BNMP

Projeto local para coletar, processar e visualizar mandados do BNMP/CNJ com foco operacional na Policia Militar do Tocantins.

## Arquivos principais

- `painel_tocantins.py`: servidor local do painel e proxy para download de PDFs do BNMP.
- `painel_tocantins.html`: interface do painel com filtros por CRP, BPM/CIPM, orgao expedidor e endereco.
- `coletar_pecas_autorizadas.py`: coleta paginada do endpoint `/pesquisa-pecas/filter`.
- `teste.py`: baixa os PDFs dos relatorios e gera `mandados_processados.json`/`.csv`.
- `bnmpScrapper.py`: script original de coleta CSV.

## Dados locais

Os arquivos com dados operacionais nao entram no Git:

- `pecas_autorizadas.json`
- `mandados_processados.json`
- `mandados_processados.csv`
- PDFs baixados

Mantenha esses arquivos apenas na maquina de trabalho.

## Como rodar o painel

Instale as dependencias:

```powershell
pip install -r requirements.txt
```

Inicie o servidor local:

```powershell
python .\painel_tocantins.py --host 127.0.0.1 --port 8765
```

Abra:

```text
http://127.0.0.1:8765
```

O painel usa `mandados_processados.json` quando ele existir na pasta. Se esse arquivo nao existir, usa `pecas_autorizadas.json`, mas sem CPF, validade e endereco extraidos dos PDFs.

## Cookie BNMP

O cookie `portalbnmp` e informado no painel e fica somente em memoria no servidor local. O temporizador do site considera 4 minutos para renovacao.

## Download de PDF

O botao `Baixar PDF do mandado` chama o backend local, que consulta:

```text
/bnmpportal/api/certidaos/relatorio/{id}/{idTipoPeca}
```

usando o cookie BNMP informado no painel.
