# M2S 流水线 v3 实施方案

> **状态**：实施方案（2026-10-30 定稿）。M0 已完成；M1 的 W1.1–W1.10 已完成（2026-10-31），W1.11 的 bookstore 等价运行进行中（2026-11-01：第 1、2 段已跑完，见 W1.11 的说明）。
>
> **概览网页**：`PIPELINE_V3_overview.html`（同目录，可以直接用浏览器打开）。
>
> **依据**：
> - 讨论过程和取舍理由：`PIPELINE_V3_EXPLORATION.md`（commit 5f4ff39），下文简称"探索稿"；
> - 模块定义：`PIPELINE_REDESIGN_v2.md`（commit 43f5c59），下文简称"v2"。
>
> **标记**：
> - **【必做】** 本方案的交付范围；
> - **【扩展】** 后续扩展，不在本方案的交付范围（§9）；
> - 写方案时新提出的 5 点设计（V3-09 至 V3-13），已于 2026-10-30 按建议确认（§10、§11）。

---

## 0. 一页总览

**目标**：把"一个 LLM 主 Agent 从头到尾撑完整条流水线"的编排方式，换成下面三样东西：
- **任务板 + 确定性调度器**：任务和依赖记在 Beads（`br`）里；由一个 Python 调度器发布任务、启动 worker、运行完成检查；
- **工作包粒度的并行**：几个相关接口一组，作为一个任务单位；
- **公告板和讨论室**：反馈不再全部经过主 Agent 转发。

同时保证两件事：隔离规则（谁能看到什么）真正得到执行；运行结果可以复现。

**不变的部分**：
- 各模块的职责、产物和 gate 工具；
- 裁判；
- 裁定通道的规则（v2 §4.12）；
- agent 定义（`~/.openharness/agents/*.md`）。

v3 改的是**谁来调度、怎么通信**，不改**每一步做什么**。

**交付物**：

| 交付物 | 位置 |
|---|---|
| 协调层代码：调度器、任务板封装、模板展开、worker 启动、事件日志、异常处理、资源锁、工作包规划 | `m2s-mcp-server/coord/`（新） |
| m2s-server 新增的协调工具 | `m2s-mcp-server/server.py` 和 `coord/tools.py` |
| 结构提取的改进（同文件辅助函数解析、MongoDB 访问模式挂接到接口） | `spec_extract.py`、`data_store.py`、`spec_extract_py.py` |
| OpenHarness 的权限修复和命令行参数修复 | `OpenHarness/src/openharness/` |
| 流水线模板：v1 模块版（先做），v2 模块版（v2 模块实现后） | `coord/templates/` |
| 测试：假 worker、单元测试、故障注入测试、权限测试、泄漏测试 | `m2s-mcp-server/tests/test_coord_*.py` |

**里程碑**：

| 里程碑 | 内容 | 验收标准（摘要） | 范围 |
|---|---|---|---|
| **M0 准备** | 先修复 OpenHarness 的命令行参数（W0.1，第一项任务）；再修权限、固定 `br` 和 agent_mail 的版本 | 权限测试全部通过；两个工具的冒烟测试通过 | 【必做】 |
| **M1 协调骨架** | 调度器 + 任务板，按 v1 的顺序串行跑 v1 模块 | bookstore 在无人值守的情况下跑完，验收结果和现有的 v1 运行一致；杀掉调度器或 worker 后能自动恢复 | 【必做】 |
| **M2 工作包并行** | 结构提取改进；工作包规划；按包展开任务；文件独占和资源锁 | bookstore、acmeair 的验收结果和 M1 一致；关键路径变短；没有越界写入 | 【必做】 |
| **M3 公告板、讨论室、可见性** | agent_mail；规格讨论室和分诊讨论室；按角色过滤可见内容 | 讨论室都由工具写入结论；泄漏测试为 0 | 【必做】 |
| **M4 接入 v2 模块** | v2 的模块实现以后，作为新的任务类型接入；O 阶段在 AWS 上实测 | v2 流水线在 v3 调度下跑通 bookstore 和 acmeair | 【必做】，前提是 v2 模块已经实现 |
| **M5 对抗评审室** | 第一次升级 harness 时，压测规则 | 产出规则修正候选，经人批准 | 【必做】，在第一次升级 harness 时做 |

**主要的后续扩展**（§9）：
- 执行层替换实验；
- Temporal 耐久执行；
- 保留用例；
- 容器沙箱；
- 更好的分组方法；
- 上百个接口的单体的分层设计；
- 常驻 worker 自行认领任务。

---

## 1. 起点

### 1.1 现状

- **v1 已经实现，正在使用**：
  - 编排协议在 `.openharness/skills/m2s-migrate/SKILL.md`：主 Agent 按 P0 → S0 → S1 → V1 → S2 → S3a/b/c → S4 → V2 → 打 tag v0 → S5 → 阶段 6 的顺序派发子 agent。
  - 外层驱动是 `oh_chain.py`：每个工作区启动一次 `oh -p` 主会话；会话中断了就带着续跑说明重启，最多重试若干次。
  - 工作区靠修改全局的 `~/.openharness/settings.json` 里 m2s-server 的 `M2S_WORKSPACE` 来切换，所以**同一时间只能跑一个工作区**。
- **v2 只有设计稿**，新模块（裁判线 J、增量测试 D、适配检查等）都还没有实现。
- **v3 的 7 个问题都已经定了**（探索稿 §8）。

### 1.2 环境（本机）

- Windows 11；Git Bash 和 PowerShell。
- conda 环境 `mono2esls`：OpenHarness（可编辑安装，源码在 `OpenHarness/src/`）、`mcp` 1.30、sam、boto3、cfn-lint。
  - **协调层 `coord/` 也在这个环境里运行**（2026-10-30 定），和 `oh` 在同一个环境里。调度器直接调用 `gates.py` 等模块，m2s-mcp-server 的测试在这个环境里同样全部通过（194 个通过，3 个跳过）。
  - m2s-server 本身仍按现有配置，在 `mono2sls` 环境里运行。
- 本机 Docker 不稳定；可以在 AWS 上实测。
- 2026-10-30 实测：`br` 0.7.6 和 mcp_agent_mail_rust 0.3.39 都可以在本机使用（探索稿 §6.4）。

### 1.3 OpenHarness 的现状（2026-10-30 读代码确认）

| 项 | 现状 | 对 v3 的影响 |
|---|---|---|
| `oh` 的 `--settings`、`--allowed-tools`、`--disallowed-tools`、`--mcp-config` | `cli.py` 里声明了这几个参数，但**没有任何代码使用它们**，传了也不起作用 | 不能按任务传入权限设置；要修（W0.1）。**已修**（2026-10-30，见 W0.1） |
| `-p` 模式下的 `--permission-mode`、`--append-system-prompt` | `run_print_mode` 收到了这两个参数，但没有传给 `build_runtime`，所以同样不起作用；dry-run 预览里的 `--append-system-prompt` 会把默认的系统提示词整个替换掉 | 一并在 W0.1 修好 |
| 工具允许名单（`allowed_tools`） | 列在允许名单里的工具，**会跳过路径规则和命令规则**，因为检查器先查允许名单。你的全局设置里就把 m2s-server 的工具都列了进去 | 调度器生成的设置不使用允许名单（统一用 `full_auto`）；在 W0.3 里改检查顺序，让禁止规则优先 |
| `OPENHARNESS_CONFIG_DIR` 环境变量 | 可以让一个进程使用另一个配置目录 | 修好之前的临时办法：每个 worker 一个配置目录 |
| 权限检查器（`permissions/checker.py`） | 支持工具禁止名单、路径规则、命令禁止规则（通配符匹配整条命令）；禁止规则在 `full_auto` 模式下也生效 | 能力够用，但要能按任务设置 |
| 路径规则 | 只有允许和禁止，**不区分读和写** | 表达不了"只准写自己的目录，其他地方可以读"；要扩展（W0.3） |
| 子 agent 的权限 | `agent_tool.py` 把 agent 定义里的 `permissions` 传了进去，但 `spawn_utils.py` 启动子进程时只带了 `--permission-mode` | v3 的 worker 由调度器直接启动成顶层进程，不再经过这条路径；顺手修（W0.4） |
| 子 agent 会绕过父进程的限制（W0.4 时发现） | 子进程以 `--task-worker` 启动，只读全局的 `settings.json`。父进程通过 `--settings` 拿到的禁止规则不会传下去，所以受限的 worker 只要开一个子 agent，就能绕过限制。另外几处问题：<br>- 参数值被 `shlex.quote` 包了一层引号，但子进程是直接按参数列表启动的，引号会原样留在值里；<br>- 内置 agent 定义用的是 Claude Code 的工具名（`Read`、`file_edit`），和 OpenHarness 的实际名称（`read_file`、`edit_file`）对不上，所以定义里的工具限制从来没有生效过 | W0.4 一并修好 |
| MCP | 支持 stdio 和 HTTP（streamable HTTP，可以带请求头） | agent_mail 只能用 HTTP 方式共用（探索稿 §6.4） |

