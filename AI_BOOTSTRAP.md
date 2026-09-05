# coreytrading — AI Bootstrap and Infrastructure Guide

[中文版](AI_BOOTSTRAP_CN.md)

This document is the execution guide for a coding agent taking a fresh clone from source to a verified local workspace. It covers project infrastructure, the private trading workspace, the optional broker connection and the optional operations layer (notifications, scheduled tasks, domain-agent runtimes). It does not replace the behavioral, architectural or trading rules in `CLAUDE.md`.

The safe default is an offline, synthetic-data environment. Broker connectivity, real account state, notifications, scheduled tasks, model runtimes and live execution are separate opt-in capabilities.

## Target outcome

A completed bootstrap has all of the following properties:

- the agent has loaded the complete project contract;
- the repository uses an isolated, supported Python environment with the dependencies declared in `pyproject.toml`, including the pinned exchange calendar;
- offline tests pass without broker connectivity, model CLIs or private data;
- only the private runtime files needed for the requested workflow exist, built from the examples under `trade/`;
- secrets, private runtime data, run artifacts under `var/` and the optional `ai_news/` workspace remain local and ignored by Git;
- every optional integration is either verified or explicitly reported as not configured;
- no order, notification, task registration, model call, commit, push or other external side effect happens implicitly.

Bootstrap is not complete merely because dependencies install. The agent must run the verification gate and produce the handoff report defined below.

## Agent instruction files: do not rename `CLAUDE.md`

There is no universal project-instruction filename across coding agents. Renaming `CLAUDE.md` to `AGENTS.md` would make the project less compatible, not more compatible.

| Agent/runtime | Default project instruction | Repository policy |
|---|---|---|
| Claude Code | `CLAUDE.md` | Read the complete root file directly. Keep it in place. |
| OpenAI Codex | `AGENTS.md` | Use the committed `AGENTS.md` entry point, then read `CLAUDE.md` in full. |
| Gemini CLI | `GEMINI.md` | Configure its context filename or create a small uncommitted adapter that imports `CLAUDE.md`; do not rename the original. |
| GitHub Copilot | Depends on the Copilot surface; several recognize `AGENTS.md`, `CLAUDE.md` or both | Inspect the active instruction list and avoid duplicated or conflicting rules. |
| Unknown agent | Vendor-specific | Check the agent's instruction-discovery documentation, create a small adapter if required, and point it to `CLAUDE.md`. |

### Single-source policy

- `CLAUDE.md` is the complete, authoritative project contract. It is compact and pointer-style; the method files it points to (`quant/README.md`, `agents/README.md`, `tasks/README.md`, `.claude/rules/`, `trade/README.md`) are the sources of truth for their subjects.
- `AGENTS.md` is a short compatibility entry point. It must direct the agent to read `CLAUDE.md`; it must not contain a copied contract.
- Tool-specific adapters must remain small and must not redefine architecture, formulas, privacy rules or trading behavior. If an adapter and `CLAUDE.md` disagree, stop and repair the adapter.
- The same contract applies to every model and runtime, including the domain-agent workers described in `agents/README.md`; a worker's research result never grants a permission the contract withholds.

## Required reading order

Before writing files or running a mutating command, the agent must read:

1. the instruction file automatically selected by the active agent;
2. root `CLAUDE.md` in full;
3. this file;
4. `quant/README.md` for package layering and dependency direction;
5. `trade/README.md` plus relevant descendant READMEs before creating private state;
6. `agents/README.md` before configuring or calling any domain-agent runtime;
7. the relevant `.claude/rules/` and `.claude/skills/*/SKILL.md` for the requested capability;
8. `tasks/README.md` before discussing or registering scheduled tasks.

Do not load every skill or every example preemptively. Read the material relevant to the requested capability, after loading the project-wide contract.

## Phase 0 — Preflight and scope

First establish facts without changing the workspace:

