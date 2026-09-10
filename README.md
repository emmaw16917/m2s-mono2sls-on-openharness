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

## 文档内容说明

- **`before/`**：迁移前的单体输入。是一个用 FastAPI 包装 [RAG-Anything](https://github.com/HKUDS/RAG-Anything)的 HTTP 接口层,一共 8 个接口,用来验证 Mono2Sls 对 AI/ML 应用的迁移能力。
- **`after/`**：Mono2Sls 自动生成的 Serverless 产物,包含 SAM 部署模板(`template.yaml`)、9 个 Lambda 函数(`lambdas/`)、共享代码 Layer(`layers/`)以及一致性校验报告(`consistency_report.json`)。
- **`pipeline-artifacts/`**：流水线运行过程中的中间产物,包含静态分析报告(`analysis_report.json`)、代码符号表(`symbol_table.json`)、架构师设计的蓝图(`blueprint.json`),方便回溯每一步 Agent 具体是根据什么输入做出的决策。
- 其余内容均为 `Mono2Sls-artifacts` 源代码。