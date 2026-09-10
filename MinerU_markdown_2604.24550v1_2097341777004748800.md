# Mono2Sls: Automated Monolith-to-Serverless Migration via Multi-Stage Pipeline with Static Analysis

Xingyan Chen chenxy979@mail2.sysu.edu.cn Sun Yat-sen University Zhuhai, China 

Yuxin Su suyx35@mail.sysu.edu.cn Sun Yat-sen University Zhuhai, China 

Yang Yu yuy@mail.sysu.edu.cn Sun Yat-sen University Zhuhai, China 

Zishan Su zshsu@link.cuhk.edu.hk The Chinese University of Hong Kong Hong Kong, China 

Zibin Zheng zhzibin@mail.sysu.edu.cn Sun Yat-sen University Zhuhai, China 

## Ab<sub>s</sub>t<sub>rac</sub>t

Cloud computing platforms ofer elastic scaling, managed infrastructure, and pay-per-use pricing, but moving existing monolithic backends to them remains a dificult software engineering task. In practice, the migration requires coordinated changes to pro gram structure, source code, infrastructure configuration, and cloud specific design decisions, and these changes are still largely carried out by hand. In this paper, we present Mono2Sls, an automated pipeline that converts monolithic web backends into deployable AWS SAM applications. The pipeline combines lightweight static analysis of entry points, call graphs, and asynchronous behavior with four sequential tool-using LLM agents: Architect, Code Developer, SAM Engineer, and Consistency Validator. These agents communicate through explicit intermediate artifacts and consult a curated SAM knowledge base. Evaluated on six benchmark ap plications totaling more than 10K lines of code and 76 business endpoints, Mono2Sls achieves 100% deployment success without manual fixes. It also reaches 66.1% end-to-end correctness and 98.7% API-coverage F1, whereas the commercial baselines achieve 53.7– 61.2% and 88.4%, respectively. The migrated systems show more consistent use of AWS-native authentication and asynchronous patterns, and an ablation study indicates that static-analysis-guided architecture planning contributes 23.4 percentage points to end-to end correctness. 

## CCS Conce<sub>p</sub>ts

• Software and its engineering → Automatic programming. 

## Ke<sub>y</sub>words

serverless migration, multi-stage pipeline, large language models, AWS SAM 

## 1 I<sub>n</sub>t<sub>ro</sub>d<sub>uc</sub>ti<sub>on</sub>

Serverless computing is now widely used for cloud applications be cause it ofers automatic scaling, pay-per-use pricing, and managed infrastructure. Industry reports estimate that the global serverless market will grow from $21.9 billion in 2024 to $44.7 billion by 2029 at a 15.3% compound annual growth rate [18], with 72% of enter prises already running serverless workloads in production [13]. AWS Lambda is central to this ecosystem [4]. At the same time, much production software still runs as monoliths, and legacy architectures remain a major source of enterprise technical debt [30]. Turning these systems into serverless applications is dificult to automate. An automated migration method must recover decomposition boundaries from tightly coupled code, preserve API contracts and business logic, redesign data access for a distributed runtime, and generate cloud-specific Infrastructure-as-Code (IaC) that remains consistent with the transformed code. Prior studies report recurring problems in testing event-driven systems, integrating legacy infrastructure, and finding reliable migration practices, especially when teams lack serverless experience [19, 35]. These coupled requirements motivate methods that combine program analysis with structured generation. 

LLMs have shown strong performance on code generation tasks, from function-level synthesis to repository-level modification [21, 24]. Benchmarks such as SWE-Bench [25] and RepoGenesis [29] suggest that commercial assistants such as Cursor and Claude Code can already make substantial repository-scale changes. This progress makes LLMs a natural starting point for migration automation, but existing assistants are not designed to maintain the long-range consistency that serverless migration requires. Decisions about function boundaries, API definitions, handler implementations, and infrastructure declarations must remain aligned across multiple generated artifacts, which is dificult to achieve in a single loosely structured generation context. The dificulty is compounded by rapidly evolving cloud APIs: code LLMs often misuse infrequent APIs [23], and this risk is particularly relevant to AWS SAM, whose configuration space spans more than 800 resource types [36]. 

Our approach, Mono2Sls, is a multi-stage pipeline for migrating monolithic web backends to AWS SAM applications. It begins by extracting structural facts from the monolith with static analysis and then feeds them into four sequential stages, each handled by a dedicated tool-using LLM agent (Architect, Code Developer, SAM Engineer, Consistency Validator). Together, these stages plan the target architecture, generate Lambda handlers and SAM templates, and verify consistency across artifacts. Decomposing the task in this way lets the system coordinate decomposition, code transformation, and infrastructure generation without forcing all decisions into a single generation context. 

Our contributions are as follows: 

• A multi-stage migration pipeline: A four-stage workflow in which dedicated tool-using LLM agents (Architect, Code Devel oper, SAM Engineer, Consistency Validator) handle architecture planning, code generation, infrastructure generation, and valida tion through explicit intermediate artifacts and isolated contexts. 

• Static-analysis-driven architecture planning: Lightweight analysis that extracts HTTP entry points, cross-file call graphs with async semantics, and DynamoDB schema information to ground migration decisions. 

• Domain-grounded SAM generation and validation: A cu rated SAM knowledge base together with a Consistency Validator that performs 11 cross-artifact checks to improve template correctness and deployment readiness. 

• A benchmark and empirical evaluation: Six web applications reverse-engineered from AWS reference serverless implemen tations, evaluated for deployability, functional correctness, and cloud-native design adoption. 

## 2 B<sub>ac</sub>k<sub>groun</sub>d <sub>an</sub>d R<sub>e</sub>l<sub>a</sub>t<sub>e</sub>d W<sub>or</sub>k

## 2<sub>.</sub>1 AWS S<sub>e</sub>r<sub>ve</sub>rl<sub>ess</sub> Ar<sub>c</sub>hit<sub>ec</sub>t<sub>u</sub>r<sub>e</sub>

Recent reviews of cloud computing identify migration from monolithic systems as an open problem in serverless computing [20, 34]. Empirical studies further show that configuration management and event-driven design are common sources of errors in prac tice [16, 35]. AWS Serverless Application Model (SAM) [6] is a widely used Infrastructure-as-Code framework for AWS serverless applications. It describes Lambda functions [4], API Gateway, Dy namoDB/S3, Cognito [1], and SQS [3]/EventBridge [2] in a YAML template that extends CloudFormation. SAM is expressive, but it is also unforgiving: small mistakes, such as invalid CORS combi nations, reserved environment variables, or missing IAM policies, can block deployment. Template correctness is therefore a key requirement for any automated migration system [36]. 

## 2.2 LLM-based Code Generation and Mi<sub>g</sub>ration

LLM-based code generation has progressed from function-level benchmarks (HumanEval [14], MBPP [12]) to repository-level tasks (SWE-Bench [25], RepoGenesis [29]). Static analysis often improves repository-scale performance: STALL+ [28] shows that file-level dependency augmentation outperforms RAG [27] alone, and CodeAgen [37] shows that tool-equipped agents outperform pure-LLM infer ence at repository scale. For code translation, AlphaTrans [22] decomposes repository-level Java-to-Python translation into reversecall-order fragments with multi-level validation, achieving 96.4% syntactic correctness across ten projects. These systems address peer-language translation, that is, functionally similar implementations in another language. They do not address architectural migration, which requires simultaneously redesigning application structure, replacing the runtime model, and authoring cloud-native IaC declarations. 

## 2<sub>.</sub>3 P<sub>rogram</sub> Mi<sub>gra</sub>ti<sub>on an</sub>d M<sub>o</sub>d<sub>ern</sub>i<sub>za</sub>ti<sub>on</sub>

Microservice decomposition. Wang et al. [33] benchmark eight decomposition tools and find that structural and semantic metrics can yield quite diferent service boundaries; Romani et al. [31] propose a data-centric identification process for legacy systems; 

Kalia et al. [26] use AI-driven runtime call graphs to partition Java monoliths automatically. All three lines of work reason about logical boundaries but stop short of producing deployable infrastructure, so cloud migration remains a manual downstream step. 

FaaSification. Podilizer [32] and Node2FaaS [15] mechanically extract Java/Node.js methods into Lambda functions via static analysis and code rewriting, without reasoning about architecture, authentication patterns, or asynchronous communication. RADF [38] adds business-logic-aware clustering but still produces blueprints and provisional structures rather than executable Lambda handlers and SAM templates. 

## 3 <sup>A</sup><sub>pp</sub>roac<sup>h</sup>

## 3.1 Overview