1. Confirm the working directory is the intended Git repository root.
2. Inspect `git status --short` and preserve all existing user changes.
3. Detect the operating system and available Python versions (`pyproject.toml` declares the minimum).
4. Classify the requested bootstrap mode from the table below.
5. List required external access and identify which steps need user authorization.
6. Report blockers, but continue with safe offline setup when optional services are unavailable.

| Mode | Included | Excluded by default |
|---|---|---|
| Documentation/schema | Read contracts and inspect examples | Dependency installation, private files, network access |
| Offline development | Editable package, test tooling, synthetic tests | `.env`, OpenD, broker calls, model CLIs, notifications, scheduled tasks |
| Private workspace | Offline development plus selected private files copied from examples | Invented holdings, bulk example copying, external data fetches |
| Broker-connected | Private workspace plus approved OpenD and broker SDK configuration | Live trading and SDK trade unlock |
| Operations | Verified broker-connected setup plus selected reports, task prompts and domain-agent runtimes | Automatic task registration, messaging, unreviewed card writes or order execution |

If the user did not specify a mode, use Offline development. Ask only for facts or authorization that materially affect an optional next phase.

## Phase 1 — Python infrastructure

### Supported runtime

`pyproject.toml` is the source of truth for the minimum Python version and dependencies: `numpy`, `pandas` and `exchange-calendars` (the XNYS session calendar behind every scheduled-task gate) for the application, the Moomoo SDK and its protobuf pin in the `broker` extra, and `pytest` in the `dev` extra. Do not copy version pins into a new requirements file or silently upgrade broker packages.

Use a repository-local virtual environment unless the user has supplied another isolated environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

On Windows PowerShell, activation is normally `.venv\Scripts\Activate.ps1` followed by the same install command.

Rules:

- use `python -m pip` from the selected interpreter rather than an ambiguous global `pip`;
- do not install into the system interpreter when an isolated environment can be used; the broker SDK's protobuf constraint conflicts with many system environments;
- do not change dependency versions merely to make installation easier;
- if the environment already exists, inspect it before replacing or upgrading it;
- if package download requires network permission, request that permission explicitly;
- report the interpreter path and Python version in the handoff.

For an application-only install, `python -m pip install -e .` is sufficient. The `dev` extra is the canonical test environment.

## Phase 2 — Offline verification baseline

Run the contract, scaffold and privacy checks first because they fail quickly and need no external service:

```bash
python -m pytest -q \
  tests/test_project_contract.py \
  tests/test_ai_bootstrap.py \
  tests/test_trade_scaffold.py \
  tests/test_public_sanitization.py \
  tests/core/test_market_sessions.py
```

Then run the complete suite:

```bash
python -m pytest -q
git diff --check
```

Offline tests use synthetic fixtures and temporary paths. A test that reads live files under `trade/` is a bootstrap failure even if it passes locally; `tests/test_live_data_isolation.py` pins this at source level. Runner integration tests that need a local model CLI or an OS sandbox are skipped or isolated by design and never call a real model. Broker smoke checks stay outside the offline gate.

When tests fail, distinguish among a pre-existing failure, an environment or dependency failure, a failure introduced by the agent, and an optional integration that is intentionally not configured. Do not hide a failure by deleting a test, weakening a guard, reading private data or installing unrelated packages.

## Phase 3 — Private trading workspace

Enter this phase only when the user requests a workflow that needs private state.

`trade/` is a schema and contract tree, not a bundled strategy or sample portfolio. Public files below it are limited to `README.md` and synthetic `*.example.*` files; generated runtime files are private and ignored by Git.

Procedure:

1. Read `trade/README.md` and every relevant descendant `README.md`.
2. Identify the minimum runtime files required by the requested workflow.
3. Copy only those examples, removing `.example` from the destination filename.
4. Preserve field names and data types.
5. Replace synthetic values only with user-supplied facts or data from an explicitly authorized source.
6. Keep unknown values `null`, empty or listed for review; never turn unknown into zero.
7. Validate JSON or JSONL syntax and the applicable schema checks.
8. Confirm the generated paths are ignored by Git.

