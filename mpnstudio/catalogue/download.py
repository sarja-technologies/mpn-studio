"""Resumable, verified downloads from the hosting a release manifest names.

Every file is checked for its exact size and SHA-256 before it is given a final name. A download that stops (network
loss, Cancel, a crash) leaves `<file>.part`; the next attempt asks the server for the rest with a Range request. A file
that arrives damaged is deleted, never installed. Mirrors are tried in order when the primary does not have the file.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

Progress = Callable[[str, int, int], None]
CHUNK = 1 << 20
MARGIN = 64 << 20        # free space kept beyond what a download needs


class DownloadError(Exception):
    """`code` is one of: disk_space, unreachable, timeout, http, auth, not_found, size_mismatch, checksum_mismatch,
    cancelled, unsafe_path. `message` is written for the person using the app."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code, self.status = code, status


def fmt_bytes(n: float) -> str:
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or u == "TiB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return str(n)


def free_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def hf_endpoint() -> str:
    from ..desktop import settings
    return (os.environ.get("HF_ENDPOINT") or settings.load()["catalogue"].get("hf_endpoint") or "https://huggingface.co").rstrip("/")


def urls_for(hosting: dict, path: str) -> list[str]:
    """Every address the file can be fetched from, primary first."""
    out = []
    for h in [hosting.get("primary")] + list(hosting.get("mirrors") or []):
        if not h:
            continue
        q = quote(path)
        if h["type"] == "huggingface":
            out.append(f"{hf_endpoint()}/{h['repo']}/resolve/{quote(str(h.get('revision') or 'main'))}/{q}")
        elif h["type"] == "github":
            out.append(f"https://github.com/{h['repo']}/releases/download/{quote(str(h['tag']))}/{quote(path.rsplit('/', 1)[-1])}")
        elif h["type"] == "https":
            out.append(f"{h['base_url'].rstrip('/')}/{q}")
    return out


def _headers(url: str) -> dict:
    h = {"User-Agent": "mpn-studio"}
    tok = os.environ.get("HF_TOKEN")
    if tok and url.startswith(hf_endpoint()):          # only to the hosting itself, never to a redirect target
        h["Authorization"] = f"Bearer {tok}"
    return h


def _explain(e: Exception, url: str) -> DownloadError:
    host = httpx.URL(url).host
    if isinstance(e, httpx.TimeoutException):
        return DownloadError("timeout", f"{host} did not answer in time.")
    if isinstance(e, httpx.ConnectError):
        return DownloadError("unreachable", f"Cannot reach {host}. Check the connection and the address.")
    return DownloadError("unreachable", f"The download from {host} was interrupted ({type(e).__name__}).")


def sha256_file(p: Path, progress: Callable[[int], None] | None = None) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


def fetch_bytes(url: str, timeout: float = 20, limit: int = 16 << 20) -> bytes:
    """A small document (a manifest or a catalogue). Raises DownloadError with a readable message."""
    try:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(timeout, connect=10)) as c:
            with c.stream("GET", url, headers=_headers(url)) as r:
                if r.status_code in (401, 403):
                    raise DownloadError("auth", f"{httpx.URL(url).host} refused access (HTTP {r.status_code}). Private repositories need HF_TOKEN.", 401)
                if r.status_code == 404:
                    raise DownloadError("not_found", f"Not found: {url}", 404)
                if r.status_code >= 400:
                    raise DownloadError("http", f"{httpx.URL(url).host} answered HTTP {r.status_code}.")
                buf = bytearray()
                for ch in r.iter_bytes():
                    buf += ch
                    if len(buf) > limit:
                        raise DownloadError("http", "The document is larger than expected.")
                return bytes(buf)
    except httpx.HTTPError as e:
        raise _explain(e, url)


