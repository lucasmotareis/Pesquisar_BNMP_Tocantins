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

## Como rodar com Docker Compose

Coloque o arquivo de dados fora do Git, na pasta `data`:

```text
data/mandados_processados.json
```

Suba o container:

```powershell
docker compose up -d --build
```

Abra:

```text
http://localhost:8765
```

Se quiser mudar a porta local:

```powershell
$env:APP_PORT=8080
docker compose up -d --build
```

## Deploy no Coolify

No Coolify, use este repositorio como app Docker Compose. O servico expõe a porta interna `8765`.
Ao configurar dominio/proxy no Coolify, aponte para o servico `painel-bnmp` na porta `8765`.

O arquivo `mandados_processados.json` nao entra no Git. Se o painel abrir com `0` registros e a mensagem `Nenhum arquivo de dados encontrado`, significa que a VPS/container nao esta enxergando esse arquivo.

Para os dados, configure uma variavel ou volume:

- opcao simples: criar uma montagem persistente apontando para `/app/data`;
- dentro dessa montagem, colocar `mandados_processados.json`;
- opcionalmente definir `BNMP_DATA_PATH` no Compose/Coolify para apontar para uma pasta da VPS que contenha o JSON;
- opcionalmente definir `BNMP_DATA_FILE=/app/data/mandados_processados.json`.

Exemplo de variaveis:

```text
APP_PORT=8765
BNMP_DATA_PATH=/caminho/na/vps/dados-bnmp
BNMP_DATA_FILE=/app/data/mandados_processados.json
```

Dentro de `/caminho/na/vps/dados-bnmp`, deixe:

```text
mandados_processados.json
```

Exemplo na VPS:

```bash
mkdir -p /opt/bnmp-data
# envie/copiei o arquivo local mandados_processados.json para:
# /opt/bnmp-data/mandados_processados.json
```

Depois configure no Coolify:

```text
BNMP_DATA_PATH=/opt/bnmp-data
```

## Cookie BNMP

O cookie `portalbnmp` e solicitado somente quando o usuario clica em `Baixar PDF do mandado`. Ele fica somente em memoria no servidor e o temporizador do site considera 4 minutos para renovacao.

Para obter o cookie, acesse `https://portalbnmp.cnj.jus.br/`, valide o Captcha, abra as ferramentas de desenvolvedor com `F12`, entre em `Application`/`Aplicação`, abra `Cookies`, selecione `https://portalbnmp.cnj.jus.br/` e copie o valor do item `portalbnmp`.

## Download de PDF

O botao `Baixar PDF do mandado` chama o backend local, que consulta:

```text
/bnmpportal/api/certidaos/relatorio/{id}/{idTipoPeca}
```

usando o cookie BNMP informado no painel.