Mono2Sls is a static-analysis-driven multi-stage pipeline that automatically migrates monolithic web applications to AWS SAM applications. As shown in Figure 1, static analysis first produces analysis_report.json, which anchors all downstream LLM reasoning. The pipeline then executes four sequential stages, each powered by a dedicated tool-using LLM agent: the Architect consumes this report and emits blueprint.json, the shared architectural contract for subsequent stages. The Code Developer and SAM Engineer generate Lambda handlers and template.yaml respectively, while the Consistency Validator cross-checks all artifacts, applies fixes, and re-validates. Each agent is equipped with purpose-built tools and a curated domain knowledge base (§3.3); stages communicate exclusively through files, with no direct inter-agent messaging. 

## 3<sub>.</sub>2 St<sub>a</sub>ti<sub>c</sub> An<sub>a</sub>l<sub>ys</sub>i<sub>s</sub>

Before invoking the LLM-based agents, we perform lightweight static analysis to extract structured information that anchors LLM reasoning. The analyzer produces analysis_report.json, which serves as the authoritative input for the Architect agent, and a separate symbol_table.json consumed exclusively by CodeRAGTool for code indexing (§3.3). 

HTTP Entry Points. For Python, we traverse the AST to detect route decorators (@app.route, @app.get, Flask Blueprint prefix+path aggregation); for JavaScript/TypeScript, we apply regex matching on Express patterns (app.get, router.post, etc.). Each entry point records (method, path, handler_function, file). 

File Tags. We classify each source file by content features, assigning tags from {AWS_SDK, DynamoDB, Auth, FileUpload}. Tags are detected via keyword matching on the file source (e.g., boto3 imports for AWS_SDK, DynamoDB API calls for DynamoDB, JWT references for Auth). These tags guide the DynamoDB Schema Locator and are available for downstream agent decisions. 

Cross-file Call Graph with Async Semantics. A two-pass analysis builds a per-file import map (Pass 1) then collects crossfile call edges from function bodies (Pass 2), using AST traversal for Python and regex for JavaScript/TypeScript. Each edge records two semantic flags: return_value_used (false for bare-expression / fire-and-forget calls) and is_awaited. Edges are aggregated per HTTP entry point into a compact dependency map, reducing the Architect’s search from O(�<sup>2</sup>) to O(1) per endpoint. Figure 2 illustrates this process on a benchmark Flask application: a route handler delegating to a cross-file helper is analysed to produce the entry_point_dependencies record in analysis_report.json. 

