# Painel PMTO de Mandados BNMP

Projeto local para coletar, processar e visualizar mandados do BNMP/CNJ com foco operacional na Policia Militar do Tocantins.

## Arquivos principais

- `painel_tocantins.py`: servidor local do painel e proxy para download de PDFs do BNMP.
- `painel_tocantins.html`: interface do painel com filtros por CRP, BPM/CIPM, orgao expedidor e endereco.
- `bnmp_auth.html`: tela de autenticacao assistida para resolver captcha e importar a sessao BNMP.
- `bnmp_browser/`: sidecar Docker com Chromium, noVNC e exportador de cookies/fingerprint.
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

O navegador remoto para resolver o captcha fica, por padrao, em:

```text
http://localhost:6080/vnc.html?autoconnect=1&resize=remote
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

- opcao simples: criar uma montagem persistente gravavel apontando para `/app/data`;
- dentro dessa montagem, colocar `mandados_processados.json`;
- opcionalmente definir `BNMP_DATA_PATH` no Compose/Coolify para apontar para uma pasta da VPS que contenha o JSON;
- opcionalmente definir `BNMP_DATA_FILE=/app/data/mandados_processados.json`.

Exemplo de variaveis:

```text
APP_PORT=8765
BNMP_DATA_PATH=/caminho/na/vps/dados-bnmp
BNMP_DATA_FILE=/app/data/mandados_processados.json
BNMP_COOKIES_FILE=/app/data/bnmp_cookies.json
BNMP_BROWSER_PORT=6080
BNMP_REMOTE_BROWSER_URL=https://seu-dominio-do-novnc/vnc.html?autoconnect=1&resize=remote
BNMP_COOKIE_TTL_SECONDS=240
ADMIN_PASSWORD=admin123
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

Tambem e possivel enviar a base pelo proprio painel: entre em `Administrador` e use `Enviar base JSON`. Para isso, a montagem de `/app/data` precisa estar gravavel e persistente no Coolify.

A senha administrativa padrao e `admin123`. Em producao, defina `ADMIN_PASSWORD` no Coolify e use essa senha ao entrar no modo `Administrador`.

Para deixar o dominio publico protegido, defina tambem `APP_BASIC_AUTH_USER` e `APP_BASIC_AUTH_PASSWORD`. Quando essas variaveis existem, o painel inteiro exige autenticacao HTTP Basic antes de abrir qualquer tela ou API, exceto `/api/health`.

## Cookie BNMP

Ao clicar em `Baixar PDF do mandado`, o painel verifica se existe uma sessao BNMP valida em memoria. Se nao existir, ele redireciona para:

```text
/bnmp/autenticar
```

Essa tela usa o sidecar `bnmp-browser`: o usuario resolve o captcha manualmente no Chromium remoto via noVNC, o painel aciona `http://bnmp-browser:7788/export`, o sidecar grava `bnmp_cookies.json` no volume compartilhado e o backend importa cookie + fingerprint para memoria. Depois o painel volta e baixa o PDF pendente. A sessao fica somente em memoria e o temporizador padrao considera 4 minutos para renovacao.

Variaveis relacionadas:

```text
BNMP_BROWSER_EXPORT_URL=http://bnmp-browser:7788/export
BNMP_BROWSER_EXPORT_TOKEN=gere-um-token-interno-longo
BNMP_REMOTE_BROWSER_URL=https://seu-dominio/vnc.html?autoconnect=1&resize=remote
BNMP_VNC_PASSWORD=gere-uma-senha-forte-para-o-novnc
BNMP_COOKIES_FILE=/app/data/bnmp_cookies.json
BNMP_COOKIE_TTL_SECONDS=240
```

O `BNMP_BROWSER_EXPORT_TOKEN` deve ter o mesmo valor no `painel-bnmp` e no `bnmp-browser`; ele impede que outro container acione a exportacao de cookies sem autorizacao. O `BNMP_VNC_PASSWORD` protege a tela remota do Chromium; o usuario vai informar essa senha no noVNC antes de resolver o captcha.

## Seguranca para VPS

Para publicar em `https://bnmp.pmto8bpm.com.br/`, use `.env.production.example` como base e troque todos os segredos antes do deploy.

Checklist recomendado:

- defina `APP_BASIC_AUTH_USER` e `APP_BASIC_AUTH_PASSWORD` para fechar o painel inteiro;
- defina `ADMIN_PASSWORD` diferente da senha padrao;
- defina `BNMP_BROWSER_EXPORT_TOKEN` com um valor longo e aleatorio;
- defina `BNMP_VNC_PASSWORD` com uma senha forte;
- mantenha a porta `7788` sem publicacao externa, como esta no `docker-compose.yml`;
- nao publique o noVNC sem HTTPS e sem senha;
- nao comite nem envie `data/bnmp_cookies.json` para o Git;
- deixe `BNMP_COOKIE_TTL_SECONDS` baixo, como `240`, para reduzir a janela da sessao BNMP.

Se o noVNC for servido pelo mesmo dominio, `BNMP_REMOTE_BROWSER_URL` pode apontar para:

```text
https://bnmp.pmto8bpm.com.br/vnc.html?autoconnect=1&resize=remote
```

Isso so funciona se o proxy da VPS/Coolify encaminhar essa rota para a porta `6080` do servico `bnmp-browser`. Caso contrario, use um subdominio protegido, por exemplo `https://bnmp-browser.pmto8bpm.com.br/vnc.html?autoconnect=1&resize=remote`, e configure esse valor em `BNMP_REMOTE_BROWSER_URL`.

O arquivo apontado por `BNMP_COOKIES_FILE` deve seguir um dos formatos aceitos:

```json
{
  "cookies": [
    {"name": "portalbnmp", "value": "...", "domain": "portalbnmp.pdpj.jus.br", "path": "/"}
  ],
  "fingerprint": "..."
}
```

ou:

```json
{
  "cookie": "...",
  "fingerprint": "..."
}
```

Enquanto o navegador remoto nao estiver configurado, a tela `/bnmp/autenticar` tambem permite informar o cookie manualmente.

Para obter o cookie, acesse `https://portalbnmp.cnj.jus.br/`, valide o Captcha, abra as ferramentas de desenvolvedor com `F12`, entre em `Application`/`Aplicação`, abra `Cookies`, selecione `https://portalbnmp.cnj.jus.br/` e copie o valor do item `portalbnmp`.

## Download de PDF

O botao `Baixar PDF do mandado` chama o backend local, que consulta:

```text
/bnmpportal/api/certidaos/relatorio/{id}/{idTipoPeca}
```

usando a sessao BNMP em memoria.
