案例编号：C003-V2-triage（一个调度器处理不了的异常）

- 案情材料：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4\coord\cases\C003-V2-triage.md
- 本次运行之前的处理记录：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4\coord\journal.md（可能为空）
- 工作区：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4（只读）

先读案情材料和处理记录，需要时再读工作区里的相关文件；然后调用一次 mcp__m2s-server__handler_decide(case="C003-V2-triage", action=..., target=..., feedback=..., rationale=...)，结束会话。
遇到网络错误时重试 1-2 次。