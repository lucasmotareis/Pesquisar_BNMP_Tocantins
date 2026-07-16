import argparse
import json
import re
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "painel_tocantins.html"
DATA_FILES = [
    ROOT / "mandados_processados.json",
    ROOT / "pecas_autorizadas.json",
]
BNMP_API = "https://portalbnmp.pdpj.jus.br/bnmpportal/api"
COOKIE_TTL_SECONDS = 4 * 60

AUTH = {
    "cookie": "",
    "expires_at": 0.0,
}


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


def load_records():
    source = None
    payload = None

    for data_file in DATA_FILES:
        if data_file.exists():
            source = data_file
            payload = json.loads(data_file.read_text(encoding="utf-8"))
            break

    if payload is None:
        return {
            "records": [],
            "meta": {
                "sourceFile": "",
                "processed": False,
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
                "warning": "Nenhum arquivo de dados encontrado.",
            },
        }

    if isinstance(payload, dict):
        raw_records = payload.get("content", [])
    elif isinstance(payload, list):
        raw_records = payload
    else:
        raw_records = []

    records = [
        normalize_record(record)
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

    return {
        "records": records,
        "meta": {
            "sourceFile": source.name,
            "processed": processed,
            "total": len(records),
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "warning": warning,
        },
    }


def extract_cookie(raw_cookie):
    raw_cookie = (raw_cookie or "").strip()

    if not raw_cookie:
        return ""

    match = re.search(r"portalbnmp=([^;\s]+)", raw_cookie)
    if match:
        return match.group(1).strip()

    return raw_cookie


def cookie_remaining_seconds():
    if not AUTH["cookie"]:
        return 0

    return max(0, int(AUTH["expires_at"] - time.time()))


def set_cookie(raw_cookie):
    cookie = extract_cookie(raw_cookie)

    if not cookie:
        AUTH["cookie"] = ""
        AUTH["expires_at"] = 0.0
        return False

    AUTH["cookie"] = cookie
    AUTH["expires_at"] = time.time() + COOKIE_TTL_SECONDS
    return True


def clear_cookie():
    AUTH["cookie"] = ""
    AUTH["expires_at"] = 0.0


def pdf_headers():
    return {
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

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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

    def serve_html(self):
        if not HTML_FILE.exists():
            self.send_json(500, {"error": "painel_tocantins.html nao encontrado."})
            return

        body = HTML_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in {"/", "/painel_tocantins.html"}:
            self.serve_html()
            return

        if path == "/api/mandados":
            self.send_json(200, load_records())
            return

        if path == "/api/auth/status":
            remaining = cookie_remaining_seconds()
            self.send_json(
                200,
                {
                    "authenticated": remaining > 0,
                    "remainingSeconds": remaining,
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

        if path == "/api/auth/cookie":
            try:
                payload = self.read_json()
            except json.JSONDecodeError:
                self.send_json(400, {"error": "JSON invalido."})
                return

            ok = set_cookie(payload.get("cookie", ""))

            if not ok:
                self.send_json(400, {"error": "Cookie nao informado."})
                return

            self.send_json(
                200,
                {
                    "authenticated": True,
                    "remainingSeconds": cookie_remaining_seconds(),
                    "ttlSeconds": COOKIE_TTL_SECONDS,
                },
            )
            return

        if path == "/api/auth/logout":
            clear_cookie()
            self.send_json(200, {"authenticated": False, "remainingSeconds": 0})
            return

        self.send_json(404, {"error": "Rota nao encontrada."})

    def handle_pdf(self, path):
        if requests is None:
            self.send_json(500, {"error": "O pacote requests nao esta instalado."})
            return

        remaining = cookie_remaining_seconds()
        if remaining <= 0:
            clear_cookie()
            self.send_json(
                401,
                {"error": "Cookie expirado. Renove o cookie BNMP."},
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
            clear_cookie()
            self.send_json(
                401,
                {"error": "Cookie recusado pelo BNMP. Cole um novo cookie."},
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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
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
