# coreytrading — Agent Entry Point

This is a compatibility entry point, not a second project contract.

Before doing any work in this repository, read `CLAUDE.md` in full with your file-reading tool. `CLAUDE.md` is the authoritative, detailed project contract even when the active agent is not Claude Code. Follow it together with explicit user instructions and any more specific instruction file that applies to the files being changed.

When the task is to initialize a clone, configure infrastructure, or construct a private workspace, also read `AI_BOOTSTRAP.md` before making changes. When the task touches private trading data, continue with `trade/README.md` and every relevant descendant `README.md`.

Do not rename, delete, summarize, or duplicate `CLAUDE.md` into this file. Keeping one complete contract prevents instruction drift and preserves Claude Code compatibility.

The live pipeline is read/sync/plan only. Never infer private facts, expose credentials, register scheduled tasks, send messages, or place, modify, or cancel an order without the authorization required by `CLAUDE.md` and the user.
