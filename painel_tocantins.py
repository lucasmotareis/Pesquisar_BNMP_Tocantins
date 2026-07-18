import argparse
import base64
import hmac
import json
import os
import re
import time
import unicodedata
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BNMP_DATA_DIR", ROOT)).resolve()
HTML_FILE = ROOT / "painel_tocantins.html"


def unique_paths(paths):
    seen = set()
    result = []

    for path in paths:
        resolved = Path(path).resolve()

        if resolved in seen:
            continue

        seen.add(resolved)
        result.append(resolved)

    return result


DATA_FILES = unique_paths(
    [
        *(
            [Path(os.environ["BNMP_DATA_FILE"])]
            if os.environ.get("BNMP_DATA_FILE")
            else []
        ),
        DATA_DIR / "mandados_processados.json",
        DATA_DIR / "pecas_autorizadas.json",
        ROOT / "mandados_processados.json",
        ROOT / "pecas_autorizadas.json",
    ]
)
BNMP_PORTAL_URL = os.environ.get(
    "BNMP_PORTAL_URL",
    "https://portalbnmp.pdpj.jus.br/#/pesquisa-peca",
)
BNMP_API = "https://portalbnmp.pdpj.jus.br/bnmpportal/api"
BNMP_AUTH_HTML_FILE = ROOT / "bnmp_auth.html"
BNMP_REMOTE_BROWSER_URL = os.environ.get("BNMP_REMOTE_BROWSER_URL", "")
BNMP_BROWSER_EXPORT_URL = os.environ.get("BNMP_BROWSER_EXPORT_URL", "")
BNMP_BROWSER_EXPORT_TOKEN = os.environ.get("BNMP_BROWSER_EXPORT_TOKEN", "")
BNMP_COOKIES_FILE = os.environ.get("BNMP_COOKIES_FILE", "")
COOKIE_TTL_SECONDS = int(os.environ.get("BNMP_COOKIE_TTL_SECONDS", str(4 * 60)))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
APP_BASIC_AUTH_USER = os.environ.get("APP_BASIC_AUTH_USER", "").strip()
APP_BASIC_AUTH_PASSWORD = os.environ.get("APP_BASIC_AUTH_PASSWORD", "")
BNMP_ENABLE_HSTS = os.environ.get("BNMP_ENABLE_HSTS", "").lower() in {
    "1",
    "true",
    "yes",
    "sim",
}

AUTH = {
    "cookie": "",
    "fingerprint": "",
    "expires_at": 0.0,
    "source": "",
    "authenticated_at": 0.0,
}
RECORD_CACHE = {
    "key": None,
    "data": None,
}


def area(id_, crp, unidade, sede, cidades, aliases=None, exclusions=None):
    return {
        "id": id_,
        "crp": crp,
        "unidade": unidade,
        "sede": sede,
        "cidade": sede,
        "cidades": cidades,
        "aliases": aliases or [],
        "exclusions": exclusions or [],
    }


