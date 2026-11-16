"""The br workflow policy every v3 task board uses (PIPELINE_V3_PLAN.md appendix B).

Only the dispatcher writes the board. Workers submit through m2s-server, and
done_check is reported by the dispatcher, so `checking -> closed` can only
happen after a tool verdict. `allow_bypass: false` must stay at the top level:
under `workflow` br ignores it (with a warning) and `br close --bypass-policy`
would skip the gate.
"""
from __future__ import annotations

STATUSES = ("open", "in_progress", "checking", "rework", "stale", "needs_human", "closed")
READY_STATUSES = ("open", "rework")
DONE_GATE = "done_check"
DISPATCHER_ACTOR = "m2s-dispatcher"

POLICY_YAML = """\
allow_bypass: false
workflow:
  strict: true
  statuses: [open, in_progress, checking, rework, stale, needs_human, closed]
  transitions:
    initial: [open]
    open: [in_progress, stale]
    in_progress: [checking, open]
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
    "needs_human -> closed": [transition_comment]
    "needs_human -> open": [transition_comment]
"""
