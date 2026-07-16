import csv
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BASE_URL = "https://portalbnmp.pdpj.jus.br/bnmpportal/api"

ARQUIVO_ENTRADA = Path("pecas_autorizadas.json")
ARQUIVO_ENTRADA_ALTERNATIVO = Path("BNMP") / "pecas_autorizadas.json"
ARQUIVO_CSV = Path("BNMP") /Path("mandados_processados.csv")
ARQUIVO_JSON = Path("BNMP") /Path("mandados_processados.json")

# Evita armazenar os PDFs, processando-os somente em memória.
SALVAR_PDFS = False
PASTA_PDFS = Path("pdfs")

# Intervalo entre os downloads para não sobrecarregar o serviço.
INTERVALO_REQUISICOES = 0


# ============================================================
# SESSÃO HTTP
# ============================================================

class CookieExpiradoError(RuntimeError):
    pass


def solicitar_cookie(
    mensagem: str = "Informe o valor do cookie portalbnmp: "
) -> str:
    while True:
        cookie = input(mensagem).strip()

        if cookie:
            return cookie

        print("Cookie vazio. Informe um cookie valido.")


def criar_sessao(cookie: str) -> requests.Session:
    sessao = requests.Session()

    sessao.cookies.set(
        "portalbnmp",
        cookie,
        domain="portalbnmp.pdpj.jus.br"
    )

    sessao.headers.update({
        "Accept": (
            "application/pdf, "
            "application/octet-stream, "
            "application/json, */*"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Origin": "https://portalbnmp.pdpj.jus.br",
        "Referer": "https://portalbnmp.pdpj.jus.br/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
    })

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"POST"}),
        raise_on_status=False,
    )

    sessao.mount(
        "https://",
        HTTPAdapter(max_retries=retry)
    )

    return sessao


# ============================================================
# DOWNLOAD DO PDF
# ============================================================

def baixar_pdf(
    sessao: requests.Session,
    id_peca: int,
    id_tipo_peca: int
) -> bytes:
    url = (
        f"{BASE_URL}/certidaos/relatorio/"
        f"{id_peca}/{id_tipo_peca}"
    )

    resposta = sessao.post(
        url,
        timeout=90
    )

    if resposta.status_code in {401, 403}:
        raise CookieExpiradoError(
            "O cookie expirou ou a sessão não possui autorização."
        )

    resposta.raise_for_status()

    content_type = resposta.headers.get(
        "Content-Type",
        ""
    ).lower()

    if not resposta.content.startswith(b"%PDF"):
        corpo = resposta.text[:500]

        raise RuntimeError(
            f"A peça {id_peca} não retornou um PDF. "
            f"Content-Type: {content_type}. "
            f"Resposta: {corpo}"
        )

    return resposta.content


# ============================================================
# EXTRAÇÃO DO TEXTO DO PDF
# ============================================================

def extrair_texto_pdf(pdf_bytes: bytes) -> str:
    leitor = PdfReader(io.BytesIO(pdf_bytes))

    paginas = []

    for pagina in leitor.pages:
        texto_pagina = pagina.extract_text() or ""
        paginas.append(texto_pagina)

    texto = "\n".join(paginas)

    # Normaliza espaços sem eliminar as quebras de linha,
    # pois elas ajudam a identificar o início e o fim dos campos.
    linhas = []

    for linha in texto.splitlines():
        linha = linha.replace("\u00A0", " ")
        linha = re.sub(r"[ \t]+", " ", linha).strip()

        if linha:
            linhas.append(linha)

    return "\n".join(linhas)


# ============================================================
# FUNÇÕES GENÉRICAS DE EXTRAÇÃO
# ============================================================

def extrair_campo(
    texto: str,
    padrao: str
) -> str | None:
    resultado = re.search(
        padrao,
        texto,
        flags=re.IGNORECASE | re.MULTILINE
    )

    if not resultado:
        return None

    valor = resultado.group(1)
    valor = re.sub(r"\s+", " ", valor)

    return valor.strip(" ,;")