Typical minimal commands, used only when those files are required:

```bash
cp trade/us_stock/holding/state/positions.example.json \
  trade/us_stock/holding/state/positions.json
cp trade/us_stock/holding/state/watchlist.example.json \
  trade/us_stock/holding/state/watchlist.json
git check-ignore -v \
  trade/us_stock/holding/state/positions.json \
  trade/us_stock/holding/state/watchlist.json
```

Runtime files that specific workflows add later, each from its own example: the event ledgers and NAV history under `holding/events/`, the external cash-flow ledger that the NAV report nets out, the valuation input region and ledgers under `valuation/`, market caches under `market/`, and the private strategy rules under `strategy/`. Do not copy every example. Example strategy documents are schemas, not active rules or recommendations.

Ownership remains strict: broker synchronization owns broker-derived facts; controlled agent and user workflows own thesis, zones, reviewed actions and private rules; decision modules own the generated guard and plan state named in the descendant READMEs; append-only event history is corrected with a new event, not rewritten.

## Phase 4 — Secrets and local configuration

No secrets are required for offline development.

For an approved broker-connected or operations setup:

```bash
cp .env.example .env
```

Populate `.env` locally. Do not echo secret values into terminal output, test logs, chat, patches, reports or documentation. Existing process environment variables take precedence over `.env` through `quant/core/env.py`.

The template separates OpenD host, port, encryption mode and private-key path; optional Telegram notification credentials (`~/.config/coreytrading/telegram.env` is the credential-file location the notifier reads); and optional external-data credentials. The optional notification footer line is configured through `TG_FOOTER_LINE`; unset means no footer.

Rules:

- keep private keys outside the repository and use an absolute local path;
- never write credentials into `opend/OpenD.xml.reference`;
- never commit a populated `.env`, `.mcp.json`, OpenD configuration, broker export, key or model-CLI login state;
- do not create placeholder secrets that look usable;
- if a required value is unknown, leave it unresolved and report it.

`.mcp.json.example` is intentionally empty. MCP is not required for the application. Add a local `.mcp.json` only for a user-approved integration, keep credentials in environment variables, and document the trust boundary before enabling tools that can mutate external systems.

## Phase 5 — Optional OpenD and broker infrastructure

OpenD is optional. Enter this phase only with explicit authorization for broker connectivity.

1. Read `.claude/rules/tools-environment.md`.
2. If the active agent supports project skills, read `.claude/skills/install-moomoo-opend/SKILL.md` before installing or upgrading OpenD.
3. Review `opend/README.md` and the appropriate OpenAPI legal notice.
4. Install or start OpenD using the provider's current official distribution.
5. Keep the listener local unless the user explicitly designs and secures remote access.
6. Copy the sanitized reference configuration to OpenD's external configuration location; populate it only there.
7. Align the broker SDK and OpenD versions according to the pinned dependency contract and the skill's checks.
8. Verify connectivity with a read-only account or quote operation before any state synchronization.
9. Keep broker smoke checks outside the offline test gate.

Hard safety boundaries:

- never unlock trading through the SDK;
- never place, modify or cancel an order as part of setup or verification;
- never assume paper trading merely from a default; verify the actual environment;
- never expose OpenD beyond localhost without an explicit network and security design;
- never copy a populated OpenD configuration back into the repository.

If OpenD is absent, offline development remains a valid completed mode. Report broker connectivity as not configured rather than treating it as a failed core setup.

## Phase 6 — Optional operations infrastructure

### Notifications

Notification modules are optional external side effects. Configure them only after credentials, destination, message scope and test-message authorization are explicit. A successful local render (`quant/ops/tg_notify.py --dry-run`) is not authorization to send it.

### Domain-agent runtimes

