# US-stock private workspace

This directory defines the local US-stock workspace that an AI can construct around the repository's code. Public files describe structure only; generated holdings, market data, research and strategies remain private.

## Initialization order for AI

1. Read `holding/README.md` and create only the runtime state required by the requested workflow.
2. Read `market/README.md` before fetching or caching market data; confirm that the provider and storage use are authorized.
3. Read `knowledge/README.md` when turning user-provided evidence into research cards.
4. Read `strategy/README.md` when turning the user's decisions into explicit rules. Examples are schemas, not recommendations.
5. Run schema/tests named by each README and report unresolved fields instead of inventing them.

Canonical path constants live in `quant/core/paths.py`. A generated private filename is normally the matching example filename with `.example` removed.
