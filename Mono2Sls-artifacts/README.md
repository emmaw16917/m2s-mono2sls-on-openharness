# Mono2Sls Artifact

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Pipeline runtime |
| Node.js | ≥ 18 | E2E test suites |
| AWS SAM CLI | latest | `sam build` / `sam deploy` |
| cfn-lint | latest | SAM template validation (`pip install cfn-lint`) |
| Ollama | latest | Local embedding model (`ollama pull mxbai-embed-large`) |
| AWS account | — | Required only for deployment and E2E evaluation |

---

## Step 1: Install Python Dependencies

```bash
cd M2S
pip install -r requirements.txt   # or: pip install crewai crewai-tools llama-index ...
```

---

## Step 2: Configure the LLM

```bash
cd M2S
cp .env.example .env
# Edit .env: set OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_MODEL_NAME
```

The pipeline is tested with **Claude Sonnet 4.6** and **DeepSeek-V3** via an
OpenAI-compatible endpoint. Any model supported by LiteLLM can be used.

---

## Step 3: Run the Migration Pipeline on a Benchmark App

Run static analysis and build the RAG index:

```bash
python src/preprocessor/static_analyzer.py --monolith-root ../mono_benchmark/todo
python src/preprocessor/build_rag.py --monolith-root ../mono_benchmark/todo
```

Place (or symlink) one benchmark application into `M2S/`:

```bash
# Example: migrate the todo application
cp -r mono_benchmark/todo M2S
cd M2S
```

Run the four-agent migration pipeline:

```bash
python src/main.py
```

Generated artifacts are written to `M2S/output/`:
- `output/template.yaml` — AWS SAM template
- `output/lambdas/` — Lambda function handlers
- `output/consistency_report.json` — Cross-artifact validation report

---

## Step 4: Deploy to AWS

```bash
cd M2S
sam build
sam deploy --guided   # follow prompts; note the API Gateway URL and Cognito Pool IDs
```

---

## Step 5: Run the E2E Test Suite

After deployment, configure and run the test suite for the target application:

```bash
cd tests/e2e-test-suite-todo   # (or any other suite)
npm install

# Copy the config template and fill in your deployment values
cp config/example.json config/my_migration_todo.json
# Edit my_migration_todo.json: set baseUrl, userPoolId, clientId, region

# Run tests against your deployed target
node run-target.js my_migration_todo
```

Test results (JSON) are saved to `results/`.

For airline-booking, seed the flight data before testing:

```bash
cd tests/e2e-test-suite-airline-booking
TARGET_URL=https://YOUR_API.execute-api.REGION.amazonaws.com/STAGE node seed-flights.js
```

---


## Notes on Anonymization

This artifact has been prepared for double-blind review:
- Author-identifying information has been removed from source code comments.
- The `.env` file containing API keys is excluded; use `.env.example` as a template.
- Target deployment configs containing account-specific AWS resource IDs are excluded;
  use `config/example.json` in each test suite as a template.
