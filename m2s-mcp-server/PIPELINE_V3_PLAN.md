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

**M2 进展（2026-11-10）**：W2.1–W2.9 已实现并测试（483 个测试通过）；W2.10 的对照运行见下（2026-11-12 完成）。
- W2.1：`_extract_data_access` 从处理函数出发就跟进同文件辅助函数（最多 2 层）；A/B 对比 7 个应用，没有 script 项丢失。
- W2.2：`data_store.detect` 的访问模式按调用链挂到接口的 `data_access`；acmeair 的 25 个接口里 17 个拿到集合（业务接口 queryflights→flight/flightSegment、bookflights→booking、customer→customer，身份接口→customerSession）。
- W2.3：Flask 版用语法树解析，本来就从处理函数跟进同文件函数；airline-booking 的 A/B 结果无变化。
- W2.4：`coord/packages.py`（规则 1–6、8；确定性、自检）；7 个应用的分组固定在 `tests/fixtures/work_packages/expected.json`。
- W2.5：事实库新增 `shared_rules`，端点用 `business_rules: [{"rule": "ref: shared/<id>"}]` 引用；`annotate_shared_entry` 工具与 annotator 说明。
- W2.6：`check_provenance(endpoint=...)`、`run_gate(code, package=/functions_json=...)`；C1 按包/函数过滤。
- W2.7：`coord/templates/v1-split.yaml`（S0、V1、S2 按包展开，S2 另有 `S2-shared`）+ 调度器展开（`expand_after`、`<family>:*`、`{pkg}`/`{pkg_dir}`）。Windows 路径不能含 `:`，子任务 id 用 `S2-<包>` 记录，计划文档里的 `S2:<包>` 记法仍用于说明。
- W2.8：`coord/locks.py`（独占/计数锁、死进程与过期回收）；调度器按 scope 冲突串行、`workers` 计数锁限制跨工作区 worker 总数；`run_validate` / `run_rie_verdict` 取 `docker-rie` 槽；提交/打 tag 取 `ws-git:<ws>`。
- W2.9：worker 的 `M2S_WORKSPACE` 由各自 `mcp.json` 注入，调度器不再切换全局设置；两个工作区并发的调度器测试通过。

**M2 进展（2026-11-12 收尾，W2.10 完成）**：
- bookstore-m2（`v1-split`，3 worker）：FINAL 111/111 PASS。墙钟 8.2 h，worker 并发峰值 2，worker 墙钟合计 7.9 h；M1 串行基线 bookstore-v3a（`v1`）墙钟 14.9 h、worker 合计 7.9 h（峰值 1）——同样的 worker 工作量，墙钟缩短到 55%。
- acmeair-m2-serial（`v1`，1 worker）：FINAL PASS，墙钟 17.1 h，worker 合计 8.9 h。
- acmeair-m3-split（`v1-split` + 讨论室，3 worker）：FINAL PASS，墙钟 27.4 h，worker 合计 32.0 h，并发峰值 3，动过 37 个任务（含 14 个展开子任务）；墙钟偏长来自 M3 本身更大的工作量（按包展开、两个规格讨论室、模型 403 中断、两次阶段复查与按裁定的重跑），不是并行变慢。
- 并发证据：同一时刻最多 3 个 worker（不同包的 S0/V1/S2），scope 冲突的任务自动串行，`workers` 计数锁未触发；每次尝试的 `write_scope` 全绿，没有越界写入被判。
- token 统计：W2.10 当时 runner 的 `stream.jsonl` 不记录 usage，以 worker 墙钟合计代替。**M4 补齐（2026-11-16）**：OpenHarness 的 `assistant_complete` 事件带 `usage`（提交 `e4eb3f0`），`task_usage` 按任务汇总，运行报告带 token（入/出）列与合计。

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

**M3 进展（2026-11-10）**：W3.1–W3.6 已实现并测试（`feat/v3-m3` 分支）；W3.7 的 acmeair 运行见下（2026-11-12 完成）。
- W3.1：`coord/mail.py` 的 `ensure_server` 复用本机 8765 端口的 agent_mail（token 存在 `D:/m2s-am/token`），`ensure_project` 按工作区建项目；调度器在 `M2S_MAIL=1` 时启动并把 URL/token/项目写进每个 worker 的 mcp.json。
- W3.2：公告板与讨论室由 m2s-server 代理（`notice_post/read`、`room_post/read`、`raise_spec_issue`）；消息存 `coord/mail/`，并镜像到 agent_mail 供 Web UI 查看；可见性按角色执行，裁定对实现线只给结论与依据；转达格式检查拒绝带 verification 路径/原文的消息。
- W3.3：spec 讨论室由调度器按轮次启动参与者（提出方 → architect → annotator → 提出方回应，最多 2 轮）；architect 用 `record_ruling` 写裁定后，调度器退回提出任务；没有裁定则交异常处理者。
- W3.4：分诊讨论室（verifier + 嫌疑模块角色 + verifier 回应）；归因一致时退回对应任务，不一致交处理者。
- W3.5：v1 模块的 `record_ruling` 写 `blueprint.metadata.spec_resolutions`（v2 仍写 `rulings.json`），并公告 ruling。
- W3.6：泄漏测试 `tests/test_coord_leak.py` 覆盖公告板/讨论室可见性、裁定过滤、房间参与者；`test_coord_notices.py` 覆盖格式拒绝与 v1 裁定。

