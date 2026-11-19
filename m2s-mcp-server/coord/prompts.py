"""Prompts for workers and the exception handler.

Worker prompts carry the elements of v1 SKILL §2 (stage id, absolute paths,
what to produce, rework information, the network-retry reminder) and replace
"print STAGE_COMPLETE" with the submit_task / report_blocked tools. The role
itself comes from the agent definition, passed as the system prompt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from coord.checks import REPO
from coord.template import TaskSpec

MERGE = REPO / "m2s-mcp-server" / "m2s_merge.py"

TASK_SUMMARIES = {
    "S0": "补全每个端点的请求、响应、业务规则，每条注明出处。规格字段只能经 mcp__m2s-server__annotate_entry_point "
          "和 annotate_global_middleware 写进 storage/analysis_report.json；写完自己用 check_provenance 自查。",
    "F2": "补全每个端点的请求、响应、业务规则，每条注明出处（同 S0）。规格字段只能经 mcp__m2s-server__annotate_entry_point "
          "和 annotate_global_middleware 写进 storage/analysis_report.json；写完自己用 check_provenance 自查。"
          "回炉信息来自规格对账（J3）时，按你的角色说明里的\"修正模式\"只改点到的事实。",
    "S1": "按规格设计 serverless 架构：用 mcp__m2s-server__merge_blueprint_key 逐个顶层字段写 storage/blueprint.json，"
          "设计决定记进 storage/DECISIONS.md；写完自己跑 run_gate(phase=\"blueprint\") 自查并修正。",
    "V1": "STAGE_ID=V1：只看规格写测试计划、测试事件和故障场景（verification/test_plan.json、"
          "verification/events/<function>/<case>.json、verification/faults/*.json）；预期写不出来的写进 test_plan.json 的 "
          "spec_issues。写完用 run_validate(dry_run=true) 自查格式。",
    "S2": "按 blueprint 写 output/lambdas/<domain>/<name>/（handler 和依赖声明），需要时写 output/layers/shared/；"
          "写完自己跑 run_gate(phase=\"code\") 自查并修正。",
    "S3a": "STAGE_ID=S3a：写 output/template.part1.yaml（AWSTemplateFormatVersion 到 Globals、Parameters、共享资源，"
           "以第一次出现的 \"# --- END METADATA ---\" 结束）。",
    "S3b": "STAGE_ID=S3b：写 output/template.part2.yaml（Function 资源、FUNCTIONS_METADATA 块、Outputs）。",
    "S3c": "STAGE_ID=S3c：用 MERGE 合并 part1 和 part2 生成 output/template.yaml（python \"{merge}\" template \"{ws}\"），"
           "然后自己跑 run_gate(phase=\"template\") 和 validate_sam 自查并修正合并后的模板。",
    "S4": "做一致性检查，写 output/consistency_report.json。",
    "V2": "STAGE_ID=V2：执行测试、失败分诊、白盒补测、记录故障行为基准（按你的角色说明里 V2 的部分）。"
          "判定以 verification/verdict_A.json 为准；FAIL 时在工作报告的 routing 里写清退回对象。",
    "S5": "STAGE_ID=S5：在 v0 上做语义保持的性能改动（按你的角色说明）。每个保留的改动单独提交到 WS 的 git；"
          "写 output/perf_report.json。",
    # v2 (PIPELINE_V2_PLAN.md appendix A). The role docs name the stages by their v1 ids.
    "A1": "按规格设计 serverless 架构（角色说明里的 S1）：用 mcp__m2s-server__merge_blueprint_key 逐个顶层字段写 storage/blueprint.json，"
          "设计决定记进 storage/DECISIONS.md。还有三项：storage/adapter_map.json（\"适配映射\"一节）、storage/gap_inventory.json 的每条决定"
          "（\"缺口决定\"一节）、迁移带来的对外变化写成裁定（\"迁移带来的对外变化\"一节）。写完跑 run_gate(phase=\"blueprint\") 自查并修正。",
    "I1": "按 blueprint 写 output/lambdas/<domain>/<name>/（角色说明里的 S2）：handler 和依赖声明，需要时写 output/layers/shared/；"
          "写完跑 run_gate(phase=\"code\") 自查并修正，再按\"用裁判自测\"一节用 mcp__m2s-server__run_judge_self_test 对自己的函数跑裁判用例。",
    "I2a": "这一步是角色说明里的 STAGE_ID=S3a：写 output/template.part1.yaml（AWSTemplateFormatVersion 到 Globals、Parameters、共享资源，"
           "以第一次出现的 \"# --- END METADATA ---\" 结束）。",
    "I2b": "这一步是角色说明里的 STAGE_ID=S3b：写 output/template.part2.yaml（Function 资源、FUNCTIONS_METADATA 块、Outputs）。",
    "I2c": "这一步是角色说明里的 STAGE_ID=S3c：用 MERGE 合并 part1 和 part2 生成 output/template.yaml（python \"{merge}\" template \"{ws}\"），"
           "然后自己跑 run_gate(phase=\"template\") 和 validate_sam 自查并修正合并后的模板。",
    "I3": "这一步是角色说明里的 S4：做一致性检查，写 output/consistency_report.json。",
    "O": "在 B0 上做语义保持的性能改动（角色说明里的 S5，先读\"v2 流水线\"一节，它改变了基线和检查）。每个保留的改动单独提交到 WS 的 git；"
         "写 output/perf_report.json。",
}

# v2 role modes (PIPELINE_V2_PLAN.md): the role doc has a section per mode.
MODE_SUMMARIES = {
    "J1": "MODE: J1。只看事实库（用 mcp__m2s-server__judge_facts 读，不要整个打印 analysis_report.json，"
          "也不要读工具源码），写裁判用例：judge/cases/<接口 slug>/<用例>.json、"
          "judge/fixtures/、judge/faults/、judge/identities.json（需要时 judge/uncovered.json）。按你的角色说明里"
          "\"J1\"一节写；写完用 mcp__m2s-server__check_judge_assets 自查，直到 PASS。单体源码对你是关闭的。",
    "J3": "MODE: J3。给 judge/reconcile.json 里的每一条差异（J1 按事实库写的 predicted 和单体实际输出对不上的地方）分类，"
          "写进 judge/reconcile_classes.json：fact_error / needs_ruling / case_error / mask_gap，每条写 reason（证据）。"
          "mask_gap 要同时在 judge/mask_rules.json 里补屏蔽规则。按你的角色说明里\"J3\"一节做。",
    "ruling": "MODE: ruling。规格对账发现单体的某些行为需要裁定，差异清单在下面的回炉信息里。按你的角色说明里"
              "\"ruling\"一节，逐条用 mcp__m2s-server__record_ruling 裁定。",
    "J4": "MODE: J4。白盒补测：可以读单体源码。按 judge/coverage_monolith/summary.json 里还没走到的行（第二轮起还有"
          "回炉信息里的 gap 变异体）补用例 judge/cases/<接口 slug>/wb-*.json；代码里有、事实库没有的规则写进 "
          "judge/spec_issues.json。按你的角色说明里\"J4\"一节做，用 mcp__m2s-server__check_judge_assets 自查到 PASS。",
    "J5": "MODE: J5。给 judge/judge_validation.json 里没被抓住的变异体分类（equivalent / gap），写进 "
          "judge/mutant_classes.json。按你的角色说明里\"J5\"一节做，用 mcp__m2s-server__check_mutant_classes 自查到 PASS。",
    "G2": "MODE: G2。storage/gap_inventory.json 已由 gap_scan 按机械信号写好。按你的角色说明里\"MODE: G2\"一节：核对这些条目，"
          "读单体代码补上信号认不出来的缺口。不写决定，不改事实库。用 mcp__m2s-server__check_gap_inventory 自查到 PASS。",
    "gaps": "MODE: gaps。按你的角色说明里\"缺口决定\"和\"MODE: gaps\"两节，对 storage/gap_inventory.json 的每一条写决定，"
            "并写进 storage/DECISIONS.md。不改 blueprint，不读 output/lambdas/ 下的代码，不改 judge/。"
            "用 mcp__m2s-server__check_gap_inventory(require_decided=true) 自查到 PASS。",
    "D1": "MODE: D1。在写代码之前，按缺口决定和规则手册的必须规则写增量测试：verification/delta/events/<函数>/<用例>.json、"
          "verification/delta/faults/*.json。按你的角色说明里\"D1\"一节写；实现代码和单体源码对你都是关闭的。"
          "用 mcp__m2s-server__run_validate(dry_run=true, assets=\"verification/delta\") 自查到 PASS。",
    "V3": "MODE: V3。实现已经有了。在增量测试上跑 run_validate、run_coverage、run_equivalence(record, label=B0)，都带 "
          "assets=\"verification/delta\"。按你的角色说明里\"V3\"一节做；失败要分诊，写进工作报告的 routing。不改测试、不改代码。",
    "fix": "MODE: fix。实现已经有了（output/）。按下面的回炉信息只改点到的地方，不要重写。其余按你的角色说明，写完照常用 "
           "run_gate 自查。工作报告的 fixes 列出每一条回炉信息改了什么。",
    "adapter": "MODE: adapter。迁移已经完成（blueprint 和 output/ 都在），只缺适配映射：按你的角色说明里"
               "\"适配映射\"和\"MODE: adapter\"两节，写 storage/adapter_map.json，用 "
               "mcp__m2s-server__check_adapter_map 自查到 PASS。不改 blueprint，不读 output/lambdas/ 下的代码，不改 judge/。",
}

IMPL_NOTE = ("需要知道\"要保持哪些行为\"时，只引用规格本身：analysis_report.json 的出处、blueprint.json 的字段"
             "（包括 metadata.spec_resolutions）。")


def _feedback_block(feedback: list[dict[str, Any]]) -> str:
    if not feedback:
        return ""
    parts = ["## 回炉信息（调度器转来的，请据此修正）"]
    for index, item in enumerate(feedback, 1):
        parts.append(f"### {index}. 来自 {item.get('source', 'dispatcher')}\n{item.get('text', '').strip()}")
    return "\n\n".join(parts)


def _o_measurement_note() -> str:
    """O's measurement section: AWS by default when the run was approved for it."""
    from coord import aws as aws_mod

    if not aws_mod.enabled():
        return (
            "## 性能判决（AWS 实测未开启）\n"
            "M2S_AWS 不是 1：不要部署到 AWS，也不要调用 measure_aws / run_aws_verdict。"
            "用 run_rie_verdict（本地 Docker RIE，5 轮：每轮 1 冷 + 30 热）判决；Docker 也不可用时用 "
            "run_proxy_metrics，并在 perf_report.json 里把它标成低置信度。"
        )
    load_cmd = os.environ.get("M2S_AWS_LOAD_CMD", "").strip()
    budget = os.environ.get("M2S_AWS_BUDGET_USD", "").strip()
    lines = [
        "## 性能判决（AWS 实测已开启）",
        "M2S_AWS=1：O 的每个候选改动用真 AWS 实测判决（PERF_OPTIMIZATION_DESIGN.md §2.2）。",
        "- 只有性能判决走 AWS。语义保持的检查照旧先在本地做完，并且用 `mode=\"local\"`（不需要 Docker、不花钱）："
        "run_judge(mode=\"local\")、run_validate(mode=\"local\")、run_coverage(mode=\"local\")、run_equivalence(mode=\"diff\")。",
        "- 先跑一次 calibrate_aws_noise 拿噪声阈值（同一工作区只需一次）。",
        "- 每个改动单独测：提交前在工作树上调用 run_aws_verdict(benchmark=..., baseline=\"HEAD\")；一个改动一条基准，"
        "互相有影响的改动分开测。",
        "- 判据：Lambda 热调用 Duration 和每请求边际成本（Lambda 时长 + 请求 + DynamoDB + SQS，不含 API Gateway）为主；"
        "端到端 p50、p95 和冷启动是 guard，明显变差就 pending_human；负载没走到的函数判 INCONCLUSIVE，先修负载再判。"
        "总成本（含 API Gateway）只写报告，不参与判决；不再做 0/5/15ms 的模拟时延扫描。",
        "- 调用 run_aws_verdict 之前可以先跑 run_proxy_metrics（快、免费）；代理指标更好时传 proxy_better=true，"
        "AWS 无显著差异才会判 kept_inconclusive。",
        "- 只有 kept / kept_inconclusive 才提交这个改动；revert 就退回；pending_human 和 INCONCLUSIVE 原样写进 "
        "perf_report.json 的 evidence 和 stage report，交给人。",
        "- 把 run_aws_verdict 返回的 verdict、dimensions、guards、cost、saved_to 记进 output/perf_report.json 对应改动的 "
        "evidence.aws；报告顶层写 \"judged_with\": \"aws\"。",
        "- 不要自己改 M2S_AWS 或绕过预算；费用只在人批准后产生。",
    ]
    if load_cmd:
        lines.append(f"- 负载命令（直接传给 run_aws_verdict 的 load_cmd）：`{load_cmd}`")
    else:
        lines.append("- 环境里没有 M2S_AWS_LOAD_CMD：先用 report_blocked 说明缺少负载命令，不要自己编一个去压测。")
    if os.environ.get("M2S_AWS_SEED_CMD", "").strip():
        lines.append("- 环境里有 M2S_AWS_SEED_CMD：run_aws_verdict 会在每个栈部署后、测量开始前自动运行一次"
                     "（灌 benchmark 的 fixtures/generate 数据），不需要你手动传参。")
    if budget:
        lines.append(f"- 预算（run_aws_verdict 的 budget_usd）：{budget}；估计超过它就判 pending_human。")
    return "\n".join(lines)


