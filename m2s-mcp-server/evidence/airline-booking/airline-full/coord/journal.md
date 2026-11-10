# 异常处理记录

本次运行中异常处理者做过的判断，按时间排列。

## C001-B0-review（review，任务 B0）

- 决定：ok
- 理由：阶段复查未发现需要处理的问题，可以进入优化线（O）。证据：(1) 裁判自洽——judge/reconcile.json 零差异、mask_rules 为空、J5 变异分 1.0，8 个存活变异体都有分类，唯一 gap（negate:app.py:224）已由 J4 新增用例抓住；uncovered 仅 2 条且理由成立。(2) 裁定与规格——storage/rulings.json 4 条全部 decided 且 deviates_from_monolith=false；R-A1-a3-1 的影响范围精确到 21 个「用例#步骤」，R-A1-a3-2 覆盖 4 个由 Cognito 取代的 /auth 端点；gap_inventory 23/23 已决定，4 条 monolith_defect 一律照搬单体，没有未经人确认的擅自修复（故无需 escalate）。(3) 验证结果——V1 PASS；V2 PASS：162 步 pass、21 步 migration-change（都命中 R-A1-a3-1 引用的步骤，同用例其余步骤照常比对并通过）、33 步 dropped 仅落在被删的 /auth 端点，23 个故障场景 pass、3 个 dropped 同理，0 个 fail；V3 PASS：delta 25/25（RIE，连跑两遍一致）、B0 等价基线 RECORDED（33 次运行、infra_errors 空）。(4) 我单独核查了 V2 里唯一的非 Users 表差异 Airline-Loyalty-{stage}[2].points（期望 150、实际 200）：目标行集合 {150,200,50000} 与单体一致，差异来自 Scan 按随机 uuid 主键排序后「按索引配对」，单体自身在 pass1/pass2 也把该位置标成了符号（J3-a5-1 已登记为非阻塞），总积分由 read_back 步骤断言 350 并通过，属良性比较失真，不是迁移缺陷。(5) 过程健康——两次失败（A1-a2、I1-a1）都是 write_scope 越界被机械回退后重试通过，tools_unchanged/judge_unchanged 全程 ok，工作树干净、B0 tag 1f47912 含全部产物。(6) 优化线前置已就位：B0 基线、可构建模板、gate(final)/consistency PASS、coverage 已产出（V3 注明四个函数无 delta 用例为有意为之，V3-1 已核对）。D1/J3/I2c/I3 各阶段报告里的 issues 均为非阻塞的工具或文档建议，不属于本流水线要在此刻处理的问题。
- 给 worker 的反馈：（无）
- 调度器的处理：继续