**M3 进展（2026-11-12 收尾，W3.7 完成）**：
- acmeair-m3-split：两个规格讨论室共 16 个回合。spec-16250（纯数字正文逐字符比较）与 spec-70814（loader 防重入闸门 / 0 客户臂）都由 architect 用 `record_ruling` 写入 blueprint（SI-26、SI-27；S1 回炉后新增 SI-28），调度器据裁定回炉 S0-ops / S1 / S2-ops；没有主 Agent 转发。分诊讨论室（W3.4）本轮没有触发（V2 没有判断不了的失败），由单测覆盖。
- 泄漏：运行后扫描 `coord/mail/`（公告板 + 两个讨论室）为 0；`tests/test_coord_leak.py` 4 项通过。
- Web UI：发现镜像静默失败——m2s-server 在事件循环线程上调用 `mail.call_sync`，`asyncio.run` 在运行中的循环里抛错且异常被吞，agent_mail 里一条讨论室消息都没有。已修：`call_sync` 在已有事件循环时改用独立线程；失败写 `coord/mail/mirror_errors.jsonl`。本运行的 35 条消息已用工作区文件回填，`room-spec-16250` / `room-spec-70814` 与公告板线程可在 agent_mail（Web UI）中检索到。
- 人工节点：第一次阶段复查把 5 件事交人（装载臂 A/B/C、5 项 migration-change 偏离、T-1/T-2 工具盲区、两件机械欠账）。人的决定：装载臂选 B（零写入仍 200）、5 项偏离全部确认、接受 T-1/T-2 过渡形态（工具改动排期 M4）、两件欠账接受并记录——记录为 R-human-1/2/3 与两条 open issue；第二次阶段复查自动接受，进入 S5。
- **M4 收口（2026-11-16）**：T-1 已实现（`verify.py` 的 `expect.body.raw_equals`，正文逐字符比较）；T-2 在共享 `verify.py` 已具备（`repeat` 字段、controller 与 judge 的比较），本轮只记录关闭；`impl_packages` 的 v1 欠账随 `v1-split` 的 S1 scope 与 `gate(blueprint)` 的 CG 检查关闭。
- 本轮发现并修掉的编排缺陷（`feat/v3-m3`）：3c13470 回炉动态包子任务要沿看板依赖边传播；2dbdc7c 重启后按事件日志识别“下游在旧上游上跑”；6806a7c V2 路由按函数定位到 `S2-<包>`；984f9f3 `_collect` 跳过同一轮被取消的 worker；b016fb1 `_downstream` 只走看板边且裁定可写族名；f7b33fb 镜像修复（事件循环线程 + 失败可见）。

### M4 接入 v2 模块【必做，前提是 v2 模块已经实现】

v2 的 26 个任务模板（`coord/templates/v2.yaml`，v2 N5 完成）已经在 v3 调度器下跑通 bookstore 和 airline（串行、`--max-workers 1`、无讨论室）。M4 补的是编排侧：让 v2 的模块用上 v3 的按包/按领域并行、讨论室、资源锁和 AWS 实测，并按本节的验收跑 bookstore + acmeair。

**M4 工作项（2026-11-13 拆出）**：

| 编号 | 内容 | 产出 | 验收 | 规模 |
|---|---|---|---|---|
| W4.1 | 事实库版本与 artifact 公告：`storage/facts_version.json`（自增版本 + facts sha）；F2 关闭时调度器升版本并发 `artifact` 公告；每个 spawn 记录当时的 facts_version | 调度器 + `server.py` 读版本 | 假 worker：F2 回炉后版本 +1，下游 spawn 带新版本；报告可查 | S |
| W4.2 | v2 的按包/按领域展开：新增 `templates/v2-split.yaml`；F1 后生成工作包；F2 按包（另有 F2-shared）、J1 按包；G2 按包写 `storage/gaps/<pkg>.json` 再由合并任务合成 `gap_inventory.json`；A1 填 `work_packages.impl_packages`；A1 后按领域展开 D1/I1（I1 另有共享层任务） | 模板 + 调度器展开逻辑 | 假 worker 的依赖图正确；每个包/领域一个子任务；scope 不重叠 | L |
| W4.3 | J2 的资源锁：`run_monolith` 取 `docker-rie` 槽 | 调度器 | 同一时刻两个工作区的 J2/RIE 不会并发 | S |
| W4.4 | J3 争议进规格讨论室：`_after_j3` 的 `needs_ruling` 不再起孤立裁定任务，改开 spec 房间（proposer=J3）；房间结束接受 architect 的 `record_ruling`，并把被阻塞的任务放行；没有一致结论时交异常处理者 | 调度器 + 假 worker 测试 | 一致时按裁定继续，不一致交处理者 | M |
| W4.5 | J6 的裁判版本进下游输入：spawn 事件与任务上下文记录 judge_version，最终报告列出 | 调度器 | 事件日志与报告 | S |
| W4.6 | 规则 7 可行性探针：J2 能否按用例分别记录覆盖率（Node inspector 的 `takePreciseCoverage`，Python coverage.py 的 `switch_context`） | 探针脚本 + 结论 | **2026-11-13 结论：已具备**——`run_monolith` 已经在 `judge/coverage_monolith/units/<用例>.json` 按用例记录每个文件的行号；重新分组的算法并入 W4.2 | M |
| W4.7 | O 阶段 AWS 实测：工具 `measure_aws`（每个候选一个栈、B0/候选 ABAB、`aws-load-client` 锁、CloudWatch 调用次数覆盖检查、噪声规则、费用记录）；2026-11-26 起默认开启，`M2S_AWS=0` 才关闭 | `coord/aws.py`、`perf.py` | 默认 AWS 模式；关闭时行为不变。**2026-11-16 完成**：`coord/aws.py` + MCP `measure_aws`/`calibrate_aws_noise`，测试 `tests/test_coord_aws.py` | L |
| W4.8 | 验收运行：`v2-split` 在 bookstore 和 acmeair 上跑到 C2，开讨论室 | 两个新工作区 | 见下 | L |