![image](https://cdn-mineru.openxlab.org.cn/result/2026-09-08/0b6b7ca2-d427-472f-8904-017dd8023ea5/35ce0cf362c076982ee422f95d10a3e07f1c848fac97a2cc65d87dd5312be984.jpg)



Figure 1: Mono2Sls <sub>p</sub>i<sub>p</sub>eline overview. Static anal<sub>y</sub>sis <sub>p</sub>re<sub>p</sub>rocessing <sub>p</sub>roduces analysis_report.json, which drives the Architect stage to design blueprint.json. The blue<sub>p</sub>rint then serves as the shared contract for the three downstream stages, namel<sub>y</sub> Code Develo<sub>p</sub>er<sub>,</sub> SAM En<sub>g</sub>ineer<sub>,</sub> and Consistenc<sub>y</sub> Validator<sub>,</sub> each <sub>p</sub>roducin<sub>g</sub> a well-defined out<sub>p</sub>ut artefact.


DynamoDB Schema Locator. For applications tagged with DynamoDB, we identify schema definition files via a priority heuris tic: initialization scripts (e.g., init_db.py, create_tables.js) ≻ database configuration modules (db.py, models.py) ≻ general busi ness logic. The top-3 candidate files are recorded in the report, en abling the SAM Engineer to read exact table schemas rather than inferring them from handler code. 

## 3.3 A<sub>g</sub>ent Tools and Domain Knowled<sub>g</sub>e

Each agent is equipped with a role-specific tool set and a curated subset of the domain knowledge base (Table 1). Tools provide structured, validated interfaces to the file system and external validators, enabling agents to navigate and operate over the entire monolith codebase, while also catching syntactically invalid artifacts before they propagate downstream. Domain knowledge supplements LLM training with up-to-date, task-specific guidance, ensuring gener ated code and templates follow documented AWS best practices rather than relying solely on the backbone LLM’s training-data coverage of rapidly evolving cloud APIs. 

File Tools. We implement three file-interaction tools as a group: • ReadFileTool: reads files with optional line-range selection; returns a truncation warning for large files (>500 lines) to prevent context overflow. 

• WriteFileTool: writes files with built-in syntax validation (Python AST, JSON, YAML); a JSON merge-key mode allows agents to construct large structured documents section-by-section without exceeding context limits. 

• FileListTool: lists directory contents with optional recursion, enabling agents to verify that all expected artifacts have been generated. 

CodeRAGTool. Provides semantic code retrieval over the monolith source. Ofline, we build a RAG [27] vector index over source code chunks using CodeBERT [17] (microsoft/codebert-base) embeddings and LlamaIndex, consuming the symbol_table.json produced by static analysis as the chunking reference. At runtime, the tool returns top-� raw code snippets with relevance scores and file/line metadata. To avoid double inference, the tool operates in retriever-only mode that returns raw snippets without LLM synthesis and leaves all semantic interpretation to the agent’s own inference call. 

SAMValidateTool. Wraps cfn-lint [7] (AWS CloudFormation Linter) and returns structured feedback distinguishing fatal errors from warnings, enabling an in-task validation–fix loop without leaving the inference context. 

Domain Knowledge Base. Four reference documents compiled from the AWS SAM and Lambda Developer Guides [6]: (1) Basic Serverless Architecture (resource types, API Gateway, Cognito); (2) Async Patterns (SQS [3]/EventBridge [2] event-source mapping); (3) Lambda Coding Reference (handler signatures, Layer packaging, env-var conventions); (4) SAM Template Reference (valid YAML properties, documented anti-patterns). Each document is chunked and indexed per agent following least-exposure: each agent receives only the knowledge relevant to its task (Table 1). 

![image](https://cdn-mineru.openxlab.org.cn/result/2026-09-08/0b6b7ca2-d427-472f-8904-017dd8023ea5/ee3726491b8a4d688c296b6135fe2f86a85f24076f73f436d101aa2efabbf04a.jpg)



Fi<sub>gure</sub> 2<sub>:</sub> St<sub>a</sub>ti<sub>c</sub> <sub>ana</sub>l<sub>ys</sub>i<sub>s</sub> <sub>an</sub>d d<sub>epen</sub>d<sub>ency</sub> <sub>ex</sub>t<sub>rac</sub>ti<sub>on</sub> <sub>wor</sub>kfl<sub>ow.</sub> L<sub>e</sub>ft<sub>:</sub> <sub>mono</sub>lith <sub>source</sub> <sub>sn</sub>i<sub>ppe</sub>t <sub>w</sub>ith <sub>anno</sub>t<sub>a</sub>t<sub>e</sub>d <sub>cross-</sub>fil<sub>e</sub> <sub>ca</sub>ll<sub>s.</sub> Mid<sub>-</sub> dle: core extraction algorithms for entr<sub>y</sub> <sub>p</sub>oints, call gra<sub>p</sub>h, and as<sub>y</sub>nc semantics. Right: the resulting entry_point_dependencies structure in analysis_report.json.



Table 1: Tool and Knowled<sub>g</sub>e Base Assi<sub>g</sub>nment <sub>p</sub>er A<sub>g</sub>ent


<table><tr><td>Agent</td><td>Tools</td><td>Knowledge Bases</td></tr><tr><td>Architect</td><td>ReadFile, WriteFile</td><td>Basic Architecture, Async Patterns</td></tr><tr><td>Code Developer</td><td>ReadFile, CodeRAG, Write-File</td><td>Lambda Coding, Async Patterns</td></tr><tr><td>SAM Engineer</td><td>ReadFile, WriteFile, FileList, SAMValidate</td><td>SAM Template Ref.</td></tr><tr><td>Validator</td><td>ReadFile, WriteFile, FileList, SAMValidate</td><td>SAM Ref., Lambda Coding, Async</td></tr></table>

## 3<sub>.</sub>4 A<sub>rc</sub>hit<sub>ec</sub>t

The Architect consumes analysis_report.json and produces blueprint.json, the single source of truth for all downstream agents. The blueprint specifies Lambda function boundaries, inter-Lambda communication patterns, shared infrastructure resources, and the authentication strategy. Its top-level structure is: 

• lambda_functions: one entry per business endpoint, each record ing trigger type, runtime, source files, auth requirement, and async publish targets 

• dynamodb_tables, s3_buckets, cognito, api_gateway: shared infrastructure specifications 

• sqs_queues, eventbridge_rules, lambda_invoke_permissions: inter-Lambda communication resources 

• dropped_functions: auth endpoints and infrastructure scripts replaced by managed services 

Static-Analysis-Driven Endpoint Planning. The Architect reads the entry_points array from the report to enumerate all HTTP routes, and consults file_tags to detect auth-related files. Endpoints bearing standard or role-based auth decorators (e.g., @login_required, @warehouse_required, etc.) are classified as auth="required"; auth endpoints (/register, /login, /logout) are dropped entirely and delegated to AWS Cognito, following the “infrastructure-over-code” principle. The remaining business endpoints are mapped one-to-one to Lambda functions (one Lambda per HTTP method+path), providing least-privilege IAM scoping and independent scaling per endpoint. 

S<sub>ou</sub>r<sub>ce</sub>-C<sub>o</sub>d<sub>e</sub>-G<sub>u</sub>id<sub>e</sub>d C<sub>o</sub>mm<sub>u</sub>ni<sub>ca</sub>ti<sub>o</sub>n P<sub>a</sub>tt<sub>e</sub>rn S<sub>e</sub>l<sub>ec</sub>ti<sub>o</sub>n<sub>.</sub> Assigning an inter-Lambda communication mechanism is a twophase process. In Phase 1, the Architect uses static analysis hints from entry_point_dependencies: return_value_used=true signals that the caller requires the callee’s result (favouring synchronous invoke), while return_value_used=false combined with is_awaited=false signals a fire-and-forget side-efect (favouring async). However, entry_point_dependencies captures only level-1 calls (route handler → service); cross-service calls (service A → service B) are invisible at this level. In Phase 2, the agent therefore autonomously reads the relevant business service source files to trace deeper call chains and resolve ambiguous cases. For each identified cross-domain relationship, the agent applies three rules: 

(1) Synchronous Lambda Invoke, when the caller needs the callee’s return value to construct its HTTP response; (2) SQS queue, for fireand-forget side-efects with a single consumer; and (3) EventBridge rule, when a single action fans out to two or more independent consumers across diferent service domains. 

## 3.5 Code Develo<sub>p</sub>er

The Code Developer reads blueprint.json and generates one Lambda handler per lambda_functions entry. For each Lambda, the blueprint provides: the monolith source_files to read, the trigger type and runtime, the auth requirement, async publish targets (publishes_to), and required environment variable names. The agent reads the designated source files first; for implementa tion details not covered by source_files (helper utilities, shared li braries), it queries CodeRAGTool and follows up with ReadFileTool for full context. 

Code Transformations. Three key adaptations are applied: (1) Framework adaptation: Flask/Express route handlers are rewrit ten to the lambda_handler(event, context) signature, extracting path parameters from event[’pathParameters’] and bodies from event[’body’]; (2) Identity model adaptation: the mono lith’s integer user_id references are replaced with the Cognito Sub UUID obtained from event[’requestContext’][’authorizer ][’claims’][’sub’], and the Users DynamoDB table is removed, and credentials are delegated to Cognito while any business profile fields are retained in a separate table; (3) State elimination: global variables are replaced by environment variables injected at deploy time. 

Shared Layer and Dependency Management. The agent creates a shared Lambda Layer when utilities are reused across ≥3 Lambdas. Layer content is placed in the runtime-specific path (python/ or nodejs/) required by the AWS Lambda Developer Guide [10], so Lambda mounts it under /opt/. Dependency files are generated only for non-empty filtered dependency sets (stripping pre-bundled runtime packages such as boto3 for Python; helper SDK packages must be explicitly declared for nodejs22.x [8]). All Node.js handlers use CommonJS (require) since Lambda’s /opt/nodejs path is resolved via NODE_PATH, which ESM does not honour. 

Cross-Lambda Communication. For those Lambdas with publishes_to or lambda_invoke_permissions in the blueprint, the agent injects the corresponding SDK call (e.g., lambda.invoke, sqs.send_message, or events.put_events). Async publishes are awaited before the HTTP response is returned, since Lambda freezes the environment immediately on handler return [9]. 

## 3<sub>.</sub>6 SAM En<sub>g</sub>in<sub>ee</sub>r

The SAM Engineer generates template.yaml from the pre-defined blueprint.json and the generated code via three sequential sub tasks following a dependency-based topological layering principle. Sub-task 1 establishes shared stateful resources first (DynamoDB tables, Cognito UserPool, Lambda Layers, API Gateway), creating the CloudFormation logical reference anchors (!Ref, !GetAtt) that downstream definitions resolve. Sub-task 2 processes Lambda func tion resources domain-by-domain, appending incrementally to en sure referential integrity. Sub-task 3 wires EventBridge rule targets, appends the Outputs section, and closes with a SAMValidateTool validation loop that iteratively corrects errors before finalisation. Template correctness is critical: a single misconfigured property blocks the entire deployment pipeline, requiring the deep AWSspecific knowledge encoded in the domain knowledge base (§3.3). 

## 3<sub>.</sub>7 C<sub>o</sub>n<sub>s</sub>i<sub>s</sub>t<sub>e</sub>n<sub>cy</sub> V<sub>a</sub>lid<sub>a</sub>t<sub>o</sub>r

The Consistency Validator performs cross-artifact verification between Lambda code, SAM template, and blueprint, automatically fixing mismatches to ensure deployment readiness. It executes 11 checks across five phases: (A) Structural: bidirectional directory/function coverage and CodeUri path validity; (B) Code-to-Template Alignment: handler name validity, environment variable completeness, IAM policy alignment with SDK calls, and sharedlayer reference consistency; (C) Blueprint Matching: API routes or methods matching against blueprint.lambda_functions and per-endpoint auth override correctness; (D) Async Resources: SQS queue existence, producer/consumer policy and env var pairs, EventBridge rule targets and invocation permissions; (E) Runtime Compliance: dependency file correctness (stripping Lambda builtins) and Node.js CJS/ESM enforcement. After all checks, fixes are applied in a single batch and re-validated with SAMValidateTool; anti-loop safeguards prevent infinite cycles. 

## 4 B<sub>enc</sub>h<sub>mar</sub>k

Real-world monolithic applications rarely come with paired serverless counterparts, which makes rigorous evaluation dificult. We therefore adopt a reverse-engineering approach. Starting from AWSmaintained reference serverless applications in aws-samples [5], we examined the top 270 repositories and applied inclusion criteria (SAM-based, HTTP API, no AI/ML dependencies, no proprietary external services). The screening yielded 12 candidates, from which we selected 6 that cover diverse complexity levels and the two dominant Lambda runtimes, Python and JavaScript [11]. 

For each serverless application, we constructed an equivalent monolith by (1) consolidating Lambda handlers into Flask/Express, (2) retaining DynamoDB for application data to isolate migration complexity from database concerns, (3) replacing Cognito with JWT-based middleware, (4) converting SQS/EventBridge async patterns to synchronous equivalents while preserving detectable async signals for the pipeline to reconstruct, and (5) removing frontend code. We then verified each constructed monolith through E2E testing against the original serverless behavior. The original serverless applications are used only to build the benchmark and identify target cloud-native properties; all migration methods operate exclusively on monolith source code. Table 2 summarizes the benchmark characteristics. 

## 5 Ex<sub>p</sub>erimental Setu<sub>p</sub>

## 5.1 Research Questions

We evaluate Mono2Sls through four RQs: RQ1 (Deployability) asks whether it can generate deployable applications without manual intervention; RQ2 (Functional Correctness) asks how faithfully it preserves monolith behavior relative to commercial AI assistants and LLM-based generation; RQ3 (Ablation) asks what the contribution of static-analysis-driven architecture planning is; and 


Table 2: Benchmark A<sub>pp</sub>lications


<table><tr><td>ID</td><td>Application</td><td>Lang</td><td>LoC</td><td><eq>EP_{obs}</eq></td><td>Tbl</td><td>Auth</td><td>Async</td></tr><tr><td>B1</td><td>Todo</td><td>JS</td><td>601</td><td>6</td><td>2</td><td>✓</td><td>✘</td></tr><tr><td>B2</td><td>Shopping Cart</td><td>Py</td><td>1,130</td><td>8</td><td>2</td><td>✓</td><td>✘</td></tr><tr><td>B3</td><td>Bookstore</td><td>JS</td><td>1,349</td><td>11</td><td>4</td><td>✓</td><td>✓</td></tr><tr><td>B4</td><td>Coffee Shop</td><td>JS</td><td>1,666</td><td>11</td><td>6</td><td>✓</td><td>✓</td></tr><tr><td>B5</td><td>Airline Booking</td><td>Py</td><td>2,109</td><td>14</td><td>4</td><td>✓</td><td>✓</td></tr><tr><td>B6</td><td>E-commerce</td><td>Py</td><td>3,623</td><td>26</td><td>6</td><td>✓</td><td>✓</td></tr><tr><td colspan="2">Total</td><td>-</td><td>10,478</td><td>76</td><td>24</td><td>6/6</td><td>4/6</td></tr></table>


EP<sub>obs</sub> = Observable business endpoints (auth endpoints excluded). Tbl = DynamoDB tables in constructed monolith. Async = monolith contains detectable async patterns (§5.3). 


![image](https://cdn-mineru.openxlab.org.cn/result/2026-09-08/0b6b7ca2-d427-472f-8904-017dd8023ea5/8f51bb8808070b10bae982628ba7ec5ebc1310a349cdf8a272770049ba0cf7be.jpg)



Fi<sub>g</sub>ure 3: Baseline mi<sub>g</sub>ration <sub>p</sub>rom<sub>p</sub>t <sub>p</sub>rovided verbatim to C<sub>ursor an</sub>d Cl<sub>au</sub>d<sub>e</sub> C<sub>o</sub>d<sub>e.</sub>


RQ4 (Cloud-Native Design) asks whether the generated applica tions adopt AWS-native patterns. For RQ1–2, we compare against Cursor, Claude Code, and an LLM-Baseline, with two backbone LLMs (Claude Sonnet 4.6, DeepSeek-V3.2) to confirm results are method-driven rather than model-driven. 

## 5<sub>.</sub>2 B<sub>ase</sub>li<sub>nes an</sub>d Abl<sub>a</sub>ti<sub>on</sub>

5.2.1 Commercial Baselines. We evaluate Cursor (Agent mode with Claude Sonnet 4.5 (Thinking)) and Claude Code (CLI with Claude Sonnet 4.6), following the methodology ofRepoGenesis [29]: each baseline receives the complete monolith source code and a task-level migration prompt (Figure 3) but no pre-computed static analysis or domain knowledge. All tool-initiated permission requests and confirmation prompts were accepted without redirecting agent decisions. 

5.2.2 LLM-Based Baseline. To isolate the efect of staged decomposition, we build an LLM-based baseline (LLM-Baseline) that handles the migration in a single generalist context. It receives the same inputs as Mono2Sls (static analysis, domain knowledge, tool access) but replaces four specialized stages with one generalist agent, collapses seven tasks into two, removes the 11-point consistency validator, and replaces detailed AWS-specific prompts with high-level instructions. Implemented with an agent framework to handle large codebases, it is essentially an end-to-end generation setup without iterative refinement. We evaluate two backbone LLMs, DeepSeek-V3.2 (LLM-Baseline-DS) and Claude Sonnet 4.6 (LLM-Baseline-SN), to keep the comparison controlled on model choice. 

5.2.3 Ablation: Without Static-Analysis-Driven Architecture Planning. For the ablation, we remove both the static analyzer and the Architect agent. These components are tightly coupled because analysis_report.json is designed specifically to support blueprint generation. The ablation tests whether pre-computed structural analysis plus explicit architectural planning adds value beyond direct source-to-target transformation. Without them, the Code Developer must identify endpoints from source code, the SAM Engineer must infer resources from generated handlers, and the Consistency Validator loses its architectural ground-truth reference. 


Table 3: Com<sub>p</sub>arison of ex<sub>p</sub>erimental conditions


<table><tr><td>Dimension</td><td>M2S (Full)</td><td>LLM-Baseline</td><td>w/o SA&amp;Arch</td></tr><tr><td>Static Analysis</td><td>√</td><td>√</td><td>✗</td></tr><tr><td>Architect Agent</td><td>√</td><td>(single)</td><td>✗</td></tr><tr><td>Blueprint</td><td>√</td><td>√</td><td>✗</td></tr><tr><td>Agents</td><td>4 spec.</td><td>1 gen.</td><td>3 spec.</td></tr><tr><td>Tasks (SAM)</td><td>7 (3)</td><td>2</td><td>6 (2)</td></tr><tr><td>Validator</td><td>11-point</td><td>Removed</td><td>Partial</td></tr><tr><td>Domain Knowledge</td><td>√</td><td>√</td><td>√</td></tr><tr><td>Prompt Guidance</td><td>Detailed</td><td>High-level</td><td>Detailed</td></tr></table>


spec. = specialized; gen. = generalist; Tasks (SAM) = total tasks (SAM sub-tasks). 


## 5<sub>.</sub>3 E<sub>va</sub>l<sub>ua</sub>ti<sub>on</sub> M<sub>e</sub>t<sub>r</sub>i<sub>cs</sub>

We define metrics across two dimensions: deployability and functional correctness. 

Deployability. We measure deployability through two complementary metrics: 

Validation Pass Rate (VPR) measures the fraction of generated projects that pass static validation checks. We use sam validate, which verifies SAM template syntax and resource references, together with cfn-lint, which enforces AWS CloudFormation best practices and detects common misconfigurations. 

Deployment Success Rate (DSR) measures the fraction of projects that achieve functional deployment to AWS: (1) sam build com pletes without error, (2) sam deploy provisions all resources without CloudFormation rollback, and (3) deployed Lambdas are invo cable without systematic failures(e.g., universal HTTP 502 from incorrect handler paths or package structures). We manually inspect invocation errors to distinguish systematic failures from isolated bugs. 

Functional Correctness. We assess functional quality through two complementary metrics that measure structural and behavioral fidelity respectively. 

API Coverage (F1) measures the match between generated HTTP endpoints � and the reference set � of observable business endpoints from the ground-truth monolith (|�| = 76). It is computed as the harmonic mean of precision (how many generated endpoints belong in $R )$ and recall (how many reference endpoints are gener ated). Redundant auth endpoints (not in �) reduce precision; async consumer Lambdas (no HTTP surface) are excluded from both sets. 

End-to-End Pass Rate (E2EPR) measures the fraction of E2E tests passing against the deployed application, capturing behavioral correctness beyond structural compliance. Let $T _ { i }$ denote the test suite for application $a _ { i }$ and $P _ { i }$ the subset of passing tests: 

$$
\mathrm{E2EPR} _ {\mathrm{micro}} = \frac {\sum_ {i} | P _ {i} |}{\sum_ {i} | T _ {i} |}, \quad \mathrm{E2EPR} _ {\mathrm{macro}} = \frac {1}{n} \sum_ {i} \frac {| P _ {i} |}{| T _ {i} |}\tag{1}
$$

We report on Core + Robustness tests (121 out of 145 total); test categories and their applicability to each method are detailed in §5.4. Unless otherwise stated, $T _ { i }$ refers to Core + Robustness tests applicable to application $a _ { i } .$ 

## 5<sub>.</sub>4 T<sub>es</sub>t S<sub>u</sub>it<sub>e a</sub>nd En<sub>v</sub>ir<sub>o</sub>nm<sub>e</sub>nt

We design application-agnostic E2E test suites by extracting API contracts from each monolith (endpoint signatures, request/response schemas, and authentication requirements) and using GPT-5.4, sep arate from the pipeline LLMs, to generate 145 tests covering four categories: Core (CRUD), Robustness (edge cases), Auth (JWT end points), and Async (event-driven). All tests then undergo expert review to ensure correctness and coverage. To evaluate methods with diferent authentication mechanisms under a common interface, we implement an Auth Provider abstraction (CognitoProvider for Mono2Sls; CustomJwtProvider for baselines), so the test logic remains auth-agnostic. The Core+Robustness tests (121) serve as the primary metric for all methods; Auth tests (18) apply only to commercial baselines; Async tests (6) apply only to methods that generate event-driven architectures (qualitative analysis in §6.4). 


T<sub>a</sub>bl<sub>e</sub> 4<sub>:</sub> T<sub>es</sub>t S<sub>u</sub>it<sub>e</sub> Di<sub>s</sub>t<sub>r</sub>ib<sub>u</sub>ti<sub>on</sub>


<table><tr><td>App</td><td>Core</td><td>Rob</td><td>Auth</td><td>Async</td><td>Total</td></tr><tr><td>B1: Todo</td><td>13</td><td>3</td><td>0</td><td>0</td><td>16</td></tr><tr><td>B2: Shopping Cart</td><td>12</td><td>3</td><td>2</td><td>0</td><td>17</td></tr><tr><td>B3: Bookstore</td><td>12</td><td>4</td><td>4</td><td>1</td><td>21</td></tr><tr><td>B4: Coffee</td><td>19</td><td>2</td><td>3</td><td>1</td><td>25</td></tr><tr><td>B5: Airline</td><td>19</td><td>5</td><td>6</td><td>2</td><td>32</td></tr><tr><td>B6: E-commerce</td><td>24</td><td>5</td><td>3</td><td>2</td><td>34</td></tr><tr><td>Total</td><td>99</td><td>22</td><td>18</td><td>6</td><td>145</td></tr></table>

Environment. Pipeline execution runs locally (Windows). Generated applications are deployed to AWS via sam deploy and tested against live infrastructure (API Gateway, Lambda, DynamoDB). Both backbone LLMs (DeepSeek-V3.2 and Claude Sonnet 4.6) were run at their default inference temperature; all experiments report single-run (pass@1) results per application. 

## 6 R<sub>esu</sub>lt<sub>s</sub>

We report results for each research question in turn, following the evaluation protocol and metrics defined in §5. 

Evaluation Protocol. Because the methods difer in deployment success, we use diferent evaluation procedures. Commercial baselines receive expert-assisted fixes for both validation and deployment failures, which isolates the comparison to functional correctness and reflects their best-case usage scenario. LLM-Baseline and Ablation receive fixes only for validation failures (VPR=0) so they can be deployed for testing, but systematic deployment failures (DSR=0) remain unfixed and are marked non-functional (E2EPR = N/A). Mono2Sls is evaluated on raw output only, because the self-verifying validator is intended to ensure deployment readiness. Under this setup, commercial tools are assessed under their intended collaborative usage model while the research variants are assessed on raw generation quality. 

## 6.1 RQ1: Deployability


Table 5: Deployment Results (Raw vs. Evaluated)


<table><tr><td>Method</td><td>VPR(raw)</td><td>VPR(eval)</td><td>DSR(raw)</td><td>DSR(eval)</td></tr><tr><td>M2S-DeepSeek</td><td>6/6</td><td>6/6</td><td>6/6</td><td>6/6</td></tr><tr><td>M2S-Sonnet</td><td>6/6</td><td>6/6</td><td>6/6</td><td>6/6</td></tr><tr><td>LLM-Baseline-DS</td><td>6/6</td><td>6/6</td><td>3/6</td><td>-</td></tr><tr><td>LLM-Baseline-SN</td><td>6/6</td><td>6/6</td><td>3/6</td><td>-</td></tr><tr><td>Cursor</td><td>2/6</td><td><eq>6/6^{\dagger}</eq></td><td>4/6</td><td><eq>6/6^{\dagger}</eq></td></tr><tr><td>Claude Code</td><td>3/6</td><td><eq>6/6^{\dagger}</eq></td><td>4/6</td><td><eq>6/6^{\dagger}</eq></td></tr></table>


raw = output without any fixes. eval = after applying evaluation-protocol fixes. Italic values difer from raw due to applied fixes. LLM-Baseline DSR failures are not fixed per evaluation protocol (§5.3). <sup>†</sup>Commercial baselines receive expert-assisted fixes for both validation and deployment failures. 


Table 5 reports deployment results. Mono2Sls reaches 100% VPR and DSR for both backbone models, without manual fixes. 

Commercial baselines produce invalid templates on 2–4 of the 6 applications. Common issues include reserved environment variables, invalid CORS settings, and unsupported Globals combinations. We do not observe these errors in Mono2Sls outputs, likely because generation is constrained by the SAM knowledge base. After expert-assisted validation fixes, both Cursor and Claude Code still fail to deploy 2 applications because of incorrect Lambda Layer directory structures. Mono2Sls avoids these failures through agent specialization and the Consistency Validator’s cross-artifact checks. 

Both LLM-Baseline variants achieve 6/6 VPR but only 3/6 DSR. Their failures arise from Cognito schema mis-declarations and API definition errors that are syntactically valid yet semantically wrong. cfn-lint does not catch these faults, but Mono2Sls’s Consistency Validator does through cross-artifact comparison with the blueprint and handler code. The same E-commerce identity configuration error appears under both backbone LLMs, which points to a limitation of single-context generation rather than a model-specific weakness: without separate phases for architecture design, code generation, and template validation, the system cannot reliably maintain structural coherence across all generated artifacts. LLM Baseline DSR values are reported as-is per the evaluation protocol (§5.3); no deployment fixes are applied. 

Mono2Sls is the only method that reaches 100% VPR and DSR without any manual intervention. The commercial baselines re quire expert-assisted fixes, and the LLM-Baseline variants still fail to deploy 3 of 6 applications despite producing structurally valid templates. 

## 6.2 RQ2: Functional Correctness


Table 6: API Co<sub>v</sub>era<sub>g</sub>e F1 Scores


<table><tr><td>Method</td><td>Micro-F1</td><td>Macro-F1</td><td>Anti-Pat.</td><td>Consumer</td></tr><tr><td>M2S-DeepSeek</td><td>1.000</td><td>1.000</td><td>0</td><td>11</td></tr><tr><td>M2S-Sonnet</td><td>0.987</td><td>0.986</td><td>1</td><td>2</td></tr><tr><td>LLM-Baseline-DS</td><td>0.9935</td><td>0.9928</td><td>1</td><td>0</td></tr><tr><td>LLM-Baseline-SN</td><td>0.974</td><td>0.969</td><td>2</td><td>0</td></tr><tr><td>Cursor</td><td>0.884</td><td>0.870</td><td>19</td><td>0</td></tr><tr><td>Claude Code</td><td>0.889</td><td>0.879</td><td>19</td><td>0</td></tr></table>


Anti-Pat. = Redundant auth endpoints. Consumer = Async consumer Lambdas (SQS/EventBridge-triggered, correctly excluded from API surface). 


6.2.1 API Coverage. Table 6 presents API Coverage F1 scores. Mono2Sls achieves near-perfect F1 (1.000 for M2S-DeepSeek, 0.987 for M2S-Sonnet), compared to LLM-Baseline variants (DS: 0.994, SN: 0.974) and commercial baselines (0.884–0.889). 

Much of the gap comes from authentication design. By delegat ing authentication to AWS Cognito, Mono2Sls does not generate redundant /register, /login, and /auth/me endpoints. Commer cial baselines generate 19 such extra APIs across the 6 applications, while LLM-Baseline-SN generates 2 and LLM-Baseline-DS gener ates 1; the smaller counts for the LLM baselines reflect the same domain knowledge that instructs the model to leave authentication to Cognito. 

The LLM-Baseline variants still achieve high API F1, which indicates that static analysis input and domain knowledge are often enough for a single-context model to recover the API surface cor rectly. Yet recovering the interface is not enough to ensure correct behaviour, as the next subsection shows. 


Table 7: End-to-End Pass Rate (%) on Core + Robustness Tests


<table><tr><td>Method</td><td>B1</td><td>B2</td><td>B3</td><td>B4</td><td>B5</td><td>B6</td><td>Micro</td><td>Macro</td></tr><tr><td>M2S-DeepSeek</td><td>100</td><td>33.3</td><td>93.8</td><td>71.4</td><td>50.0</td><td>51.7</td><td>64.5</td><td>66.7</td></tr><tr><td>M2S-Sonnet</td><td>100</td><td>33.3</td><td>93.8</td><td>76.2</td><td>50.0</td><td>55.2</td><td>66.1</td><td>68.1</td></tr><tr><td>LLM-Baseline-DS</td><td>N/A</td><td>33.3</td><td>31.3</td><td>N/A</td><td>37.5</td><td>N/A</td><td><eq>34.5^*</eq></td><td><eq>34.0^*</eq></td></tr><tr><td>LLM-Baseline-SN</td><td>37.5</td><td>N/A</td><td>N/A</td><td>23.8</td><td>41.7</td><td>N/A</td><td><eq>34.4^*</eq></td><td><eq>34.3^*</eq></td></tr><tr><td>Cursor</td><td>100</td><td>26.7</td><td>31.2</td><td>100</td><td>45.8</td><td>27.6</td><td>53.7</td><td>55.2</td></tr><tr><td>Claude Code</td><td>100</td><td>33.3</td><td>31.2</td><td>100</td><td>87.5</td><td>20.7</td><td>61.2</td><td>62.1</td></tr></table>


N/A = DSR=0 (LLM-Baseline, unfixed per protocol). <sup>∗</sup>Computed over 3 DSR-passing apps only (SN: B1, B4, B5; DS: B2, B3, B5). 


6.2.2 End-to-End Correctness. Table 7 compares end-to-end functional correctness. M2S-Sonnet obtains the highest micro-E2EPR at 66.1%, ahead of Claude Code (61.2%) and Cursor (53.7%). The margin is notable because the commercial baselines were allowed expert-assisted deployment fixes, whereas Mono2Sls was evaluated on raw output. The remaining gap therefore reflects generated code quality rather than deployment scafolding. 

LLM-Baseline Performance. Despite high API F1, both LLM-Baseline variants achieve only about 34% micro-E2EPR (34.5% for LLM-Baseline-DS and 34.4% for LLM-Baseline-SN), computed over the three applications each variant deploys successfully. The contrast exposes a structural-to-functional gap: a single-context LLM can often identify what APIs to generate while still failing to implement the underlying business logic, database access patterns, and cross-function coordination correctly. The F1-to-E2EPR gap reaches about 65pp for LLM-Baseline-DS (99.4% F1 vs. 34.5% micro-E2EPR) and about 63pp for LLM-Baseline-SN (97.4% vs. 34.4%), compared with about 33pp for M2S-Sonnet (98.7% vs. 66.1%). The smaller gap reflects the implementation benefit of staged decomposition and specialized prompting. 

Backbone LLM Comparison. M2S-Sonnet and M2S-DeepSeek produce similar results: E2EPR difers by 1.6pp (66.1% vs. 64.5%), and both reach 6/6 VPR and DSR. In our setting, pipeline design appears to matter more than the choice of backbone model. The same pattern appears in the LLM baselines, where SN and DS reach nearly identical E2EPR (34.4% vs. 34.5%). The roughly 32pp gap between Mono2Sls and the LLM baselines is therefore more plausibly explained by pipeline structure than by model strength. 

## 6.3 RQ3: Contribution of Static-Analysis-Driven A<sub>rc</sub>hit<sub>ec</sub>t<sub>ure</sub> Pl<sub>ann</sub>i<sub>ng</sub>

We compare the ablation variant (w/o SA&Arch) against M2S-Sonnet (the same backbone LLM used in the ablation) across deployability, API coverage, and end-to-end correctness. 


T<sub>a</sub>bl<sub>e</sub> 8: Abl<sub>a</sub>ti<sub>o</sub>n R<sub>esu</sub>lt<sub>s</sub>: M2S-S<sub>o</sub>nn<sub>e</sub>t <sub>vs. w</sub>/<sub>o</sub> SA&Ar<sub>c</sub>h


<table><tr><td>App</td><td>DSR</td><td>F1</td><td>E2EPR</td><td>Failure Mode</td></tr><tr><td>B1: Todo</td><td>√</td><td>0.833</td><td>56.3%</td><td>None</td></tr><tr><td>B2: Shopping Cart</td><td>√</td><td>1.000</td><td>20.0%</td><td>None</td></tr><tr><td>B3: Bookstore</td><td>✕</td><td>1.000</td><td>N/A</td><td>Env var not injected</td></tr><tr><td>B4: Coffee</td><td>✕</td><td>0.957</td><td>N/A</td><td>Path variable conflict</td></tr><tr><td>B5: Airline</td><td>✕</td><td>0.714</td><td>N/A</td><td>Layer double-nesting</td></tr><tr><td>B6: E-commerce</td><td>√</td><td>0.962</td><td>37.9%</td><td>None</td></tr><tr><td>w/o SA&amp;Arch</td><td>3/6</td><td>0.915</td><td>38.3%*</td><td></td></tr><tr><td>M2S-Sonnet</td><td>6/6</td><td>0.987</td><td>66.1%†</td><td></td></tr></table>


VPR = 6/6 for both variants. M2S-Sonnet F1 and overall E2EPR from Tables 6–7. <sup>∗</sup>micro-E2EPR over 3 DSR-passing applications (B1, B2, B6). 



<sup>†</sup>Overall micro-E2EPR (all 6 apps); on the same 3-app subset (B1, B2, B6), M2S-Sonnet achieves 61.7%. 


Table 8 shows a clear drop in performance without static analysis and architectural planning. 

Deployability (DSR: 6/6→3/6). The root cause is the absence of blueprint.json as a shared contract: without a centrally authored endpoint inventory and resource specification, each agent must independently infer structure from source code, leading to cross-artifact inconsistencies that survive template validation but break at deployment. Concretely, all three DSR failures involve handler–template mismatches that cfn-lint cannot detect: missing environment variable injections, inconsistent API path param eters across sibling routes, and incorrect Lambda Layer directory structures. These are precisely the failure modes the Architect and Consistency Validator are designed to prevent. 

API Coverage (F1: 0.987→0.915). The F1 drop reflects RESTshape drift, not only missing endpoints. Applications with flat API structures (B2, B3) retain perfect F1, whereas those with more complex routing (B1, B5) regress the most. The drift appears as resource hierarchy changes (e.g., B5: /customers/{id}/bookings → /bookings/customer/{id}), HTTP method reassignment (B5: POST → PUT for state transitions, leading to HTTP 405 errors for conforming clients), and path compression in the multi-domain B6 application. Static analysis therefore does more than point to relevant files; it constrains the space of plausible API designs. 

End-to-End Correctness. On the 3 applications deployable by both variants (B1, B2, B6), M2S-Sonnet reaches 61.7% micro-E2EPR (computed from Table 7: (16+5+16)/(16+15+29)), compared with 38.3% for w/o SA&Arch, a 23.4pp gap on the same application set. Without blueprint-defined Lambda boundaries and dependency relationships, the Code Developer must read the monolith, infer endpoint semantics, and generate handlers at the same time. That broader context hurts implementation accuracy even when deploy ment still succeeds. 

Overall, removing static-analysis-driven architecture planning degrades all three quality dimensions: DSR (6/6→3/6), API Coverage F1 (0.987→0.915), and micro-E2EPR on the comparable deployable subset (61.7%→38.3%). The ablation therefore supports the claim that pre-computed structural analysis plus explicit archi tectural planning adds value beyond direct source-to-target generation. 

## 6.4 RQ4: Cloud-Native Design Adoption

Beyond functional correctness, we also examine whether the gen erated applications adopt AWS-native architectural patterns in two areas. (1) Authentication: AWS Cognito handles user registration, token issuance, and token validation as a managed service; API Gateway authorizers can consume Cognito tokens directly, remov ing authentication logic from Lambda handlers. (2) Asynchronous communication: AWS SQS and EventBridge let producers and consumers communicate through queues or event buses, which better matches the stateless execution model of serverless systems. 

6.4.1 Authentication Architecture. Mono2Sls uses AWS-native authentication in all 6 applications for both backbone models. API Gateway Cognito authorizers [1] protect the routes, handlers read validated claims from the authorizer context, and no custom authen tication logic remains in the generated code. In contrast, the commercial baselines retain the monolith’s JWT-based design in all 6 applications for both Cursor and Claude Code, including applicationmanaged token validation, explicit /register//login endpoints, and developer-maintained middleware. Figure 4 (a) shows this con trast on the Cofee Shop application. 

6.4.2 Asynchronous Communication. Across the 4 applications with detectable event-driven signals in the monolith source (B3– B6), Mono2Sls often reconstructs full event-driven chains. M2S-DeepSeek does so in all 4 applications; M2S-Sonnet does so in 2 and produces partial async structure in the other 2 (producer wired, but not all consumers generated). In both variants, the Architect records the communication pattern in blueprint.json, and the downstream agents implement it in code and infrastructure. By contrast, the commercial baselines generate purely synchronous architectures across all 4 applications: neither Cursor nor Claude Code attempts event-driven integration despite clear async signals in the source. Figure 4 (b) shows the diference on the Cofee Shop application. 

6.4.3 LLM-Baseline: Hybrid Architecture and Responsibility Confusion. LLM-Baseline variants use AWS domain knowledge, but they often produce hybrid designs that mix cloud-native and monolithstyle components within one application. 

Qualitative inspection suggests that this pattern comes from blurred architectural responsibilities. First, several applications provision Cognito User Pools while still keeping redundant Users DynamoDB tables, which shows that the model knows how to add Cognito but not when it should replace custom identity storage. Second, some applications declare Cognito resources but omit or misconfigure API Gateway authorizers, leaving part of the authentication logic inside handlers. Third, some async-capable applications fall back to synchronous implementations: queues appear in the template, but no consumer Lambdas are generated. LLM-Baseline-DS even combines correct async adoption with incorrect authentication redesign in the same application. This mixed outcome is consistent with a single-context setup that has to resolve several architectural concerns at once. Mono2Sls avoids this by assigning each concern to a narrower agent context. 

## 7 Di<sub>scuss</sub>i<sub>o</sub>n

Mono2Sls is intended for teams maintaining monolithic web backends under scalability and operational cost pressures. Given a monolith repository, the pipeline can produce a deployable SAM appli cation, including Lambda handlers, infrastructure template, and shared layers, without requiring cloud expertise from the developer. M2S-Sonnet required 58–106 minutes per benchmark application (601–3,623 LoC), with execution time dominated by the Code Devel oper’s iterative source transformation and scaling with application size and backbone LLM choice. By automating architectural decomposition, authentication transformation, and IaC generation, Mono2Sls reduces what practitioners often describe as weeks of manual refactoring efort to hours of automated processing per application. 

## 8 Li<sub>m</sub>it<sub>a</sub>ti<sub>ons</sub> <sub>an</sub>d Th<sub>rea</sub>t<sub>s</sub> t<sub>o</sub> V<sub>a</sub>lidit<sub>y</sub>

Benchmark Scope. Our benchmark contains 6 applications derived from AWS reference architectures. Although it covers both major serverless languages and a range of sizes (601–3,623 LoC, 6–26 observable business endpoints), it does not cover every monolith pattern, especially legacy enterprise systems with heavy ORM usage or multiple databases. Future evaluation should include larger industrial applications. 

![image](https://cdn-mineru.openxlab.org.cn/result/2026-09-08/0b6b7ca2-d427-472f-8904-017dd8023ea5/1424ca1b1bfa61d992157a29fcdee2342f9d646f88921b626423299612f0bfe2.jpg)


![image](https://cdn-mineru.openxlab.org.cn/result/2026-09-08/0b6b7ca2-d427-472f-8904-017dd8023ea5/08d95b5752c5f701d21af7a9ede4cc24a3a41fbda95797925a3a17b007d01b37.jpg)



Figure 4: Case Study: architecture contrast on the Cofee Shop application (RQ4). (a) Authentication: Mono2Sls routes requests throu<sub>g</sub>h API Gatewa<sub>y</sub> with a Co<sub>g</sub>nito Authorizer<sub>,</sub> eliminatin<sub>g</sub> custom auth lo<sub>g</sub>ic from Lambda handlers<sub>;</sub> commercial baselines replicate the monolith’s JWT middleware pattern. (b) Asynchronous communication: Mono2Sls builds a complete SQS eventd<sub>r</sub>i<sub>ven c</sub>h<sub>a</sub>i<sub>n w</sub>ith d<sub>ecoup</sub>l<sub>e</sub>d <sub>pro</sub>d<sub>ucer an</sub>d <sub>consumer</sub> L<sub>am</sub>bd<sub>as; commerc</sub>i<sub>a</sub>l b<sub>ase</sub>li<sub>nes pro</sub>d<sub>uce sync</sub>h<sub>ronous</sub> di<sub>rec</sub>t <sub>wr</sub>it<sub>es.</sub>


AWS-Specific. Mono2Sls currently targets AWS SAM. The core ideas in the pipeline, namely static analysis, staged decomposition, and domain knowledge, should transfer to other platforms, but support for Azure Functions or Google Cloud Run would require platform-specific knowledge bases and generators. 

Async Correctness. While Mono2Sls achieves 100% structural correctness for async design (RQ4), only 67% of async end-to-end tests pass for M2S-Sonnet. We observe two main failure categories: (1) upstream confounding, where earlier operations fail before the async workflow is reached (e.g., payment integration in Airline Booking), and (2) eventual consistency bugs, where producers and consumers are wired correctly but still contain logic errors, such as incorrect event payload schemas or missing idempotency keys. The first category reflects failures outside the async mechanism itself; the second points to limits in current LLM reasoning about distributed semantics. 

Internal Validity. LLM inference is non-deterministic, and our pass@1 evaluation does not measure run-to-run variance. Single run evaluation is standard in repository-level code generation [25], but it remains a limitation. We expect variance to be smaller here than in open-ended generation because the static-analysis inputs are deterministic and each agent follows explicit task instructions with concrete AWS constraints. Still, only repeated runs can confirm that expectation. Multi-run evaluation with statistical testing is left to future work. 

## 9 C<sub>o</sub>n<sub>c</sub>l<sub>us</sub>i<sub>o</sub>n

Mono2Sls is a static-analysis-driven multi-stage pipeline for automated monolith-to-serverless migration. Rather than asking a single model to solve the entire migration problem at once, Mono2Sls separates the work into four sequential stages handled by specialized tool-using LLM agents and grounds their decisions in static-analysis outputs and a curated AWS knowledge base. The staged organization helps keep architectural decomposition, code transformation, and infrastructure generation aligned more reliably than singlecontext generation. 

Across 6 benchmark applications (10K+ LoC, 76 business endpoints), Mono2Sls achieves 100% deployment success without manual intervention, 66.1% micro-average end-to-end correctness, 98.7% API-coverage F1, and stronger adoption of AWS-native authentication and asynchronous patterns than the baselines. Commercial baselines trail these results even after expert-assisted deployment fixes, and the ablation study shows that static-analysis-driven architecture planning adds 23.4 percentage points to end-to-end cor rectness on the comparable deployable subset. 

The main limitations are benchmark scale and AWS specificity. The current pipeline also does not fully address stateful HTTP session patterns or eventual-consistency edge cases in async work flows. Extending the approach to other serverless platforms and improving LLM reasoning about distributed event semantics are natural next steps. 

## D<sub>a</sub>t<sub>a</sub> A<sub>va</sub>il<sub>a</sub>bilit<sub>y</sub>

Our pipeline code, benchmarks, and test suites are available at: https://doi.org/10.5281/zenodo.19230004. 

## A<sub>c</sub>k<sub>now</sub>l<sub>e</sub>d<sub>gmen</sub>t<sub>s</sub>

Parts of this paper were written and revised with the assistance of AI language models (including Claude by Anthropic). All experimental results, technical claims, figures, and tables are the authors’ own work; AI tools were used solely for language editing, text organization, and literature search assistance. 

## R<sub>e</sub>f<sub>erences</sub>



[1] Amazon Web Services. 2024. Amazon Cognito Developer Guide. https://docs. aws.amazon.com/cognito/latest/developerguide/what-is-amazon-cognito.html. 





[2] Amazon Web Services. 2024. Amazon EventBridge User Guide. https://docs.aws. amazon.com/eventbridge/latest/userguide/eb-what-is.html. 





[3] Amazon Web Services. 2024. Amazon Simple Queue Service Developer Guide. https://docs.aws.amazon.com/AWSSimpleQueueService/latest/ SQSDeveloperGuide/welcome.html. 





[4] Amazon Web Services. 2024. AWS Lambda — Serverless Compute. https://aws. amazon.com/lambda/. 





[5] Amazon Web Services. 2024. aws-samples: AWS Sample Applications and Refer ence Architectures. https://github.com/aws-samples 





[6] Amazon Web Services. 2024. AWS Serverless Application Model (SAM) Devel oper Guide. https://docs.aws.amazon.com/serverless-application-model/latest/ developerguide/what-is-sam.html. 





[7] Amazon Web Services. 2024. cfn-lint: CloudFormation Linter. https://github. com/aws-cloudformation/cfn-lint 





[8] Amazon Web Services. 2024. Deploy Node.js Lambda Functions with .zip File Archives. https://docs.aws.amazon.com/lambda/latest/dg/nodejs-package.html. 





[9] Amazon Web Services. 2024. Understanding the Lambda Execution Environment Lifecycle. https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimeenvironment.html. 





[10] Amazon Web Services. 2024. Working with Lambda Layers — Packaging Layer Content. https://docs.aws.amazon.com/lambda/latest/dg/packaging-layers.html. 





[11] Shrikara Arun, Meghana Tedla, and Karthik Vaidhyanathan. 2025. LLMs for Generation of Architectural Components: An Exploratory Empirical Study in the Serverless World. In Proceedings ofthe 22nd IEEE International Conference on Software Architecture (ICSA). IEEE. 





[12] Jacob Austin, Augustus Odena, Maxwell Nye, Maarten Bosma, Henryk Michalewski, David Dohan, Ellen Jiang, Carrie Cai, Michael Terry, Quoc Le, and Charles Sutton. 2021. Program Synthesis with Large Language Models. arXiv:2108.07732 https://arxiv.org/abs/2108.07732 





[13] Calyo Consulting. 2026. Serverless Computing in Enterprise 2026: What You Need to Know. https://www.calyo-consulting.fr/en/resources/10-serverlesscomputing-enterprise-2026. 





[14] Mark Chen, Jerry Tworek, Heewoo Jun, Qiming Yuan, Henrique Ponde de Oliveira Pinto, Jared Kaplan, Harri Edwards, Yuri Burda, Nicholas Joseph, Greg Brockman, Alex Ray, Raul Puri, Gretchen Krueger, Michael Petrov, Heidy Khlaaf, Girish Sastry, Pamela Mishkin, Brooke Chan, Scott Gray, Nick Ryder, Mikhail Pavlov, Alethea Power, Lukasz Kaiser, Mohammad Bavarian, Clemens Win ter, Philippe Tillet, Felipe Petroski Such, Dave Cummings, Matthias Plappert, Fotios Chantzis, Elizabeth Barnes, Ariel Herbert-Voss, William Hebgen Guss, Alex Nichol, Alex Paino, Nikolas Tezak, Jie Tang, Igor Babuschkin, Suchir Bal aji, Shantanu Jain, William Saunders, Christopher Hesse, Andrew N. Carr, Jan Leike, Joshua Achiam, Vedant Misra, Evan Morikawa, Alec Radford, Matthew Knight, Miles Brundage, Mira Murati, Katie Mayer, Peter Welinder, Bob Mc-Grew, Dario Amodei, Sam McCandlish, Ilya Sutskever, and Wojciech Zaremba. 2021. Evaluating Large Language Models Trained on Code. arXiv:2107.03374 https://arxiv.org/abs/2107.03374 





[15] Leonardo Rebôuças de Carvalho and Eduardo Araujo Oliveira. 2023. FaaS-Oriented Node.js Applications in an RPC Approach Using the Node2FaaS Framework. IEEE Access 11 (2023). 





[16] Simon Eismann, Joel Scheuner, Erwin van Eyk, Maximilian Schwinger, Johannes Grohmann, Nikolas Herbst, Cristina L. Abad, and Alexandru Iosup. 2022. The State of Serverless Applications: Collection, Characterization, and Community Consensus. IEEE Transactions on Software Engineering 48, 10 (2022), 4066–4086. 





[17] Zhangyin Feng, Daya Guo, Duyu Tang, Nan Duan, Xiaocheng Feng, Ming Gong, Linjun Shou, Bing Qin, Ting Liu, Daxin Jiang, and Ming Zhou. 2020. CodeBERT: A Pre-Trained Model for Programming and Natural Languages. In Findings ofthe Association for Computational Linguistics: EMNLP 2020. Association for Computa tional Linguistics. 





[18] GlobeNewsWire. 2026. Serverless Computing Market Surges to $44.7 Billion by 2029, CAGR 15.3%. https://globenewswire.com/newsrelease/2026/02/10/3235443/0/en/Serverless-Computing-Market-Surges-to-44- 7-billion-by-2029-CAGR-15-3.html. 





[19] Muhammad Hamza, Muhammad Azeem Akbar, and Kari Smolander. 2023. The Journey to Serverless Migration: An Empirical Analysis of Intentions, Strategies, and Challenges. arXiv:2311.13249 https://arxiv.org/abs/2311.13249 





[20] Joseph M. Hellerstein, Jose Faleiro, Joseph E. Gonzalez, Johann Schleier-Smith, Vikram Sreekanti, Alexey Tumanov, and Chenggang Wu. 2019. Serverless Computing: One Step Forward, Two Steps Back. In Proceedings ofthe 9th Biennial Conference on Innovative Data Systems Research (CIDR). 





[21] Xinyi Hou, Yanjie Zhao, Yue Liu, Zhou Yang, Kailong Wang, Li Li, Xiapu Luo, David Lo, John Grundy, and Haoyu Wang. 2024. Large Language Models for Software Engineering: A Systematic Literature Review. ACM Transactions on Software Engineering and Methodology (2024). 





[22] Ali Reza Ibrahimzada, Kaiyao Ke, Mrigank Pawagi, Muhammad Salman Abid, Rangeet Pan, Saurabh Sinha, and Reyhaneh Jabbarvand. 2025. AlphaTrans: A Neuro-Symbolic Compositional Approach for Repository-Level Code Translation and Validation. Proceedings of the ACM on Software Engineering 2, FSE (2025). 





[23] Nihal Jain, Robert Kwiatkowski, Baishakhi Ray, Murali Krishna Ramanathan, and Varun Kumar. 2024. On Mitigating Code LLM Hallucinations with API Documentation. arXiv:2407.09726 https://arxiv.org/abs/2407.09726 





[24] Juyong Jiang, Fan Wang, Jiasi Shen, Sungju Kim, and Sunghun Kim. 2024. A Survey on Large Language Models for Code Generation. arXiv:2406.00515 https: //arxiv.org/abs/2406.00515 





[25] Carlos E. Jimenez, John Yang, Alexander Wettig, Shunyu Yao, Kexin Pei, Ofir Press, and Karthik Narasimhan. 2024. SWE-bench: Can Language Models Resolve Real-World GitHub Issues?. In Proceedings ofthe 12th International Conference on Learning Representations (ICLR). OpenReview.net. https://arxiv.org/abs/2310. 06770 





[26] Anup K. Kalia, Jin Xiao, Saurabh Sinha, Maja Vukovic, and Debasish Banerjee. 2020. Mono2Micro: An AI-Based Toolchain for Evolving Monolithic Enterprise Applications to a Microservice Architecture. In Proceedings ofthe 28th ACM Joint Meeting on European Software Engineering Conference and Symposium on the Foundations ofSoftware Engineering (ESEC/FSE). ACM. 





[27] Patrick Lewis, Ethan Perez, Aleksandra Piktus, Fabio Petroni, Vladimir Karpukhin, Naman Goyal, Heinrich Küttler, Mike Lewis, Wen tau Yih, Tim Rocktäschel, Sebastian Riedel, and Douwe Kiela. 2020. Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. In Advances in Neural Information Processing Systems (NeurIPS), Vol. 33. Curran Associates, Inc., 9459–9474. 





[28] Junwei Liu, Yixuan Chen, Mingwei Liu, Xin Peng, and Yiling Lou. 2024. STALL+: Boosting LLM-based Repository-level Code Completion with Static Analysis. arXiv:2406.10018 https://arxiv.org/abs/2406.10018 





[29] Zhiyuan Peng, Xin Yin, Pu Zhao, Fangkai Yang, Lu Wang, Ran Jia, Xu Chen, Qingwei Lin, Saravan Rajmohan, and Dongmei Zhang. 2026. RepoGenesis: Benchmarking End-to-End Microservice Generation from Readme to Repository. 





arXiv:2601.13943 https://arxiv.org/abs/2601.13943 





[30] Protiviti. 2023. Technical Debt Remains a Major Burden. https://www.protiviti. com/de-de/global-technology-executive-survey-tech-debt-major-burden. 





[31] Yamina Romani, Okba Tibermacine, and Chouki Tibermacine. 2022. Towards Migrating Legacy Software Systems to Microservice-based Architectures: A Data-Centric Process for Microservice Identification. In Proceedings ofthe 19th IEEE International Conference on Software Architecture Companion (ICSA-C). IEEE 





[32] Josef Spillner and Serhii Dorodko. 2017. Java Code Analysis and Transformation into AWS Lambda Functions. arXiv:1702.05510 https://arxiv.org/abs/1702.05510 





[33] Yingying Wang, Sarah Bornais, and Julia Rubin. 2024. Microservice Decomposition Techniques: An Independent Tool Comparison. In Proceedings of the 39th IEEE/ACM International Conference on Automated Software Engineering (ASE). ACM. 





[34] Jinfeng Wen, Zhenpeng Chen, Xin Jin, and Xuanzhe Liu. 2023. Rise of the Planet of Serverless Computing: A Systematic Review. ACM Transactions on Software Engineering and Methodology 32, 5 (2023). 





[35] Jinfeng Wen, Zhenpeng Chen, Yi Liu, Yiling Lou, Yun Ma, Gang Huang, Xin Jin, and Xuanzhe Liu. 2021. An Empirical Study on Challenges of Application Development in Serverless Computing. In Proceedings ofthe 29th ACM Joint Meeting on European Software Engineering Conference and Symposium on the Foundations ofSoftware Engineering (ESEC/FSE). ACM, 416–428. 





[36] Jinfeng Wen, Zhenpeng Chen, Federica Sarro, Zixi Zhu, Yi Liu, Haodi Ping, and Shangguang Wang. 2024. LLM-Based Misconfiguration Detection for AWS Serverless Computing. In ACM Transactions on Software Engineering and Methodology. 





[37] Kechi Zhang, Jia Li, Ge Li, Xianjie Shi, and Zhi Jin. 2024. CodeAgent: Enhancing Code Generation with Tool-Integrated Agent Systems for Real-World Repo-Level Coding Challenges. In Proceedings ofthe 62nd Annual Meeting ofthe Association for Computational Linguistics (ACL). Association for Computational Linguistics. 





[38] Lulai Zhu, Damian Andrew Tamburri, and Giuliano Casale. 2023. RADF: Architecture Decomposition for Function as a Service. Software: Practice and Experience (2023). 

