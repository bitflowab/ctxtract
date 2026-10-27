# Kontextkompressor

Kokar ner en webbsida eller ett dokument till exakt det en AI-agent fragade efter.
Ren Python-stdlib + lokal Ollama (bge-m3) for embeddings. Driftkostnad: 0 kr.

## Anvandning
    python3 compressor.py -q "vad ar smarta kontrakt" -f doc.txt
    python3 compressor.py -q "vad ar smarta kontrakt" -u https://en.wikipedia.org/wiki/Ethereum

## Utdata
JSON med utdrag (text + kallposition + likhetspoang + est. tokens) och statistik
(hur mycket tokens som sparas jamfort med originalet).

## Test
    python3 test_compressor.py

Del av agentverktyg-programmet: kompressor -> MCP-verktyg -> agent-sokning.
