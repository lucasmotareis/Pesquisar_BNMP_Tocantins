import json
import hmac
import os
import base64
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
PORTAL_COOKIE_NAME = "portalbnmp"
COOKIE_WAIT_SECONDS = float(os.environ.get("BNMP_BROWSER_COOKIE_WAIT_SECONDS", "12"))
CAPTURED_SESSION_MAX_AGE_SECONDS = int(
    os.environ.get("BNMP_CAPTURED_SESSION_MAX_AGE_SECONDS", "600")
)


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

    def recv_message(self, timeout: float | None = None) -> dict[str, Any]:
        if timeout is not None:
            self.ws.settimeout(timeout)

        try:
            raw = self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return {}
        finally:
            if timeout is not None:
                self.ws.settimeout(10)

        return json.loads(raw)

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
            message = self.recv_message()

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


def safe_call(
    session: CDPSession,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return session.call(method, params)
    except CDPError:
        return {}


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


def jwt_expiry(token: str) -> int | None:
    parts = token.split(".")

    if len(parts) != 3:
        return None

    payload = parts[1] + "=" * (-len(parts[1]) % 4)

    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    exp = data.get("exp")

    return int(exp) if isinstance(exp, (int, float)) else None


def looks_like_bnmp_token(value: str) -> bool:
    token = normalize_authorization_token(value)
    return token.count(".") == 2 and len(token) > 80


def normalize_authorization_token(value: str) -> str:
    token = (value or "").strip()

    if token.lower().startswith("bearer "):
        return token[7:].strip()

    return token


def cookie_from_token(token: str, current_url: str = "") -> dict[str, Any]:
    domain = urlparse(current_url or BNMP_PORTAL_URL).hostname or "portalbnmp.pdpj.jus.br"
    cookie = {
        "name": PORTAL_COOKIE_NAME,
        "value": normalize_authorization_token(token),
        "domain": domain,
        "path": "/",
        "httpOnly": False,
        "secure": current_url.startswith("https://")
        if current_url
        else BNMP_PORTAL_URL.startswith("https://"),
    }
    expires = jwt_expiry(cookie["value"])

    if expires:
        cookie["expires"] = expires

    return cookie


def dedupe_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()

    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "")
        path = str(cookie.get("path") or "/")

        if not name:
            continue

        key = (name, domain, path, value)
        if key in seen:
            continue

        seen.add(key)
        result.append(cookie)

    return result


def cookies_from_document_cookie(raw_cookie: str, current_url: str) -> list[dict[str, Any]]:
    host = urlparse(current_url).hostname or ""
    cookies = []

    for part in raw_cookie.split(";"):
        name, separator, value = part.strip().partition("=")

        if not separator or not name:
            continue

        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": host,
                "path": "/",
                "httpOnly": False,
                "secure": current_url.startswith("https://"),
            }
        )

    return cookies


def cookies_from_set_cookie_header(value: str, current_url: str = "") -> list[dict[str, Any]]:
    cookies = []

    if not value:
        return cookies

    for raw_cookie in str(value).replace("\r\n", "\n").split("\n"):
        if not raw_cookie.strip():
            continue

        parsed = SimpleCookie()

        try:
            parsed.load(raw_cookie)
        except CookieError:
            continue

        for morsel in parsed.values():
            domain = morsel["domain"] or urlparse(current_url or BNMP_PORTAL_URL).hostname
            path = morsel["path"] or "/"
            cookie = {
                "name": morsel.key,
                "value": morsel.value,
                "domain": domain or "portalbnmp.pdpj.jus.br",
                "path": path,
                "httpOnly": bool(morsel["httponly"]),
                "secure": bool(morsel["secure"]),
            }
            expires = jwt_expiry(cookie["value"])

            if expires:
                cookie["expires"] = expires

            cookies.append(cookie)

    return cookies


def current_page_url(session: CDPSession, fallback: str = "") -> str:
    return str(evaluate(session, "location.href") or fallback or "")


def read_browser_cookies(
    session: CDPSession,
    current_url: str,
) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    safe_call(session, "Network.enable")

    for method in ("Network.getAllCookies", "Storage.getCookies"):
        result = safe_call(session, method)
        cookies.extend(
            normalize_cookie(cookie)
            for cookie in result.get("cookies", [])
            if isinstance(cookie, dict)
        )

    document_cookie = evaluate(
        session,
        """
        (() => {
          try {
            return document.cookie || '';
          } catch (error) {
            return '';
          }
        })()
        """,
    )

    if document_cookie:
        cookies.extend(cookies_from_document_cookie(str(document_cookie), current_url))

    return dedupe_cookies(cookies)


def wait_for_browser_cookies(
    session: CDPSession,
    initial_url: str,
) -> tuple[list[dict[str, Any]], str]:
    deadline = time.time() + COOKIE_WAIT_SECONDS
    current_url = initial_url
    cookies: list[dict[str, Any]] = []

    while True:
        current_url = current_page_url(session, current_url)
        cookies = read_browser_cookies(session, current_url)

        if has_cookie_named(cookies, PORTAL_COOKIE_NAME):
            return cookies, current_url

        if time.time() >= deadline:
            return cookies, current_url

        time.sleep(0.5)


