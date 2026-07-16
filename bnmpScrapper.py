import requests
import csv
import pandas as pd



sessao = input("Informe o cookie de sessão: ")

cookies = {
    'portalbnmp': sessao,
}

headers = {
    'authority': 'portalbnmp.cnj.jus.br',
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'content-type': 'application/json',
    'cookie': 'portalbnmp=' + sessao,
    'origin': 'https://portalbnmp.cnj.jus.br',
    'referer': 'https://portalbnmp.cnj.jus.br/',
    'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
}

estado = 27

dados = []





json_data = {'buscaOrgaoRecursivo': False,'orgaoExpeditor': {}, 'idEstado': estado,}

response = requests.post('https://portalbnmp.cnj.jus.br/bnmpportal/api/pesquisa-pecas/csv', cookies=cookies, headers=headers, json=json_data,)
lines = response.text.splitlines()

reader = str(response.text)


dados.append(reader)

  

with open("dados.csv", "w", encoding="utf-16") as f:
    for bloco_texto in dados:
        f.write(bloco_texto)