MARCADORES_VAZAMENTO_DOCUMENTO = [
    r"\|\s*Identificação biométrica\b",
    r"\|\s*Endereços?\b",
    r"\|\s*Informações Processuais\b",
    r"\|\s*Teor do Documento\b",
    r"\|\s*Síntese da Decisão\b",
    r"\|\s*Prazo Mínimo da Internação\b",
    r"\|\s*Regime Prisional\b",
    r"\|\s*Data\s*(?:\||$)",
    r"\|\s*Tribunal(?: de Justiça|Órgão)\b",
    r"\|\s*N[º°o]\s*(?:do\s*)?Mandado\b",
    r"\|\s*Documento assinado\b",
    r"\|\s*Para confirmar a autenticidade\b",
    r"\|\s*Documento gerado em\b",
    r"\|\s*Documento criado em\b",
    r"\bData\s+Tribunal(?: de Justiça|Órgão)\b",
    r"\bTribunal de Justiça\b",
    r"\bTribunalÓrgão do Judiciário\b",
    r"\bN[º°o]\s*(?:do\s*)?Mandado\b",
    r"\bDocumento assinado digitalmente\b",
    r"\bPara confirmar a autenticidade\b",
    r"\bDocumento gerado em\b",
    r"\bDocumento criado em\b",
    r"\be-mail\s*:",
]


def remover_vazamento_documento(
    valor: str | None
) -> str | None:
    if not valor:
        return None

    limite = len(valor)

    for marcador in MARCADORES_VAZAMENTO_DOCUMENTO:
        resultado = re.search(
            marcador,
            valor,
            flags=re.IGNORECASE
        )

        if resultado:
            limite = min(
                limite,
                resultado.start()
            )

    valor = valor[:limite]
    valor = re.sub(r"\s+", " ", valor)

    return valor.strip(" |,;") or None


def extrair_secao(
    texto: str,
    titulo: str,
    proximos_titulos: list[str]
) -> str | None:
    """
    Extrai o conteúdo iniciado por um título até que apareça
    outro título conhecido ou o final do documento.
    """

    finais = "|".join(
        re.escape(item)
        for item in proximos_titulos
    )

    padrao = (
        rf"(?im)^\s*{re.escape(titulo)}\s*:?\s*"
        rf"(.*?)"
        rf"(?=^\s*(?:{finais})\s*:?\s*|\Z)"
    )

    resultado = re.search(
        padrao,
        texto,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL
    )

    if not resultado:
        return None

    linhas = []

    for linha in resultado.group(1).splitlines():
        linha = re.sub(r"\s+", " ", linha).strip(" ,;")

        if linha:
            linhas.append(linha)

    if not linhas:
        return None

    return remover_vazamento_documento(
        " | ".join(linhas)
    )


# ============================================================
# CPF
# ============================================================

def formatar_cpf(cpf: str | None) -> str | None:
    if not cpf:
        return None

    numeros = re.sub(r"\D", "", cpf)

    if len(numeros) != 11:
        return cpf.strip()

    return (
        f"{numeros[0:3]}."
        f"{numeros[3:6]}."
        f"{numeros[6:9]}-"
        f"{numeros[9:11]}"
    )


def extrair_cpf(texto: str) -> str | None:
    cpf = extrair_campo(
        texto,
        r"CPF\s*:\s*([0-9]{3}\.?[0-9]{3}\.?[0-9]{3}-?[0-9]{2})"
    )

    return formatar_cpf(cpf)


# ============================================================
# ENDEREÇO DA PESSOA
# ============================================================

def normalizar_endereco(endereco: str) -> str:
    endereco = endereco.replace("\u00A0", " ")
    endereco = re.sub(r"\s+", " ", endereco)
    endereco = re.sub(r"\s+,", ",", endereco)
    endereco = re.sub(r",\s*,+", ",", endereco)
    endereco = re.sub(r"\s*;\s*", "; ", endereco)

    return endereco.strip(" ,;")


