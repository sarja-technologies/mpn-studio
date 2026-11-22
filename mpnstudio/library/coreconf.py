"""Cognitive cores that Studio does not run: a model on any OpenAI-compatible server (llama.cpp, vLLM, Ollama, a remote API).

A configured core is a name, the server's base URL, the model id to ask for, and optionally a context size, an output cap,
a timeout and an API key. It lives apart from every network: a pairing refers to it by id, so changing the core of a pairing
never touches the network.

  <data>/core-servers/<id>.json     name, base_url, model_id, limits, key source, last test result (never the key)
  <config>/secrets.json             {"core-server:<id>": "<key>"}, mode 0600; read only by the code that calls the server

A key is never returned by the API, logged, written to a model's MANIFEST.json or exported. A runtime process receives it in
its environment. `api_key_env` names an environment variable to read instead of storing a key at all.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..desktop import paths

FORMAT = "mpn-studio-core-server/1"
PREFIX = "endpoint:"                 # how a pairing refers to one: core_id "endpoint:<id>"
KEY_ENV = "MPN_CORE_API_KEY"         # the variable a runtime process reads its key from


class CoreServerError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def is_ref(core_id: str | None) -> bool:
    return bool(core_id) and str(core_id).startswith(PREFIX)


def ref_id(core_id: str) -> str:
    return core_id[len(PREFIX):]


def servers_dir() -> Path:
    d = paths.data_dir() / "core-servers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(cid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", cid or ""):
        raise CoreServerError("Invalid core id.")
    return servers_dir() / f"{cid}.json"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------------------------------------ secrets
def _secrets_path() -> Path:
    return paths.config_dir() / "secrets.json"


def _read_secrets() -> dict:
    try:
        return json.loads(_secrets_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_secrets(d: dict) -> None:
    p = _secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(".secrets.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _skey(cid: str) -> str:
    return f"core-server:{cid}"


def api_key(cid: str) -> str | None:
    """The key for a configured core, for the code that calls its server. Not for any response, log or file."""
    try:
        c = json.loads(_file(cid).read_text())
    except (OSError, ValueError):
        return None
    env = c.get("api_key_env")
    if env:
        return os.environ.get(env) or None
    return _read_secrets().get(_skey(cid)) or None


# ------------------------------------------------------------------------------------------------ configuration
def normalise_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise CoreServerError("The server address must start with http:// or https:// and name a host, for example http://127.0.0.1:8080/v1.")
    if p.username or p.password:
        raise CoreServerError("Do not put a user name or password in the address; use the API key field.")
    if p.path in ("", "/"):
        u = u + "/v1"               # every OpenAI-compatible server answers under /v1
    return u


def _view(c: dict) -> dict:
    out = {k: v for k, v in c.items() if k not in ("api_key",)}
    env = c.get("api_key_env")
    out["id_ref"] = PREFIX + c["id"]
    out["has_key"] = bool(os.environ.get(env)) if env else bool(_read_secrets().get(_skey(c["id"])))
    out["key_source"] = f"environment variable {env}" if env else ("saved in Studio" if out["has_key"] else None)
    return out


def get(cid: str) -> dict:
    try:
        return _view(json.loads(_file(cid).read_text()))
    except OSError:
        raise CoreServerError("No such core server.", 404)


def list_servers() -> list[dict]:
    out = []
    for p in sorted(servers_dir().glob("*.json")):
        try:
            out.append(_view(json.loads(p.read_text())))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda c: c.get("created", ""), reverse=True)


def _int(v, name: str, lo: int, hi: int) -> int | None:
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise CoreServerError(f"{name} must be a whole number.")
    if not lo <= n <= hi:
        raise CoreServerError(f"{name} must be between {lo:,} and {hi:,}.")
    return n


def _clean(body: dict) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise CoreServerError("Give the core a name.")
    model = (body.get("model_id") or "").strip()
    if not model:
        raise CoreServerError("Enter the model id the server expects (for llama.cpp, any name; for vLLM, the model it serves).")
    env = (body.get("api_key_env") or "").strip() or None
    if env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", env):
        raise CoreServerError("The environment variable name may use letters, digits and underscores.")
    return {"name": name, "base_url": normalise_url(body.get("base_url")), "model_id": model,
            "context_tokens": _int(body.get("context_tokens"), "Context", 256, 4_000_000),
            "max_output_tokens": _int(body.get("max_output_tokens"), "Output limit", 1, 1_000_000),
            "timeout_s": _int(body.get("timeout_s"), "Timeout", 5, 3600) or 120, "api_key_env": env,
            "notes": (body.get("notes") or "").strip()[:300] or None}


def save(body: dict, cid: str | None = None) -> dict:
    """Create (cid None) or update a configured core. `api_key`: a string stores it; "" removes the stored key; absent keeps it."""
    new = _clean(body)
    if cid is None:
        base = re.sub(r"[^a-z0-9]+", "-", new["name"].lower()).strip("-")[:40] or "core"
        cid, n = base, 1
        while _file(cid).exists():
            n += 1
            cid = f"{base}-{n}"
        rec = {"format": FORMAT, "id": cid, "created": now(), "last_test": None, **new}
    else:
        old = json.loads(_file(cid).read_text()) if _file(cid).exists() else None
        if old is None:
            raise CoreServerError("No such core server.", 404)
        rec = {**old, **new, "modified": now()}
        if new["base_url"] != old["base_url"] or new["model_id"] != old["model_id"]:
            rec["last_test"] = None          # the earlier result described another server
    rec["id"] = cid
    if "api_key" in body:
        sec = _read_secrets()
        k = body.get("api_key")
        if k:
            sec[_skey(cid)] = str(k).strip()
        else:
            sec.pop(_skey(cid), None)
        _write_secrets(sec)
    p = _file(cid)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    os.replace(tmp, p)
    return get(cid)


def remove(cid: str) -> dict:
    from . import models
    users = [m["name"] for m in models.list_models() if (m.get("core") or {}).get("endpoint_id") == cid]
    if users:
        raise CoreServerError(f"{', '.join(users)} uses this core. Change that pairing's core first.", 409)
    p = _file(cid)
    if not p.exists():
        raise CoreServerError("No such core server.", 404)
    p.unlink()
    sec = _read_secrets()
    if sec.pop(_skey(cid), None) is not None:
        _write_secrets(sec)
    return {"removed": cid}


def record_test(cid: str, result: dict) -> None:
    p = _file(cid)
    if p.exists():
        rec = json.loads(p.read_text())
        rec["last_test"] = {k: result.get(k) for k in ("ok", "code", "message", "latency_ms", "tested_at")}
        p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))


# ------------------------------------------------------------------------------------------------ test connection
def _scrub(text: str, key: str | None) -> str:
    return text.replace(key, "***") if key and key in text else text


def _server_message(r: httpx.Response, key: str | None) -> str:
    try:
        j = r.json()
        m = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("error") or j.get("message") or j.get("detail")
        if m:
            return _scrub(str(m)[:300], key)
    except Exception:  # noqa: BLE001
        pass
    return _scrub(r.text[:200].strip(), key)


def headers(key: str | None) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "mpn-studio"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def root_of(base_url: str) -> str:
    return re.sub(r"/v1$", "", base_url.rstrip("/"))


def test(base_url: str, model_id: str, key: str | None = None, timeout: float = 20, probe: bool = True) -> dict:
    """Ask the server what it serves, then ask the model for a few tokens. Says what is wrong in words a person can act on."""
    t0 = time.perf_counter()
    out = {"ok": False, "code": None, "message": "", "latency_ms": None, "models": [], "reply": None, "context_tokens": None, "warnings": [],
           "tested_at": now()}
    try:
        base = normalise_url(base_url)
    except CoreServerError as e:
        return {**out, "code": "bad_url", "message": str(e)}
    host = urlparse(base).netloc
    h = headers(key)
    tmo = httpx.Timeout(timeout, connect=min(timeout, 6.0))

    def fail(code: str, msg: str) -> dict:
        out.update(code=code, message=msg, latency_ms=round((time.perf_counter() - t0) * 1000))
        return out
    try:
        with httpx.Client(timeout=tmo, follow_redirects=False) as c:
            lr = c.get(base + "/models", headers=h)
            models_ok = lr.status_code == 200
            if lr.status_code in (401, 403):
                return fail("auth", f"{host} rejected the API key." if key else f"{host} needs an API key. Enter one, then test again.")
            if models_ok:
                try:
                    data = lr.json()
                    rows = data.get("data") if isinstance(data, dict) else None
                    out["models"] = [str(x.get("id")) for x in rows or [] if isinstance(x, dict) and x.get("id")][:50]
                    mm = next((x for x in rows or [] if isinstance(x, dict) and x.get("id") == model_id), None) or ((rows or [None])[0] if rows and len(rows) == 1 else None)
                    if isinstance(mm, dict) and mm.get("max_model_len"):
                        out["context_tokens"] = int(mm["max_model_len"])
                except (ValueError, AttributeError):
                    return fail("not_openai", f"{host} answered, but not with an OpenAI-style model list. Check the address; it usually ends in /v1.")
            elif lr.status_code not in (404, 405):
                return fail("server_error", f"{host} answered HTTP {lr.status_code} to the model list: {_server_message(lr, key)}")
            if models_ok and out["models"] and model_id not in out["models"]:
                single = len(out["models"]) == 1
                out["warnings"].append(f"The server lists {', '.join(out['models'][:5])}, not {model_id}." + (" Servers with one model (llama.cpp) answer with it regardless of the id." if single else ""))
            if out["context_tokens"] is None:               # llama.cpp reports its context in /props, at the server root
                try:
                    pr = c.get(root_of(base) + "/props", headers=h)
                    if pr.status_code == 200:
                        out["context_tokens"] = int(((pr.json().get("default_generation_settings") or {}).get("n_ctx")) or 0) or None
                except (httpx.HTTPError, ValueError, AttributeError):
                    pass
            if not probe:
                out.update(ok=True, message="The server answered.", latency_ms=round((time.perf_counter() - t0) * 1000))
                return out
            t1 = time.perf_counter()
            cr = c.post(base + "/chat/completions", headers=h, json={"model": model_id, "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                                                                       "max_tokens": 12, "temperature": 0, "stream": False})
            if cr.status_code in (401, 403):
                return fail("auth", f"{host} rejected the API key." if key else f"{host} needs an API key. Enter one, then test again.")
            if cr.status_code == 404:
                msg = _server_message(cr, key)
                if re.search(r"model", msg, re.I):
                    avail = f" The server offers: {', '.join(out['models'][:6])}." if out["models"] else ""
                    return fail("model_not_found", f"{host} has no model called {model_id}.{avail}")
                return fail("bad_url", f"{base}/chat/completions was not found. The address should end where the OpenAI-style API starts, usually /v1.")
            if cr.status_code >= 400:
                return fail("request_rejected", f"{host} refused the test question (HTTP {cr.status_code}): {_server_message(cr, key)}")
            try:
                j = cr.json()
                msg = (j["choices"][0].get("message") or {})
                out["reply"] = _scrub((msg.get("content") or "").strip()[:120], key) or None      # a model that only "thinks" in 12 tokens has no reply to show
                out["usage"] = j.get("usage")
            except (ValueError, KeyError, IndexError, AttributeError, TypeError):
                return fail("not_openai", f"{host} answered the test question, but not in the OpenAI chat format.")
            out.update(ok=True, message="The core answered.", latency_ms=round((time.perf_counter() - t1) * 1000))
            return out
    except httpx.TimeoutException:
        return fail("timeout", f"{host} did not answer within {timeout:.0f} s. A model that is still loading, or a very slow machine, looks like this; try again.")
    except httpx.ConnectError as e:
        why = "The host name could not be found." if re.search(r"name or service|nodename|getaddrinfo|resolve", str(e), re.I) else f"Nothing is listening at {host}."
        return fail("unreachable", f"{why} Is the server running, and is the address right?")
    except httpx.HTTPError as e:
        return fail("unreachable", f"The connection to {host} failed ({type(e).__name__}).")


def test_saved(cid: str, timeout: float = 20) -> dict:
    c = get(cid)
    r = test(c["base_url"], c["model_id"], api_key(cid), timeout=min(timeout, float(c.get("timeout_s") or 20)))
    record_test(cid, r)
    return r
