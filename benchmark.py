#!/usr/bin/env python3
"""Benchmark: kompressorn mot mangfaldiga dokument."""
import json
import sys
import time

sys.path.insert(0, "/home/openclaw/projects/context-compressor")
from compressor import compress, fetch_url

TESTS = [
    ("Wikipedia Ethereum (en)", "https://en.wikipedia.org/wiki/Ethereum",
     "vad ar smarta kontrakt och gasavgifter", ["contract"]),
    ("Wikipedia Whisper (en)", "https://en.wikipedia.org/wiki/Whisper_(speech_recognition_system)",
     "hur manga parametrar har modellerna", ["parameter"]),
    ("Python asyncio-dokumentation", "https://docs.python.org/3/library/asyncio.html",
     "hur skapar och hanterar man en event loop", ["loop"]),
    ("Svenska: Sverige", "https://sv.wikipedia.org/wiki/Sverige",
     "huvudstad och storsta stad", ["stockholm"]),
    ("Wikipedia Global warming", "https://en.wikipedia.org/wiki/Global_warming",
     "orsaker till vaxthusgasutslapp", ["emission", "greenhouse"]),
]

rows = []
for name, url, query, expect in TESTS:
    try:
        doc = fetch_url(url)
    except Exception as e:
        print(f"{name}: HAMT-FEL {str(e)[:70]}")
        continue
    t0 = time.time()
    out = compress(query, doc, budget_chars=3000)
    lat = round(time.time() - t0, 2)
    s = out["stats"]
    top = " ".join(e["text"] for e in out["excerpts"][:2]).lower()
    hits = sum(1 for k in expect if k.lower() in top)
    saving = round(100 - s["kept_pct"], 1)
    rows.append((name, saving, hits, len(expect), lat))
    print(f"{name}: original {s['original_chars']} tecken -> sparar {saving} % | traff {hits}/{len(expect)} | {lat} s")
    with open("/home/openclaw/projects/events.jsonl", "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "type": "benchmark", "name": name, "value": saving}) + "\n")

if rows:
    n = len(rows)
    print("---")
    print("SNITT: sparar " + str(round(sum(r[1] for r in rows)/n, 1)) + " % tokens | traff "
          + str(sum(r[2] for r in rows)) + "/" + str(sum(r[3] for r in rows))
          + " | latens " + str(round(sum(r[4] for r in rows)/n, 2)) + " s")