| v2 模块 | 编排侧要补的（原始清单，逐项对应上面的工作项） |
|---|---|
| M0、F1–F3 | 事实库版本号：每次修正都升版本，并在公告板发 `artifact` 事件，用来驱动失效传播（W4.1） |
| G | 按包展开（G2）（W4.2） |
| J1–J6 | 按包展开 J1；J2 运行单体时拿 `docker-rie` 等资源锁（W4.2/W4.3）；J3 对账有争议的差异进规格讨论室（W4.4）；J3 判为事实错误的，退回 F2 修正（现状已这样）；J6 冻结后记下裁判版本，之后的任务都以它为输入（W4.5） |
| 规则 7 | 先做可行性检查（W4.6） |
| A1、A2 | 按蓝图领域展开 D、I1（W4.2） |
| D1 | 按包展开；可见性规则：增量测试的目录对实现线禁止读取（W4.2；deny_read 已生效） |
| I1–I3、V1–V4 | 和 v1 对应模块的做法相同；B0 打 tag 后做阶段复查（已生效） |
| O1–O4 | §4.13 的 AWS 实测（W4.7） |
| C1、C2 | `archive_run` 把 `coord/` 也打包进去（已完成，见 `coord/closing.py`） |

**M4 的验收标准**：v2 流水线在 v3 调度下跑通 bookstore 和 airline-booking（Flask）。acmeair / MongoDB 按 v2 方案 V2P-08 与 Q5 列为扩展（单体只能做接口层比对），不在 M4 验收内。

**M4 进展（2026-11-13）**：
- W4.1 完成：`coord/facts.py`。annotator 任务（v1 S0/<pkg>、v2 F2）关闭时按 sha 去重、升版本、写 `storage/facts_version.json`，并发 `artifact` 公告；spawn 事件记录当时的 `facts_version` 与 `judge_version`。测试 `tests/test_coord_facts.py`。
- W4.3 完成：`run_monolith` 取 `docker-rie` 槽（与 `run_validate` / `run_rie_verdict` 同一个机器级资源）。
- W4.4 完成：J3 的 `needs_ruling` 在有讨论室时开 spec 房间（proposer=J3）；门任务 `start: recheck`，每个房间回合作为它的依赖；architect 的 `record_ruling` 覆盖全部差异时门通过、被阻塞的下游放行；没有讨论室时退回原来的 architect worker（v2 N1 行为）。测试 `tests/test_coord_room_gate.py`。
- W4.5 完成（记录口径）：spawn 事件带 `judge_version`；J6 的版本在 `judge_frozen` 事件与 `judge_manifest.json` 中。
- W4.6 结论：已具备（见上表）。
- W4.2 部分完成（2026-11-13）：新增 `coord/templates/v2-split.yaml`。
  - F2 按包展开（F2-<包>），另有一个 F2-shared：`shared_after_children: true` 让它在全部子任务之后运行，用完整的 `check_provenance` 汇总校验事实库；G1/J1 等 `F2:*` 与 `F2:shared`。
  - I1 按领域展开：新增 `split: domain`，A1 填 `storage/work_packages.json` 的 `impl_packages` 后，按「包 → 函数、目录」创建 I1-<包>，scope 用 `output/lambdas/{app}/{fn}/**`（每函数目录不重叠，可并行），另有 I1-shared 共享层；I2a 等 `I1:*` 与 `I1:shared`。A1 的 scope 加 `storage/work_packages.json`，角色说明新增"填 impl_packages"一节。若 A1 没填（或某个包缺失），`_expand_domain` 用蓝图的 `lambda_functions[].entry_points` 与工作包的 endpoints 机械补齐并记 `impl_packages_fallback` 事件——M3 遗留的 `impl_packages=null` 不再阻塞。
  - 模板/加载器/展开的测试在 `tests/test_coord_split.py`（domain 展开、shared_after_children、v2-split 可加载）。
  - F2 的包级子任务都写 `storage/analysis_report.json`；2026-11-24 之前按 scope 冲突串行执行（事实库是单文件）。现在模板给 F2 声明 `shared_write: [storage/analysis_report.json]`，`scopes_conflict` 不再串行化子任务，改由 `provenance.annotate_*` 的跨进程文件锁串行化每次读-改-写（见下方进展）。