def organizar_enderecos(
    linhas: list[str]
) -> list[str]:
    if not linhas:
        return []

    quantidade_logradouros = sum(
        1
        for linha in linhas
        if linha.lower().startswith("logradouro:")
    )

    # Formato estruturado:
    #
    # Logradouro: RUA...
    # Bairro: ...
    # Cidade: ...
    # UF: ...
    # CEP: ...
    if quantidade_logradouros > 0:
        enderecos = []
        endereco_atual = []

        for linha in linhas:
            comeca_logradouro = (
                linha.lower().startswith("logradouro:")
            )

            if comeca_logradouro and endereco_atual:
                endereco = normalizar_endereco(
                    " ".join(endereco_atual)
                )

                if endereco:
                    enderecos.append(endereco)

                endereco_atual = []

            endereco_atual.append(linha)

        if endereco_atual:
            endereco = normalizar_endereco(
                " ".join(endereco_atual)
            )

            if endereco:
                enderecos.append(endereco)

        return enderecos

    # Formato em que cada linha já representa um endereço:
    #
    # RUA HUMBERTO DE CAMPOS, 104, ..., CEP ..., RS
    linhas_completas = [
        linha
        for linha in linhas
        if (
            "cep" in linha.lower()
            or re.search(r",\s*[A-Z]{2}\s*$", linha)
        )
    ]

    if (
        len(linhas) > 1
        and len(linhas_completas) == len(linhas)
    ):
        return [
            normalizar_endereco(linha)
            for linha in linhas
            if normalizar_endereco(linha)
        ]

    # Caso o endereço tenha sido apenas quebrado visualmente
    # em várias linhas no PDF.
    endereco_unico = normalizar_endereco(
        " ".join(linhas)
    )

    return [endereco_unico] if endereco_unico else []


def extrair_enderecos_pessoa(
    texto: str
) -> list[str]:
    inicio = re.search(
        r"(?im)^\s*Endereços?\s*(?::\s*(.*)|$)",
        texto
    )

    if not inicio:
        return []

    primeira_linha = (inicio.group(1) or "").strip()
    restante = texto[inicio.end():]

    proximos_campos = [
        r"CPF",
        r"RG",
        r"RJI",
        r"Telefones?",
        r"Informações Processuais",
        r"Teor do Documento",
        r"Síntese da decisão",
        r"N[º°o]\s*(?:do\s*)?processo",
        r"Órgão Judicial",
        r"Espécie de prisão",
        r"Local de Ocorrência do Delito",
        r"Regime Prisional",
        r"Tipificação Penal",
        r"Pena restante",
        r"Lavrado por",
        r"Documento assinado",
        r"Para confirmar a autenticidade",
        r"Documento (?:gerado|criado) em",
        r"Tribunal de Justiça",
        r"Advertências",
    ]

    padrao_fim = (
        r"(?im)^\s*(?:"
        + "|".join(proximos_campos)
        + r")\s*:?"
    )

    fim = re.search(
        padrao_fim,
        restante
    )

    if fim:
        bloco = restante[:fim.start()]
    else:
        bloco = restante

    linhas = []

    if primeira_linha:
        linhas.append(primeira_linha)

    for linha in bloco.splitlines():
        linha = re.sub(
            r"\s+",
            " ",
            linha
        ).strip(" ,;")

        if linha:
            linhas.append(linha)

    enderecos = organizar_enderecos(linhas)
    enderecos_limpos = []

    for endereco in enderecos:
        endereco_limpo = remover_vazamento_documento(endereco)

        if endereco_limpo:
            enderecos_limpos.append(endereco_limpo)

    return enderecos_limpos


# ============================================================
# LOCAL DA OCORRÊNCIA DO DELITO
# ============================================================

def extrair_local_ocorrencia(
    texto: str
) -> str | None:
    resultado = re.search(
        (
            r"(?im)^\s*Local de Ocorrência do Delito\s*:\s*"
            r"(.*?)"
            r"(?=^\s*(?:"
            r"Regime Prisional|"
            r"Tipificação Penal|"
            r"Pena restante|"
            r"Telefones?|"
            r"Lavrado por|"
            r"Regime Prisional|"
            r"Identificação biométrica|"
            r"Endereços?|"
            r"Tribunal de Justiça|"
            r"Documento assinado|"
            r"Observações"
            r")\s*:?\s*|\Z)"
        ),
        texto,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL
    )

    if not resultado:
        return None

    valor = re.sub(
        r"\s+",
        " ",
        resultado.group(1)
    )

    return valor.strip(" ,;")


# ============================================================
# TIPIFICAÇÃO PENAL
# ============================================================

