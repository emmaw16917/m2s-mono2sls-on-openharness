# M2S baseline-generated serverless samples (no performance optimizations)

This folder collects the serverless code that the pipeline in this repo's root
(`m2s-mcp-server/` + `.openharness/skills/m2s-migrate/`) generates from the monolith
benchmark apps under `../Mono2Sls-artifacts/mono_benchmark/` — **before any of the
performance-optimization work** described in `../m2s-mcp-server/PERF_OPTIMIZATION_DESIGN.md`
has been applied to the pipeline itself. It's the "current baseline" to diff against
official/reference serverless implementations, to find optimization opportunities
beyond what we've already found by testing the pipeline's output directly.

See the repo root `README.md` for how to run the pipeline yourself and generate a new
app's sample.

Each app's monolith source (in `mono_benchmark/<app>/` in the main project) was
originally reverse-engineered from a real, official AWS serverless reference
architecture — so for each app here, there should be a real official serverless
implementation to compare against.

## What's in each app folder

- `lambdas/<domain>/<name>/` — the generated Lambda handlers (`handler.js`/`handler.py`
  + `package.json`/`requirements.txt`), exactly as the pipeline wrote them.
- `template.yaml` — the generated AWS SAM template (API Gateway, Lambda, DynamoDB,
  Cognito, SQS, IAM).
- `blueprint.json` — the architecture design the `m2s-architect` stage produced from
  the monolith (which endpoints became which Lambda, DynamoDB schema, sync/async
  wiring, dropped auth endpoints, etc.) — explains *why* the code looks the way it
  does, not just what it looks like.
- `analysis_report.json` — the static-analysis facts extracted from the monolith that
  the architect worked from (entry points, call graph, DynamoDB usage, file tags).
- `consistency_report.json` — the pipeline's own final validation report. Both apps
  here passed (`status: PASS`) — the code is internally consistent (correct
  CodeUri/Handler wiring, IAM permissions, etc.), not just "generated", but **it has
  not been deployed or performance-tested as part of this repo** (that was done
  separately, once, on real AWS, and is written up in `PERF_OPTIMIZATION_DESIGN.md`,
  not included here).
- `audit.log` (where present) — a spawn/completion audit trail for the pipeline's
  sub-agents, for provenance.

## Apps included so far

| App | Status |
|---|---|
| `todo` | generated, `gate(final)` PASS |
| `bookstore` | generated, `gate(final)` PASS |

## Adding more apps (e.g. coffee, ecommerce, shopping-cart, airline-booking)

See the repo root `README.md`, "Running the pipeline" + "Contributing a new sample
back" — this folder is just the destination for that last step.

## What this folder is *not*

Not the pipeline itself (that's the repo root — you need it to generate a new sample),
and not a deployable application on its own (no `sam build`/`sam deploy` has been run
against what's checked in here). It exists purely as comparison material for the
performance-optimization design work.
