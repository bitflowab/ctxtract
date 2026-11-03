#!/usr/bin/env python3
"""Stresstest för kontextkompressorn – hela flödet (chunka -> prefilter -> embed -> ranka -> utdrag).

Faser:
  A. Storstort dokument (~1 MB, >50 chunkar) – nyckelords-prefilter aktiveras.
  B. Uthållighet – flera frågor i rad mot medeldokument, latenspercentiler.
  C. Samtidighet – flera trådar parallellt, felkvot och genomströmning.
  D. Edgefall under last – tomt dokument, ett enda jätteavsnitt, tom fråga.

Kör: python3 stress_test.py
"""
import json
import resource
import statistics
import sys
import threading
import time

sys.path.insert(0, "/home/openclaw/projects/context-compressor")
from compressor import compress, chunk_document

EVENTS = "/home/openclaw/projects/events.jsonl"

NEEDLE = "zqxvulkan42"
FILLER = (
    "Python är ett programmeringsspråk som ofta används för automation, "
    "dataanalys och maskininlärning. Biblioteket numpy hanterar arrayer "
    "medan pandas använder dataframes för tabeller. "
)


def make_doc(n_paras, needles=()):
    paras = [FILLER * 3 for _ in range(n_paras)]
    for i, needle in enumerate(needles):
        paras[(i + 1) * max(1, n_paras // (len(needles) + 1))] += (
            f" Hemligt nyckelord: {NEEDLE}{i}."
        )
    return "\n\n".join(paras)


def log_event(phase, **kv):
    with open(EVENTS, "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "type": "stress_test",
                            "phase": phase, **kv}) + "\n")


def pct(vals, p):
    if not vals:
        return 0.0
    vs = sorted(vals)
    return round(vs[min(len(vs) - 1, int(len(vs) * p / 100))], 2)


def main():
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    errors = []
    summary = {}

    # --- Fas A: stort dokument, prefilter-väg -----------------------------
    doc_a = make_doc(1800, needles=("0", "1"))  # ~1 MB
    chunks_a = len(chunk_document(doc_a))
    t0 = time.time()
    try:
        out_a = compress(f"vad är {NEEDLE}0", doc_a, budget_chars=4000)
        dt_a = round(time.time() - t0, 2)
        hit = any(NEEDLE in e["text"] for e in out_a["excerpts"])
        summary["A_stort"] = {"chunks": chunks_a, "sek": dt_a,
                              "vald": out_a["stats"]["chunks_selected"],
                              "nål_träff": hit}
    except Exception as e:
        errors.append(f"A: {e}")
        summary["A_stort"] = {"fel": str(e)}
    print("FAS A (1 MB, prefilter):", summary["A_stort"])

    # --- Fas B: uthållighet, sekventiellt ---------------------------------
    doc_b = make_doc(90, needles=("0",))
    lats = []
    for i in range(8):
        q = f"automation dataanalys numpy pandas fråga {i}"
        t0 = time.time()
        try:
            compress(q, doc_b, budget_chars=3000)
            lats.append(time.time() - t0)
        except Exception as e:
            errors.append(f"B{i}: {e}")
    summary["B_uthallighet"] = {
        "anrop": len(lats), "fel": 8 - len(lats),
        "snitt_s": round(statistics.mean(lats), 2) if lats else None,
        "p50_s": pct(lats, 50), "p95_s": pct(lats, 95),
        "max_s": pct(lats, 100),
    }
    print("FAS B (uthållighet):", summary["B_uthallighet"])

    # --- Fas C: samtidighet, 4 trådar x 2 anrop ---------------------------
    doc_c = make_doc(90, needles=("0",))
    lats_c, lock = [], threading.Lock()

    def worker(tid):
        for j in range(2):
            t0 = time.time()
            try:
                compress(f"samtidig fråga {tid}-{j}", doc_c, budget_chars=3000)
                with lock:
                    lats_c.append(time.time() - t0)
            except Exception as e:
                with lock:
                    errors.append(f"C{tid}-{j}: {e}")

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = round(time.time() - t0, 2)
    summary["C_samtidighet"] = {
        "tradar": 4, "anrop": len(lats_c), "fel": 8 - len(lats_c),
        "vaggtid_s": wall, "genomstromning_anrop_per_s":
            round(len(lats_c) / wall, 2) if wall else None,
        "p95_s": pct(lats_c, 95),
    }
    print("FAS C (samtidighet):", summary["C_samtidighet"])

    # --- Fas D: edgefall --------------------------------------------------
    edge = {}
    try:
        out = compress("fråga", "", budget_chars=1000)
        edge["tomt_dokument"] = "ok" if not out["excerpts"] else "oväntade utdrag"
    except Exception as e:
        edge["tomt_dokument"] = f"fel: {e}"
    try:
        huge = "Rad om python och automation. " * 20000  # ett enda avsnitt
        t0 = time.time()
        compress("automation", huge, budget_chars=1000)
        edge["jatteavsnitt"] = f"ok ({round(time.time() - t0, 1)} s)"
    except Exception as e:
        edge["jatteavsnitt"] = f"fel: {e}"
    try:
        out = compress("", "Text om python.\n\nText om java.", budget_chars=1000)
        edge["tom_fråga"] = "ok"
    except Exception as e:
        edge["tom_fråga"] = f"fel: {e}"
    summary["D_edgefall"] = edge
    print("FAS D (edgefall):", edge)

    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary["minne_max_mb"] = round(rss1 / 1024, 1)
    summary["fel_lista"] = errors
    print("MINNE (peak RSS):", summary["minne_max_mb"], "MB")
    print("TOTALT FEL:", len(errors), errors[:5])
    log_event("stress_test", summary=summary)

    with open("/home/openclaw/projects/context-compressor/stress_result.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