def extrair_tipificacao_penal(
    texto: str
) -> str | None:
    tipificacao = extrair_secao(
        texto,
        "Tipificação Penal",
        [
            "Pena restante",
            "Prazo Mínimo da Internação",
            "Informações Processuais",
            "Teor do Documento",
            "Identificação biométrica",
            "Endereços",
            "Regime Prisional",
            "Telefones",
            "Lavrado por",
            "Observações",
            "Síntese da decisão",
            "Assinado por",
            "Tribunal de Justiça",
        ]
    )

    return tipificacao


# ============================================================
# PENA RESTANTE
# ============================================================

def extrair_pena_restante(
    texto: str
) -> str | None:
    resultado = re.search(
        (
            r"(?im)^\s*Pena restante\s*:\s*"
            r"(.*?)"
            r"(?=^\s*(?:"
            r"Telefones?|"
            r"Lavrado por|"
            r"Regime Prisional|"
            r"Identificação biométrica|"
            r"Endereços?|"
            r"Tribunal de Justiça|"
            r"Documento assinado|"
            r"Observações|"
            r"Síntese da decisão|"
            r"Assinado por"
            r")\s*:?\s*|\Z)"
        ),
        texto,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL
    )

    if not resultado:
        return None

    valor = re.sub(
        r"\s+",
        " ",
        resultado.group(1)
    )

    return remover_vazamento_documento(
        valor.strip(" ,;")
    )


# ============================================================
# EXTRAÇÃO DOS DADOS DO PDF
# ============================================================

def extrair_dados_pdf(
    texto: str
) -> dict[str, Any]:
    data_validade = extrair_campo(
        texto,
        (
            r"Data de validade\s*:\s*"
            r"([0-9]{2}[./][0-9]{2}[./][0-9]{4})"
        )
    )

    enderecos = extrair_enderecos_pessoa(texto)

    return {
        "dataValidade": data_validade,
        "cpf": extrair_cpf(texto),

        # Mantém todos os endereços separados por " | ".
        "enderecosPessoa": " | ".join(enderecos),

        # Campo independente do endereço da pessoa.
        "localOcorrenciaDelito": extrair_local_ocorrencia(
            texto
        ),

        "tipificacaoPenal": extrair_tipificacao_penal(
            texto
        ),

        "penaRestante": extrair_pena_restante(
            texto
        ),
    }


# ============================================================
# ARQUIVO DE ENTRADA
# ============================================================

def carregar_pecas() -> list[dict[str, Any]]:
    arquivo_entrada = ARQUIVO_ENTRADA

    if not arquivo_entrada.exists():
        arquivo_entrada = ARQUIVO_ENTRADA_ALTERNATIVO

    if not arquivo_entrada.exists():
        raise FileNotFoundError(
            f"Os arquivos {ARQUIVO_ENTRADA} e "
            f"{ARQUIVO_ENTRADA_ALTERNATIVO} não foram encontrados."
        )

    conteudo = arquivo_entrada.read_text(
        encoding="utf-8"
    )

    dados = json.loads(conteudo)

    # Permite receber diretamente uma lista ou uma resposta
    # completa do endpoint /filter contendo "content".
    if isinstance(dados, dict):
        pecas = dados.get("content", [])
    elif isinstance(dados, list):
        pecas = dados
    else:
        raise ValueError(
            "O JSON deve conter uma lista ou um objeto "
            "com a propriedade 'content'."
        )

    if not isinstance(pecas, list):
        raise ValueError(
            "A propriedade 'content' não contém uma lista."
        )

    return pecas


def eh_mandado_prisao(
    peca: dict[str, Any]
) -> bool:
    descricao = str(
        peca.get("descricaoPeca") or ""
    ).strip().lower()

    # Inclui, por exemplo:
    # Mandado de Prisão
    # Mandado de Prisão Recaptura
    return descricao.startswith("mandado de prisão")


# ============================================================
# NOMES DE ARQUIVOS
# ============================================================

def sanitizar_nome_arquivo(
    nome: str
) -> str:
    nome = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        nome
    )

    nome = re.sub(r"\s+", "_", nome)

    return nome.strip("._")


