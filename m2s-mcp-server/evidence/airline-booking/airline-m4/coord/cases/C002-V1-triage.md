# C002-V1-triage：check_adapter 读回的结果和单体不一致

- 任务：V1
- 类型：triage
- 时间：2026-11-14 02:53:38

## check_adapter 的结果

{
 "status": "FAIL",
 "cases": 13,
 "data_steps": 23,
 "failed": [
  "booking-read-guards",
  "customer-bookings-owner-only",
  "customer-bookings-status-filter",
  "loyalty-no-records",
  "loyalty-sum-and-level",
  "loyalty-tier-boundaries"
 ],
 "failures": {
  "booking-read-guards": {
   "owner": [
    {
     "path": "steps.owner.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.owner.body",
     "why": "keys differ",
     "missing": [
      "bookingOutboundFlightId",
      "bookingReference",
      "checkedIn",
      "createdAt",
      "customer",
      "id",
      "paymentToken",
      "status"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "admin": [
    {
     "path": "steps.admin.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.admin.body",
     "why": "keys differ",
     "missing": [
      "bookingOutboundFlightId",
      "bookingReference",
      "checkedIn",
      "createdAt",
      "customer",
      "id",
      "paymentToken",
      "status"
     ],
     "extra": [
      "error"
     ]
    }
   ]
  },
  "customer-bookings-owner-only": {
   "own": [
    {
     "path": "steps.own.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.own.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "other-own": [
    {
     "path": "steps.other-own.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.other-own.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "admin-asking": [
    {
     "path": "steps.admin-asking.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.admin-asking.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "empty-status": [
    {
     "path": "steps.empty-status.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.empty-status.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ]
  },
  "customer-bookings-status-filter": {
   "confirmed": [
    {
     "path": "steps.confirmed.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.confirmed.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "cancelled": [
    {
     "path": "steps.cancelled.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.cancelled.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "lowercase": [
    {
     "path": "steps.lowercase.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.lowercase.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "bogus": [
    {
     "path": "steps.bogus.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.bogus.body",
     "why": "keys differ",
     "missing": [
      "bookings"
     ],
     "extra": [
      "error"
     ]
    }
   ]
  },
  "loyalty-no-records": {
   "own": [
    {
     "path": "steps.own.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.own.body",
     "why": "keys differ",
     "missing": [
      "level",
      "points",
      "remainingPoints"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "stranger-id": [
    {
     "path": "steps.stranger-id.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.stranger-id.body",
     "why": "keys differ",
     "missing": [
      "level",
      "points",
      "remainingPoints"
     ],
     "extra": [
      "error"
     ]
    }
   ]
  },
  "loyalty-sum-and-level": {
   "own": [
    {
     "path": "steps.own.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.own.body",
     "why": "keys differ",
     "missing": [
      "level",
      "points",
      "remainingPoints"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "other-own": [
    {
     "path": "steps.other-own.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.other-own.body",
     "why": "keys differ",
     "missing": [
      "level",
      "points",
      "remainingPoints"
     ],
     "extra": [
      "error"
     ]
    }
   ]
  },
  "loyalty-tier-boundaries": {
   "silver-edge": [
    {
     "path": "steps.silver-edge.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.silver-edge.body",
     "why": "keys differ",
     "missing": [
      "level",
      "points",
      "remainingPoints"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "bronze-edge": [
    {
     "path": "steps.bronze-edge.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.bronze-edge.body",
     "why": "keys differ",
     "missing": [
      "level",
      "points",
      "remainingPoints"
     ],
     "extra": [
      "error"
     ]
    }
   ],
   "gold": [
    {
     "path": "steps.gold.status",
     "expected": 200,
     "actual": 403,
     "why": "differs"
    },
    {
     "path": "steps.gold.body",
     "why": "keys differ",
     "missing": [
      "level",
      "points",
      "remainingPoints"
     ],
     "extra": [
      "error"
     ]
    }
   ]
  }
 },
 
…（截断，原文 6071 字符）

## 怎么判断

映射写错了（表、键、属性、身份）退回 A1；读接口的代码不对退回 I1；分不清就交给人。裁判已经冻结，分诊不能改裁判（judge/），也不能放宽它的比较。可以退回的任务：代码的问题给 I1，打包和模板的问题给 I2，蓝图和适配映射的问题给 A1；单体的这个行为其实是迁移带来的变化、需要裁定的，交给人。feedback 写失败的函数、事实库或规则的出处、期望和实际；裁判用例对实现线可见，可以直接引用。

## 任务定义

{
 "id": "V1",
 "runner": "dispatcher",
 "role": null,
 "action": "check_adapter",
 "line": null,
 "deps": [
  "I3",
  "J6"
 ],
 "scope": [
  "verification/check_adapter.json"
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

- {"ts": "2026-11-14T02:52:51+0800", "type": "action_start", "action": "check_adapter"}
- {"ts": "2026-11-14T02:53:37+0800", "type": "checks", "mode": "action", "results": [{"name": "check_adapter", "ok": false, "summary": "check_adapter FAIL: 23 data steps in 13 cases, failed ['booking-read-guards', 'customer-bookings-owner-only', 'customer-bookings-status-filter', 'loyalty-no-records', 'loyalty-sum-and-level', 'loyalty-tier-boundaries']"}]}