def local_storage_keys(session: CDPSession) -> list[str]:
    keys = evaluate(
        session,
        """
        (() => {
          try {
            return Object.keys(window.localStorage || {}).slice(0, 50);
          } catch (error) {
            return [];
          }
        })()
        """,
    )

    if not isinstance(keys, list):
        return []

    return [str(key) for key in keys]


def has_cookie_named(cookies: list[dict[str, Any]], name: str) -> bool:
    return any(cookie.get("name") == name for cookie in cookies)


def read_fingerprint(session: CDPSession) -> str:
    return str(
        evaluate(
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
        )
        or ""
    ).strip()


def snapshot_browser_state(extra_cookies=None, source_url: str = "") -> dict[str, Any]:
    wait_for_browser()
    targets = fetch_json(f"{CDP_BASE_URL}/json/list")
    target = pick_page_target(targets)
    session = CDPSession(target["webSocketDebuggerUrl"])

    try:
        current_url = current_page_url(session, str(target.get("url", "")))
        cookies = read_browser_cookies(session, current_url)
        if extra_cookies:
            cookies.extend(extra_cookies)

        fingerprint = read_fingerprint(session)
        user_agent = evaluate(session, "navigator.userAgent") or ""
        page_title = evaluate(session, "document.title") or target.get("title", "")
        storage_keys = local_storage_keys(session)
    finally:
        session.close()

    return {
        "cookies": dedupe_cookies(cookies),
        "fingerprint": fingerprint,
        "userAgent": user_agent,
        "portalUrl": BNMP_PORTAL_URL,
        "currentUrl": current_url,
        "targetUrl": str(target.get("url", "")),
        "pageTitle": page_title,
        "localStorageKeys": storage_keys,
        "sourceUrl": source_url,
        "authorizationHeaderCaptured": bool(extra_cookies),
        "targetUrls": [
            str(item.get("url", ""))
            for item in targets
            if isinstance(item, dict) and item.get("type") == "page"
        ][:10],
        "exportedAt": datetime.now(timezone.utc).isoformat(),
    }