def salvar_pdf(
    pdf_bytes: bytes,
    peca: dict[str, Any]
) -> Path:
    PASTA_PDFS.mkdir(
        parents=True,
        exist_ok=True
    )

    numero = (
        peca.get("numeroPecaFormatado")
        or peca.get("numeroPeca")
        or str(peca.get("id"))
    )

    nome = sanitizar_nome_arquivo(
        str(numero)
    )

    caminho = PASTA_PDFS / f"{nome}.pdf"
    caminho.write_bytes(pdf_bytes)

    return caminho


# ============================================================
# PROCESSAMENTO
# ============================================================

def processar_peca(
    sessao: requests.Session,
    peca: dict[str, Any]
) -> dict[str, Any]:
    id_peca = peca.get("id")
    id_tipo_peca = peca.get("idTipoPeca")

    if id_peca is None:
        raise ValueError("Registro sem o campo 'id'.")

    if id_tipo_peca is None:
        raise ValueError(
            f"Peça {id_peca} sem o campo 'idTipoPeca'."
        )

    pdf_bytes = baixar_pdf(
        sessao,
        int(id_peca),
        int(id_tipo_peca)
    )

    if SALVAR_PDFS:
        salvar_pdf(
            pdf_bytes,
            peca
        )

    texto = extrair_texto_pdf(
        pdf_bytes
    )

    dados_pdf = extrair_dados_pdf(
        texto
    )

    return {
        "id": id_peca,
        "idTipoPeca": id_tipo_peca,
        "numeroPeca": peca.get(
            "numeroPecaFormatado"
        ),
        "numeroProcesso": peca.get(
            "numeroProcesso"
        ),
        "nomePessoa": peca.get(
            "nomePessoa"
        ),
        "alcunha": peca.get(
            "alcunha"
        ),
        "nomeMae": peca.get(
            "nomeMae"
        ),
        "nomePai": peca.get(
            "nomePai"
        ),
        "dataNascimento": peca.get(
            "dataNascimentoFormatada"
        ),
        "sexo": peca.get(
            "descricaoSexo"
        ),
        "profissao": peca.get(
            "descricaoProfissao"
        ),
        "status": peca.get(
            "descricaoStatus"
        ),
        "dataExpedicao": peca.get(
            "dataExpedicaoFormatada"
        ),
        "orgaoExpedidor": peca.get(
            "nomeOrgao"
        ),
        "descricaoPeca": peca.get(
            "descricaoPeca"
        ),
        **dados_pdf,
    }


# ============================================================
# GRAVAÇÃO DOS RESULTADOS
# ============================================================