TERRITORIO = [
    area("cpc-1bpm", "CPC", "1º BPM", "Palmas", ["Palmas"]),
    area(
        "cpc-6bpm",
        "CPC",
        "6º BPM",
        "Palmas",
        ["Palmas"],
        ["Taquaralto", "Região Sul de Palmas"],
    ),
    area(
        "cpc-13bpm",
        "CPC",
        "13º BPM",
        "Taquaruçu",
        [
            "Taquaruçu",
            "Aparecida do Rio Negro",
            "Lagoa do Tocantins",
            "Lizarda",
            "Mateiros",
            "Novo Acordo",
            "Pindorama do Tocantins",
            "Ponte Alta do Tocantins",
            "Santa Tereza do Tocantins",
            "São Félix do Tocantins",
        ],
    ),
    area("cpc-1cipm", "CPC", "1ª CIPM", "Luzimangues", ["Luzimangues"]),
    area(
        "crp1-3bpm",
        "CRP-1",
        "3º BPM",
        "Pedro Afonso",
        [
            "Bom Jesus do Tocantins",
            "Centenário",
            "Itacajá",
            "Itapiratins",
            "Pedro Afonso",
            "Recursolândia",
            "Rio Sono",
            "Santa Maria do Tocantins",
            "Tupirama",
        ],
    ),
    area(
        "crp1-5bpm",
        "CRP-1",
        "5º BPM",
        "Porto Nacional",
        [
            "Brejinho de Nazaré",
            "Ipueiras",
            "Monte do Carmo",
            "Porto Nacional",
            "Santa Rosa do Tocantins",
            "Silvanópolis",
        ],
    ),
    area(
        "crp1-7bpm",
        "CRP-1",
        "7º BPM",
        "Guaraí",
        [
            "Colmeia",
            "Couto Magalhães",
            "Goianorte",
            "Guaraí",
            "Itaporã do Tocantins",
            "Pequizeiro",
            "Presidente Kennedy",
            "Tabocão",
            "Tupiratins",
        ],
    ),
    area(
        "crp1-8bpm",
        "CRP-1",
        "8º BPM",
        "Paraíso do Tocantins",
        [
            "Abreulândia",
            "Araguacema",
            "Barrolândia",
            "Caseara",
            "Chapada de Areia",
            "Divinópolis do Tocantins",
            "Dois Irmãos do Tocantins",
            "Marianópolis do Tocantins",
            "Monte Santo do Tocantins",
            "Paraíso do Tocantins",
            "Pugmil",
        ],
        ["Paraíso", "Paraiso"],
    ),
    area(
        "crp1-4cipm",
        "CRP-1",
        "4ª CIPM",
        "Lagoa da Confusão",
        [
            "Cristalândia",
            "Fátima",
            "Lagoa da Confusão",
            "Nova Rosalândia",
            "Oliveira de Fátima",
            "Pium",
            "Santa Rita do Tocantins",
        ],
    ),
    area(
        "crp1-16bpm",
        "CRP-1",
        "16º BPM",
        "Miracema do Tocantins",
        [
            "Lajeado",
            "Miracema do Tocantins",
            "Miranorte",
            "Rio dos Bois",
            "Tocantínia",
        ],
        ["Miracema"],
    ),
    area("crp2-2bpm", "CRP-2", "2º BPM", "Araguaína", ["Araguaína"]),
    area(
        "crp2-9bpm",
        "CRP-2",
        "9º BPM",
        "Araguatins",
        [
            "Araguatins",
            "Augustinópolis",
            "Axixá do Tocantins",
            "Buriti do Tocantins",
            "Carrasco Bonito",
            "Esperantina",
            "Itaguatins",
            "Maurilândia do Tocantins",
            "Praia Norte",
            "Sampaio",
            "São Bento do Tocantins",
            "São Miguel do Tocantins",
            "São Sebastião do Tocantins",
            "Sítio Novo do Tocantins",
        ],
    ),
    area(
        "crp2-14bpm",
        "CRP-2",
        "14º BPM",
        "Colinas do Tocantins",
        [
            "Arapoema",
            "Bandeirantes do Tocantins",
            "Bernardo Sayão",
            "Brasilândia do Tocantins",
            "Colinas do Tocantins",
            "Juarina",
            "Nova Olinda",
            "Palmeirante",
            "Pau D’Arco",
        ],
        ["Colinas", "Pau Darco"],
    ),
    area(
        "crp2-2cipm",
        "CRP-2",
        "2ª CIPM",
        "Xambioá",
        [
            "Aragominas",
            "Araguanã",
            "Carmolândia",
            "Darcinópolis",
            "Muricilândia",
            "Piraquê",
            "Santa Fé do Araguaia",
            "Wanderlândia",
            "Xambioá",
        ],
    ),
    area(
        "crp2-3cipm",
        "CRP-2",
        "3ª CIPM",
        "Goiatins",
        ["Babaçulândia", "Barra do Ouro", "Campos Lindos", "Filadélfia", "Goiatins"],
    ),
    area(
        "crp2-15bpm",
        "CRP-2",
        "15º BPM",
        "Tocantinópolis",
        [
            "Aguiarnópolis",
            "Ananás",
            "Angico",
            "Cachoeirinha",
            "Luzinópolis",
            "Nazaré",
            "Palmeiras do Tocantins",
            "Riachinho",
            "Santa Terezinha do Tocantins",
            "Tocantinópolis",
        ],
        exclusions=["Brejinho de Nazaré"],
    ),
    area(
        "crp3-4bpm",
        "CRP-3",
        "4º BPM",
        "Gurupi",
        [
            "Aliança do Tocantins",
            "Cariri do Tocantins",
            "Crixás do Tocantins",
            "Dueré",
            "Formoso do Araguaia",
            "Gurupi",
            "Peixe",
            "Sucupira",
        ],
    ),
    area(
        "crp3-10bpm",
        "CRP-3",
        "10º BPM",
        "Arraias",
        [
            "Arraias",
            "Combinado",
            "Conceição do Tocantins",
            "Novo Alegre",
            "Taipas do Tocantins",
        ],
    ),
    area(
        "crp3-11bpm",
        "CRP-3",
        "11º BPM",
        "Dianópolis",
        [
            "Almas",
            "Chapada da Natividade",
            "Dianópolis",
            "Natividade",
            "Porto Alegre do Tocantins",
            "Rio da Conceição",
        ],
        exclusions=["São Valério", "São Valério da Natividade"],
    ),
    area(
        "crp3-12bpm",
        "CRP-3",
        "12º BPM",
        "Taguatinga",
        [
            "Aurora do Tocantins",
            "Lavandeira",
            "Novo Jardim",
            "Ponte Alta do Bom Jesus",
            "Taguatinga",
        ],
    ),
    area(
        "crp3-7cipm",
        "CRP-3",
        "7ª CIPM",
        "Alvorada",
        ["Alvorada", "Araguaçu", "Figueirópolis", "Sandolândia", "Talismã"],
    ),
    area(
        "crp3-8cipm",
        "CRP-3",
        "8ª CIPM",
        "Palmeirópolis",
        [
            "Jaú do Tocantins",
            "Palmeirópolis",
            "Paraná",
            "São Salvador do Tocantins",
            "São Valério",
        ],
        ["São Valério da Natividade"],
    ),
]