- **W4.2 补完（2026-11-16）：J1/D1/G2 的拆分与资产约定**
  - **G2 按包 + G2M 合并**：G2 拆成 `G2-<包>` 与 `G2-shared`（`shared_after_children`）；子任务只写 `storage/gaps/<包>.json`（属于本包、`endpoints` 非空的缺口；新条目可省略 id），shared 收无端点/跨包缺口；`G2M`（调度器 `gap_merge`）把 `storage/gaps/*.json` 合并回 `storage/gap_inventory.json`——既有条目按 `key` 保留原 id 与决定，新条目按包顺序分配 `GAP-<n>`，然后 `gap_inventory` 检查收口。新检查 `gap_package`（`gaps.check_package`）校验端点归属、覆盖本包既有条目、id 与证据格式。测试 `tests/test_gaps_packages.py`。
  - **D1 按领域**：A1 之后按 `impl_packages` 展开 `D1-<领域>`，每个领域写 `verification/delta/events/{fn}/**`，故障场景文件名必须以 `delta-<领域>-` 开头（scope 用 `delta-{pkg}-*.json`，领域之间不共享文件）；自查仍是整份资产的 `delta_assets` dry run。I1 的依赖改为 `D1:*`（同领域优先）。
  - **J1 按包 + J1-shared**：`J1-<包>` 只写 `judge/cases/<包>/<用例>.json`（不再按接口 slug 建目录），子任务检查 `judge_cases_pkg`（每个函数至少一条用例、id 唯一、spec_refs 非空、目录内 function 属于本包，`judge.check_cases_package`）；`J1-shared` 在所有包之后写 `judge/fixtures/**`、`judge/identities.json`、`judge/faults/**`、`judge/uncovered.json` 等并跑整份 `judge_assets`。J2/A1 改依赖 `J1:*` 与 `J1:shared`。为了让"在另一个族的 shared 子任务之后展开"能写成静态依赖，新增无操作任务 `F2DONE`（`barrier` 动作）与 `expand_after: [F2DONE]`；`_resolve_deps`/启动依赖现在也解析 `<族>:shared`。
  - 角色说明补了"按包/按领域拆分时"的小节（`m2s-spec-annotator` G2、`m2s-verifier` J1/D1）。
  - 全流程假 worker 测试 `tests/test_coord_full_flow.py::test_v2_split_expands_f2_and_i1` 覆盖三族的子任务、G2M/J2/I2a 的顺序。