---

## 2. 和 v2 的先后关系（已定，V3-09）

**已定（2026-10-30）：v3 先在现有的 v1 模块上落地（M0–M3），v2 的模块实现以后，再作为新的任务类型接入（M4）。**

理由：
1. 探索稿 §1 列的四个问题（只回复文字进程就退出、`max_turns` 用完、OOM 后只能人工续跑、工具进程用着旧版代码）在**每一次运行**里都会出现，开发和验证 v2 模块要跑很多次，先解决调度问题，后面的每次运行都更省事。
2. v1 已经有跑完的参照运行（bookstore-oh 等），可以用来检查"换了调度方式，结果还一致"。v2 还没有可以对照的运行。
3. v3 的调度器不关心模块内部做什么，只认"任务类型、依赖、完成检查、产物"。v2 的模块接进来，只需要新增模板条目和完成检查，不用改调度器。

代价是：有几项 v3 的能力，要等 v2 的模块接入后才能完整测试，包括：
- 裁判冻结；
- 事实库版本变化引起的任务失效；
- 增量测试的可见性；
- B0 的阶段复查。

M1–M3 只能用 v1 里的对应物来测这些能力，比如用 blueprint 版本变化来测失效传播。

另一种做法是先实现 v2 再做 v3，那么 v2 的开发期间还要继续忍受主 Agent 撑长会话的问题。

---

## 3. 总体架构

```
                 人：处理 needs_human；可以在 agent_mail 的 Web UI 里旁观；参与 U3
                   │
┌──────────── 调度器 coord.dispatcher（Python，确定性，可以随时杀掉重启）─────────────┐
│ 状态：WS/coord/beads/（br 任务板）· WS/coord/events.jsonl（事件日志）                   │
│       WS/coord/journal.md（异常处理记录）· WS/coord/cases/（案情材料）                   │
│ 职责：展开任务 → 选出可以开始的任务 → 认领 → 启动 worker → 运行完成检查 → 关闭、回炉或失效 │
└───┬───────────────────────────────┬──────────────────────────────┬──────────────────┘
    │ 每个任务启动一次                │ 出现异常时启动                 │ 直接调用
    ▼                               ▼                              ▼
 oh -p worker（角色 = agent 定义） oh -p 异常处理者               gates.py / analyze_project 等
    │ stdio                          │ stdio
    ▼                               ▼
 m2s-server（每个 worker 一个进程，带 M2S_WORKSPACE、M2S_TASK_ID、M2S_ROLE）
    │ 工具：原有 gate 工具 + submit_task、report_blocked、notice_*、room_*、record_ruling ……
    ├── 写：自己任务范围内的 WS 文件；WS/coord/submissions/
    ├── HTTP ──► agent_mail（本机一个共用服务，STORAGE_ROOT=D:\m2s-am）
    └── 文件锁 ──► D:\m2s-locks\（本机范围的资源锁）
```

| 组件 | 职责 | 谁写它 |
|---|---|---|
| 任务板（`br`） | 任务、依赖、状态、门禁结果 | **只有调度器** |
| 事件日志 | 每个任务的输入版本、执行者、模型、结果，用于复现 | 调度器 |
| 提交记录（`WS/coord/submissions/`） | worker 交上来的东西：完成、阻塞、提出问题 | m2s-server（代 worker 写） |
| agent_mail | 公告板、讨论室的消息存档；给人看的 Web UI | m2s-server（代 worker 写）、调度器 |
| 工作区里的产物 | 各模块的产物 | worker，只能写自己任务的 `scope.paths` |

---

## 4. 组件规格

### 4.1 任务板（`br`）【必做】

- **位置**：`WS/coord/beads/`。调度器调用 `br` 时一律带上这个目录对应的 `--db` 参数。
  - 工作区有自己的 git 仓库（v0 tag 就打在里面）。工作区的 `.gitignore` 排除 `coord/`，任务板不进这个仓库，在归档时另外保存。
  - 所有 worker 都**禁止读** `WS/coord/**`（§4.10）。
- **一个任务就是一个 issue**：
  - 标题：任务 ID，例如 `S2:flights`；
  - 标签：`kind:S2`、`role:m2s-code-developer`、`pkg:flights`；
  - 描述：一段 JSON 格式的任务说明（附录 C）。
- **依赖**：只用 `blocks`。工作包靠标签表示，不用父子关系，以免影响"可以开始"的判定。
- **策略文件**：`WS/coord/beads/.beads/policy.yaml`，全文见附录 B，要点如下：
  - 状态：`open`、`in_progress`、`checking`、`rework`、`stale`、`needs_human`、`closed`，并设成严格模式（不在允许列表里的状态迁移一律拒绝）；
  - "可以开始"的任务包括 `open` 和 `rework` 两种状态；
  - `checking -> closed` 必须有 `done_check` 门禁通过；
  - 顶层写 `allow_bypass: false`；
  - `needs_human -> closed` 必须附带一条迁移说明（`transition_comment`），用来记录人的决定。
- **调度器用到的命令**：
  - `create`、`dep add`；
  - `ready --json`；
  - `update --claim --actor dispatcher`；
  - `update -s checking|rework|stale|open|needs_human`；
  - `gate report --gate done_check --provider m2s-dispatcher`；
  - `close`；
  - `defer` / `undefer`（暂停和恢复）；
  - `show --json`、`list --json`。

  全部带上 `--json` 和 `--lock-timeout 15000`。

### 4.2 流水线模板和任务展开【必做】

**模板文件**：`coord/templates/v1.yaml`（M1），以后再加 `v2.yaml`（M4）。每个条目包含：

```yaml
- kind: S2
  role: m2s-code-developer
  split: package          # none | package | change
  deps: [S1, "V1:*"]      # 依赖；"*" 表示同类任务的全部
  scope: ["output/lambdas/{pkg_dir}/"]
  outputs: ["output/lambdas/{pkg_dir}/", "storage/stage_reports/S2-{pkg}.json"]
  done_check: {tool: run_gate, args: {phase: code, package: "{pkg}"}}
  resources: []
  max_turns: 120
  timeout_min: 90
  max_attempts: 2
```

v1 模块对应的完整模板见附录 A。

**展开**：由调度器机械执行，不由 LLM 决定。
- 静态部分：运行开始时，按模板建好所有 `split: none` 的任务和它们之间的依赖。
- 按工作包展开：
  - F1 / P0 关闭后，调用工作包规划（§4.9），为每个包创建 `S0:<包>`；M2 起还有 `V1:<包>`；
  - S1 关闭后，按蓝图的领域目录（规则 8）创建 `S2:<包>` 和 `S2:shared`。
- 按改动展开（M4，O2）：O1 关闭后，为每个改动请求创建一个任务。
- 运行中新增：
  - 回炉任务；
  - 讨论室的发言任务；
  - 异常处理者通过 `handler_decide` 申请的任务。

  新增的任务都要经过调度器检查：任务类型必须在模板里，依赖必须存在，角色必须有权限。

### 4.3 调度器【必做】

**入口**：

```
python -m coord.dispatcher --ws <WS> --template v1 [--max-workers 1] [--until <kind>]
```

每个工作区一个调度器进程。多个工作区可以同时运行，它们共用本机的资源锁和 worker 总数上限。

**主循环**（每轮间隔几秒）：

```
启动 / 重启时先对账：
  - in_progress 的任务，如果对应的 worker 进程已经不在了：放回 open，记一次"崩溃"（不计入 attempts）；
  - checking 的任务：重新运行完成检查；
  - 事件日志末尾没写完的条目：补写。
loop:
  1. 展开：触发条件满足的展开规则（§4.2）
  2. 失效传播：读取产物版本变化事件，把用到旧版本的已关闭任务设为 stale，再设为 open
  3. 选任务：br ready --json，依次过滤
       - 本工作区和本机的 worker 数上限
       - scope.paths 和正在运行的任务没有重叠
       - 需要的资源锁可以拿到
     → 按模板顺序和优先级认领：update --claim，执行者记为本次启动的 worker 名
     → 启动 worker（§4.4）
  4. 监控：
       - worker 退出 → 读它的提交记录 → 设为 checking → 运行完成检查（§4.5）
           - 通过：gate report pass → close
           - 不通过：gate report fail → attempts+1
               - 没到上限：设为 rework，把检查结果按转达格式写进反馈
               - 到上限：交给异常处理者，还不行就设为 needs_human
       - worker 退出时没有提交：当作一次失败。
         原来"只回复了文字，整个运行就结束"的问题，现在只算这个任务失败一次，然后重试
       - 运行超时（timeout_min）：杀掉 worker，当作一次失败
  5. 相似失败归并：按失败特征归并 → 同一特征有 2 个以上未解决的失败
       → 建一个异常案例，交给异常处理者
       → 用 br defer 暂停这个特征下游的任务
  6. 阶段复查：在 v0（M1–M3）或 B0（M4）打 tag、裁判冻结（M4）这几个节点，各启动一次异常处理者复查
  7. 结束：终点任务（FINAL）关闭，或者只剩 needs_human 的任务 → 输出运行报告，然后退出
```

