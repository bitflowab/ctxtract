#!/usr/bin/env python3
"""Kontextkompressor v1.

query + dokument -> relevanta utdrag med kallhanvisning.
Ren stdlib; embeddings via lokal Ollama (bge-m3). 0 kr i drift.

Anv: python3 compressor.py -q "fraga" -f dokument.txt [--budget 4000]
     python3 compressor.py -q "fraga" -u https://exempel.se [--budget 4000]
"""
import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import sys
import urllib.parse
import urllib.request

EMBED_URL = "http://localhost:11434/api/embed"
MODEL = "bge-m3"


def chunk_document(text, max_chars=1200):
    """Dela upp i chunkar (stycken) med positionssparing."""
    spans, last = [], 0
    for m in re.finditer(r"\n\s*\n", text):
        spans.append((last, m.start()))
        last = m.end()
    spans.append((last, len(text)))
    chunks, buf = [], []
    buf_chars, buf_start = 0, None
    for s, e in spans:
        para = text[s:e]
        if not para.strip():
            continue
        if buf_start is None:
            buf_start = s
        if buf_chars + len(para) > max_chars and buf:
            chunks.append({"text": "".join(buf).strip(), "start": buf_start, "end": buf_start + buf_chars})
            buf, buf_chars, buf_start = [], 0, s
        buf.append(para)
        buf_chars += len(para)
    if buf:
        chunks.append({"text": "".join(buf).strip(), "start": buf_start, "end": buf_start + buf_chars})
    return chunks


def embed(texts):
    """Embeddings via lokal Ollama."""
    payload = json.dumps({"model": MODEL, "input": texts}).encode("utf-8")
    req = urllib.request.Request(EMBED_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)["embeddings"]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def est_tokens(s):
    return max(1, len(s) // 4)


def keyword_prefilter(query, chunks, max_to_embed=100):
    """Rangorda chunkar mot frågan med enkel nyckelords-matching.

    Används bara vid stora dokument (>50 chunkar) för att skicka färre
    texter till embeddings. Returnerar högst max_to_embed chunkar.
    """
    terms = set(w for w in re.findall(r"[a-z0-9åäö]+", query.lower()) if len(w) > 2)

    def kw_score(chunk):
        text = chunk["text"].lower()
        return sum(text.count(t) for t in terms)

    ranked = sorted(range(len(chunks)), key=lambda i: (-kw_score(chunks[i]), i))
    keep = set(ranked[:max_to_embed])
    return [c for i, c in enumerate(chunks) if i in keep]


def compress(query, document, budget_chars=4000):
    chunks = chunk_document(document)
    if not chunks:
        return {"query": query, "excerpts": [], "stats": {"original_chars": 0}}
    candidates = chunks
    if len(chunks) > 50:
        candidates = keyword_prefilter(query, chunks)
    embs = embed([query] + [c["text"] for c in candidates])
    qvec, cvecs = embs[0], embs[1:]
    scored = sorted(((cosine(qvec, v), c) for c, v in zip(candidates, cvecs)), key=lambda p: p[0], reverse=True)
    selected, used = [], 0
    for score, c in scored:
        if used >= budget_chars:
            break
        take = min(len(c["text"]), budget_chars - used)
        excerpt = c["text"][:take]
        selected.append({"text": excerpt, "start": c["start"], "end": c["start"] + take,
                         "score": round(score, 3), "est_tokens": est_tokens(excerpt)})
        used += take
    selected.sort(key=lambda x: x["start"])
    kept = sum(len(x["text"]) for x in selected)
    return {
        "query": query,
        "excerpts": selected,
        "stats": {
            "chunks_total": len(chunks),
            "chunks_selected": len(selected),
            "original_chars": len(document),
            "original_est_tokens": est_tokens(document),
            "kept_chars": kept,
            "kept_est_tokens": est_tokens("".join(x["text"] for x in selected)),
            "kept_pct": round(100 * kept / max(1, len(document)), 1),
        },
    }


ALLOW_PRIVATE = os.environ.get("COMPRESSOR_ALLOW_PRIVATE") == "1"
MAX_FETCH_BYTES = 2000000
MAX_REDIRECTS = 5
ALLOWED_FILE_ROOTS = ("/home/openclaw/projects", "/tmp")

# CGNAT/delat utrymme – passerar ipaddress.is_private men ar inte publikt.
BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


def _resolve_all(host):
    """Slag upp alla adresser for host; uppslagsfal tolkas som blockerat."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("kunde inte sla upp varden (blockerat)")
    addrs = [i[4][0] for i in infos]
    if not addrs:
        raise ValueError("kunde inte sla upp varden (blockerat)")
    return addrs


def _ip_is_blocked(ip):
    """Blockera allt som inte ar en publik, global adress (SSRF-skydd)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in net for net in BLOCKED_NETWORKS):
        return True
    return not ip.is_global