def repair_text(value):
    if value is None:
        return ""

    if not isinstance(value, str):
        return value

    text = value.strip()

    if any(marker in text for marker in ("Ã", "Â", "â")):
        try:
            text = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass

    return text


DOCUMENT_LEAK_MARKERS = [
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
    r"\|\s*Documento (?:gerado|criado) em\b",
    r"\bData\s+Tribunal(?: de Justiça|Órgão)\b",
    r"\bTribunal de Justiça\b",
    r"\bTribunalÓrgão do Judiciário\b",
    r"\bN[º°o]\s*(?:do\s*)?Mandado\b",
    r"\bDocumento assinado digitalmente\b",
    r"\bPara confirmar a autenticidade\b",
    r"\bDocumento (?:gerado|criado) em\b",
    r"\be-mail\s*:",
]


def clean_document_leaks(value):
    text = repair_text(value)

    if not isinstance(text, str) or not text:
        return ""

    limit = len(text)

    for marker in DOCUMENT_LEAK_MARKERS:
        match = re.search(marker, text, flags=re.IGNORECASE)

        if match:
            limit = min(limit, match.start())

    text = text[:limit]
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\bData$", "", text, flags=re.IGNORECASE)

    return text.strip(" |,;")


def pick(record, *keys):
    for key in keys:
        value = record.get(key)

        if value not in (None, ""):
            return repair_text(value)

    return ""


def format_date(value):
    value = repair_text(value)

    if not value:
        return ""

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        year, month, day = value.split("-")
        return f"{day}/{month}/{year}"

    if re.fullmatch(r"\d{2}[.]\d{2}[.]\d{4}", value):
        return value.replace(".", "/")

    return value


def iso_date(value):
    value = format_date(value)

    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", value or "")
    if not match:
        return ""

    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def norm_text(value):
    value = repair_text(value)
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).upper()
    return re.sub(r"\s+", " ", value).strip()


def has_alias(text, aliases):
    return has_alias_norm(norm_text(text), [norm_text(alias) for alias in aliases])


def has_alias_norm(source_norm, alias_norms):
    source = f" {source_norm} "

    for term in alias_norms:

        if term and f" {term} " in source:
            return True

    return False


def unit_terms(unit):
    terms = [
        unit.get("sede", ""),
        unit.get("cidade", ""),
        *unit.get("cidades", []),
        *unit.get("aliases", []),
    ]
    deduped = []

    for term in terms:
        if term and term not in deduped:
            deduped.append(term)

    return deduped


def unit_term_norms(unit):
    if "_termNorms" not in unit:
        unit["_termNorms"] = [norm_text(term) for term in unit_terms(unit)]

    return unit["_termNorms"]


def unit_exclusion_norms(unit):
    if "_exclusionNorms" not in unit:
        unit["_exclusionNorms"] = [
            norm_text(term) for term in unit.get("exclusions", [])
        ]

    return unit["_exclusionNorms"]


def has_territory(text, unit):
    return has_territory_norm(norm_text(text), unit)


def has_territory_norm(source_norm, unit):
    if has_alias_norm(source_norm, unit_exclusion_norms(unit)):
        return False

    return has_alias_norm(source_norm, unit_term_norms(unit))


def active_units(filters):
    unit_id = filters.get("unit", "")
    crp = filters.get("crp", "")

    if unit_id:
        return [unit for unit in TERRITORIO if unit["id"] == unit_id]

    if crp:
        return [unit for unit in TERRITORIO if unit["crp"] == crp]

    return []


def public_unit(unit):
    return {
        key: value
        for key, value in unit.items()
        if not key.startswith("_")
    }


def match_territory(record, filters):
    units = active_units(filters)

    if not units:
        return {
            "matches": True,
            "orgao": True,
            "endereco": False,
            "score": 0,
            "units": [],
            "all": True,
        }

    matches = []
    orgao = False
    endereco = False
    orgao_source = record.get("_normOrgao") or norm_text(
        record.get("orgaoExpedidor", "")
    )
    endereco_source = record.get("_normEndereco") or norm_text(
        record.get("enderecosPessoa", "")
    )

    for unit in units:
        orgao_match = has_territory_norm(orgao_source, unit)
        endereco_match = has_territory_norm(endereco_source, unit)

        if orgao_match or endereco_match:
            found = {
                **public_unit(unit),
                "orgao": orgao_match,
                "endereco": endereco_match,
            }
            matches.append(found)
            orgao = orgao or orgao_match
            endereco = endereco or endereco_match

    if filters.get("origin") == "orgao" and not orgao:
        return {"matches": False}

    if filters.get("origin") == "endereco" and not endereco:
        return {"matches": False}

    return {
        "matches": bool(matches),
        "orgao": orgao,
        "endereco": endereco,
        "score": 0 if orgao else 1,
        "units": matches,
    }