**设计约束**：
- 调度器的所有状态，都能从任务板加事件日志里重新得到。调度器本身不在内存里保存任何不可恢复的东西。
- 调度器不调用 LLM；凡是需要判断的地方，都交给异常处理者（§4.8）。
- M1 的默认设置是 `--max-workers 1`，并且所有任务都不拆包，执行顺序和 v1 完全相同，用来做等价对照。

### 4.4 启动 worker【必做】

**命令**：

```
oh -p <任务提示词> --append-system-prompt <角色说明> --max-turns <模板值> --output-format json --cwd <WS>
   --settings <本次启动的设置文件>            # W0.1 修好以后
   （修好之前：设置环境变量 OPENHARNESS_CONFIG_DIR=<本次启动的配置目录>）
```

- **角色说明**：读取 `~/.openharness/agents/<role>.md` 的正文。
- **允许用的工具**：取 frontmatter 里的工具列表，换算成本次启动设置里的禁止名单。
- **本次启动的设置**由调度器生成，包括：
  - 模型：按角色设定，沿用现在的配置；
  - MCP 服务：只有 m2s-server 一个，stdio 方式，环境变量为 `M2S_WORKSPACE`、`M2S_TASK_ID`、`M2S_ROLE`；
  - 权限：工具禁止名单、按读写区分的路径规则、命令禁止规则（§4.10）。
- **任务提示词**（沿用 v1 SKILL §2 的要素）：
  - 任务 ID 和角色；
  - 绝对路径；
  - 要产出的文件；
  - 回炉时附上上一次的反馈，用转达格式；
  - "遇到网络错误重试 1–2 次"的提醒；
  - 提交方式："完成后调用 `submit_task`；做不下去时调用 `report_blocked`"。
  - 给实现线的提示词里，仍然不写任何和测试有关的内容（v1 SKILL §2 的规定）。
- **worker 的名字**：`<角色简称>-<任务ID>-a<第几次>`。这个名字同时记进任务板（执行者）和事件日志。

### 4.5 完成检查和写入范围检查【必做】

worker 提交后，调度器按顺序做三项检查，全部通过才关闭任务：

1. **写入范围**：
   - 在工作区里比较 worker 运行前后的文件状态（`git status --porcelain`，加上 `coord/` 以外的未跟踪文件）；
   - 改动必须都在 `scope.paths` 之内；
   - 越界的改动一律撤销，这次提交判为失败。

   这一项连 bash 写入的文件也能查到，是"写"方向最可靠的强制手段（§4.10）。（已定，V3-11）
2. **工具未改动**：沿用 v1 的检查，即 `m2s-mcp-server`、`.openharness` 没有被修改。`ledger.json` 除外：它由 S5 的 `update_ledger` 工具写，不算 worker 越界。
3. **模板规定的完成检查**：例如 `run_gate`、`check_provenance`、`run_validate`、产物是否存在。由调度器在自己的进程里调用 `gates.py` 等模块，不经过 LLM。

**要给几个工具加"范围"参数**（W2.6）：
- `check_provenance` 加上按接口过滤；
- `run_gate(code)` 加上按包或按函数过滤；
- `run_validate` 已经支持按函数过滤。

### 4.6 m2s-server 新增的工具【必做】

| 工具 | 谁能调用 | 作用 |
|---|---|---|
| `submit_task(summary, notes?)` | 所有 worker | 写 `WS/coord/submissions/<任务>-a<n>.json`（状态为完成）。worker 调用后就应该结束 |
| `report_blocked(reason, evidence, suspected_module?)` | 所有 worker | 提交记录的状态为阻塞，进入分诊 |
| `raise_spec_issue(topic, options, evidence)` | verifier、architect、annotator、code-developer | 申请开一个规格讨论室（M3） |
| `notice_post(topic, body)` / `notice_read(topics?)` | 所有 worker | 公告板，按角色过滤（M3，§4.7） |
| `room_read(room)` / `room_post(room, body)` | 讨论室的参与者 | 讨论室（M3）。只有轮到自己时才能发言 |
| `record_ruling(...)` | 讨论室里指定的拍板者 | 写入裁定。<br>- M3 用 v1 模块时：写进 blueprint 的 `spec_resolutions`，走现有的合并逻辑；<br>- M4 起：写进 `storage/rulings.json`（v2 §4.12） |
| `handler_decide(case, action, rationale)` | 只有异常处理者 | 可选的动作：回炉到某个模块、新建任务、交给人、关闭讨论室、标记无法判断 |
| `get_task_context()` | 所有 worker | 返回本任务的说明和反馈，用于提示词装不下的情况 |

**可见性**：
- 这些工具按调用者的 `M2S_ROLE` 过滤返回内容（M3）。
- `M2S_ROLE` 由调度器写进 m2s-server 进程的环境变量，worker 改不了。

**并发**：
- 每个 worker 各有一个 m2s-server 进程，所以 m2s-server 要能承受多个进程同时运行。
- 所有 JSON 写入都改成"先写临时文件、再改名"，避免写到一半被读到。
- 共用资源用 §4.11 的文件锁。

### 4.7 公告板和讨论室【必做，M3】

**agent_mail 服务**：
- 本机只起一个：
  - `mcp-agent-mail serve --host 127.0.0.1 --port 8765 --no-tui --reuse-running`；
  - 环境变量：`STORAGE_ROOT=D:\m2s-am`，token 从环境文件读取，长度至少 8 个字符。
- 由调度器在需要时启动。
- 每个工作区对应一个 agent_mail 项目，项目路径就是工作区路径。

**worker 不直接调用 agent_mail，由 m2s-server 代理**（已定，V3-10）。原因：
- 可见性过滤、讨论室的轮次控制、转达格式检查，都要在一个我们控制的地方执行；
- worker 只需要配置 m2s-server 一个 MCP 服务。

agent_mail 在这里提供四样东西：消息存档、讨论串、搜索，以及给人看的 Web UI。

**公告板主题**（初始）：

| 主题 | 谁发 | 谁能看到 |
|---|---|---|
| `artifact`：某个产物发布了新版本（事实库、blueprint、裁判） | 调度器 | 所有角色 |
| `ruling`：一条裁定已经写入 | m2s-server | 所有角色。但实现线只能看到结论和出处，看不到讨论过程 |
| `task`：任务第 N 次失败、被暂停、交给了人 | 调度器 | 调度器、异常处理者、人 |
| `spec-issue`：一个规格问题已经提出 | m2s-server | verifier、architect、annotator |

**讨论室的机制**：worker 都是一次性进程，所以讨论室不是自由聊天，而是**由调度器驱动的轮流发言**（已定，V3-12）。
- 每一轮发言，调度器为该参与者启动一次短的 `oh -p`。这次启动的输入是讨论串的内容，输出是一条 `room_post`。
- 调度器负责数轮次。

| 讨论室 | 什么时候开 | 发言顺序 | 轮数上限 | 谁拍板 | 关闭时写什么 |
|---|---|---|---|---|---|
| **规格讨论室** | 有人调用 `raise_spec_issue`；或者 V1 报了阻塞级别的 `spec_issues`（v1）；或者 J3 有争议的差异（M4） | 提出方 → architect →（涉及事实时）annotator → 提出方回应 | 2 轮 | 按 v2 §4.12：<br>- 事实错误：由 annotator 修正事实库；<br>- 放宽：architect 决定；<br>- 偏离单体的缺陷：architect 提出，人确认 | 裁定记录或事实库修订 → 调度器把相关任务设为失效 |
| **分诊讨论室** | 机械分诊规则判断不了的失败（v1 的 V2 回炉路由；v2 §4.11） | 每个嫌疑模块的角色各提交一份结构化归因：{模块、理由、把握程度}；verifier 代表裁判一方 | 1 轮，加 1 次回应 | 一致：按共识；不一致：交给异常处理者 | 归因结果 → 调度器给被归因的模块建回炉任务。如果争议在裁判的期望本身，转去规格讨论室 |
| **对抗评审室** | 升级 harness 时的 U3（M5） | 见 §5 M5 | 每个评审可以反驳 1 次 | 仲裁提出，人批准 | 规则修正候选 |