def extract_browser_session() -> dict[str, Any]:
    wait_for_browser()

    targets = fetch_json(f"{CDP_BASE_URL}/json/list")
    target = pick_page_target(targets)
    session = CDPSession(target["webSocketDebuggerUrl"])

    try:
        target_url = str(target.get("url", ""))
        current_url = target_url
        post_captcha_navigation_tried = False

        if not current_url.startswith("http"):
            session.call("Page.navigate", {"url": BNMP_PORTAL_URL})
            time.sleep(2)

        current_url = current_page_url(session, current_url)
        fingerprint = read_fingerprint(session)
        cookies, current_url = wait_for_browser_cookies(session, current_url)

        if (
            not has_cookie_named(cookies, PORTAL_COOKIE_NAME)
            and fingerprint
            and "portalbnmp" in current_url
            and "#/captcha" in current_url
        ):
            post_captcha_navigation_tried = True
            session.call("Page.navigate", {"url": BNMP_PORTAL_URL})
            time.sleep(3)
            cookies, current_url = wait_for_browser_cookies(session, BNMP_PORTAL_URL)
            fingerprint = read_fingerprint(session) or fingerprint

        user_agent = evaluate(session, "navigator.userAgent") or ""
        page_title = evaluate(session, "document.title") or target.get("title", "")
        storage_keys = local_storage_keys(session)
    finally:
        session.close()

    return {
        "cookies": cookies,
        "fingerprint": fingerprint,
        "userAgent": user_agent,
        "portalUrl": BNMP_PORTAL_URL,
        "currentUrl": current_url,
        "targetUrl": target_url,
        "pageTitle": page_title,
        "localStorageKeys": storage_keys,
        "postCaptchaNavigationTried": post_captcha_navigation_tried,
        "targetUrls": [
            str(item.get("url", ""))
            for item in targets
            if isinstance(item, dict) and item.get("type") == "page"
        ][:10],
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


def read_saved_session() -> dict[str, Any] | None:
    if not BNMP_COOKIES_FILE.exists():
        return None

    try:
        session_data = json.loads(BNMP_COOKIES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return session_data if isinstance(session_data, dict) else None


def saved_session_is_fresh(session_data: dict[str, Any]) -> bool:
    cookies = session_data.get("cookies") or []
    now = time.time()

    for cookie in cookies:
        if not isinstance(cookie, dict) or cookie.get("name") != PORTAL_COOKIE_NAME:
            continue

        expires = cookie.get("expires")
        if isinstance(expires, (int, float)):
            return expires > now + 5

        break

    exported_at = session_data.get("exportedAt")
    if not exported_at:
        return False

    try:
        exported_at_dt = datetime.fromisoformat(str(exported_at))
    except ValueError:
        return False

    if exported_at_dt.tzinfo is None:
        exported_at_dt = exported_at_dt.replace(tzinfo=timezone.utc)

    age = datetime.now(timezone.utc) - exported_at_dt.astimezone(timezone.utc)
    return age.total_seconds() <= CAPTURED_SESSION_MAX_AGE_SECONDS


def save_authorization_session(token: str, source_url: str = "") -> bool:
    if not looks_like_bnmp_token(token):
        return False

    cookie = cookie_from_token(token, source_url)
    session_data = snapshot_browser_state([cookie], source_url)
    save_session(session_data)
    return True


def public_summary(session_data: dict[str, Any]) -> dict[str, Any]:
    cookies = session_data.get("cookies") or []
    cookie_names = sorted(
        {
            cookie.get("name")
            for cookie in cookies
            if isinstance(cookie, dict) and cookie.get("name")
        }
    )
    cookie_domains = sorted(
        {
            cookie.get("domain")
            for cookie in cookies
            if isinstance(cookie, dict) and cookie.get("domain")
        }
    )

    return {
        "ok": True,
        "savedTo": str(BNMP_COOKIES_FILE),
        "cookieNames": cookie_names,
        "cookieDomains": cookie_domains,
        "portalCookiePresent": PORTAL_COOKIE_NAME in cookie_names,
        "fingerprintPresent": bool(session_data.get("fingerprint")),
        "currentUrl": session_data.get("currentUrl", ""),
        "targetUrl": session_data.get("targetUrl", ""),
        "pageTitle": session_data.get("pageTitle", ""),
        "postCaptchaNavigationTried": bool(
            session_data.get("postCaptchaNavigationTried")
        ),
        "authorizationHeaderCaptured": bool(
            session_data.get("authorizationHeaderCaptured")
        ),
        "exportedAt": session_data.get("exportedAt", ""),
    }


def has_portal_cookie(session_data: dict[str, Any]) -> bool:
    cookies = session_data.get("cookies") or []

    return any(
        isinstance(cookie, dict) and cookie.get("name") == PORTAL_COOKIE_NAME
        for cookie in cookies
    )


def missing_portal_cookie_payload(session_data: dict[str, Any]) -> dict[str, Any]:
    summary = public_summary(session_data)
    summary["ok"] = False
    summary["error"] = (
        "Cookie portalbnmp nao encontrado. "
        "Confirme que a aba remota esta no portal BNMP apos o captcha "
        "e aguarde o carregamento completo antes de importar."
    )
    summary["localStorageKeys"] = session_data.get("localStorageKeys", [])
    summary["targetUrls"] = session_data.get("targetUrls", [])
    return summary


def header_value(headers: dict[str, Any], name: str) -> str:
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)

    return ""


def headers_from_network_event(message: dict[str, Any]) -> tuple[dict[str, Any], str]:
    params = message.get("params", {})
    response = params.get("response") or {}
    headers = params.get("headers") or response.get("headers") or {}
    url = str(response.get("url") or params.get("url") or "")

    if not isinstance(headers, dict):
        return {}, url

    return headers, url


def capture_session_from_network_headers(headers: dict[str, Any], url: str) -> bool:
    authorization = header_value(headers, "authorization")

    if authorization and looks_like_bnmp_token(authorization):
        return save_authorization_session(authorization, url)

    set_cookie = header_value(headers, "set-cookie")
    cookies = cookies_from_set_cookie_header(set_cookie, url)

    for cookie in cookies:
        if cookie.get("name") == PORTAL_COOKIE_NAME:
            session_data = snapshot_browser_state(cookies, url)
            save_session(session_data)
            return True

    return False


def monitor_authorization_headers() -> None:
    while True:
        session = None

        try:
            wait_for_browser()
            targets = fetch_json(f"{CDP_BASE_URL}/json/list")
            target = pick_page_target(targets)
            session = CDPSession(target["webSocketDebuggerUrl"])
            session.call("Network.enable")

            while True:
                message = session.recv_message(timeout=1)

                if not message:
                    continue

                method = message.get("method")

                if method not in {
                    "Network.responseReceived",
                    "Network.responseReceivedExtraInfo",
                }:
                    continue

                headers, url = headers_from_network_event(message)
                capture_session_from_network_headers(headers, url)
        except Exception as error:
            print(f"Monitor BNMP reiniciando: {error}", flush=True)
            time.sleep(2)
        finally:
            if session:
                session.close()


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
                saved_session = read_saved_session()

                if (
                    saved_session
                    and has_portal_cookie(saved_session)
                    and saved_session_is_fresh(saved_session)
                ):
                    self.send_json(200, public_summary(saved_session))
                    return

                self.send_json(409, missing_portal_cookie_payload(session_data))
                return

            save_session(session_data)
        except Exception as error:
            self.send_json(500, {"ok": False, "error": str(error)})
            return

        self.send_json(200, public_summary(session_data))


def main() -> None:
    threading.Thread(target=monitor_authorization_headers, daemon=True).start()

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