def infer_territory(record):
    orgao_source = record.get("_normOrgao") or norm_text(
        record.get("orgaoExpedidor", "")
    )
    endereco_source = record.get("_normEndereco") or norm_text(
        record.get("enderecosPessoa", "")
    )

    for unit in TERRITORIO:
        if has_territory_norm(orgao_source, unit) or has_territory_norm(
            endereco_source, unit
        ):
            return unit

    return None


def record_search_text(record):
    return norm_text(
        " ".join(
            str(record.get(key) or "")
            for key in (
                "nomePessoa",
                "cpf",
                "nomeMae",
                "nomePai",
                "numeroProcesso",
                "numeroPeca",
                "orgaoExpedidor",
                "enderecosPessoa",
                "tipificacaoPenal",
            )
        )
    )


def validity_class(record):
    iso = record.get("dataValidadeIso", "")

    if not iso:
        return "sem"

    try:
        valid_until = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return "sem"

    days = (valid_until - datetime.now().date()).days

    if days < 0:
        return "vencido"

    if days <= 90:
        return "90"

    if days <= 365:
        return "365"

    return "ok"


def normalize_record(record):
    return {
        "id": record.get("id"),
        "idTipoPeca": record.get("idTipoPeca"),
        "numeroPeca": pick(record, "numeroPecaFormatado", "numeroPeca"),
        "numeroProcesso": pick(record, "numeroProcesso"),
        "nomePessoa": pick(record, "nomePessoa"),
        "alcunha": pick(record, "alcunha"),
        "cpf": pick(record, "cpf") or "Não Informado",
        "nomeMae": pick(record, "nomeMae"),
        "nomePai": pick(record, "nomePai"),
        "dataNascimento": format_date(
            pick(record, "dataNascimentoFormatada", "dataNascimento")
        ),
        "sexo": pick(record, "sexo", "descricaoSexo"),
        "profissao": pick(record, "profissao", "descricaoProfissao"),
        "status": pick(record, "status", "descricaoStatus"),
        "descricaoPeca": pick(record, "descricaoPeca"),
        "dataExpedicao": format_date(
            pick(record, "dataExpedicaoFormatada", "dataExpedicao")
        ),
        "dataExpedicaoIso": iso_date(
            pick(record, "dataExpedicaoFormatada", "dataExpedicao")
        ),
        "dataValidade": format_date(pick(record, "dataValidade")),
        "dataValidadeIso": iso_date(pick(record, "dataValidade")),
        "orgaoExpedidor": pick(record, "orgaoExpedidor", "nomeOrgao"),
        "enderecosPessoa": clean_document_leaks(
            pick(record, "enderecosPessoa")
        ),
        "localOcorrenciaDelito": clean_document_leaks(
            pick(record, "localOcorrenciaDelito")
        ),
        "tipificacaoPenal": clean_document_leaks(
            pick(record, "tipificacaoPenal")
        ),
        "penaRestante": clean_document_leaks(
            pick(record, "penaRestante")
        ),
    }


def enrich_record(record):
    record["_normOrgao"] = norm_text(record.get("orgaoExpedidor", ""))
    record["_normEndereco"] = norm_text(record.get("enderecosPessoa", ""))
    record["_searchText"] = record_search_text(record)
    record["_inferredTerritory"] = infer_territory(record)
    return record


def public_record(record):
    return {
        key: value
        for key, value in record.items()
        if key == "_territory" or not key.startswith("_")
    }


def load_records():
    source = None
    payload = None
    cache_key = None

    for data_file in DATA_FILES:
        if data_file.exists():
            source = data_file
            stat = data_file.stat()
            cache_key = (str(data_file), stat.st_mtime_ns, stat.st_size)

            if RECORD_CACHE["key"] == cache_key:
                return RECORD_CACHE["data"]

            payload = json.loads(data_file.read_text(encoding="utf-8"))
            break

    if payload is None:
        checked_files = ", ".join(str(path) for path in DATA_FILES)
        result = {
            "records": [],
            "meta": {
                "sourceFile": "",
                "processed": False,
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
                "dataFilesChecked": [str(path) for path in DATA_FILES],
                "warning": (
                    "Nenhum arquivo de dados encontrado. Coloque "
                    "mandados_processados.json em /app/data no container "
                    f"ou defina BNMP_DATA_FILE. Caminhos verificados: {checked_files}"
                ),
            },
        }
        RECORD_CACHE["key"] = None
        RECORD_CACHE["data"] = result
        return result

    if isinstance(payload, dict):
        raw_records = payload.get("content", [])
    elif isinstance(payload, list):
        raw_records = payload
    else:
        raw_records = []

    records = [
        enrich_record(normalize_record(record))
        for record in raw_records
        if isinstance(record, dict)
    ]

    processed = source.name == "mandados_processados.json"
    warning = ""

    if not processed:
        warning = (
            "Base parcial: pecas_autorizadas.json nao possui CPF, validade "
            "e endereco extraidos do PDF. Gere mandados_processados.json "
            "para usar todos os filtros operacionais."
        )

    result = {
        "records": records,
        "meta": {
            "sourceFile": source.name,
            "sourcePath": str(source),
            "dataFilesChecked": [str(path) for path in DATA_FILES],
            "processed": processed,
            "total": len(records),
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "warning": warning,
        },
    }

    RECORD_CACHE["key"] = cache_key
    RECORD_CACHE["data"] = result
    return result


