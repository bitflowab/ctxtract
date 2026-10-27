#!/usr/bin/env python3
"""Test: lokalt dokument + riktig webbsida, visa besparingen."""
import json
import subprocess
import sys

# Test A: lokalt dokument (garanterat)
doc = ("Smart contracts ar program som kors automatiskt pa blockkedjan nar villkor uppfylls. "
       "De skrivs i Solidity och granskas av alla i natverket.\n\n"
       + "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 30 + "\n\n"
       "Ethereum anvander gasavgifter for att betala berakning. Vid hojd trafik blir avgifterna dyrare, "
       "och skalbarhetslosningar som lag-tva-kedjor kan ska ner kostnaden.\n\n"
       + "Sed ut perspiciatis unde omnis iste natus error sit voluptatem. " * 30)
open("/tmp/testdoc.txt", "w").write(doc)
res = subprocess.run([sys.executable, "compressor.py", "-q", "vad ar smarta kontrakt och gasavgifter",
                      "-f", "/tmp/testdoc.txt", "-b", "800"], capture_output=True, text=True, timeout=300)
out = json.loads(res.stdout)
s = out["stats"]
print("TEST A (lokalt): original", s["original_chars"], "tecken -> behallet", s["kept_chars"], "=", s["kept_pct"], "%")
print("BESPARING: ~", 100 - s["kept_pct"], "% av tokens | chunkar:", s["chunks_total"], "->", s["chunks_selected"])
hits = sum(1 for e in out["excerpts"] if "smart contract" in e["text"].lower() or "gasavgift" in e["text"].lower())
print("RELEVANS:", hits, "av", len(out["excerpts"]), "utdrag traffar fragan")

# Test B: riktig webbsida (basta effort)
try:
    res = subprocess.run([sys.executable, "compressor.py", "-q", "vad ar smarta kontrakt och ether",
                          "-u", "https://en.wikipedia.org/wiki/Ethereum", "-b", "3000"],
                         capture_output=True, text=True, timeout=300)
    out = json.loads(res.stdout)
    s = out["stats"]
    print("TEST B (wikipedia): original", s["original_chars"], "-> behallet", s["kept_pct"], "% (~", 100 - s["kept_pct"], "% besparing)")
    print("--- forsta utdraget ---")
    print(out["excerpts"][0]["text"][:300])
except Exception as e:
    print("TEST B: hoppade over (natverk/sida):", str(e)[:120])
