# CtxTract — context compression for AI agents

Give your agent the 20% of the document that actually answers the question.

CtxTract turns a **query + document (or URL)** into **exact, source-verified excerpts** —
never rewritten, never paraphrased — at a fraction of the token cost.
Benchmarked at **~82% fewer tokens** (typical range 80–95%).

## Why

Long documents blow up token budgets and bury the answer. Most "summarizers" rewrite
the text, which means an LLM downstream can no longer trust it. CtxTract keeps only
the passages that are relevant to the query and returns them **verbatim** with
character offsets, so every claim can be checked against the source.

## How it works

1. **Chunk** the document by paragraphs, keeping character positions.
2. **Keyword pre-filter** (only for documents > 50 chunks): rank chunks by term
   overlap, send only the top 100 to embeddings.
3. **Embed** the query and candidate chunks with **bge-m3** via a local Ollama
   instance (no API cost, no data leaves the machine).
4. **Rank** chunks by cosine similarity against the query.
5. **Select** up to a character budget and return the excerpts as JSON:
   verbatim text + source position + similarity score + estimated tokens.

## Requirements

- Python 3 (standard library only — no pip installs)
- [Ollama](https://ollama.com) with the `bge-m3` model: `ollama pull bge-m3`

## Usage

```bash
# From a local file
python3 compressor.py -q "what are smart contracts" -f doc.txt

# From a URL (SSRF-hardened fetcher)
python3 compressor.py -q "what are smart contracts" -u https://en.wikipedia.org/wiki/Ethereum

# Adjust the output budget (default 4000 chars ≈ 1000 tokens)
python3 compressor.py -q "..." -f doc.txt --budget 4000
```

## Output

JSON with the excerpts and savings statistics:

```json
{
  "query": "what are smart contracts",
  "excerpts": [
    {"text": "A smart contract is ...", "start": 1204, "end": 2310,
     "score": 0.81, "est_tokens": 276}
  ],
  "stats": {
    "chunks_total": 84, "chunks_selected": 5,
    "original_est_tokens": 23000, "kept_est_tokens": 4100,
    "kept_pct": 17.8
  }
}
```

## Safety

URL fetching is hardened against SSRF:

- every resolved IP is validated (only public, global addresses)
- DNS-rebinding protection by pinning the verified IP before connecting
- every redirect is re-validated (max 5)
- CGNAT range 100.64.0.0/10 blocked in addition to RFC 1918
- fetch capped at 2 MB
- set `COMPRESSOR_ALLOW_PRIVATE=1` to allow private hosts for local testing

## Tests

```bash
python3 test_compressor.py   # extraction correctness
python3 test_security.py     # SSRF / fetch hardening
python3 stress_test.py       # full-pipeline stress run
```

## Roadmap

- MCP tool interface (drop CtxTract into any agent runtime)
- Hosted premium tier

## License

MIT