def fetch_file(urls: list[str], dest: Path, size: int, sha256: str, progress: Callable[[int], None] | None = None,
               cancel: threading.Event | None = None, retries: int = 3, timeout: float = 30) -> Path:
    """Download one file to `dest`, resuming `dest.part`. Returns dest once its size and SHA-256 are right."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if dest.exists():
        if dest.stat().st_size == size and sha256_file(dest) == sha256:
            if progress:
                progress(size)
            return dest
        dest.unlink()
    if not urls:
        raise DownloadError("not_found", f"No address is known for {dest.name}.")
    last: DownloadError | None = None
    for url in urls:
        for attempt in range(retries):
            if cancel is not None and cancel.is_set():
                raise DownloadError("cancelled", "Cancelled. The part already downloaded is kept, so the next attempt continues from it.", 499)
            try:
                _one(url, part, size, sha256, progress, cancel, timeout)
                os.replace(part, dest)
                return dest
            except DownloadError as e:
                last = e
                if e.code in ("cancelled", "checksum_mismatch", "size_mismatch"):
                    if e.code != "cancelled":
                        part.unlink(missing_ok=True)
                    if e.code == "cancelled":
                        raise
                    break                                  # a damaged copy: try the next mirror, not the same one again
                if e.code in ("not_found", "auth", "http", "disk_space"):
                    break
                time.sleep(min(2.0 * (attempt + 1), 5.0) if os.environ.get("MPN_TEST_NO_SLEEP") is None else 0)
    raise last or DownloadError("unreachable", f"Could not download {dest.name}.")


def _one(url: str, part: Path, size: int, sha256: str, progress, cancel, timeout: float) -> None:
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    h = hashlib.sha256()
    if have:
        with part.open("rb") as f:
            while True:
                b = f.read(CHUNK)
                if not b:
                    break
                h.update(b)
    done = have
    if progress and have:
        progress(have)
    if have < size:
        headers = _headers(url)
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(timeout, connect=10)) as c:
                with c.stream("GET", url, headers=headers) as r:
                    if r.status_code == 416:
                        part.unlink(missing_ok=True)
                        raise DownloadError("unreachable", "The server could not continue the download; starting again.")
                    if r.status_code in (401, 403):
                        raise DownloadError("auth", f"{r.url.host} refused access (HTTP {r.status_code}). A private repository needs HF_TOKEN.", 401)
                    if r.status_code == 404:
                        raise DownloadError("not_found", f"{r.url.host} does not have {part.name[:-5]}.", 404)
                    if r.status_code >= 400:
                        raise DownloadError("http", f"{r.url.host} answered HTTP {r.status_code}.")
                    if have and r.status_code == 200:       # the server ignored the Range header: start again from byte 0
                        if progress:
                            progress(-have)
                        have, done, h = 0, 0, hashlib.sha256()
                    elif have and r.status_code == 206:
                        cr = r.headers.get("content-range", "")
                        if not cr.startswith(f"bytes {have}-"):
                            raise DownloadError("unreachable", "The server answered a different range than asked for.")
                    with part.open("ab" if have else "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):     # small, so an interrupted body keeps what arrived
                            if cancel is not None and cancel.is_set():
                                f.flush()
                                raise DownloadError("cancelled", "Cancelled. The part already downloaded is kept, so the next attempt continues from it.", 499)
                            f.write(chunk)
                            h.update(chunk)
                            done += len(chunk)
                            if progress:
                                progress(len(chunk))
                            if done > size:
                                break
                        f.flush()
                        os.fsync(f.fileno())
        except httpx.HTTPError as e:
            raise _explain(e, url)
        except OSError as e:
            if e.errno == 28:
                raise DownloadError("disk_space", "The disk is full.", 507)
            raise
    if done > size:
        part.unlink(missing_ok=True)
        raise DownloadError("size_mismatch", f"{part.name[:-5]} is larger than the manifest says ({done} bytes, expected {size}).")
    if done < size:
        raise DownloadError("unreachable", f"The connection closed after {fmt_bytes(done)} of {fmt_bytes(size)}.")
    if h.hexdigest() != sha256:
        raise DownloadError("checksum_mismatch", f"{part.name[:-5]} does not match its SHA-256 (got {h.hexdigest()[:16]}…, expected {sha256[:16]}…). It was not installed.")