The four domain roles run on the Claude Code and Codex CLIs through `quant/ops/agent_runners.py`; `agents/routing.json` names the runtime, primary model, fallback models and effort per role. Before enabling them:

1. read `agents/README.md` and `agents/MODEL_POLICY.md`;
2. install and log in the CLIs the routing needs on this machine; the code never reads or stores their credentials, and `python3 quant/ops/agent_doctor.py --json` reports how the binaries resolve without executing anything;
3. confirm the isolation prerequisites (Claude runs tool-free; the Codex runner needs macOS `sandbox-exec` and a proxy limited to model endpoints) and review `python3 quant/ops/agent_runtime.py roles`;
4. run a synthetic contract probe in a separate run directory before any production job, respecting the global call limits;
5. leave `autopaint.apply` false until the operator explicitly authorizes reviewed automatic card writes, and never read a `succeeded` research receipt as a written card: only `applied` is.

Run artifacts, leases and backups live under the ignored `var/` directory. A worker has no workspace, broker, shell, messaging or order access; do not extend its permissions to make a workflow easier.

### Scheduled tasks

`tasks/` contains reusable prompts, not an active schedule. Before registration:

1. read `tasks/README.md` and the selected prompt;
2. confirm timezone, working directory, environment and notification destination; the exchange calendar and daylight-saving behavior are decided by the gate every prompt runs first (`python3 quant/ops/agent_jobs.py due --kind <kind>`), so verify that the gate answers on this machine;
3. verify the underlying command manually in a non-trading mode;
4. show the exact schedule and prompt to the user;
5. obtain authorization to create or change the task.

Never register all tasks automatically. Never infer that a prompt file is already installed in the user's task runner; registration, file presence and a completed-run receipt are three different pieces of evidence.

### CI

A CI environment installs the declared development dependencies and runs the offline verification gate only. It must not receive broker credentials or model-CLI logins, access private `trade/` runtime files, start OpenD, register tasks, send notifications or exercise order scripts.

## Data and time readiness

Before running an operational report, distinguish installation readiness from data readiness. Confirm:

- required private files exist and pass schema validation;
- timestamps include an offset or `Z` and are normalized before session rules;
- market-data source, permission, freshness and timezone are known, and completed bars are used for close-dependent transitions;
- broker state has been reconciled for the requested mode;
- missing data produces a fail-closed result rather than fabricated continuity;
- the exchange-calendar gate answers for the requested session; an unsupported date or a missing calendar dependency is an error, not a weekday guess.

An installed system with stale or synthetic market data is not operationally ready.

## Mutation and recovery discipline