- **W4.7 完成（2026-11-16）**：`coord/aws.py` 的 `measure_aws` / `calibrate_noise`（MCP 工具 `measure_aws`、`calibrate_aws_noise`）。2026-11-16 时默认关闭（`M2S_AWS=1` 才启用）；2026-11-26 起改为默认开启，显式 `M2S_AWS=0` 才返回 `DISABLED` 且不触碰 AWS。每个候选一个栈（`m2s-<ws>-b0` 保留、候选测完即拆）、ABAB、`aws-load-client` 锁、CloudWatch 改动函数调用次数覆盖检查（未走到的函数记 uncovered、不参与判定）、B0 对 B0 校准的噪声阈值（`perf/aws_noise.json`）、费用记录（`perf/aws_costs.json`）。2026-11-26 起真实运行默认执行（费用由发起人承担），`M2S_AWS=0` 关闭。测试 `tests/test_coord_aws.py`（假 backend）。**本机未做真实 AWS 运行**。
- **W2.10 的 token 统计（M4 待办）完成**：OpenHarness 的 `-p` 流在 `assistant_complete` 上带 `usage`（input/output tokens，提交 `e4eb3f0`）；`coord.dispatcher.task_usage` 按任务汇总 `coord/run/<task>-a*/stream.jsonl`，运行报告的任务表新增 token（入/出）列与合计。测试 `tests/test_coord_units.py::test_task_usage_...`。
- **M3 排期 M4 的工具欠账**：T-1 完成——`verify.py` 新增 `expect.body.raw_equals`（正文逐字符比较，`10000.0`/`1e4`/引号串/尾随空白都判失败；base64 响应不匹配文本契约）；T-2 已在共享 `verify.py` 具备（`repeat` 字段、controller 的 repeats、judge 的比较），只做记录关闭。v1 侧的 `impl_packages` 欠账：`v1-split.yaml` 的 S1 scope 加入 `storage/work_packages.json`，角色说明覆盖 S1/A1，`gate(blueprint)` 新增 CG 检查 `check_impl_packages`（填了就必须把每个蓝图函数恰好映射到一个包；null 仍合法，展开时有 fallback）。
- **仍未做**：无（M4 的 W4.1–W4.8 与 M3 排期 M4 的工具欠账全部落地；W4.7 的真实 AWS 运行按设计留在人确认费用之后）。
- **F2 并行标注（2026-11-24）**：v2-split 给 F2 声明 `shared_write: [storage/analysis_report.json]`，`scopes_conflict` 只对“双方都声明”的同一路径放行（单方声明仍按冲突处理）；`provenance.py` 的三个 annotate 工具在 `_load → validate → _save` 全程持有 `storage/.analysis_report.lock`（O_EXCL 创建，pid 死亡或 TTL 过期可回收，做法同 `coord/locks.py`），`_save` 改用 PID 唯一 tmp + `os.replace`。锁文件与 `analysis_report.json.*.tmp` 计入调度器 `ALWAYS_ALLOWED`，并发任务的 write_scope 不会误删别人的锁/临时文件。`F2-shared` 仍由 `shared_after_children: true` 排在全部子任务之后。`--stop-after` 现在也接受拆分族：`--stop-after F2` 在全部 F2 子任务（含 shared）关闭后以 `stopped` 收尾。测试：`tests/test_provenance_lock.py`（两进程并发无丢失更新、锁超时、异常释放、死锁回收）、`tests/test_coord_locks.py`（`shared_write` 放行调度、锁文件不算越界）、`tests/test_coord_f2_parallel.py`（F2-only：用 v2-split 原样的 F2 定义只跑 F1→F2→F2DONE，两个 fake worker 进程各自通过真实 `annotate_entry_point` 标注同一份报告；事件日志证明两个 F2 子任务在第一个退出前都已启动，两个 `check_provenance_pkg` 与 F2-shared 的全局 `check_provenance` 均 PASS）。**真 LLM 实跑（2026-11-24，coffee-f2-par-1）**：coffee 的 4 个 F2 包在同一份报告上由 3 个真实 LLM worker 并发标注（第 4 个排队），所有包级检查和全局 `check_provenance` PASS，F2 墙钟 54.6 min（4 个包串行约 117 min，约 2.1×），运行以 `stopped` 结束。
- **验证角色拆分（2026-11-25）**：`agents/m2s-verifier.md` 只保留 v2 模式（J1/J3/J4/J5/D1/V3 + D1 用的增量资产格式与预期规则）；旧 v1 内容（V1/V2/VR/VS + test plan）迁到新角色 `agents/m2s-verifier-v1.md`，`v1.yaml` / `v1-split.yaml` 的 V1/V2 改用该角色；v2 模板保持 `m2s-verifier`。J1 worker 的 system prompt 从约 32.8k 字符降到 26.4k，且不再包含 `## V1/## V2/## VR/## VS`（v1 角色仍完整保留）。通知/讨论室的角色集合把 `m2s-verifier-v1` 与 `m2s-verifier` 同等对待（v1 不使用讨论室）。测试：`tests/test_coord_j1_perm.py::test_verifier_role_split_keeps_v1_out_of_the_v2_prompt`、`tests/test_coord_harness.py`（harness stamp 记录两份 agent）、`tests/test_coord_leak.py`（v1 verifier 的公告可见性）。
- **J3/J4 事实回炉族选择修复（2026-11-25）**：`_fact_targets()` 原来取最后一个 `m2s-spec-annotator` 拆分族；v2-split 在 2026-11-16 加入按包的 G2 后，`families[-1]` 变成 G2，J3/J4 的 `fact_error` 会被送到 gap 任务而不是事实库任务。新增 `Dispatcher._fact_family()`：从 J1 的 `<family>:*` 依赖里选事实族（模板没写这条关系时回退旧行为），`_fact_targets()` 改用它；`_after_j3` / `_after_j4` 的非拆分分支不变。测试：真实 v2-split（F2 与 G2 都展开）的 `_fact_targets` 单元测试 + `_after_j3` / `_after_j4` 端到端回炉目标断言。
- **架构师角色拆分（2026-11-25）**：`agents/m2s-architect.md` 只保留 v2/A1 内容（身份/设计原则 + `MODE: ruling/adapter/gaps`、适配映射、缺口决定、迁移带来的对外变化），删掉 v1 的 `## 回炉：裁定验证线提出的 spec_issues` 与 `## 裁定 S5 的 spec 放宽申请`；工作报告改为 `storage/stage_reports/<task_id>.json`，完成方式改为 `submit_task`/`report_blocked`，frontmatter 重写。旧 v1 内容（V1 回炉、S5 裁定、S1 报告与 `STAGE_COMPLETE/STAGE_BLOCKED`）迁到新角色 `agents/m2s-architect-v1.md`；`v1.yaml` / `v1-split.yaml` 的 S1 改用该角色，v2 模板保持 `m2s-architect`。`dispatcher._route_target` / `_flow_failed` 按模板解析路由角色（`m2s-architect` 或 `m2s-architect-v1`），v1 的 spec/blueprint 回炉仍能找到 S1；通知/讨论室角色集合加入 `m2s-architect-v1`。测试：`tests/test_coord_j1_perm.py` 的架构师拆分与 v1 路由断言、`tests/test_coord_harness.py`（harness stamp 记录两份 agent）、`tests/test_coord_leak.py`（v1 架构师的公告可见性）。
- **代码/模板角色拆分（2026-11-26）**：`agents/m2s-code-developer.md` 只保留 v2/I1 内容（`submit_task`/`report_blocked`、裁判自测、`run_gate(phase="code")`），S2 的 STAGE 完成协议、`stage_reports/S2.json` 和主 Agent 输入契约迁到 `agents/m2s-code-developer-v1.md`（v1 文件不含 v2 的裁判自测）。`agents/m2s-sam-engineer.md` 改为 I2a/I2b/I2c：I2a/I2b 不调用 `run_gate`/`validate_sam` 但完成时必须 `submit_task`/`report_blocked`，I2c 用 prompt 里的 `MERGE`（`python "<MERGE>" template "<WS>"`）而不是写死仓库/Python 路径；S3a/S3b/S3c 与 SG 迁到 `agents/m2s-sam-engineer-v1.md`。`v1.yaml` / `v1-split.yaml` 的 S2/S3a/S3b/S3c 改用 -v1 角色，v2 模板保持 `m2s-code-developer`/`m2s-sam-engineer`；`coord/prompts.py` 的 A1/I1/I2a-c 摘要改用 v2 的任务 id；通知/讨论室角色集合加入 `m2s-code-developer-v1` / `m2s-sam-engineer-v1`。v1 的 code/template 回炉经 `_route_role` 仍能找到 S2/S3c。测试：两个新角色拆分断言（`tests/test_coord_j1_perm.py`）、harness stamp 与公告可见性、v1 路由断言；47+135 项相邻回归通过。
- **优化线默认 AWS 实测（2026-11-26）**：`coord.aws.enabled()` 改为默认 `True`，只有显式 `M2S_AWS=0/false/no/off` 才关闭；`s5_report` 用同一判定，O1M/O2 的 prompt 默认生成 AWS 实测段（`M2S_AWS=0` 才生成 RIE/代理段）。没提供 `M2S_AWS_LOAD_CMD` 时 O1M/O2 会 `report_blocked`，不会静默退回本地。文档（设计 §2.2、README、skill、角色说明）和测试同步；`test_coord_aws` 新增默认开启断言，原来的“默认关闭”用例改为显式 `M2S_AWS=0`。
- **O1/P → B0P → O2 两基线（2026-11-26）**：`v2-split` 的优化线改为 `B0 → O1（计划）→ 候选 owner → O1M（测量）→ accept/revert → V2P（check_adapter + 全量 judge）→ V3P（全量 delta + `fault_baseline_B0P.json`）→ B0P → O2（局部优化）→ C1`。O1 用方式 B：新角色 `m2s-perf-triage` 只写 `perf/**` 和 `O1.json`，不写 `output/**`、blueprint、模板、delta 或 judge；候选的 owner（A1/D1/I1/I2）写最终实现并提交 `perf(cand-<id>): ...`；O1M 只测一个候选，写 `perf/verdicts/<id>.json`；`perf_accept` 按 verdict 保留或 revert owner 的提交。`O1C` 是 dispatcher 展开的 `split: candidate` 族（owner→measure→accept 串行链），`O1D` 收口；没有候选时 B0P 与 B0 同一 commit，V2P/V3P 跳过。`O2` 就是原来的局部 O（报告改 `O2.json`），`run_equivalence` 的 auto baseline 变为 `B0P > B0 > v0`，`s5_report` 同样优先 B0P。C1 失败时先从最新 O2 commit 逐个 revert 并重跑三项全量检查（最多 3 个），通过即停；否则整体回到 B0P；同步 `perf_report.json`/`O2.json`，最终报告分开 O1（B0→B0P）和 O2（B0P→final）。测试：`tests/test_coord_o1_b0p.py`（DAG、候选展开、accept/revert、B0P 无候选/有候选、C1 精细回退），`test_coord_units.py`/`test_coord_j1_perm.py`/`test_coord_full_flow.py::test_v2_split_expands_f2_and_i1` 同步。
- **优化线角色、设计与 skill 拆分（2026-11-26）**：`agents/m2s-perf-optimizer.md` 只保留 v3 O（B0、`O.json`、四项正确性检查、RIE/AWS、`submit_task`/`report_blocked`），删掉旧优化线内容（旧基线、`S5.json`、STAGE 协议、复核/重新判决、规格放宽、SG、主 Agent）；旧内容迁到 `agents/m2s-perf-optimizer-v1.md`，`v1.yaml` / `v1-split.yaml` 的 S5 改用该角色，v2 模板保持 `m2s-perf-optimizer`。设计文档拆为 v2 `PERF_OPTIMIZATION_DESIGN.md`（O：B0、四项检查、§2.2 AWS、RIE 默认/代理降级、允许改动、规则库、输出 schema、测量窗口）与 `PERF_OPTIMIZATION_DESIGN_v1.md`（v1 归档：旧流程、模拟延迟与 sam+Docker、复核/重新判决、SG、v1 默认配置、prompt/消融、附录实验）。新增 `.openharness/skills/m2s-perf-optimizer-kb/`（O 的判决框架摘要 + `references/PERF_OPTIMIZATION_DESIGN.md`），O 角色显式调用 `skill(name="m2s-perf-optimizer-kb")`；`m2s-performance-kb` 保留给 I1 写代码时参考。所有引用（agent、`PIPELINE_REDESIGN.md`、`CONTRACT_TESTING_PLAN.md`、`m2s-migrate`、`perf-tools`、代码注释）同步。测试：角色拆分断言、模板角色、harness stamp、公告可见性。

