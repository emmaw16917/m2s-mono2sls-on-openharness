案例编号：C002-B0-review（阶段复查：检查到目前为止的运行有没有需要处理的问题）

- 案情材料：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4\coord\cases\C002-B0-review.md
- 本次运行之前的处理记录：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4\coord\journal.md（可能为空）
- 工作区：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4（只读）

先读案情材料和处理记录，需要时再读工作区里的相关文件；然后调用一次 mcp__m2s-server__handler_decide(case="C002-B0-review", action=..., target=..., feedback=..., rationale=...)，结束会话。
遇到网络错误时重试 1-2 次。