def salvar_resultados_json(
    resultados: list[dict[str, Any]]
) -> None:
    ARQUIVO_JSON.write_text(
        json.dumps(
            resultados,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def carregar_resultados_existentes() -> list[dict[str, Any]]:
    if not ARQUIVO_JSON.exists():
        return []

    dados = json.loads(
        ARQUIVO_JSON.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(dados, list):
        raise ValueError(
            f"O arquivo {ARQUIVO_JSON} deve conter uma lista."
        )

    for resultado in dados:
        if not isinstance(resultado, dict):
            continue

        for campo in [
            "enderecosPessoa",
            "tipificacaoPenal",
            "penaRestante",
        ]:
            valor = resultado.get(campo)

            if isinstance(valor, str):
                resultado[campo] = remover_vazamento_documento(valor)

    return dados


def endereco_parece_texto_decisao(
    resultado: dict[str, Any]
) -> bool:
    endereco = resultado.get("enderecosPessoa")

    if not isinstance(endereco, str):
        return False

    texto = endereco.casefold()

    marcadores_decisao = [
        "expeça-se mandado",
        "expeca-se mandado",
        "autoridade policial",
        "lei de execução penal",
        "lei de execucao penal",
        "ministério público",
        "ministerio publico",
        "juiz de direito",
        "defensoria pública",
        "defensoria publica",
        "intime-se",
        "cumpra-se",
    ]

    return any(
        marcador in texto
        for marcador in marcadores_decisao
    )


def salvar_resultados_csv(
    resultados: list[dict[str, Any]]
) -> None:
    if not resultados:
        return

    campos = [
        "id",
        "idTipoPeca",
        "numeroPeca",
        "numeroProcesso",
        "nomePessoa",
        "alcunha",
        "nomeMae",
        "nomePai",
        "dataNascimento",
        "cpf",
        "sexo",
        "profissao",
        "status",
        "descricaoPeca",
        "dataExpedicao",
        "dataValidade",
        "orgaoExpedidor",
        "enderecosPessoa",
        "localOcorrenciaDelito",
        "tipificacaoPenal",
        "penaRestante",
    ]

    with ARQUIVO_CSV.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos,
            extrasaction="ignore"
        )

        escritor.writeheader()
        escritor.writerows(resultados)


# ============================================================
# EXECUÇÃO PRINCIPAL
# ============================================================

def main() -> None:
    sessao = criar_sessao(
        solicitar_cookie()
    )
    todas_pecas = carregar_pecas()

    pecas = [
        peca
        for peca in todas_pecas
        if eh_mandado_prisao(peca)
    ]

    print(
        f"{len(todas_pecas)} registros encontrados no JSON."
    )

    print(
        f"{len(pecas)} registros identificados como "
        f"mandados de prisão."
    )

    resultados_carregados = carregar_resultados_existentes()
    resultados_reprocessar = [
        resultado
        for resultado in resultados_carregados
        if endereco_parece_texto_decisao(resultado)
    ]
    resultados = [
        resultado
        for resultado in resultados_carregados
        if not endereco_parece_texto_decisao(resultado)
    ]
    ids_processados = {
        resultado.get("id")
        for resultado in resultados
        if resultado.get("id") is not None
    }

    if resultados_carregados:
        print(
            f"{len(resultados_carregados)} registros ja estavam salvos em "
            f"{ARQUIVO_JSON}."
        )

        if resultados_reprocessar:
            print(
                f"{len(resultados_reprocessar)} registros salvos parecem "
                "ter endereco extraido de texto da decisao e serao "
                "processados novamente."
            )

        salvar_resultados_json(resultados)
        salvar_resultados_csv(resultados)

    erros = []

    for indice, peca in enumerate(
        pecas,
        start=1
    ):
        id_peca = peca.get("id")
        nome = peca.get("nomePessoa")
        numero = peca.get("numeroPecaFormatado")

        if id_peca in ids_processados:
            print(
                f"[{indice}/{len(pecas)}] "
                f"Pulando {id_peca} - {numero} - {nome}"
            )

            continue

        print(
            f"[{indice}/{len(pecas)}] "
            f"Processando {id_peca} - {numero} - {nome}"
        )

        while True:
            try:
                resultado = processar_peca(
                    sessao,
                    peca
                )

                resultados.append(resultado)
                ids_processados.add(id_peca)

                print(
                    "  Endereço:",
                    resultado.get("enderecosPessoa")
                    or "não localizado"
                )

                print(
                    "  Tipificação:",
                    resultado.get("tipificacaoPenal")
                    or "não localizada"
                )

                break

            except CookieExpiradoError as erro:
                print(f"  {erro}")
                print(
                    "  Atualize o captcha no portal, copie um novo "
                    "cookie e cole abaixo."
                )

                sessao = criar_sessao(
                    solicitar_cookie(
                        "Informe o novo valor do cookie portalbnmp: "
                    )
                )

                print(
                    "  Cookie atualizado. Tentando novamente a "
                    "mesma peca..."
                )

            except Exception as erro:
                mensagem = str(erro)

                print(
                    f"  Erro: {mensagem}"
                )

                erros.append({
                    "id": id_peca,
                    "numeroPeca": numero,
                    "nomePessoa": nome,
                    "erro": mensagem,
                })

                break

        # Salva o progresso após cada registro.
        salvar_resultados_json(resultados)
        salvar_resultados_csv(resultados)

        time.sleep(INTERVALO_REQUISICOES)

    if erros:
        Path("erros_processamento.json").write_text(
            json.dumps(
                erros,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    print()
    print("Processamento finalizado.")
    print(f"Registros processados: {len(resultados)}")
    print(f"Erros: {len(erros)}")
    print(f"CSV: {ARQUIVO_CSV.resolve()}")
    print(f"JSON: {ARQUIVO_JSON.resolve()}")


if __name__ == "__main__":
    main()