def clear_record_cache():
    RECORD_CACHE["key"] = None
    RECORD_CACHE["data"] = None


def default_data_file():
    configured = os.environ.get("BNMP_DATA_FILE")

    if configured:
        return Path(configured).resolve()

    return (DATA_DIR / "mandados_processados.json").resolve()


def is_data_file_path_allowed(path):
    path = Path(path).resolve()
    allowed_dirs = [DATA_DIR, ROOT]

    return any(path == directory or directory in path.parents for directory in allowed_dirs)


def write_data_file(raw_body):
    try:
        payload = json.loads(raw_body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, f"JSON invalido: {error}"

    if not isinstance(payload, (list, dict)):
        return None, "O arquivo precisa conter uma lista JSON ou um objeto JSON."

    target = default_data_file()

    if target.name not in {"mandados_processados.json", "pecas_autorizadas.json"}:
        return None, "BNMP_DATA_FILE deve apontar para mandados_processados.json ou pecas_autorizadas.json."

    if not is_data_file_path_allowed(target):
        return None, "Caminho de dados fora do diretorio permitido."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        return None, f"Nao foi possivel salvar o arquivo em {target}: {error}"

    clear_record_cache()
    return target, ""


def query_value(query, key, default=""):
    values = query.get(key)

    if not values:
        return default

    return repair_text(values[0]) or default


def query_int(query, key, default, minimum=None, maximum=None):
    try:
        value = int(query_value(query, key, str(default)))
    except ValueError:
        value = default

    if minimum is not None:
        value = max(minimum, value)

    if maximum is not None:
        value = min(maximum, value)

    return value


def facets_for(records):
    return {
        "types": sorted(
            {record.get("descricaoPeca") for record in records if record.get("descricaoPeca")}
        ),
        "statuses": sorted(
            {record.get("status") for record in records if record.get("status")}
        ),
    }


def sort_records(records, sort):
    if sort == "recentes":
        records.sort(key=lambda record: record.get("dataExpedicaoIso") or "", reverse=True)
        return records

    if sort == "validade":
        records.sort(key=lambda record: record.get("dataValidadeIso") or "9999-99-99")
        return records

    if sort == "nome":
        records.sort(key=lambda record: norm_text(record.get("nomePessoa", "")))
        return records

    records.sort(key=lambda record: record.get("dataExpedicaoIso") or "", reverse=True)
    records.sort(key=lambda record: record.get("_territory", {}).get("score", 0))
    return records


def summarize_records(records):
    by_crp = {}
    by_type = {}
    orgao = 0
    endereco_only = 0
    expiring = 0

    for record in records:
        territory = record.get("_territory") or {}
        unit = None

        if territory.get("units"):
            unit = territory["units"][0]
        else:
            unit = record.get("_inferredTerritory")

        crp = unit["crp"] if unit else "Sem vínculo territorial"
        by_crp[crp] = by_crp.get(crp, 0) + 1

        record_type = record.get("descricaoPeca") or "Sem tipo"
        by_type[record_type] = by_type.get(record_type, 0) + 1

        if territory.get("orgao"):
            orgao += 1
        elif territory.get("endereco"):
            endereco_only += 1

        if validity_class(record) in {"90", "365"}:
            expiring += 1

    return {
        "byCrp": by_crp,
        "byType": by_type,
        "orgao": orgao,
        "enderecoOnly": endereco_only,
        "expiring": expiring,
    }


def filter_records(records, filters):
    q = norm_text(filters.get("q", ""))
    filtered = []

    for record in records:
        territory = match_territory(record, filters)

        if not territory.get("matches"):
            continue

        decorated = {**record, "_territory": territory}

        if q and q not in decorated.get("_searchText", record_search_text(decorated)):
            continue

        if filters.get("type") and decorated.get("descricaoPeca") != filters["type"]:
            continue

        if filters.get("status") and decorated.get("status") != filters["status"]:
            continue

        if filters.get("validade") and validity_class(decorated) != filters["validade"]:
            continue

        if filters.get("tab") == "orgao" and not territory.get("orgao"):
            continue

        if filters.get("tab") == "endereco" and (
            territory.get("orgao") or not territory.get("endereco")
        ):
            continue

        filtered.append(decorated)

    return filtered


def paginated_records(query):
    dataset = load_records()
    records = dataset["records"]
    size = query_int(query, "size", 50, minimum=1, maximum=200)
    page = query_int(query, "page", 1, minimum=1)
    filters = {
        "q": query_value(query, "q"),
        "crp": query_value(query, "crp"),
        "unit": query_value(query, "unit"),
        "origin": query_value(query, "origin", "ambos"),
        "type": query_value(query, "type"),
        "validade": query_value(query, "validade"),
        "status": query_value(query, "status"),
        "sort": query_value(query, "sort", "territorio"),
        "tab": query_value(query, "tab", "todos"),
    }

    filtered = filter_records(records, filters)
    sort_records(filtered, filters["sort"])

    total = len(filtered)
    total_pages = max(1, (total + size - 1) // size)
    page = min(page, total_pages)
    start = (page - 1) * size
    end = start + size
    page_records = [public_record(record) for record in filtered[start:end]]

    return {
        "records": page_records,
        "meta": {
            **dataset["meta"],
            "facets": facets_for(records),
            "summary": summarize_records(filtered),
        },
        "pagination": {
            "page": page,
            "size": size,
            "total": total,
            "totalPages": total_pages,
            "start": start + 1 if total else 0,
            "end": min(end, total),
        },
    }


def extract_cookie(raw_cookie):
    raw_cookie = (raw_cookie or "").strip()

    if not raw_cookie:
        return ""

    match = re.search(r"(?:^|[;\s])portalbnmp=([^;\s]+)", raw_cookie)
    if match:
        return match.group(1).strip()

    return raw_cookie


def extract_cookie_from_list(cookies):
    if not isinstance(cookies, list):
        return ""

    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue

        if cookie.get("name") == "portalbnmp":
            return str(cookie.get("value") or "").strip()

    return ""


def session_from_payload(payload):
    if isinstance(payload, list):
        return extract_cookie_from_list(payload), ""

    if not isinstance(payload, dict):
        return "", ""

    cookie = (
        payload.get("cookie")
        or payload.get("portalbnmp")
        or extract_cookie_from_list(payload.get("cookies"))
    )
    fingerprint = payload.get("fingerprint") or ""

    return str(cookie or ""), str(fingerprint or "").strip()


def load_session_from_cookies_file():
    if not BNMP_COOKIES_FILE:
        return False, "BNMP_COOKIES_FILE nao configurado."

    path = Path(BNMP_COOKIES_FILE).resolve()

    if not path.exists():
        return False, f"Arquivo de sessao BNMP nao encontrado: {path}"

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return False, f"Nao foi possivel ler a sessao BNMP: {error}"

    cookie, fingerprint = session_from_payload(payload)

    if not set_bnmp_session(cookie, fingerprint, "cookies-file"):
        return False, "Arquivo de sessao sem cookie portalbnmp valido."

    return True, ""


def export_remote_browser_session():
    if not BNMP_BROWSER_EXPORT_URL:
        return False, "BNMP_BROWSER_EXPORT_URL nao configurado."

    if requests is None:
        return False, "O pacote requests nao esta instalado."

    headers = {}
    if BNMP_BROWSER_EXPORT_TOKEN:
        headers["X-BNMP-Export-Token"] = BNMP_BROWSER_EXPORT_TOKEN

    try:
        response = requests.post(BNMP_BROWSER_EXPORT_URL, headers=headers, timeout=30)
    except requests.RequestException as error:
        return False, f"Falha ao acionar o navegador remoto BNMP: {error}"

    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = remote_export_error_detail(payload, response.text)
        except (ValueError, AttributeError):
            detail = response.text[:300]

        return False, f"Navegador remoto recusou a exportacao: {detail}"

    return load_session_from_cookies_file()


def remote_export_error_detail(payload, fallback_text):
    if not isinstance(payload, dict):
        return fallback_text[:300]

    detail = payload.get("error") or fallback_text[:300]
    diagnostics = []

    if payload.get("currentUrl"):
        diagnostics.append(f"URL atual: {payload['currentUrl']}")

    if "cookieNames" in payload:
        cookie_names = ", ".join(payload.get("cookieNames") or []) or "nenhum"
        diagnostics.append(f"cookies: {cookie_names}")

    if "cookieDomains" in payload:
        cookie_domains = ", ".join(payload.get("cookieDomains") or []) or "nenhum"
        diagnostics.append(f"dominios: {cookie_domains}")

    if payload.get("postCaptchaNavigationTried"):
        diagnostics.append("tentou voltar para a pesquisa apos o captcha")

    if diagnostics:
        detail = f"{detail} ({'; '.join(diagnostics)})"

    return detail


def cookie_remaining_seconds():
    if not AUTH["cookie"]:
        return 0

    return max(0, int(AUTH["expires_at"] - time.time()))


def set_bnmp_session(raw_cookie, fingerprint="", source="manual"):
    cookie = extract_cookie(raw_cookie)

    if not cookie:
        clear_bnmp_session()
        return False

    AUTH["cookie"] = cookie
    AUTH["fingerprint"] = (fingerprint or "").strip()
    AUTH["expires_at"] = time.time() + COOKIE_TTL_SECONDS
    AUTH["source"] = source
    AUTH["authenticated_at"] = time.time()
    return True


def clear_bnmp_session():
    AUTH["cookie"] = ""
    AUTH["fingerprint"] = ""
    AUTH["expires_at"] = 0.0
    AUTH["source"] = ""
    AUTH["authenticated_at"] = 0.0


def auth_status_payload():
    remaining = cookie_remaining_seconds()

    if remaining <= 0 and AUTH["cookie"]:
        clear_bnmp_session()

    return {
        "authenticated": remaining > 0,
        "remainingSeconds": max(0, remaining),
        "ttlSeconds": COOKIE_TTL_SECONDS,
        "source": AUTH["source"] if remaining > 0 else "",
        "fingerprintPresent": bool(AUTH["fingerprint"]) if remaining > 0 else False,
        "remoteBrowserConfigured": bool(BNMP_REMOTE_BROWSER_URL),
        "browserExportConfigured": bool(BNMP_BROWSER_EXPORT_URL),
        "cookiesFileConfigured": bool(BNMP_COOKIES_FILE),
        "portalUrl": BNMP_PORTAL_URL,
    }


def pdf_headers():
    headers = {
        "Accept": "application/pdf, application/octet-stream, application/json, */*",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Origin": "https://portalbnmp.pdpj.jus.br",
        "Referer": "https://portalbnmp.pdpj.jus.br/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
    }

    if AUTH["fingerprint"]:
        headers["fingerprint"] = AUTH["fingerprint"]

    return headers


def safe_filename(value):
    value = repair_text(value) or "mandado"
    value = re.sub(r'[<>:"/\\|?*\r\n]+', "_", value)
    value = re.sub(r"\s+", "_", value)
    return value.strip("._")[:120] or "mandado"


def find_record(record_id):
    records = load_records()["records"]

    for record in records:
        if str(record.get("id")) == str(record_id):
            return record

    return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "PainelTocantins/1.0"

    def log_message(self, fmt, *args):
        return

    def send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-store")

        if BNMP_ENABLE_HSTS:
            self.send_header(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

    def basic_auth_enabled(self):
        return bool(APP_BASIC_AUTH_USER and APP_BASIC_AUTH_PASSWORD)

    def basic_auth_valid(self):
        header = self.headers.get("Authorization", "")

        if not header.startswith("Basic "):
            return False

        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False

        username, separator, password = decoded.partition(":")

        if not separator:
            return False

        return hmac.compare_digest(username, APP_BASIC_AUTH_USER) and hmac.compare_digest(
            password,
            APP_BASIC_AUTH_PASSWORD,
        )

    def require_basic_auth(self, path):
        if path == "/api/health" or not self.basic_auth_enabled():
            return False

        if self.basic_auth_valid():
            return False

        self.send_response(401)
        self.send_security_headers()
        self.send_header("WWW-Authenticate", 'Basic realm="BNMP PMTO", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or "0")

        if length <= 0:
            return {}

        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")

    def read_body(self):
        length = int(self.headers.get("Content-Length") or "0")

        if length <= 0:
            return b""

        return self.rfile.read(length)

    def serve_html(self):
        if not HTML_FILE.exists():
            self.send_json(500, {"error": "painel_tocantins.html nao encontrado."})
            return

        body = HTML_FILE.read_bytes()
        self.send_response(200)
        self.send_security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_auth_html(self):
        if not BNMP_AUTH_HTML_FILE.exists():
            self.send_json(500, {"error": "bnmp_auth.html nao encontrado."})
            return

        body = BNMP_AUTH_HTML_FILE.read_bytes()
        self.send_response(200)
        self.send_security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_security_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Password")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if self.require_basic_auth(path):
            return

        if path in {"/", "/painel_tocantins.html"}:
            self.serve_html()
            return

        if path == "/bnmp/autenticar":
            self.serve_auth_html()
            return

        if path == "/api/mandados":
            self.send_json(200, paginated_records(parse_qs(parsed.query)))
            return

        if path == "/api/health":
            self.send_json(200, {"status": "ok"})
            return

        if path == "/api/auth/status":
            self.send_json(200, auth_status_payload())
            return

        if path == "/api/auth/config":
            self.send_json(
                200,
                {
                    "portalUrl": BNMP_PORTAL_URL,
                    "remoteBrowserUrl": BNMP_REMOTE_BROWSER_URL,
                    "remoteBrowserConfigured": bool(BNMP_REMOTE_BROWSER_URL),
                    "browserExportConfigured": bool(BNMP_BROWSER_EXPORT_URL),
                    "cookiesFileConfigured": bool(BNMP_COOKIES_FILE),
                    "ttlSeconds": COOKIE_TTL_SECONDS,
                },
            )
            return

        if path.startswith("/api/pdf/"):
            self.handle_pdf(path)
            return

        self.send_json(404, {"error": "Rota nao encontrada."})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if self.require_basic_auth(path):
            return

        if path == "/api/admin/login":
            try:
                payload = self.read_json()
            except json.JSONDecodeError:
                self.send_json(400, {"error": "JSON invalido."})
                return

            if payload.get("password", "") != ADMIN_PASSWORD:
                self.send_json(403, {"error": "Senha administrativa invalida."})
                return

            self.send_json(200, {"ok": True})
            return

        if path == "/api/auth/cookie":
            try:
                payload = self.read_json()
            except json.JSONDecodeError:
                self.send_json(400, {"error": "JSON invalido."})
                return

            ok = set_bnmp_session(
                payload.get("cookie", ""),
                payload.get("fingerprint", ""),
                "manual-cookie",
            )

            if not ok:
                self.send_json(400, {"error": "Cookie nao informado."})
                return

            self.send_json(200, auth_status_payload())
            return

        if path == "/api/auth/session":
            try:
                payload = self.read_json()
            except json.JSONDecodeError:
                self.send_json(400, {"error": "JSON invalido."})
                return

            cookie, fingerprint = session_from_payload(payload)
            ok = set_bnmp_session(
                cookie,
                fingerprint,
                payload.get("source", "manual-session"),
            )

            if not ok:
                self.send_json(400, {"error": "Sessao BNMP sem cookie valido."})
                return

            self.send_json(200, auth_status_payload())
            return

        if path == "/api/auth/export-remote-session":
            ok, error = export_remote_browser_session()

            if not ok:
                self.send_json(400, {"error": error})
                return

            self.send_json(200, auth_status_payload())
            return

        if path == "/api/auth/import-cookies-file":
            ok, error = load_session_from_cookies_file()

            if not ok:
                self.send_json(400, {"error": error})
                return

            self.send_json(200, auth_status_payload())
            return

        if path == "/api/auth/logout":
            clear_bnmp_session()
            self.send_json(200, auth_status_payload())
            return

        if path == "/api/data/upload":
            if self.headers.get("X-Admin-Password", "") != ADMIN_PASSWORD:
                self.send_json(403, {"error": "Senha administrativa invalida."})
                return

            target, error = write_data_file(self.read_body())

            if error:
                self.send_json(400, {"error": error})
                return

            loaded = load_records()
            self.send_json(
                200,
                {
                    "ok": True,
                    "sourceFile": target.name,
                    "sourcePath": str(target),
                    "total": loaded["meta"].get("total", 0),
                },
            )
            return

        self.send_json(404, {"error": "Rota nao encontrada."})

    def handle_pdf(self, path):
        if requests is None:
            self.send_json(500, {"error": "O pacote requests nao esta instalado."})
            return

        remaining = cookie_remaining_seconds()
        if remaining <= 0:
            clear_bnmp_session()
            self.send_json(
                401,
                {"error": "Sessao BNMP expirada. Abra a autenticacao BNMP."},
            )
            return

        parts = [unquote(part) for part in path.split("/") if part]

        if len(parts) != 4:
            self.send_json(400, {"error": "URL de PDF invalida."})
            return

        _, _, record_id, record_type = parts
        url = f"{BNMP_API}/certidaos/relatorio/{record_id}/{record_type}"

        try:
            response = requests.post(
                url,
                headers=pdf_headers(),
                cookies={"portalbnmp": AUTH["cookie"]},
                timeout=90,
            )
        except requests.RequestException as error:
            self.send_json(502, {"error": f"Falha ao consultar o BNMP: {error}"})
            return

        if response.status_code in {401, 403}:
            clear_bnmp_session()
            self.send_json(
                401,
                {"error": "Sessao BNMP recusada. Autentique novamente."},
            )
            return

        if response.status_code >= 400:
            self.send_json(
                response.status_code,
                {
                    "error": "BNMP retornou erro ao baixar o PDF.",
                    "statusCode": response.status_code,
                    "body": response.text[:500],
                },
            )
            return

        if not response.content.startswith(b"%PDF"):
            self.send_json(
                502,
                {
                    "error": "A resposta do BNMP nao veio como PDF.",
                    "contentType": response.headers.get("Content-Type", ""),
                    "body": response.text[:500],
                },
            )
            return

        record = find_record(record_id)
        base_name = safe_filename(
            record.get("numeroPeca") or f"mandado-{record_id}-{record_type}"
        )
        filename = f"{base_name}.pdf"

        self.send_response(200)
        self.send_security_headers()
        self.send_header("Content-Type", "application/pdf")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"',
        )
        self.send_header("Content-Length", str(len(response.content)))
        self.end_headers()
        self.wfile.write(response.content)


def main():
    parser = argparse.ArgumentParser(
        description="Painel operacional de mandados do Tocantins."
    )
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Painel Tocantins: http://{args.host}:{args.port}")
    print("Pressione Ctrl+C para encerrar.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