- Inspect existing files and Git state before changing anything.
- Preserve unrelated user changes and private runtime data.
- Prefer the project's atomic writers and shared locks for mutable state; card writes go through `.claude/skills/trading-analysis/scripts/update_position_zones.py`.
- Do not use destructive Git commands or broad recursive deletion during setup.
- Back up a material private state file before an authorized migration.
- One-off migrations must default to dry-run and require an explicit apply step.
- Do not stage, commit, push, release, notify, register tasks or call a model runtime unless the user requested that action.

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
Notifications/tasks/MCP/model-runtime status:
Targeted checks:
Full offline test result:
Unresolved inputs or approvals:
Recommended next safe command:
```

Report private file paths only; do not paste their contents. If setup stopped at an earlier mode, say so explicitly and list the optional phases that were not attempted.

## Fast path for an AI agent

For a standard fresh-clone request with no broker requirement:

1. read `AGENTS.md` or the native agent entry point;
2. read `CLAUDE.md` in full, then this file and `quant/README.md`;
3. inspect Git state and Python availability;
4. create or reuse an isolated environment;
5. install `.[dev]` from `pyproject.toml`;
6. run the targeted checks, then the full offline suite;
7. do not create `.env`, private runtime files, MCP configuration, OpenD, notifications, tasks or model-runtime logins;
8. return the structured handoff report.

That baseline is useful on its own. Expand into private, broker-connected or operations infrastructure only when the requested workflow requires it.

## Common questions and answers

### Do I need to rename `CLAUDE.md` when I am not using Claude Code?

No. Keep `CLAUDE.md` because it is the complete project contract and Claude Code depends on that filename. Use the committed `AGENTS.md` for Codex, or create a small runtime-specific adapter that directs the agent to read `CLAUDE.md` in full. Never maintain two copied contracts.

### Which file should an AI read first?

Start with the file automatically discovered by the active agent: Codex starts from `AGENTS.md`, Claude Code from `CLAUDE.md`. In every case, read the complete `CLAUDE.md` before acting. For setup work continue with this file; for private data continue with `trade/README.md`; for model runtimes continue with `agents/README.md`.

### Must this project be used with Claude Code?

No. The application code, tests, schemas and bootstrap process are agent-independent; only automatic instruction discovery differs. The domain-agent runtimes are also a choice: routing can point a role at either CLI, and the offline system works with neither installed.

### Which bootstrap mode should be used when the request is vague?

Offline development. It provides a testable code environment without creating private files, connecting external systems or calling a model.

### Is OpenD required to install or test the project?

No. The offline suite passes without OpenD, broker credentials or live account data. OpenD is needed only for approved broker-connected workflows, and the agent must not install it during a general setup request.

### What should happen when OpenD, a market-data service or a model CLI is unavailable?

Complete the safe offline phases, mark the optional integration as not configured or unavailable, and report the exact failed check. Do not fabricate broker state, replace live data with synthetic values, or weaken tests to hide the missing service.

### Can setup place a paper-trading order as a connectivity test?

No. Setup verification is read-only. Paper and live environments must both be verified explicitly, and neither is authorization to place, modify or cancel orders. Trading unlock must never be performed through the SDK.

### Does a domain-agent research result write a card?

No. A worker returns JSON; only the host controller may submit, and only after an independent review by the other runtime, policy and revision checks under the writer lock. A `succeeded` receipt is research; `applied` is the only written-card receipt.

### Are `*.example.*` files starter data or active strategies?

No. They are synthetic schemas that define shape, ownership and runtime filenames. They are not market data, a sample portfolio to adopt or recommended trading rules, and real user data must never be written into an example file.

### What if a required private value is unknown?

Keep it `null`, empty or unresolved according to the schema and ask for the missing fact when it blocks the workflow. Never infer an account identifier, holding, cost, risk limit, authorization or strategy preference.

### What if a private workspace already exists?

Treat the task as repair or migration, not initialization. Read and validate the existing files first, preserve user-authored and broker-owned fields, back up material state before an approved migration, and change only the minimum required paths. Never overwrite an existing runtime file by blindly copying an example.

### How can I verify that a generated private file will not be committed?

Use `git check-ignore -v <path>` and inspect `git status --short --untracked-files=all`. A private runtime file, a `var/` artifact or an `ai_news/` file appearing as trackable output is a failure to fix before continuing.

### What about `.env.example` and `.mcp.json.example`?

They are public templates. Copy `.env.example` to the ignored `.env` only for an integration that needs it, and put local values in that private file. MCP is not required; create a local `.mcp.json` only for an approved integration.

### Are skipped tests acceptable?

Only when the skip is an intentional repository behavior, such as an unavailable optional integration or a host-state audit that needs an explicit flag. Report the skip count and reason. A newly skipped contract, privacy, architecture or synthetic-behavior test is not a successful bootstrap.

### Can task prompt files be treated as an installed schedule?

No. `tasks/` versions reusable prompts only. Registration requires a separate check of timezone, environment, destination, exact cadence and user authorization, and the calendar gate inside each prompt decides whether a wake-up actually runs.

### May the AI commit or push after a successful bootstrap?

Only when the user requested that Git action. Passing tests authorizes neither staging nor publication. Review the diff and the public/private boundary before any separately authorized commit or push.
