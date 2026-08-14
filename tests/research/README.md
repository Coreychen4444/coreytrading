# Research tests

`tests/research/` validates only the reusable public mechanics in `quant/research/`.

- Fixtures are wholly synthetic.
- Tests never read private state, account exports, symbol lists, or market caches.
- Missing and insufficient evidence remain explicit outcomes.
- Private preregistrations and historical rulings stay outside Git.
- Research may import production code; production code may never import research.
