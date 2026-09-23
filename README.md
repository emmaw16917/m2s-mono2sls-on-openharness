# M2S baseline-generated serverless samples (no performance optimizations)

This repo collects the serverless code that the **OpenHarness version of the Mono2Sls
migration pipeline** (`m2s-migrate`, in the main `Mono2SLs` project) generates from
monolith benchmark apps — **before any of the performance-optimization work** described
in `m2s-mcp-server/PERF_OPTIMIZATION_DESIGN.md` has been applied to the pipeline itself.
It's the "current baseline" to diff against official/reference serverless
implementations, to find optimization opportunities beyond what we've already found by
testing the pipeline's output directly.

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

These come from `Mono2SLs/Mono2Sls-artifacts/mono_benchmark/<app>/` in the main
project. To generate a new app's baseline sample with the same pipeline:

1. In the `Mono2SLs` project, invoke the `m2s-migrate` skill (or ask an OpenHarness
   session to follow its protocol) against `mono_benchmark/<app>` with a fresh
   workspace, e.g. `Mono2Sls-artifacts/M2S/runs/<app>-oh`. Let it run through S1-S4
   (architect → code-developer → sam-engineer → validator) to a `gate(final)` PASS —
   don't stop early at S1/S2 like some earlier partial runs did.
2. Copy `output/lambdas/`, `output/template.yaml`, `output/consistency_report.json`,
   `storage/blueprint.json`, `storage/analysis_report.json` (and `audit.log` if
   present) into a new `<app>/` folder here, following the same layout as `todo/` and
   `bookstore/`.
3. Don't include `.aws-sam/` (regenerable build artifacts) or
   `template.part1.yaml`/`template.part2.yaml` (intermediate merge inputs —
   `template.yaml` is the merged final product).
4. Open a PR (or however this repo's owner wants contributions) with a short note of
   which app and whether `gate(final)` actually passed — if it didn't, say so plainly
   rather than including a broken/partial run.

## What this repo is *not*

It's not the pipeline itself (that's in the main `Mono2SLs` project — you need that to
generate a new app's sample), and it's not a deployable application on its own (no
`sam build`/`sam deploy` has been run against what's checked in here). It exists purely
as comparison material for the performance-optimization design work.
