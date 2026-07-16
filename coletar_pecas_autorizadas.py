import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://portalbnmp.pdpj.jus.br"
ENDPOINT = "/bnmpportal/api/pesquisa-pecas/filter"

ID_ESTADO = 27
TAMANHO_PAGINA = 30
ARQUIVO_SAIDA = Path("pecas_autorizadas.json")
INTERVALO_REQUISICOES = 0.5


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
        domain="portalbnmp.pdpj.jus.br",
    )

    sessao.headers.update({
        "Accept": "application/json",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Fingerprint": "cc38874eb5ce6118ae44325f300572e3",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
        "Sec-Ch-Ua": '"Not-A.Brand";v="24", "Chromium";v="146"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
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
        HTTPAdapter(max_retries=retry),
    )

    return sessao


def buscar_pagina(
    sessao: requests.Session,
    pagina: int,
    tamanho: int,
    id_estado: int,
) -> dict[str, Any]:
    url = f"{BASE_URL}{ENDPOINT}"

    parametros = {
        "page": pagina,
        "size": tamanho,
        "sort": "",
    }

    corpo = {
        "buscaOrgaoRecursivo": False,
        "orgaoExpeditor": {},
        "idEstado": id_estado,
    }

    resposta = sessao.post(
        url,
        params=parametros,
        json=corpo,
        timeout=60,
    )

    if resposta.status_code in {401, 403}:
        raise CookieExpiradoError(
            "O cookie expirou ou a sessao nao possui autorizacao."
        )

    resposta.raise_for_status()

    content_type = resposta.headers.get("Content-Type", "").lower()

    if "application/json" not in content_type:
        raise RuntimeError(
            "A resposta nao veio em JSON. "
            f"Content-Type: {content_type}. "
            f"Inicio da resposta: {resposta.text[:300]}"
        )

    dados = resposta.json()

    if not isinstance(dados, dict):
        raise RuntimeError("A resposta JSON nao veio como objeto.")

    if "content" not in dados:
        raise RuntimeError(
            "A resposta JSON nao possui a chave 'content'. "
            f"Chaves recebidas: {list(dados.keys())}"
        )

    return dados


def salvar_resultado(
    dados_base: dict[str, Any],
    pecas: list[dict[str, Any]],
    id_estado: int,
) -> None:
    resultado = {
        **dados_base,
        "content": pecas,
        "numberOfElements": len(pecas),
        "idEstado": id_estado,
        "coletadoEm": datetime.now().isoformat(timespec="seconds"),
    }

    ARQUIVO_SAIDA.write_text(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    sessao = criar_sessao(
        solicitar_cookie()
    )

    print(
        f"Buscando estado {ID_ESTADO} com "
        f"{TAMANHO_PAGINA} registros por pagina..."
    )

    while True:
        try:
            primeira_pagina = buscar_pagina(
                sessao,
                pagina=0,
                tamanho=TAMANHO_PAGINA,
                id_estado=ID_ESTADO,
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

    total_paginas = int(primeira_pagina.get("totalPages") or 0)
    total_elementos = int(primeira_pagina.get("totalElements") or 0)
    pecas = list(primeira_pagina.get("content") or [])

    print(
        f"Total informado pelo portal: "
        f"{total_elementos} registros em {total_paginas} paginas."
    )

    salvar_resultado(
        primeira_pagina,
        pecas,
        ID_ESTADO,
    )

    for pagina in range(1, total_paginas):
        print(f"Buscando pagina {pagina + 1}/{total_paginas}...")

        while True:
            try:
                dados_pagina = buscar_pagina(
                    sessao,
                    pagina=pagina,
                    tamanho=TAMANHO_PAGINA,
                    id_estado=ID_ESTADO,
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
                    "mesma pagina..."
                )

        conteudo = dados_pagina.get("content") or []

        if not isinstance(conteudo, list):
            raise RuntimeError(
                f"A pagina {pagina} retornou 'content' invalido."
            )

        pecas.extend(conteudo)

        salvar_resultado(
            primeira_pagina,
            pecas,
            ID_ESTADO,
        )

        print(f"  Registros acumulados: {len(pecas)}")
        time.sleep(INTERVALO_REQUISICOES)

    salvar_resultado(
        primeira_pagina,
        pecas,
        ID_ESTADO,
    )

    print()
    print("Coleta finalizada.")
    print(f"Registros salvos: {len(pecas)}")
    print(f"Arquivo: {ARQUIVO_SAIDA.resolve()}")


if __name__ == "__main__":
    main()
