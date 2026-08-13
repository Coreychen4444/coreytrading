# TradingOps — AI Bootstrap and Infrastructure Guide

[中文版](AI_BOOTSTRAP_CN.md)

This document is an execution guide for a coding agent taking a fresh clone from source to a verified local workspace. It covers project infrastructure and private-workspace construction. It does not replace the behavioral, architectural, or trading rules in `CLAUDE.md`.

The safe default is an offline, synthetic-data environment. Broker connectivity, real account state, notifications, scheduled tasks, external services, and live execution are separate opt-in capabilities.

## Target outcome

A completed bootstrap has all of the following properties:

- the agent has loaded the complete project contract;
- the repository uses an isolated supported Python environment;
- package and test dependencies are installed from `pyproject.toml`;
- offline tests pass without broker connectivity or private data;
- only the private runtime files needed for the requested workflow exist;
- secrets and private runtime data remain local and ignored by Git;
- every optional external integration is either verified or explicitly reported as not configured;
- no order, notification, task registration, commit, push, or other external side effect occurs implicitly.

Bootstrap is not complete merely because dependencies install. The agent must run the verification gate and produce the handoff report defined below.

## Agent instruction files: do not rename `CLAUDE.md`

There is no universal project-instruction filename across coding agents. Renaming `CLAUDE.md` to `AGENTS.md` would make the project less compatible, not more compatible.

Verified discovery behavior:

| Agent/runtime | Default project instruction | Repository policy |
|---|---|---|
| Claude Code | `CLAUDE.md` | Read the complete root file directly. Keep it in place. |
| OpenAI Codex | `AGENTS.md` | Use the committed `AGENTS.md` entry point, then read `CLAUDE.md` in full. |
| Gemini CLI | `GEMINI.md` | Configure its context filename or create a small adapter that loads `CLAUDE.md`; do not rename the original. |
| GitHub Copilot | Support depends on the Copilot surface; several surfaces recognize `AGENTS.md`, `CLAUDE.md`, or both | Inspect the active instruction list and avoid adding duplicated or conflicting rules. |
| Unknown agent | Vendor-specific | Check the agent's official instruction-discovery documentation, create a small adapter if required, and point it to `CLAUDE.md`. |

Official references:

- [Codex custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md/)
- [Claude Code project memory and CLAUDE.md](https://code.claude.com/docs/en/memory)
- [Gemini CLI context with GEMINI.md](https://geminicli.com/docs/cli/gemini-md/)
- [GitHub Copilot custom-instruction support](https://docs.github.com/en/copilot/reference/custom-instructions-support)

### Single-source policy

- `CLAUDE.md` is the complete, authoritative project contract.
- `AGENTS.md` is a short compatibility entry point. It must direct the agent to read `CLAUDE.md`; it must not contain a copied contract.
- Codex limits automatically discovered project instructions to 32 KiB by default. The complete contract is intentionally larger, so the thin `AGENTS.md` entry point must trigger an explicit full-file read instead of copying or truncating it.
- Tool-specific adapters must remain small and must not redefine architecture, formulas, privacy rules, or trading behavior.
- If an adapter and `CLAUDE.md` disagree, stop and repair the adapter. Do not choose one silently.
- Do not rely on a symlink unless every supported operating system and agent is known to handle it correctly.

For a Gemini-only local environment, a minimal uncommitted adapter may use Gemini's documented import syntax:

```markdown
# Project instructions

@./CLAUDE.md
```

Before adding another committed instruction file, check whether any supported agent would load both files and duplicate the same context.

## Required reading order

Before writing files or running a mutating command, the agent must read:

1. the instruction file automatically selected by the active agent;
2. root `CLAUDE.md` in full;
3. this file;
4. `quant/README.md` for package layering and dependency direction;
5. `trade/README.md` plus relevant descendant READMEs before creating private state;
6. the relevant `.claude/rules/` and `.claude/skills/*/SKILL.md` for the requested capability;
7. `tasks/README.md` before discussing or registering scheduled tasks.

Do not load every skill or every private-workspace example preemptively. Read the material relevant to the requested capability, after loading the project-wide contract.

## Phase 0 — Preflight and scope

First establish facts without changing the workspace:

1. Confirm the working directory is the intended Git repository root.
2. Inspect `git status --short` and preserve all existing user changes.
3. Detect the operating system and available Python versions.
4. Classify the requested bootstrap mode from the table below.
5. List required external access and identify which steps need user authorization.
6. Report blockers, but continue with safe offline setup when optional services are unavailable.

| Mode | Included | Excluded by default |
|---|---|---|
| Documentation/schema | Read contracts and inspect examples | Dependency installation, private files, network access |
| Offline development | Editable package, test tooling, synthetic tests | `.env`, OpenD, broker calls, notifications, scheduled tasks |
| Private workspace | Offline development plus selected private files copied from examples | Invented holdings, bulk example copying, external data fetches |
| Broker-connected | Private workspace plus approved OpenD and broker SDK configuration | Live trading and SDK trade unlock |
| Operations | Verified broker-connected setup plus selected reports or task prompts | Automatic task registration, messaging, or order execution |

If the user did not specify a mode, use Offline development. Ask only for facts or authorization that materially affect an optional next phase.

## Phase 1 — Python infrastructure

### Supported runtime

`pyproject.toml` is the source of truth for the minimum Python version and dependencies. Do not copy version pins into a new requirements file or silently upgrade broker packages.

Use a repository-local virtual environment unless the user has supplied another isolated environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

On Windows PowerShell, activation is normally:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Rules:

- use `python -m pip` from the selected interpreter rather than an ambiguous global `pip`;
- do not install into the system interpreter when an isolated environment can be used;
- do not change dependency versions merely to make installation easier;
- if the environment already exists, inspect it before replacing or upgrading it;
- if package download requires network permission, request that permission explicitly;
- report the interpreter path and Python version in the handoff.

For an application-only install, `python -m pip install -e .` is sufficient. The `dev` extra is the canonical test environment. The `broker` extra installs broker support without the test-only package. Exact pins remain in `pyproject.toml`.

## Phase 2 — Offline verification baseline

Run the contract and privacy checks first because they fail quickly and do not require external services:

```bash
python -m pytest -q \
  tests/test_project_contract.py \
  tests/test_ai_bootstrap.py \
  tests/test_trade_scaffold.py \
  tests/test_public_sanitization.py
```

Then run the complete suite:

```bash
python -m pytest -q
git diff --check
```

Offline tests must use synthetic fixtures and temporary paths. A test that reads live files under `trade/` is a bootstrap failure, even if it passes locally. Broker-dependent smoke checks must remain separate from the offline suite.

When tests fail, distinguish among:

- a pre-existing failure present before the bootstrap;
- an environment/dependency failure;
- a failure introduced by the agent;
- an optional integration that is intentionally not configured.

Do not hide a failure by deleting a test, weakening a guard, reading private data, or installing unrelated packages.

## Phase 3 — Private trading workspace

Enter this phase only when the user requests a workflow that needs private state.

`trade/` is a schema and contract tree, not a bundled strategy or sample portfolio. Public files below it are limited to `README.md` and synthetic `*.example.*` files. Generated runtime files are private.

Procedure:

1. Read `trade/README.md` and every relevant descendant `README.md`.
2. Identify the minimum runtime files required by the requested workflow.
3. Copy only those examples, removing `.example` from the destination filename.
4. Preserve field names and data types.
5. Replace synthetic values only with user-supplied facts or data from an explicitly authorized source.
6. Keep unknown values `null`, empty, or listed for review; never turn unknown into zero.
7. Validate JSON or JSONL syntax and the applicable schema checks.
8. Confirm the generated paths are ignored by Git.

Typical minimal commands, used only when those files are required, are:

```bash
cp trade/us_stock/holding/state/positions.example.json \
  trade/us_stock/holding/state/positions.json
cp trade/us_stock/holding/state/watchlist.example.json \
  trade/us_stock/holding/state/watchlist.json
git check-ignore -v \
  trade/us_stock/holding/state/positions.json \
  trade/us_stock/holding/state/watchlist.json
```

Do not copy every example. In particular, research cards, journals, market caches, option policies, and strategy rules are created only for an explicit use case. Example strategy documents are schemas, not active rules or recommendations.

Ownership remains strict:

- broker synchronization owns broker-derived facts;
- controlled agent/user workflows own thesis, zones, reviewed actions, and private rules;
- decision modules own generated guard/plan state identified in the descendant README;
- append-only event history is corrected with a new event, not silently rewritten.

## Phase 4 — Secrets and local configuration

No secrets are required for offline development.

For an approved broker-connected setup:

```bash
cp .env.example .env
```

Populate `.env` locally. Do not echo secret values into terminal output, test logs, chat, patches, reports, or documentation. Existing process environment variables take precedence over `.env` through `quant/core/env.py`.

The current template separates:

- OpenD host, port, encryption mode, and private-key path;
- optional Telegram notification credentials;
- optional external-data credentials.

Rules:

- keep private keys outside the repository and use an absolute local path;
- never write credentials into `opend/OpenD.xml.reference`;
- never commit a populated `.env`, `.mcp.json`, OpenD configuration, broker export, or key;
- do not create placeholder secrets that look usable;
- if a required value is unknown, leave it unresolved and report it.

`.mcp.json.example` is intentionally empty. MCP is not required for the application. Add a local `.mcp.json` only for a user-approved integration, keep credentials in environment variables, and document the trust boundary before enabling tools that can mutate external systems.

## Phase 5 — Optional OpenD and broker infrastructure

OpenD is optional. Enter this phase only with explicit authorization for broker connectivity.

1. Read `.claude/rules/tools-environment.md`.
2. If the active agent supports project skills, read `.claude/skills/install-moomoo-opend/SKILL.md` before installing or upgrading OpenD.
3. Review `opend/README.md` and the appropriate OpenAPI legal notice.
4. Install or start the OpenD GUI using the provider's current official distribution.
5. Keep the listener local unless the user explicitly designs and secures remote access.
6. Copy the sanitized reference configuration to OpenD's external configuration location; populate it only there.
7. Align the broker SDK and OpenD versions according to the repository's pinned dependency contract and skill checks.
8. Verify connectivity with a read-only account or quote operation before any state synchronization.
9. Keep broker smoke checks outside the offline test gate.

Hard safety boundaries:

- never unlock trading through the SDK;
- never place, modify, or cancel an order as part of setup or verification;
- never assume paper trading merely from a default; verify the actual environment;
- never expose OpenD beyond localhost without an explicit network/security design;
- never copy a populated OpenD configuration back into the repository.

If OpenD is absent, offline development remains a valid completed mode. Report broker connectivity as not configured rather than treating it as a failed core setup.

## Phase 6 — Optional operations infrastructure

### Notifications

Notification modules are optional external side effects. Configure them only after credentials, destination, message scope, and test-message authorization are explicit. A successful local render is not authorization to send it.

### Scheduled tasks

`tasks/` contains reusable prompts, not an active schedule. Before registration:

1. read `tasks/README.md` and the selected prompt;
2. confirm timezone, market calendar, daylight-saving behavior, working directory, environment, and notification destination;
3. verify the underlying command manually in a non-trading mode;
4. show the exact schedule and prompt to the user;
5. obtain authorization to create or change the task.

Never register all tasks automatically. Never infer that a prompt file is already installed in the user's task runner.

### CI

A CI environment should install the declared development dependencies and run the offline verification gate only. It must not receive broker credentials, access private `trade/` runtime files, start OpenD, register tasks, send notifications, or exercise order scripts. CI configuration is a separate repository change and requires normal review.

## Data and time readiness

Before running an operational report, distinguish installation readiness from data readiness. Confirm:

- required private files exist and pass schema validation;
- timestamps include an offset or `Z` and are normalized before session rules;
- market-data source, permission, freshness, and timezone are known;
- broker state has been reconciled for the requested mode;
- missing data produces a fail-closed result rather than fabricated continuity;
- task timing accounts for the operator timezone, exchange calendar, and daylight-saving changes.

An installed system with stale or synthetic market data is not operationally ready.

## Mutation and recovery discipline

- Inspect existing files and Git state before changing anything.
- Preserve unrelated user changes and private runtime data.
- Prefer atomic writers and shared locks defined by the project for mutable state.
- Do not use destructive Git commands or broad recursive deletion during setup.
- Back up a material private state file before an authorized migration.
- One-off migrations must default to dry-run and require an explicit apply step.
- Do not stage, commit, push, release, notify, or register tasks unless the user requested that action.

## Completion gate and handoff report

At the end, give the user a compact report with this exact information:

```text
Bootstrap mode:
Agent/runtime and instruction files loaded:
Repository root and existing-change status:
Python executable and version:
Installed dependency set:
Private runtime files created or changed (paths only):
Secrets/configuration status (never values):
OpenD/broker status:
Notifications/tasks/MCP status:
Targeted checks:
Full offline test result:
Unresolved inputs or approvals:
Recommended next safe command:
```

Report private file paths only; do not paste their contents. If setup stopped at an earlier mode, say so explicitly and list the optional phases that were not attempted.

## Fast path for an AI agent

For a standard fresh-clone request with no broker requirement, the correct fast path is:

1. read `AGENTS.md` or the native agent entry point;
2. read `CLAUDE.md` in full, then this file and `quant/README.md`;
3. inspect Git state and Python availability;
4. create or reuse an isolated environment;
5. install `.[dev]` from `pyproject.toml`;
6. run targeted contract/privacy tests, then the full offline suite;
7. do not create `.env`, private runtime files, MCP configuration, OpenD, notifications, or tasks;
8. return the structured handoff report.

That baseline is intentionally useful on its own. Expand into private or broker-connected infrastructure only when the requested workflow requires it.

## Common questions and answers

### Do I need to rename `CLAUDE.md` when I am not using Claude Code?

No. Keep `CLAUDE.md` because it is the complete project contract and Claude Code depends on that filename. Use the committed `AGENTS.md` for Codex, or create a small runtime-specific adapter that directs the agent to read `CLAUDE.md` in full. Never maintain two copied contracts.

### Which file should an AI read first?

Start with the file automatically discovered by the active agent. In this repository, Codex starts from `AGENTS.md` and Claude Code starts from `CLAUDE.md`. In every case, the agent must read the complete `CLAUDE.md` before acting. For setup work, continue with `AI_BOOTSTRAP.md`; for private data, continue with `trade/README.md` and the relevant descendant contracts.

### Why is `AGENTS.md` short while `CLAUDE.md` is detailed?

`AGENTS.md` is a discovery adapter. The detailed rules remain in one authoritative file so they cannot drift. Its small size also leaves room in agents that limit automatically loaded project instructions; the adapter explicitly tells the agent to read the complete contract with its file-reading tool.

### Must this project be used with Claude Code?

No. The application code, tests, schemas, and bootstrap process are agent-independent. Only automatic instruction discovery differs between tools. A non-Claude agent is acceptable if it loads and follows the complete project contract.

### Should an AI read every rule and skill during startup?

No. It must read the project-wide contract and this setup guide, then load only the rules, skills, task prompts, and descendant READMEs relevant to the requested work. Loading every task-specific resource wastes context and can introduce irrelevant instructions.

### Which bootstrap mode should be used when the request is vague?

Use Offline development. It provides a testable code environment without creating private files or connecting external systems. Expand to Private workspace, Broker-connected, or Operations only when the requested workflow requires it.

### Is OpenD required to install or test the project?

No. OpenD is optional. The offline suite must pass without OpenD, broker credentials, or live account data. OpenD is needed only for approved broker-connected workflows.

### May the AI install OpenD automatically during a general setup request?

No. OpenD installation, GUI launch, login, configuration, and network access belong to the optional broker phase. Confirm that broker connectivity is in scope and follow the relevant rule and skill before changing that external infrastructure.

### What should happen when OpenD or a market-data service is unavailable?

Complete the safe offline phases, mark the optional integration as not configured or unavailable, and report the exact failed check. Do not fabricate broker state, replace live data with synthetic values, or weaken offline tests to hide the missing service.

### Can setup place a paper-trading order as a connectivity test?

No. Setup verification is read-only. Paper and live environments must both be verified explicitly, and neither is authorization to place, modify, or cancel orders. Trading unlock must never be performed through the SDK.

### Should all files under `trade/` be created during bootstrap?

No. Create only the minimum private files required by the requested workflow. A portfolio report may need positions and watchlist state; it does not automatically need research cards, journals, option policies, market caches, or strategy documents.

### Are `*.example.*` files starter data or active strategies?

No. They are synthetic schemas. They define shape, ownership, and expected runtime filenames, but they are not current market data, a sample portfolio to adopt, or recommended trading rules.

### May the AI edit an example file with real user data?

No. Copy the example to its private runtime filename and populate only the private copy. Public examples must remain synthetic and safe to commit.

### What if a required private value is unknown?

Keep it `null`, empty, or unresolved according to the schema and ask for the missing fact when it blocks the workflow. Never infer an account identifier, holding, cost, risk limit, authorization, strategy preference, or unknown numeric value.

### What if a private workspace already exists?

Treat the task as repair or migration, not initialization. Read and validate the existing files first, preserve user-authored and broker-owned fields, back up material state before an approved migration, and change only the minimum required paths. Never overwrite an existing runtime file by blindly copying an example.

### How can I verify that a generated private file will not be committed?

Use `git check-ignore -v <path>` and inspect `git status --short --untracked-files=all`. A private runtime file appearing as trackable output is a failure that must be fixed before continuing.

### Should `.env.example` be filled in directly?

No. It is a public key-name template. Copy it to the ignored `.env` only for an integration that needs it, and put local values in that private file. Never place real values in `.env.example`.

### Is `.mcp.json` required?

No. The repository has no mandatory MCP server. Create a local configuration only for an approved integration, keep credentials in environment variables, and review the tools' read/write authority before enabling them.

### What should the AI do when dependency installation conflicts with the system Python?

Stop using the system environment and create or select an isolated virtual environment. Install the versions declared by `pyproject.toml` with that interpreter. Do not solve a broker SDK or protobuf conflict by silently changing repository pins.

### Can the AI upgrade all dependencies to their latest versions during setup?

No. Setup should reproduce the declared environment. Dependency upgrades are separate code changes that require compatibility review and the full verification gate.

### Are skipped tests acceptable?

Only when the skip is an intentional repository behavior, such as an unavailable optional integration. Report the skip count and reason. A newly skipped contract, privacy, architecture, or synthetic behavior test is not a successful bootstrap.

### Can offline tests use the user's real `trade/` files for realism?

No. Offline tests must use synthetic fixtures and temporary directories. Reading live holdings or market caches creates privacy, reproducibility, and mutation risks.

### Should CI configure OpenD, secrets, notifications, or scheduled tasks?

No. CI runs the offline verification gate. Broker connectivity and operational side effects stay outside public CI unless a separate, explicitly reviewed private design establishes the required isolation.

### Can task prompt files be treated as an installed schedule?

No. `tasks/` versions reusable prompts only. Registration requires a separate check of timezone, market calendar, environment, destination, exact cadence, and user authorization.

### What if two instruction files conflict?

Stop and report the conflict. Explicit user instructions for the current task have priority within their valid scope; `CLAUDE.md` remains the repository contract; a tool-specific adapter must not redefine it. Repair stale adapters or prose and add a regression test instead of choosing silently.

### How does an AI know the system is operationally ready?

Installation readiness is not enough. The required private schemas must validate, data sources and timestamps must be fresh and authorized, broker state must be reconciled for the requested mode, offline tests must pass, optional connectivity checks must be reported, and unresolved approvals must be explicit.

### What information belongs in the final answer after setup?

Use the handoff report in this document. Include modes, paths, interpreter, dependency set, checks, integration status, and unresolved inputs. Mention private files by path only; never paste credentials, account data, holdings, journals, or private strategy content.

### May the AI commit or push after a successful bootstrap?

Only when the user requested that Git action. Passing tests authorizes neither staging nor publication. Review the diff and the public/private boundary before any separately authorized commit or push.