def worker_prompt(spec: TaskSpec, ws: Path, monolith: Path, attempt: int,
                  feedback: list[dict[str, Any]], extra: str = "") -> str:
    summary = (MODE_SUMMARIES.get(spec.mode or "") or TASK_SUMMARIES.get(spec.id)
               or f"STAGE_ID={spec.id}：按你的角色说明完成这一阶段。").format(merge=MERGE, ws=ws)
    # A task the monolith is closed to (deny_read: monolith) is not told where it is.
    paths = ([] if "monolith" in spec.deny_read else [f"- MONOLITH = {monolith}"]) + [f"- WS = {ws}"]
    if spec.id in ("S3c", "I2c"):
        paths.append(f"- MERGE = {MERGE}")
    scope = "、".join(spec.scope) or "（无）"
    sections = [
        f"STAGE_ID: {spec.id}" + (f"\nMODE: {spec.mode}" if spec.mode else ""),
        "这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。"
        f"这是这个任务第 {attempt} 次尝试。",
        "## 绝对路径\n" + "\n".join(paths),
        "## 任务\n" + summary + (f"\n\n{extra.strip()}" if extra.strip() else ""),
    ]
    if spec.id == "O":
        sections.append(_o_measurement_note())
    if spec.line == "impl":
        sections.append(IMPL_NOTE)
    if spec.extra.get("package") == "shared":
        sections.append(
            "## 共享任务\n"
            "这是按工作包展开后的共享任务：不负责某个包的接口，而是处理被多个包共用的部分。"
            "先看 `WS/storage/work_packages.json` 的 `shared_code`（被 2 个以上包用到的函数）和 "
            "`analysis_report.json` 的 `global_middleware`，按角色说明里的「共享代码」一节处理；"
            "每个共享条目只写一次，包任务会引用它。"
        )
    if spec.extra.get("room_id"):
        from coord import rooms as rooms_mod

        room_id = str(spec.extra["room_id"])
        kind = str(spec.extra.get("room_kind") or "spec")
        role = str(spec.extra.get("room_role") or spec.role or "")
        state = rooms_mod.load(ws / "coord", room_id) or {}
        history = rooms_mod.messages(ws, room_id)
        sections.append(
            "## 讨论室\n"
            f"这是 {kind} 讨论室 `{room_id}`，主题：{state.get('theme', '')}。你这一轮以 `{role}` 身份发言。\n"
            "规则：只在你这一轮发言；用 `mcp__m2s-server__room_post(room_id, kind, body)` 提交一条意见，"
            "然后用 `submit_task` 结束。不要修改工作区文件（这是讨论，不是实现）。\n"
            "讨论室和公告板里给实现线看的内容只能有：失败的函数、出处、期望和实际结果；"
            "不能出现 verification/ 下的路径或原文，否则会被拒绝。\n"
            "spec 讨论室：architect 的发言里用 `record_ruling` 写出结论；triage 讨论室："
            "每个嫌疑模块用 JSON 归因 {\"module\": \"code|template|spec|任务 id\", \"reason\": \"...\", \"confidence\": \"high|medium|low\"}。\n"
            "当前讨论：\n" + json.dumps(history[-12:], ensure_ascii=False, indent=1)
        )
    block = _feedback_block(feedback)
    if block:
        sections.append(block)
    sections.append(
        "## 完成方式\n"
        f"1. 写工作报告 WS/storage/stage_reports/{spec.id}.json（格式按你的角色说明）。\n"
        "2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。\n"
        "3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；"
        "认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。\n"
        f"4. 调度器会自己运行这个任务的检查（{', '.join(spec.check) or '无'}），你的自述不作为判定依据。"
    )
    sections.append(
        "## 注意\n"
        "- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，"
        "不要因为一次网络错误就直接放弃或提前结束。\n"
        f"- 只改这个任务范围内的文件：{scope}。范围之外的改动会被撤销，这次尝试判为失败。\n"
        "- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。\n"
        "- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。\n"
        "- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。\n"
        "- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。"
    )
    return "\n\n".join(sections)