**转达格式**：
- 讨论室和公告板里给实现线看的内容，只能有：失败的函数、出处、期望和实际结果（沿用 v2 §4.11）；
- m2s-server 在写入前检查，看到增量测试和 V1 测试资产的路径或原文时，拒绝写入。

### 4.8 异常处理者【必做】

- **什么时候启动**：
  - 任务的尝试次数用完；
  - 相似失败归并到 2 个以上；
  - 讨论室超过轮数；
  - 分诊讨论室没有达成一致；
  - 阶段复查（§4.3 第 6 步）。
- **输入**：
  - 调度器生成的案情材料 `WS/coord/cases/<案例>.md`，内容包括：相关任务、检查输出的全文、失败特征、相关事件、相关的讨论串；
  - 本次运行之前的处理记录 `journal.md`。

  异常处理者是中立角色，**可以看**完整的检查输出。
- **输出**：只能通过 `handler_decide` 输出，每次决定都自动追加进 `journal.md`。
- **不能做的**：标记任务完成、修改裁判或增量测试、修改工具。
- **配置**：每个案例最多 1 次启动，`max_turns` 设得较小。判断不了的，一律交给人（`needs_human`）。

### 4.9 工作包规划和结构提取改进【必做，M2】

**结构提取（F1 / P0）的改进**：
1. **`spec_extract.py` 的 `_extract_data_access`**：从处理函数本身出发，就跟进同文件里的辅助函数，最多 2 层。现在只有进入其他模块以后才跟（深度 ≥1）。
2. **MongoDB**：`data_store.py` 已经能得到访问模式，每条都带调用位置。改动是：把每个接口的调用链（处理函数 → 辅助函数 → 调用位置）和这些调用位置对上，写进这个接口的 `data_access`。`operation` 用访问模式里的值，表名一栏填集合名。
3. **`spec_extract_py.py`**：检查 Flask 版是否有同样的问题，有就一并修。
4. **验收**：
   - acmeair 的 25 个接口里，业务接口都能拿到正确的集合，例如 `bookflights` → booking，`queryflights` → flight、flightSegment；
   - 另外 6 个应用的现有结果不能变差：用现有工作区的报告做回归对比。

**工作包规划** `coord/packages.py`：
- **输入**：`analysis_report.json`；M4 起还有 J2 的实际访问记录（可选）。
- **输出**：`storage/work_packages.json`（结构见附录 C）。
- **规则**：按探索稿 §3.6 的 8 条执行，依次是：
  1. 先分出运维类接口；
  2. 身份包，可以跨进程；
  3. 以 URL 第一段为初始分组，按亲和关系合并，排除枢纽；
  4. 按代码量控制大小；
  5. 共享代码单独成任务；
  6. 接口少于 10 个的应用不拆；
  7. J3 之后重新分组一次（M4）；
  8. 设计完成后按蓝图的领域目录划分。
- **自检**：
  - 每个接口恰好属于一个包；
  - 包的大小在上下限之内；
  - 输出里记录每次合并的理由。

  同一份输入，必须得到同一个结果。
- **共享代码任务 `S0:shared`**：
  - 需要事实库支持"引用共享条目"，即接口的 `business_rules` 可以写 `ref: shared/<id>`；
  - `check_provenance` 要能检查这种引用；
  - annotator 的提示词要说明它的用法（W2.5）。
- **测试**：7 个应用各有一份期望的分组结果。acmeair 的期望结果见探索稿 §3.6：身份、航班和预订、客户、运维，共 3–4 个包。

### 4.10 OpenHarness 权限修复和隔离规则【必做，M0 修复，M3 套用规则】

**代码改动**（`OpenHarness/src/openharness/`）：

| 编号 | 改动 | 位置 |
|---|---|---|
| W0.1 | 让 `--settings` 真正生效：接受文件路径或内联 JSON，深度合并到读入的设置上。同时让 `--allowed-tools`、`--disallowed-tools`、`--mcp-config` 生效 | `cli.py`，以及读取设置的代码 |
| W0.3 | 三项改动：<br>- 路径规则加上 `access: read \| write \| any`（默认 `any`，兼容现有配置）。检查时用工具调用已有的 `is_read_only` 来区分读和写；<br>- 新增可写路径名单 `permission.writable_paths`：名单不为空时，内置的改文件工具只能写匹配的路径。现有的路径规则只有"禁止"起作用，"允许"规则不生效，所以只靠路径规则表达不了"只准写自己的目录"；<br>- 所有禁止规则都在工具允许名单之前检查 | `config/settings.py`（`PathRuleConfig`、`PermissionSettings`）、`permissions/checker.py` |
| W0.4 | 子 agent 启动时：<br>- 继承父进程的覆盖层（`--settings`、工具名单、MCP），以及父进程的权限模式；<br>- 再叠加 agent 定义里的工具允许名单和禁止名单（换算成 OpenHarness 的工具名）、定义里的权限模式，以及定义里的 `permissions`（作为允许名单）；<br>- 参数值不再加引号 | `tools/agent_tool.py`、`swarm/spawn_utils.py`、`swarm/types.py`、`coordinator/agent_definitions.py`、`config/settings.py`、`ui/runtime.py` |
| W0.5 | 测试：<br>- 被禁止的路径，用读文件、grep、glob 都读不到；<br>- bash 里出现被禁止路径的命令会被拒绝；<br>- 写到范围之外会被拒绝；<br>- 原来的全局设置照样有效 | OpenHarness 的测试目录，外加 `m2s-mcp-server/tests/test_coord_perm.py` 做端到端检查 |

OpenHarness 的改动也属于 harness 的一部分。调度器在每次运行开始时，把 OpenHarness 的 commit、`br` 和 agent_mail 的版本记进事件日志。

**按角色套用的规则**（由调度器生成到每次启动的设置里）：

| 角色或模式 | 禁止读 | 只准写 | bash |
|---|---|---|---|
| 所有 worker | `WS/coord/**`；其他工作区 `runs/*`（只能读自己的）；归档目录 | 自己任务的 `scope.paths`，加上 `storage/stage_reports/<本任务>*` | 禁止出现 `br `、`coord`、`am `、`mcp-agent-mail` 的命令 |
| 实现线（architect、code-developer、sam-engineer、validator；和 v1 SKILL §2 的范围一致，S5 的 perf-optimizer 沿用 v1，不额外限制） | `WS/verification/**`（v1 的验证线资产）；M4 起还有增量测试的目录、保留用例 | 同上 | 还禁止出现 `verification` 的命令 |
| verifier（V1 模式，以及 v2 的 D1） | `output/lambdas/**`、`output/template*.yaml`（看不到实现代码） | `verification/**` | 还禁止出现 `output/lambdas` 的命令 |
| annotator | 无额外限制 | `storage/analysis_report.json`，只能通过 MCP 标注工具写 | 默认禁止使用 bash |

- **"写"方向**：由三样东西共同保证，可靠：
  - 可写路径名单（W0.3）："只准写"一栏就是用它表达的；
  - 只禁止写的路径规则（`access: write`），例如裁判对实现线可读、不可改；
  - §4.5 的事后比对。

  MCP 工具不受可写路径名单约束，由 m2s-server 自己检查写入范围；bash 没有路径参数，靠命令规则和事后比对。
- **"读"方向**：由路径规则和命令规则保证。剩下的风险：
  - 递归命令（`grep -r`、`find`）会读到工作区里受保护的目录；
  - 内置的 `grep` / `glob` 工具，如果把根目录设为工作区，同样会读到受保护的目录，因为路径规则只检查根目录本身；
  - 用拼接字符串写出的路径能绕过命令规则（探索稿 §4.3）。
- **验收测试**（M3）：在受保护的文件里埋入金丝雀标记，跑完一次运行后，扫描所有 worker 的输出和记录，结果必须为 0。如果测试不通过，就启动 §9 的"受保护资产移出工作区"。

### 4.11 资源锁【必做】