**M4 验收（2026-11-16）：bookstore-m4 + airline-m4 都跑到 C2**
- 两个新工作区都用 `v2-split`、3 个 worker、开讨论室，`run done` / `dispatcher exit 0`，C2 分别归档到 `evidence/bookstore/bookstore-m4` 与 `evidence/airline-booking/airline-m4`。
- **bookstore-m4**：两轮 J3 规格室（spec-j3-r1/r2，共 16+ 回合），裁定 `R-A1-a3-1..4`；两个 HUMAN_J6 决定（未认证超大体按网关 401 先于 413、`wb-cart-x-customer-id-header-ignored#spoof-*` 记账延伸）由人接受；O 的 REQ-1（`update-bestsellers`）由人接受，连同 1 条未覆盖的防御性行；C1 的 `gate(final)`/裁判/delta PASS。
- **airline-m4**：身份 id 冲突由人定方案 (A)；工具修复 `788d9cf`（`check_adapter` 的 `${id}` 取裁判记录的字面值，回退 register body 的 userId）。V2 首跑败在 3 个按「同一条预订第 n 次读」注入的故障场景；异常处理者按 A1 的 D-20 补裁定 `R-C003-V2-triage-a1-1`（implementation-trace，7 个 `case#step`，`deviates_from_monolith=false`），重跑 PASS。终局裁判 cases `34 pass / 7 migration-change / 14 dropped`、fault runs `21 pass / 4 migration-change / 4 dropped / 2 ruled`；delta 16/16；`gate(final)` PASS；22 条 gap 全 decided；B0 复查 `ok`（C6 的 `SharedUtilsLayer` 未被引用一项交优化线）。O 保留 1 处改动（`bookings-cancel` 两步补偿并发化，`5fe0c7c`；串行深度 5→4，调用数/RRU/WRU 不变）。裁判 v1 冻结后未变（`judge_unchanged` 全程通过）。
- 运行中发现并修掉的调度器缺陷：`63ab894`（J3/J4 的事实修正按端点路由到 F2 包子任务）、`f286ec3`（门阻塞拆分子任务的整个族，而不是族名）、`8c06d7f`（`br` label 截断到 50 字符；崩溃留下的部分展开可续）、`61e37e3`（一个 action 可能重开任务，ready 列表不能跨 action 复用）、`788d9cf`（身份 id 字面值）。
- 验收口径：v2 方案 V2P-08 指定的两个应用（bookstore、airline-booking）已覆盖；acmeair/MongoDB 仍是 Q5 扩展，不在 M4 内。
- 顺带收敛：`_expand_family`/`_expand_domain` 只在真正新建了子任务时才打 "expanded to" 日志（此前每轮都刷一行）。
- W4.2 的 J1/D1/G2 拆分与 W4.7 的工具均已完成；W4.7 的真实 AWS 运行仍待人确认费用。

