import json
import hmac
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import websocket


BNMP_PORTAL_URL = os.environ.get(
    "BNMP_PORTAL_URL",
    "https://portalbnmp.pdpj.jus.br/#/pesquisa-peca",
)
BNMP_COOKIES_FILE = Path(
    os.environ.get("BNMP_COOKIES_FILE", "/app/data/bnmp_cookies.json")
)
EXPORT_PORT = int(os.environ.get("EXPORT_PORT", "7788"))
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CDP_BASE_URL = f"http://127.0.0.1:{CDP_PORT}"
EXPORT_TOKEN = os.environ.get("BNMP_BROWSER_EXPORT_TOKEN", "")
EXPORT_TOKEN_HEADER = "X-BNMP-Export-Token"


class CDPError(RuntimeError):
    pass


def fetch_json(url: str, timeout: int = 5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def browser_ready() -> bool:
    try:
        fetch_json(f"{CDP_BASE_URL}/json/version", timeout=2)
        return True
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def wait_for_browser(timeout: int = 30) -> None:
    deadline = time.time() + timeout

    while time.time() < deadline:
        if browser_ready():
            return
        time.sleep(0.5)

    raise CDPError("Chromium nao ficou pronto para exportar a sessao.")


def pick_page_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    pages = [
        target
        for target in targets
        if (
            target.get("type") == "page"
            and target.get("webSocketDebuggerUrl")
            and not str(target.get("url", "")).startswith("devtools://")
        )
    ]

    if not pages:
        raise CDPError("Nenhuma aba do Chromium encontrada.")

    for page in pages:
        if "portalbnmp" in str(page.get("url", "")):
            return page

    for page in pages:
        if "portalbnmp" in str(page.get("title", "")):
            return page

    return pages[0]


class CDPSession:
    def __init__(self, websocket_url: str):
        self.ws = websocket.create_connection(websocket_url, timeout=10)
        self.next_id = 1

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self.ws.send(
            json.dumps(
                {
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )

        while True:
            raw = self.ws.recv()
            message = json.loads(raw)

            if message.get("id") != request_id:
                continue

            if "error" in message:
                raise CDPError(f"{method}: {message['error']}")

            return message.get("result", {})


def evaluate(session: CDPSession, expression: str) -> Any:
    result = session.call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    remote_object = result.get("result", {})
    return remote_object.get("value")


def normalize_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "value",
        "domain",
        "path",
        "expires",
        "httpOnly",
        "secure",
        "sameSite",
    }
    return {key: cookie[key] for key in allowed if key in cookie}


def extract_browser_session() -> dict[str, Any]:
    wait_for_browser()

    targets = fetch_json(f"{CDP_BASE_URL}/json/list")
    target = pick_page_target(targets)
    session = CDPSession(target["webSocketDebuggerUrl"])

    try:
        current_url = str(target.get("url", ""))
        if not current_url.startswith("http"):
            session.call("Page.navigate", {"url": BNMP_PORTAL_URL})
            time.sleep(2)

        cookies_result = session.call("Network.getAllCookies")
        cookies = [
            normalize_cookie(cookie)
            for cookie in cookies_result.get("cookies", [])
            if isinstance(cookie, dict)
        ]
        fingerprint = evaluate(
            session,
            """
            (() => {
              if (window.localStorage && window.localStorage.getItem('fingerprint')) {
                return window.localStorage.getItem('fingerprint');
              }
              if (window.fingerprint) {
                return String(window.fingerprint);
              }
              return '';
            })()
            """,
        ) or ""
        user_agent = evaluate(session, "navigator.userAgent") or ""
    finally:
        session.close()

    return {
        "cookies": cookies,
        "fingerprint": fingerprint,
        "userAgent": user_agent,
        "portalUrl": BNMP_PORTAL_URL,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
    }


def save_session(session_data: dict[str, Any]) -> None:
    BNMP_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = BNMP_COOKIES_FILE.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(session_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(BNMP_COOKIES_FILE)


def public_summary(session_data: dict[str, Any]) -> dict[str, Any]:
    cookies = session_data.get("cookies") or []
    cookie_names = sorted(
        {
            cookie.get("name")
            for cookie in cookies
            if isinstance(cookie, dict) and cookie.get("name")
        }
    )

    return {
        "ok": True,
        "savedTo": str(BNMP_COOKIES_FILE),
        "cookieNames": cookie_names,
        "portalCookiePresent": "portalbnmp" in cookie_names,
        "fingerprintPresent": bool(session_data.get("fingerprint")),
        "exportedAt": session_data.get("exportedAt", ""),
    }


def has_portal_cookie(session_data: dict[str, Any]) -> bool:
    cookies = session_data.get("cookies") or []

    return any(
        isinstance(cookie, dict) and cookie.get("name") == "portalbnmp"
        for cookie in cookies
    )


def export_token_configured() -> bool:
    return bool(EXPORT_TOKEN.strip())


def export_token_valid(received_token: str) -> bool:
    return export_token_configured() and hmac.compare_digest(
        received_token,
        EXPORT_TOKEN,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "BNMPBrowserExporter/1.0"

    def log_message(self, fmt, *args):
        return

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {EXPORT_TOKEN_HEADER}")
        self.end_headers()
        self.wfile.write(body)

    def require_export_token(self) -> bool:
        if not export_token_configured():
            self.send_json(
                503,
                {
                    "ok": False,
                    "error": "BNMP_BROWSER_EXPORT_TOKEN nao configurado no sidecar.",
                },
            )
            return False

        if not export_token_valid(self.headers.get(EXPORT_TOKEN_HEADER, "")):
            self.send_json(403, {"ok": False, "error": "Token de exportacao invalido."})
            return False

        return True

    def do_OPTIONS(self):
        self.send_json(204, {})

    def do_GET(self):
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "browserReady": browser_ready(),
                    "cookiesFile": str(BNMP_COOKIES_FILE),
                    "exportTokenConfigured": export_token_configured(),
                },
            )
            return

        if self.path == "/session":
            if not self.require_export_token():
                return

            if not BNMP_COOKIES_FILE.exists():
                self.send_json(404, {"ok": False, "error": "Sessao nao exportada."})
                return

            try:
                session_data = json.loads(BNMP_COOKIES_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                self.send_json(500, {"ok": False, "error": str(error)})
                return

            self.send_json(200, public_summary(session_data))
            return

        self.send_json(404, {"ok": False, "error": "Rota nao encontrada."})

    def do_POST(self):
        if self.path != "/export":
            self.send_json(404, {"ok": False, "error": "Rota nao encontrada."})
            return

        if not self.require_export_token():
            return

        try:
            session_data = extract_browser_session()
            if not has_portal_cookie(session_data):
                self.send_json(
                    409,
                    {
                        "ok": False,
                        "error": (
                            "Cookie portalbnmp nao encontrado. "
                            "Resolva o captcha no navegador remoto e aguarde "
                            "o portal carregar antes de importar."
                        ),
                        "fingerprintPresent": bool(session_data.get("fingerprint")),
                        "portalUrl": session_data.get("portalUrl", ""),
                    },
                )
                return

            save_session(session_data)
        except Exception as error:
            self.send_json(500, {"ok": False, "error": str(error)})
            return

        self.send_json(200, public_summary(session_data))


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", EXPORT_PORT), Handler)
    print(f"BNMP browser exporter listening on http://0.0.0.0:{EXPORT_PORT}")
    print(f"Cookies file: {BNMP_COOKIES_FILE}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
