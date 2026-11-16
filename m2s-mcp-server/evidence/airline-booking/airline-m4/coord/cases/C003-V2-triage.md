# C003-V2-triage：裁判在目标版本上没有通过

- 任务：V2
- 类型：triage
- 时间：2026-11-16 17:32:37

## run_judge 的结果

{
 "status": "FAIL",
 "summary": {
  "cases": {
   "dropped": 14,
   "migration-change": 7,
   "pass": 34
  },
  "fault_runs": {
   "dropped": 4,
   "fail": 3,
   "migration-change": 3,
   "pass": 21
  }
 },
 "mode": "rie",
 "backend": "ddblocal",
 "verdict": "verification/verdict_judge.json",
 "failures": [],
 "failures_total": 0,
 "seconds": 970.6,
 "run_dir": "D:\\tmp\\m2sv\\airline-m4\\judge\\20261116-171625-judge"
}

## 完整判定

verification/verdict_judge.json

## 规则

裁判已经冻结，分诊不能改裁判（judge/），也不能放宽它的比较。可以退回的任务：代码的问题给 I1，打包和模板的问题给 I2，蓝图和适配映射的问题给 A1；单体的这个行为其实是迁移带来的变化、需要裁定的，交给人。feedback 写失败的函数、事实库或规则的出处、期望和实际；裁判用例对实现线可见，可以直接引用。

## 任务定义

{
 "id": "V2",
 "runner": "dispatcher",
 "role": null,
 "action": "run_judge",
 "line": null,
 "deps": [
  "V1"
 ],
 "scope": [
  "verification/verdict_judge.json"
 ],
 "check": [],
 "on_stale": "recheck",
 "max_turns": 200,
 "timeout_min": 120.0,
 "mode": null,
 "deny_read": [],
 "start": "spawn",
 "split": "none",
 "expand_after": [],
 "split_shared": false,
 "shared_scope": [],
 "shared_check": []
}

## 这个任务的事件

- {"ts": "2026-11-16T17:16:24+0800", "type": "action_start", "action": "run_judge"}
- {"ts": "2026-11-16T17:32:36+0800", "type": "checks", "mode": "action", "results": [{"name": "run_judge", "ok": false, "summary": "run_judge FAIL: cases {'dropped': 14, 'migration-change': 7, 'pass': 34}, fault runs {'dropped': 4, 'fail': 3, 'migration-change': 3, 'pass': 21}"}]}