**M4 后：O 的 AWS 判决模式（2026-11-19 设计定稿并实现）**

- **范围**：只改 O。正确性（裁判、增量、等价、覆盖）保持本地 `mode="local"`（无 Docker、无 AWS 费用）；AWS 只提供性能、成本、覆盖。
- **判决**：主维度 = Lambda 热调用 Duration（CloudWatch/REPORT，剔除冷启动）+ 边际成本/请求（Lambda Duration/GB-秒 + 请求费 + DynamoDB RRU/WRU + SQS，**不含 API Gateway**）；guard = 端到端 p50、p95、冷启动（Init Duration/次数）；覆盖 = 改动函数调用次数 > 0，否则 `INCONCLUSIVE`。含 API Gateway 的总成本只进报告。
- **规则**：沿用“没有任何维度 worse 且至少一个 better → kept；一好一坏 → pending_human；无显著差异但代理指标更好 → kept_inconclusive；覆盖不到 → INCONCLUSIVE”；RIE 的 0/5/15ms 模拟延迟扫描和 `realistic_latency_rule` 在 AWS 模式下取消，用 B0 对 B0 校准的噪声 + ABAB 代替。
- **单改动流程**沿用 Docker 时期的做法：基线 = 当前 HEAD（B0 + 已 kept 的改动）；滚动基线栈 + 一个候选栈，B0 栈只用于噪声校准；`aws-load-client` 锁、`calibrate_aws_noise`、≥3 轮 ABAB、每轮 ≥10 个热样本；互不影响的改动可分别对同一基线测。
- **实现（2026-11-19）**：
  - `coord/aws.py`：`measure_aws(ref=None)` 直接部署工作树（临时拷贝，不污染 `.aws-sam`）；`Backend` 新增 `pass_metrics`（Lambda 日志 REPORT：调用次数、热 Duration、billed ms、init；DynamoDB RRU/WRU，含 GSI；SQS 请求估算）、`resource_tables`/`resource_queues`、`api_type`、`stack_tags`；结果新增每 pass 的 `lambda`/`units`/`uncovered`、两侧汇总 `sides` 和 `api_type`。测量窗口沿用 §10.1 的纪律：每个 pass 用 `minute_gap` 独占整分钟，REPORT 按 RequestId 去重并以 `until=下一个 pass 开始` 截断，负载结束后统一等 90 秒再读指标。滚动基线：基线栈打 `M2SBaseline=<HEAD sha>` 标签，HEAD 变了就重部署，同一 HEAD 的重复测量才复用。
  - `perf.py`：新增 `run_aws_verdict`（MCP 工具同名）。主维度 Lambda 热 Duration + 每请求边际成本；guard 端到端 p50/p95 + 冷启动；覆盖不到或热样本 <10 → `INCONCLUSIVE`；含 API Gateway 的总成本只进报告；`budget_usd` 超预算 → `pending_human`；结果写 `perf/verdicts/`。
  - O 的 prompt：静态的“不部署到 AWS”去掉，按 `M2S_AWS` 生成两段之一；开启时带上 `M2S_AWS_LOAD_CMD` 和 `M2S_AWS_BUDGET_USD`，并要求把 `run_aws_verdict` 结果写进 `evidence.aws`。
  - `s5_report`：AWS 模式（默认；`M2S_AWS=0` 关闭）且本次 O 的 spawn 事件记录 `aws: true`（或报告标注 `judged_with: "aws"`、已有改动带 AWS 判决）时，每个本次判过的 kept/kept_inconclusive 改动必须有 `evidence.aws.verdict` 且为 kept/kept_inconclusive；spawn 事件记录启动时的 HEAD，之前已提交的 kept 改动（O-only 重跑旧工作区）不追溯。最终报告展示 `evidence.aws`、advisories 和“哪些层在 AWS 上测”的说明。
  - 负载与数据：`measure_aws` 的环境带 `M2S_AWS_OUTPUTS`/`M2S_AWS_RESOURCES`/`M2S_AWS_WS`/`M2S_AWS_BENCHMARK`；`seed_cmd`（`M2S_AWS_SEED_CMD`）在每个栈部署后、测量前运行一次。参考实现 `perf-tools/aws_load.py` 复用 e2e 的 Cognito 会话，HTTP benchmark 重复发请求，SQS benchmark 按轮发送 `sqs.records`（每轮换 orderId）。
  - 测试：`tests/test_perf_aws.py`（判决映射、benchmark/seed 传递）、`tests/test_s5_aws.py`、`tests/test_coord_aws.py` 的滚动基线与 seed/load 环境、`tests/test_coord_units.py` 的 O prompt 条件用例、`tests/test_aws_load.py`（占位符替换、表名映射、HTTP/SQS 分发）。
  - 真实 AWS 冒烟：默认即 AWS 模式，提供 `M2S_AWS_LOAD_CMD` 即可（先 `calibrate_aws_noise` 可选）；不想花钱时 `M2S_AWS=0`。