def _is_private_host(host):
    """True om host (namn eller IP) pekar pa nagot annat an publik adress."""
    try:
        return any(_ip_is_blocked(ipaddress.ip_address(a)) for a in _resolve_all(host))
    except ValueError:
        return True


def _resolve_and_pin(host, port):
    """Slag upp host, validera ALLA adresser, pinna forsta (mot DNS-rebinding)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("kunde inte sla upp varden (blockerat)")
    if not infos:
        raise ValueError("kunde inte sla upp varden (blockerat)")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ALLOW_PRIVATE and _ip_is_blocked(ip):
            raise ValueError("privata adresser ar blockerade (satt COMPRESSOR_ALLOW_PRIVATE=1 for lokala tester)")
    return infos[0][4][0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Ansluter till ett forhand verifierat IP (DNS-rebinding-skydd)."""

    def __init__(self, host, port, ip, timeout=60):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Samma som ovan men med TLS; SNI/cert valideras mot vrdnamnet."""

    def __init__(self, host, port, ip, timeout=60):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def safe_file_path(path):
    """Tillat bara filer under godkanda rotkataloger (skyddar hemligheter)."""
    real = os.path.realpath(path)
    if not any(real == root or real.startswith(root + os.sep) for root in ALLOWED_FILE_ROOTS):
        raise ValueError("filen ligger utanfor godkanda kataloger")
    return real


def fetch_url(url):
    """Hamta sida med validering per omdirigering + pinnat IP per anrop."""
    current = url
    html = None
    for _hop in range(MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlparse(current)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("bara http och https tillats")
        host = parsed.hostname
        if not host:
            raise ValueError("ogiltig adress")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ip = _resolve_and_pin(host, port)  # validerar alla adresser, pinna forsta
        target = parsed.path or "/"
        if parsed.params:
            target += ";" + parsed.params
        if parsed.query:
            target += "?" + parsed.query
        if parsed.scheme == "https":
            conn = _PinnedHTTPSConnection(host, port, ip)
        else:
            conn = _PinnedHTTPConnection(host, port, ip)
        try:
            conn.request("GET", target, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ContextCompressor/0.1)",
                "Accept": "*/*",
            })
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                if not loc:
                    raise ValueError("omdirigering utan mal")
                current = urllib.parse.urljoin(current, loc)
                continue  # nasta hopp valideras innan anslutning
            if resp.status >= 400:
                raise ValueError("hamtning misslyckades (HTTP %d)" % resp.status)
            html = resp.read(MAX_FETCH_BYTES).decode("utf-8", errors="replace")
            break
        finally:
            conn.close()
    if html is None:
        raise ValueError("for manga omdirigeringar")
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def main():
    ap = argparse.ArgumentParser(description="Kontextkompressor: query + dokument -> relevanta utdrag med kallor")
    ap.add_argument("-q", "--query", required=True)
    ap.add_argument("-f", "--file", help="textfil")
    ap.add_argument("-u", "--url", help="webbadress")
    ap.add_argument("-b", "--budget", type=int, default=4000, help="teckenbudget for utdrag (default 4000)")
    args = ap.parse_args()
    try:
        if args.file:
            with open(safe_file_path(args.file), encoding="utf-8") as fh:
                doc = fh.read()
            source = args.file
        elif args.url:
            doc = fetch_url(args.url)
            source = args.url
        else:
            ap.error("ange --file eller --url")
    except (ValueError, OSError) as exc:
        print("SAKERHETSBLOCKERING: " + str(exc), file=sys.stderr)
        sys.exit(2)
    out = compress(args.query, doc, budget_chars=args.budget)
    out["source"] = source
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
