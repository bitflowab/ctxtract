#!/usr/bin/env python3
"""Kontextkompressor v1.

query + dokument -> relevanta utdrag med kallhanvisning.
Ren stdlib; embeddings via lokal Ollama (bge-m3). 0 kr i drift.

Anv: python3 compressor.py -q "fraga" -f dokument.txt [--budget 4000]
     python3 compressor.py -q "fraga" -u https://exempel.se [--budget 4000]
"""
import argparse
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


def compress(query, document, budget_chars=4000):
    chunks = chunk_document(document)
    if not chunks:
        return {"query": query, "excerpts": [], "stats": {"original_chars": 0}}
    embs = embed([query] + [c["text"] for c in chunks])
    qvec, cvecs = embs[0], embs[1:]
    scored = sorted(((cosine(qvec, v), c) for c, v in zip(chunks, cvecs)), key=lambda p: p[0], reverse=True)
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
ALLOWED_FILE_ROOTS = ("/home/openclaw/projects", "/tmp")


def _is_private_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def safe_file_path(path):
    """Tillat bara filer under godkanda rotkataloger (skyddar hemligheter)."""
    real = os.path.realpath(path)
    if not any(real == root or real.startswith(root + os.sep) for root in ALLOWED_FILE_ROOTS):
        raise ValueError("filen ligger utanfor godkanda kataloger")
    return real


def fetch_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("bara http och https tillats")
    if not ALLOW_PRIVATE and _is_private_host(parsed.hostname or ""):
        raise ValueError("privata adresser ar blockerade (satt COMPRESSOR_ALLOW_PRIVATE=1 for lokala tester)")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ContextCompressor/0.1)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read(MAX_FETCH_BYTES).decode("utf-8", errors="replace")
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