- **锁目录**：`D:\m2s-locks\`，本机范围，所有工作区共用。
- **独占锁**：
  - 创建锁文件时记下持有者的 pid、任务 ID 和过期时间；
  - 持有进程已经不在了，或者锁过期了，就可以回收。
- **计数锁**：用 N 个编号的独占锁实现，比如 `docker-rie-1..N`。

| 资源 | 类型 | 谁来拿 |
|---|---|---|
| `aws-load-client` | 独占，每台压测客户端一个 | AWS 测量工具（M4） |
| `aws-infra-stack` | 独占 | e2e 共用基础设施栈的部署和拆除 |
| `aws-concurrency` | 计数，按账号 Lambda 并发额度分配 | 同时在测的栈 |
| `docker-rie` | 计数，初始 N=1 | `run_validate`、`run_rie_verdict` 等在 RIE 里运行的工具 |
| `workers` | 计数，本机 worker 总数上限，初始 3 | 调度器 |
| `ws-git:<工作区>` | 独占 | 调度器打 tag 和提交时 |

agent_mail 的 build slot 只是建议性的，也不能计数，所以只用来展示，不用来加锁（探索稿 §6.4）。

### 4.12 事件日志、复现和归档【必做】

- **`events.jsonl`** 只追加，每行一个事件，包括：
  - 时间、事件类型、任务、第几次尝试；
  - worker 名、模型；
  - 输入产物的版本、产物的 hash；
  - 检查结果摘要、耗时、token 用量（从 `--output-format json` 里读）。
- **复现要求**：同样的输入，必须得到同样的任务图和工作包划分；worker 的产出可以不同，但每一次产出都能追溯到它的输入。
- **归档**：运行结束时，把 `coord/` 整体打包。M4 起并入 v2 的 C2 `archive_run`，在那之前先复制进 `runs/<ws>/archive/`。

### 4.13 O 阶段在 AWS 上实测【必做，M4；也可以提前用于 v1 的 S5】

1. **本机初筛**：用代理指标 `run_proxy_metrics`（调用次数、调用深度）筛选。只有通过了裁判、代理指标也有改善的候选，才进入第 2 步。
2. **部署**：每个候选部署成一个独立的栈 `m2s-<ws>-<候选>`；B0（v1 里是 v0）的栈在整个 O 阶段保持不拆。复用 `e2e_aws.py`。
3. **测量**：
   - 用同区域 EC2 上的 JMeter 或 e2e 负载（`e2e_ec2.py`）；
   - B0 和候选交替测量，例如 ABAB；
   - 每次测量都要拿到 `aws-load-client` 锁。
4. **覆盖检查**：用 CloudWatch 里改动函数的调用次数，确认负载走到了改动的路径。没走到的，结果记为"未覆盖"。
5. **判定**：
   - 沿用现有报告的规则：去掉冷启动样本，样本少于 10 的标记出来；
   - 差异小于噪声阈值，判为"无变化"。重复次数和阈值，在 W4.6 里用 B0 对 B0 的测量来校准。
6. **收尾**：拆掉候选的栈，记录这次测量的费用；O 阶段结束时再拆 B0 的栈。

---

## 5. 实施计划

规模：S 约 1 天，M 约 2–4 天，L 约 1 周以上。都是粗估。

### M0 准备【必做】

执行顺序：**W0.1 是整个方案的第一项任务**。W0.3、W0.5、W1.4 都依赖它。W0.2、W0.4、W0.6 可以和它并行进行。

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| W0.1 | 修复 `--settings`、`--allowed-tools`、`--disallowed-tools`、`--mcp-config`；以及 `-p` 模式下的 `--permission-mode`、`--append-system-prompt` | OpenHarness 补丁 + 测试 | 用 `--settings` 传入的禁止规则在 `oh -p` 中生效。**2026-10-30 完成**：<br>- OpenHarness 分支 `feat/cli-settings-flags`；<br>- 新增测试 21 个，全部通过；<br>- 全量测试的失败集合和改动前相同（14 个，都和 Windows 环境有关）；<br>- 真实的 `oh -p` 中，受保护文件用 `read_file` 和 bash `cat` 读取都被拒绝 | — | M |
| W0.2 | 固定版本：`br` 0.7.6、mcp_agent_mail_rust 0.3.39，校验 sha256 后放进 `D:\m2s-tools\`；写一个冒烟测试脚本 | `coord/toolchain.py`、`coord/smoke_tools.py`；`coord/board.py`、`coord/mail.py`（最小封装，W1.1、W3.1 在此基础上扩展）；`coord/policy.py`（附录 B 的策略） | 本机上冒烟测试通过：建任务、依赖、门禁、`allow_bypass`、HTTP 连接、建讨论串。**2026-10-30 完成**：<br>- 安装命令：`python -m coord.toolchain install`。安装包先按固定的 sha256 校验，再解压；<br>- 冒烟测试 `python -m coord.smoke_tools` 的全部检查通过，包括附录 B 里原先没验证过的 `needs_human` 两条迁移规则（都必须附说明）；<br>- 单元测试 8 个；带工具的冒烟测试要设置 `M2S_RUN_TOOL_SMOKE=1` 才运行 | — | S |
| W0.3 | 路径规则区分读和写；新增可写路径名单；检查顺序改为"禁止规则优先于工具允许名单" | OpenHarness 补丁 + 测试 | 只读工具读得到但写不进去；列在允许名单里的工具也受路径规则和命令规则约束；不写 `access` 的老配置照样有效。**2026-10-30 完成**：<br>- 新增测试 15 个，全部通过，包括用真实的写文件工具，经过查询引擎实测；<br>- 全量测试 1175 个通过，失败的仍是原来那 14 个 | W0.1 | S |
| W0.4 | 子 agent 的权限传递 | OpenHarness 补丁 + 测试 | 子 agent 也受 agent 定义里的权限约束。**2026-10-30 完成**：<br>- 子 agent 继承父进程的限制，不能再通过开子 agent 绕过；<br>- 新增测试 20 个，全部通过；<br>- 全量测试 1195 个通过，失败的仍是原来那 14 个；<br>- 用 `agent` 工具生成的真实参数启动了一次子进程（dry-run）：经过 Windows 命令行转义后，权限规则和 MCP 配置都解析正确 | W0.1 | S |
| W0.5 | 端到端权限测试：用一个真实的 `oh -p` worker，故意去读受保护的文件、越界写文件 | `tests/test_coord_perm.py` | 全部被拒绝。bash 的递归读取作为已知风险记下来。**2026-10-30 完成**。测试要调用模型，所以要设置 `M2S_RUN_LLM_TESTS=1` 才运行。用 qwen3.8-flash 实测的结果：<br>- worker 自己的 6 次读写，拒绝和放行都正确，磁盘上的文件没有被改动；<br>- 子 agent 的越权读写也都被拒绝；<br>- bash 的 `grep -r` 确实读出了受保护文件里的金丝雀标记，作为已知风险用 xfail 记下 | W0.1、W0.3、W0.4 | S |
| W0.6 | 记录 harness 版本：OpenHarness commit、m2s-mcp-server commit、`br` / agent_mail 版本、各角色的模型 | `coord/harness.py` | 每次运行的事件日志第一行都是 harness 版本。**2026-10-30 完成**：<br>- `python -m coord.harness` 输出两个仓库的 commit 和未提交的改动、工具版本、每个角色实际使用的模型、agent 定义和技能文件的 hash；<br>- 不记录 API key；<br>- 写进事件日志的第一行，在 W1.7 里接上 | — | S |

### M1 协调骨架，和 v1 等价【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| W1.1 | 任务板封装：调用 `br`、解析 JSON、处理错误和锁等待；初始化 `policy.yaml` | `coord/board.py` | 单元测试覆盖所有状态迁移，包括被拒绝的迁移。**2026-10-31 完成**：`tests/test_coord_board.py` 用真实的 `br` 跑通。`br --claim` 不肯认领还挂着上一个执行者的任务，所以认领前先清空执行者 | W0.2 | M |
| W1.2 | v1 模板（附录 A），不拆包 | `coord/templates/v1.yaml` | 模板能展开成和 SKILL §1 相同的依赖图。**2026-10-31 完成**：每个任务写明角色、执行线（实现线 / 验证线）、写入范围、完成检查、失效后的处理方式、`max_turns` 和超时 | — | S |
| W1.3 | 调度器主循环（§4.3），包括对账、选任务、监控、回炉、needs_human、结束 | `coord/dispatcher.py` | 用假 worker 跑完整个模板。**2026-10-31 完成** | W1.1–W1.2 | L |
| W1.4 | 启动 worker（§4.4）：生成本次启动的设置和提示词；把 SKILL §2 的要素移植过来 | `coord/workers.py` | 能以任意角色启动一个真实的 `oh -p`，并收到它的提交。**2026-10-31 完成**：`tests/test_coord_real_worker.py` 用 qwen 实测通过（46 秒）。和原方案的差别：角色说明经设置文件的 `system_prompt` 传入（效果同 v1 子 agent，替换默认系统提示词），因为放在命令行上会超出 Windows 的长度限制；超过 2 万字的任务说明改为让 worker 调用 `get_task_context` 读取 | W0.1 | M |
| W1.5 | `submit_task`、`report_blocked`、`get_task_context` | `server.py`、`coord/tools.py` | worker 能提交；不提交就退出的，判为失败。**2026-10-31 完成**：另加了异常处理者专用的 `handler_decide` | — | S |
| W1.6 | 完成检查（§4.5）：写入范围比对、工具未改动、模板规定的检查 | `coord/checks.py` | 越界写入被撤销，并判为失败。**2026-10-31 完成**：写入范围用一个影子 git 库（`coord/shadow.git`）做启动前快照和比对，不碰工作区自己的 `.git`；工具改动只撤回启动前就是干净的文件，人自己没提交的改动不动 | W1.3 | M |
| W1.7 | 事件日志、harness 版本记录、运行报告 | `coord/events.py` | 从事件日志能还原每个任务的执行过程。**2026-10-31 完成** | W0.6 | S |
| W1.8 | 异常处理者：生成案情材料、处理记录、`handler_decide`、相似失败归并、阶段复查 | `coord/handler.py` | 用假 worker 制造的同类失败能被归并成一个案例。**2026-10-31 完成**：一轮里非复查类的案例最多开一个；处理者给的重试不重置计数 | W1.3 | M |
| W1.9 | 假 worker：一个脚本，按任务说明写出预先准备好的产物，并调用提交接口；可以模拟成功、失败、超时、崩溃、不提交 | `tests/fake_worker.py` | 被调度器的单元测试使用。**2026-10-31 完成** | W1.3 | S |
| W1.10 | 故障注入测试：运行中杀掉调度器、杀掉 worker、杀掉 m2s-server、让网络中断 | `tests/test_coord_resume.py` | 重启调度器后能自动继续，不重做已经关闭的任务。**2026-10-31 完成**，写在 `tests/test_coord_dispatcher.py` 里：调度器在 worker 运行中和 worker 结束后被杀、worker 崩溃、超时。m2s-server 被杀和断网在调度器看来就是 worker 崩溃或没有提交，没有单独注入 | W1.3–W1.9 | M |
| W1.11 | 等价运行：bookstore，`--max-workers 1`，真实的 worker | 一个新工作区（例如 `bookstore-v3a`） | 见下 | 以上全部 | M |

**M1 实现时定下的几点（2026-10-31）**：
- 以往 12 次运行一次都没有触发过的两条流程（S5 之后的测试复核、规格放宽），M1 先交给人处理，M3 起再接讨论室。
- V1 的规格问题：最多 2 轮"S1 回炉、V1 重跑"。之后还剩阻塞级别的，交给人；非阻塞的带到后面。
- V2 失败的路由：代码问题回 S2，模板问题回 S3c，规格问题回 S1；只转交失败的函数、出处、期望和实际结果。判断不了的开分诊案例。
- 假 worker 的场景测试要将近 5 分钟，设 `M2S_RUN_SLOW=1` 才运行；单元测试和任务板测试默认运行。
- W1.11 分段运行：`run --stop-after <任务>` 只启动这个任务和它的上游，关闭后停下（退出码 5）；再次 `run` 不带这个参数就接着往下跑。第 1 段到 V1，第 2 段到 TAG_V0，第 3 段跑完。
- W1.11 进展（2026-11-01）：第 1、2 段跑完，各任务都是第一次就通过，v0 已打 tag（V2 127/127，因为 Docker 没开，跑在 local + dynalite 上）。打 tag 之后的阶段复查把三件事交给人。按人的决定：(1) gates 新增 C14（模板的 Cognito 设置要和 blueprint.cognito 一致），用新加的 `rework --recheck` 把 S3c 退回；(2) 启动 Docker，从 S3c 往后都跑在 rie + DynamoDB Local 上，和以往 12 次运行一致；(3) SI-22（JSON 标量请求体的 400 该走哪条分支）先不裁定，用 `--open-issue` 写进最终报告。调度器同时修了两处：过期的调度器动作（TAG_V0）会重新执行，以前只复查一个空的检查列表、不会重新打 tag；人已经关闭的关口，在复查再次提出问题时重新打开，以前会被忽略。

**M1 的验收标准**：
- bookstore 跑到 gate(final) PASS，`verdict_A.json` PASS，打了 v0 tag，有 S5 报告。
- 测试数、通过数和 S5 保留的改动数，和 bookstore-oh 处在同一水平。LLM 的产出本来就不确定，所以比较的是验收结果，不是逐字节比较。
- 全程没有需要撑完整条流水线的 LLM 会话；除了 needs_human，不需要人工介入。
- W1.10 的故障注入测试全部通过。

### M2 工作包并行【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| W2.1 | Express 版：从处理函数出发，跟进同文件辅助函数 | `spec_extract.py` + 测试 | §4.9 第 4 条 | — | M |
| W2.2 | MongoDB 访问模式挂到接口上 | `data_store.py`、`spec_extract.py` + 测试 | acmeair 的业务接口都拿到正确的集合 | W2.1 | M |
| W2.3 | 检查并修复 Flask 版 | `spec_extract_py.py` + 测试 | 3 个 Flask 应用的回归对比不变差 | — | S |
| W2.4 | 工作包规划（规则 1–6、8） | `coord/packages.py` + 测试 | 7 个应用的分组和期望一致；结果确定；每个接口恰好属于一个包 | W2.1–W2.3 | M |
| W2.5 | 共享代码任务：事实库的引用结构、`check_provenance` 支持引用、annotator 提示词 | 结构改动 + 测试 | 一条共享规则只标注一次，各接口通过引用使用它 | W2.4 | M |
| W2.6 | 给工具加范围参数：`check_provenance`（按接口）、`run_gate(code)`（按包或函数） | `server.py`、`gates.py`、`provenance.py` | 按包检查的结果，和全量检查里对应的部分一致 | — | S |
| W2.7 | 按包展开：S0、V1、S2（加上共享层）；模板加 `split: package` | `templates/v1.yaml`、`dispatcher.py` | 依赖图正确：S2:包 依赖 S2:shared 和 V1:包 | W2.4–W2.6 | M |
| W2.8 | 并发：worker 上限、范围冲突检查、资源锁（§4.11） | `coord/locks.py` | 两个范围重叠的任务不会同时运行；锁的持有进程被杀掉后，锁能被回收 | W1.3 | M |
| W2.9 | 多个工作区同时运行：去掉全局 `M2S_WORKSPACE` 的切换 | `coord/workers.py` | 两个工作区同时跑，互不干扰 | W1.4 | S |
| W2.10 | 对照运行：bookstore 和 acmeair，`--max-workers 3`，对照 M1 的串行模式 | 两个新工作区 | 见下 | 以上全部 | M |

**V1 和 S2 的先后顺序**：
- M1 保持 v1 的规定：所有 V1 都在 S2 之前完成，用顺序实现隔离。
- M2 起，可以改成"`V1:包` 只要求在 `S2:包` 之前"。前提是 §4.10 里"verifier（V1 模式）禁止读 `output/lambdas/**`"已经生效，并且通过了测试。

**M2 的验收标准**：
- 验收结果和 M1 处在同一水平；
- 关键路径时长变短；
- 没有越界写入；
- 有各任务的 token 统计，可以和 M1 对比。

### M3 公告板、讨论室和可见性【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| W3.1 | 管理 agent_mail 服务：启动或复用、健康检查、按工作区建项目 | `coord/mail.py` | 调度器重启后能复用正在运行的服务 | W0.2 | S |
| W3.2 | m2s-server 的消息代理：`notice_*`、`room_*`、转达格式检查、按角色过滤 | `coord/tools.py` | 实现线读不到被过滤掉的内容；违反转达格式的写入被拒绝 | W3.1 | M |
| W3.3 | 规格讨论室：替代 v1 里"V1 的 spec_issues 交给主 Agent，再退回 S1"的转发 | 调度器的讨论室逻辑 | 讨论室按轮次进行，结论经 `record_ruling` 写入，相关任务随之失效 | W3.2、W3.5 | M |
| W3.4 | 分诊讨论室：处理 V2 回炉路由判断不了的情况 | 同上 | 一致时自动回炉，不一致时交给异常处理者 | W3.2 | M |
| W3.5 | `record_ruling`：v1 模块版写进 blueprint 的 `spec_resolutions` | `server.py` | 和 v1 现在的裁定结构一致 | — | S |
| W3.6 | 按角色套用权限规则（§4.10 表），加上金丝雀泄漏测试 | `coord/workers.py`、`tests/test_coord_leak.py` | 一次完整运行之后，泄漏为 0 | W0.5 | M |
| W3.7 | 对照运行：acmeair，打开讨论室 | 一个新工作区 | 见下 | 以上全部 | M |

**M3 的验收标准**：
- 规格问题和分诊争议都通过讨论室解决，或者交给了处理者或人；不再需要主 Agent 转发；
- 泄漏测试为 0；
- 人可以在 agent_mail 的 Web UI 里看到讨论过程。

### M4 接入 v2 模块【必做，前提是 v2 模块已经实现】

v2 每实现一个模块，就在 `templates/v2.yaml` 里加一个任务类型，并补上编排侧需要的支持：

| v2 模块 | 编排侧要补的 |
|---|---|
| M0、F1–F3 | 事实库版本号：每次修正都升版本，并在公告板发 `artifact` 事件，用来驱动失效传播 |
| G | 按包展开（G2） |
| J1–J6 | 按包展开 J1；J2 运行单体时拿 `docker-rie` 等资源锁；J3 对账有争议的差异进规格讨论室；J3 判为事实错误的，新建 F2 修正任务；J6 冻结后记下裁判版本，之后的任务都以它为输入 |
| 规则 7 | 先做可行性检查：J2 能不能按用例分别记录覆盖率（Node 用 inspector 的 `takePreciseCoverage`，Python 用 coverage.py 的 `switch_context`）。可行就实现 J3 之后的重新分组 |
| A1、A2 | 按蓝图领域展开 D、I1 |
| D1 | 按包展开；可见性规则：增量测试的目录对实现线禁止读取 |
| I1–I3、V1–V4 | 和 v1 对应模块的做法相同；B0 打 tag 后做阶段复查 |
| O1–O4 | §4.13 的 AWS 实测：W4.6 校准噪声规则；工具 `measure_aws`；每个候选一个栈；覆盖检查；费用记录 |
| C1、C2 | `archive_run` 把 `coord/` 也打包进去 |

**M4 的验收标准**：v2 流水线在 v3 调度下跑通 bookstore 和 acmeair。

### M5 对抗评审室【必做，在第一次升级 harness 时做】

- **触发条件**：同一个 harness 版本下，已经归档了 2 个以上应用。这是 v2 §5 里"晋升需要至少 2 个应用的证据"的前提。
- **第一步，独立评审**：由调度器分别启动两个评审 worker。两者看不到对方，这是由构造保证的。两人各自攻击试点应用的蓝图和架构规则，每条意见都要引用规则编号。
- **第二步，仲裁**：在 Claude Code Agent Teams 的交互式会话里进行，人在场。仲裁同时读两份意见，可以追问任一评审；每个评审可以反驳另一方 1 次。也可以用 §4.7 的讨论室机制来做，由人在场旁观。
- **产出**：规则修正候选，写进 v2 U3 的流程，由人批准。演练产出的代码一律丢弃。

---

## 6. 测试和验收方法

| 类别 | 做法 | 在哪个里程碑做 |
|---|---|---|
| 单元测试 | 任务板的状态迁移；模板展开；失效传播；相似失败归并；工作包规划；锁的获取和回收 | M1、M2 |
| 假 worker | 不调用 LLM，用预先准备好的产物跑完整个模板，可以模拟各种失败。这类测试几秒钟就能跑完，每次改调度器都要跑 | M1 起 |
| 故障注入 | 杀掉调度器、worker、m2s-server、agent_mail；断网；锁的持有进程被杀 | M1、M3 |
| 权限和泄漏 | 故意越权读写的 worker；金丝雀扫描 | M0、M3 |
| 确定性 | 同样的输入跑两次调度器（使用假 worker），任务图、工作包划分和事件序列必须相同 | M1、M2 |
| 端到端对照 | M1 对照 v1 的参照运行；M2 对照 M1；M3、M4 对照前一个里程碑。比较的是验收结果，不是逐字节比较 | 各里程碑 |

---

## 7. 指标

每次运行由调度器自动统计，写进运行报告：
- 端到端时长、关键路径时长；
- 总 token 和费用，以及按任务类型的分布；
- 返工次数，以及返工原因的分布；
- 异常处理者的启动次数、交给人的次数、人工处理的耗时；
- 讨论室的数量、轮数，以及有多少在轮数上限内达成了结论；
- 崩溃和自动恢复的次数；
- 验收结果：gate、裁判、增量测试，以及和前一个里程碑的对比；
- 泄漏测试的结果。

---

## 8. 风险和应对

| 风险 | 应对 |
|---|---|
| `br` 和 agent_mail 更新得很快，行为可能改变 | 固定版本号；适配层保持薄（`board.py`、`mail.py`）；升级前先跑 W0.2 的冒烟测试 |
| Windows 路径太长，agent_mail 的 git 存档失败 | 使用短的根目录（`D:\m2s-am`、`D:\m2s-locks`、`D:\m2s-tools`） |
| OpenHarness 的补丁和上游版本分叉 | 补丁集中在 W0.1、W0.3、W0.4，各自配有测试；harness 版本里记录 OpenHarness 的 commit |
| 每个 worker 一个 m2s-server 进程，多个进程同时写工作区的文件 | 写入用"临时文件 + 改名"；共用资源加文件锁；范围检查保证各任务写的文件不重叠 |
| 启动次数增多，token 成本上升 | 用工作包控制任务数；限制 worker 数；按任务类型统计 token，有异常就调整粒度 |
| LLM 产出不确定，对照结果受噪声影响 | 比较验收结果；需要时，同一配置跑两次 |
| 本机 Docker 和 RIE 不稳定 | `docker-rie` 锁初始设为 N=1；失败按机械信号归类为环境问题，不计入 attempts |
| 调度器自身的 bug 会影响整条流水线 | 状态全部在任务板和事件日志里，可以随时重启；用假 worker 测试覆盖主要路径 |
| 读取方向隔离的剩余风险（递归命令） | M3 做泄漏测试；如果不通过，启动 §9 的"受保护资产移出工作区" |

---

## 9. 后续扩展【扩展】

| 项 | 为什么放到以后 | 什么时候启动 |
|---|---|---|
| 执行层替换实验：同一个协调层，分别接 Claude Code 或 Codex 的非交互模式 worker | 不是当前重点（2026-10-30 定） | M4 完成以后 |
| Temporal 耐久执行 | 调度器本身已经可以重启；先观察长时间运行的稳定性 | 调度器的重启和恢复不够用时 |
| 保留用例（v2 Q1） | 要求真正看不到，需要更强的隔离 | 容器沙箱就绪以后 |
| 受保护资产移出工作区；worker 进容器沙箱 | 先用权限修复（2026-10-30 定） | 泄漏测试不通过时，或者要做保留用例时 |
| 更好的分组方法（探索稿 §9.1） | 先用规则，需要先有衡量指标和数据 | 积累了 M2–M4 的指标以后 |
| 上百个接口的单体：A0 加 A:领域 的分层设计、I2 分片、G 按包拆 | 现有 7 个应用用不到 | 加入更大的基准应用时 |
| 常驻 worker 自行认领（`br update --claim`、`claim_exclusive`） | 现在的 worker 都是一次性进程，由调度器分配就够了 | 改用常驻 worker 时 |
| 用 `br` 的容量限制做资源准入 | 文件锁已经够用；容量限制还没实测过 | 资源种类变多时 |
| 多台机器、远程 worker（例如在 EC2 上跑 worker） | 目前单机就够 | 本机资源成为瓶颈时 |
| Claude Code Agent Teams 用于无人值守的运行 | 它只能在交互式会话里组建团队 | 它支持非交互模式以后 |

---

## 10. 决策记录

| 编号 | 决定 | 日期 | 出处 |
|---|---|---|---|
| V3-01 | 不保留常驻的主 Agent：确定性调度器 + 出现异常才启动的处理者；补上处理记录和相似失败归并；做阶段复查 | 2026-10-30 | 探索稿 §4.5 |
| V3-02 | 按工作包拆任务：8 条规则；结构提取增加同文件辅助函数解析；更好的分组方法列为扩展 | 2026-10-30 | 探索稿 §3.6 |
| V3-03 | 三个讨论室都做 | 2026-10-30 | 探索稿 §4.4 |
| V3-04 | 隔离：修好 OpenHarness 的权限 | 2026-10-30 | 探索稿 §4.3 |
| V3-05 | 性能测量改到 AWS 上实测 | 2026-10-30 | 探索稿 §5.1 |
| V3-06 | 执行层替换列为后续扩展 | 2026-10-30 | 探索稿 §6.3 |
| V3-07 | 协调层直接采用 Beads（`br`）和 mcp_agent_mail_rust（一个共用的 HTTP 服务） | 2026-10-30 | 探索稿 §6.4 |
| V3-08 | 核实结果：<br>- `br` 可以在 Windows 上使用，多进程写入安全，门禁可以用、但必须写 `allow_bypass: false`；<br>- build slot 是建议性的，也不能计数；<br>- agent_mail 只能用 HTTP 方式共用 | 2026-10-30 | 探索稿 §6.4 |
| V3-09 | 先在 v1 模块上落地，再接入 v2 模块 | 2026-10-30 | 本文 §2 |
| V3-10 | worker 不直接调用 agent_mail，由 m2s-server 代理 | 2026-10-30 | 本文 §4.7 |
| V3-11 | 写入范围用"路径规则 + 事后比对"强制执行；越界的改动撤销，这次提交判为失败 | 2026-10-30 | 本文 §4.5 |
| V3-12 | 讨论室由调度器驱动轮流发言，每次发言是一次短的 `oh -p` | 2026-10-30 | 本文 §4.7 |
| V3-13 | 直接修 OpenHarness 的 `cli.py`，让 `--settings` 等参数生效，列为第一项任务（W0.1）；修好之前，临时用每个 worker 一个配置目录 | 2026-10-30 | 本文 §1.3、W0.1 |

---

## 11. 待确认

目前没有待确认的事项。写方案时提出的 5 点，已于 2026-10-30 全部按建议确认（V3-09 至 V3-13），确认时考虑到的代价如下：
- **V3-10**：worker 用不上 agent_mail 的其他工具，比如搜索；
- **V3-11**：越界改动直接撤销、提交判为失败，不采用"只警告、交给处理者"；
- **V3-12**：每次发言都要启动一次 worker，成本高一些，换来的是结果确定、可以复现。

---

## 附录 A：v1 模块到 v3 任务类型的对照（M1 模板）

| v1 阶段 | v3 任务 | 执行者 | 依赖 | 完成检查 | M2 起能否按包拆 |
|---|---|---|---|---|---|
| P0 | `P0` | 调度器直接调用 `analyze_project` | — | `analysis_report.json` 和 `symbol_table.json` 存在，且有 `spec_meta` | 否 |
| — | `PKG` | 调度器调用工作包规划 | P0 | `work_packages.json` 通过自检 | M2 起才有 |
| S0 | `S0`（M2 起为 `S0:<包>` 和 `S0:shared`） | m2s-spec-annotator | P0（M2 起：PKG，以及 S0:shared） | `check_provenance`（M2 起按包的接口过滤） | 是 |
| S1 | `S1` | m2s-architect | 所有 S0 | `run_gate(blueprint)` | 否 |
| V1 | `V1`（M2 起为 `V1:<包>`） | m2s-verifier（V1 模式） | S1 | `run_validate(dry_run=true)`（M2 起按函数过滤）；报了阻塞级别的 `spec_issues` 时：M1 重新打开 S1，M3 起开规格讨论室 | 是 |
| S2 | `S2`（M2 起为 `S2:shared` 和 `S2:<包>`） | m2s-code-developer | S1；所有 V1（M2 起：`V1:<包>`，以及 S2:shared） | `run_gate(code)`（M2 起按包过滤） | 是 |
| S3a、S3b、S3c | `S3a` → `S3b` → `S3c` | m2s-sam-engineer | 所有 S2；三者串行 | S3a：`output/template.part1.yaml` 存在，并且包含 `# --- END METADATA ---`；S3b：`output/template.part2.yaml` 存在；S3c：`output/template.yaml` 存在，`run_gate(template)` 通过（片段本身不是完整模板，不单独跑 `validate_sam`） | 否 |
| S4 | `S4` | m2s-validator | S3c | `run_gate(final)` | 否 |
| V2 | `V2` | m2s-verifier（V2 模式） | S4 | `run_validate()`，且 `verdict_A.json` 为 PASS。失败时按路由建回炉任务（S2:<包> 等），下游任务随之失效；路由判断不了的，M3 起进分诊讨论室 | 否 |
| 打 tag v0 | `TAG_V0` | 调度器 | V2 | 工作区 git 打了 v0 tag；之后做阶段复查 | — |
| S5 | `S5` | m2s-perf-optimizer | TAG_V0 | `perf_report.json` 里没有 pending_human；`run_gate(final)` 通过 | 否（M4 起按改动拆，见 O2） |
| 阶段 6 | `FINAL` | 调度器 | S5 | `run_gate(final)` + `run_validate()`（全量回归）；生成运行报告 | — |

## 附录 B：`policy.yaml`（初稿）

`needs_human` 这个状态和 `required_fields` 的写法，还没有在本机实测过，在 W1.1 里验证。

```yaml
allow_bypass: false            # 必须写在顶层；写在 workflow 下面会被忽略
workflow:
  strict: true
  statuses: [open, in_progress, checking, rework, stale, needs_human, closed]
  transitions:
    initial: [open]
    open: [in_progress, stale]
    in_progress: [checking, open]          # open = worker 崩溃或超时，放回待办
    checking: [closed, rework, needs_human]
    rework: [in_progress, stale, needs_human]
    stale: [open]
    needs_human: [open, closed]
    closed: [stale]
  status_groups:
    ready: [open, rework]
  gates:
    "checking -> closed":
      require_all: [done_check]
  required_fields:
    "needs_human -> closed": [transition_comment]   # 记录人的决定
    "needs_human -> open": [transition_comment]
```

## 附录 C：数据结构

**任务说明**（写在 issue 的描述里，是一段 JSON）：

```json
{
  "id": "S2:flights",
  "kind": "S2",
  "role": "m2s-code-developer",
  "package": "flights",
  "scope": {"paths": ["output/lambdas/flights/", "storage/stage_reports/S2-flights.json"]},
  "inputs": [{"artifact": "blueprint", "version": 3, "sha256": "…"},
             {"artifact": "work_packages", "version": 1, "sha256": "…"}],
  "outputs": ["output/lambdas/flights/", "storage/stage_reports/S2-flights.json"],
  "done_check": {"tool": "run_gate", "args": {"phase": "code", "package": "flights"}},
  "resources": [],
  "max_turns": 120,
  "timeout_min": 90,
  "max_attempts": 2,
  "attempts": 0,
  "crashes": 0,
  "feedback": [{"attempt": 1, "from": "run_gate(code)", "relay": {"function": "flights-search", "refs": ["…"], "expected": "…", "observed": "…"}}]
}
```

**`storage/work_packages.json`**：

```json
{
  "version": 1,
  "rules_version": "v3-2026-10-30",
  "source": {"analysis_report_sha256": "…", "footprint": null},
  "packages": [
    {"id": "identity", "kind": "identity",
     "endpoints": [{"method": "POST", "path": "/rest/api/login"}],
     "files": ["acmeair/routes/index.js"],
     "tables": ["customer", "customerSession"],
     "loc_estimate": 120,
     "reasons": ["rule2: auth endpoint", "rule3: shares customerSession with authservice"]}
  ],
  "shared_code": [{"file": "acmeair/routes/index.js", "function": "validateSession", "used_by": ["identity", "flights-bookings"]}],
  "hubs": {"tables": ["customerSession"], "files": ["acmeair/routes/index.js"], "functions": ["checkForValidSessionCookie"]},
  "impl_packages": null
}
```

`impl_packages` 在 S1 / A 完成以后填写，内容是：包 → 函数、目录。

## 附录 D：目录布局

```
m2s-mcp-server/
├── coord/                       新增：协调层
│   ├── dispatcher.py            调度器主循环
│   ├── board.py                 br 的封装
│   ├── workers.py               启动 worker：设置、提示词、权限规则
│   ├── checks.py                完成检查、写入范围比对
│   ├── handler.py               异常处理者：案情材料、处理记录、相似失败归并
│   ├── packages.py              工作包规划
│   ├── locks.py                 本机资源锁
│   ├── mail.py                  agent_mail 服务管理和客户端
│   ├── events.py                事件日志、运行报告
│   ├── harness.py               harness 版本记录
│   ├── tools.py                 m2s-server 的协调工具实现
│   ├── smoke_tools.py           br 和 agent_mail 的冒烟测试
│   └── templates/
│       ├── v1.yaml
│       └── v2.yaml              （M4）
├── tests/
│   ├── fake_worker.py
│   ├── test_coord_board.py / test_coord_dispatcher.py / test_coord_resume.py
│   ├── test_coord_packages.py / test_coord_perm.py / test_coord_leak.py
│   └── fixtures/work_packages/  7 个应用的期望分组结果

<WS>/
├── coord/                       不进工作区的 git；所有 worker 禁止读
│   ├── beads/.beads/            br 任务板 + policy.yaml
│   ├── events.jsonl
│   ├── journal.md
│   ├── cases/
│   ├── submissions/
│   └── settings/                每次启动 worker 用的设置（W0.1 修好前是配置目录）
└── storage/work_packages.json

D:\m2s-tools\   固定版本的 br.exe、mcp-agent-mail.exe
D:\m2s-am\      agent_mail 的 STORAGE_ROOT
D:\m2s-locks\   本机资源锁
```