- 依据：`PERF_OPTIMIZATION_DESIGN.md` §2.2。

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
| S1 | `S1` | m2s-architect-v1 | 所有 S0 | `run_gate(blueprint)` | 否 |
| V1 | `V1`（M2 起为 `V1:<包>`） | m2s-verifier-v1（V1 模式） | S1 | `run_validate(dry_run=true)`（M2 起按函数过滤）；报了阻塞级别的 `spec_issues` 时：M1 重新打开 S1，M3 起开规格讨论室 | 是 |
| S2 | `S2`（M2 起为 `S2:shared` 和 `S2:<包>`） | m2s-code-developer-v1 | S1；所有 V1（M2 起：`V1:<包>`，以及 S2:shared） | `run_gate(code)`（M2 起按包过滤） | 是 |
| S3a、S3b、S3c | `S3a` → `S3b` → `S3c` | m2s-sam-engineer-v1 | 所有 S2；三者串行 | S3a：`output/template.part1.yaml` 存在，并且包含 `# --- END METADATA ---`；S3b：`output/template.part2.yaml` 存在；S3c：`output/template.yaml` 存在，`run_gate(template)` 通过（片段本身不是完整模板，不单独跑 `validate_sam`） | 否 |
| S4 | `S4` | m2s-validator | S3c | `run_gate(final)` | 否 |
| V2 | `V2` | m2s-verifier-v1（V2 模式） | S4 | `run_validate()`，且 `verdict_A.json` 为 PASS。失败时按路由建回炉任务（S2:<包> 等），下游任务随之失效；路由判断不了的，M3 起进分诊讨论室 | 否 |
| 打 tag v0 | `TAG_V0` | 调度器 | V2 | 工作区 git 打了 v0 tag；之后做阶段复查 | — |
| S5 | `S5` | m2s-perf-optimizer-v1 | TAG_V0 | `perf_report.json` 里没有 pending_human；`run_gate(final)` 通过 | 否（M4 起按改动拆，见 O2） |
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