HANDLER_SYSTEM_PROMPT = """你是 m2s 迁移流水线 v3 的异常处理者。调度器是确定性的程序，遇到它的规则判断不了的情况时，才会启动你。

你的职责：读案情材料和本次运行的处理记录，做一个决定，然后结束会话。
- 只能通过 mcp__m2s-server__handler_decide 输出决定，调用一次；
- 不要修改工作区里的任何产物，不要修改流水线的工具；
- 判断不了就选择 escalate（交给人），不要猜。
- 处理记录里标题为「人的决定」的条目是人已经做过的决定：同一件事没有新证据时，照它办，不要再交给人。

可选的决定：
- retry：让同一个任务再试一次，在 feedback 里写清这次要怎么改；
- reroute：退回更早的任务（target 写任务编号，例如 S2），feedback 写清问题；
- escalate：交给人，在 rationale 里写清要人判断什么；
- ok：只用于阶段复查，表示没有发现需要处理的问题。

给实现线任务（S1、S2、S3a、S3b、S3c、S4）的 feedback 只能用规格的语言：接口、字段、状态码、表状态、业务规则、gate 的检查项。
不能写测试用例、fixture、断言写法或 verification/ 下的路径。
v2 流水线（任务 A1、I1、I2）里裁判用例对实现线可见，可以引用 judge/ 下的用例；增量测试 verification/delta/ 仍然不能写给实现线。
裁判冻结之后谁都不能改 judge/，分诊也不能。

v2 里裁判判失败时，先看差异是不是迁移带来的：
- 规则手册默认做法的必然结果（例如未登录由网关应答 401，响应体和头和单体不同）：用 mcp__m2s-server__record_ruling 记一条 category 为 migration-change 的裁定，
  affected_cases 精确到步骤（写 用例#步骤，不要写整个用例，否则同一个用例里其他步骤的差异也被放过了），basis 写规则手册的哪一条，然后 retry；
- 这是有人选择不照搬单体（例如修掉单体的 500）：不要自己裁定，escalate 交给人；
- 其他：是代码或设计的问题，reroute。"""


def handler_prompt(case_id: str, case_file: Path, journal: Path, ws: Path, kind: str) -> str:
    what = "阶段复查：检查到目前为止的运行有没有需要处理的问题" if kind == "review" else "一个调度器处理不了的异常"
    return (
        f"案例编号：{case_id}（{what}）\n\n"
        f"- 案情材料：{case_file}\n"
        f"- 本次运行之前的处理记录：{journal}（可能为空）\n"
        f"- 工作区：{ws}（只读）\n\n"
        "先读案情材料和处理记录，需要时再读工作区里的相关文件；然后调用一次 "
        f"mcp__m2s-server__handler_decide(case=\"{case_id}\", action=..., target=..., feedback=..., rationale=...)，结束会话。\n"
        "遇到网络错误时重试 1-2 次。"
    )
