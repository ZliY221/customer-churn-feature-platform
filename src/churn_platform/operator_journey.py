"""Guide retention operators through safe, low-friction customer journeys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetentionOperatorJourney:
    journey_id: str
    customer_key: str
    consent_granted: bool
    contact_conflict: bool
    channel_available: bool
    task_overdue: bool
    outcome_matured: bool


def rehearse_retention_journeys(
    journeys: list[RetentionOperatorJourney],
) -> dict[str, Any]:
    """Assess exactly 100 operator journeys and provide a safe next action."""
    if len(journeys) != 100:
        raise ValueError("retention usability rehearsal requires exactly 100 journeys")
    identifiers = [item.journey_id.strip() for item in journeys]
    if any(not value for value in identifiers) or len(identifiers) != len(set(identifiers)):
        raise ValueError("journey ids must be non-empty and unique")

    results = []
    for item in journeys:
        if (item.journey_id != item.journey_id.strip()
                or not item.customer_key.strip()
                or item.customer_key != item.customer_key.strip()):
            raise ValueError("retention operator journey is invalid")
        if not item.consent_granted:
            status, action = "PROTECTED", "RECORD_CONSENT_RESTRICTION"
            steps = ["停止本次触达", "登记授权限制", "保留客户审计记录"]
            message = "客户未授权触达，系统优先保护联系边界。"
        elif item.contact_conflict:
            status, action = "GUIDED", "RESCHEDULE_CONTACT"
            steps = ["查看冲突活动", "保留高优先级安排", "顺延当前触达"]
            message = "发现联系冷却窗口冲突，先调整排期以减少重复打扰。"
        elif not item.channel_available:
            status, action = "GUIDED", "ASSIGN_MANUAL_REVIEW"
            steps = ["核对客户渠道偏好", "转交人工复核", "等待可用渠道"]
            message = "当前没有合规可用渠道，转入人工复核而不是强制发送。"
        elif item.task_overdue:
            status, action = "PRIORITY", "PRIORITIZE_RETENTION_TASK"
            steps = ["提升任务优先级", "确认当前负责人", "记录本次联系结果"]
            message = "任务已超时，工作台应将其置顶并缩短查找路径。"
        elif not item.outcome_matured:
            status, action = "MONITOR", "WAIT_FOR_OUTCOME_WINDOW"
            steps = ["保留处置结果", "显示成熟日期", "到期后复核留存结果"]
            message = "结果观察窗口尚未成熟，不提前将其计入效果指标。"
        else:
            status, action = "READY", "REVIEW_EFFECTIVENESS"
            steps = ["核对处置结果", "查看成熟留存指标", "完成策略复盘"]
            message = "当前旅程资料完整，可进入效果复核。"
        results.append({
            "journey_id": item.journey_id,
            "customer_key": item.customer_key,
            "status": status,
            "primary_action": action,
            "steps": steps,
            "message": message,
            "expected_steps": len(steps),
            "requires_confirmation": action in {
                "RESCHEDULE_CONTACT", "ASSIGN_MANUAL_REVIEW", "PRIORITIZE_RETENTION_TASK"
            },
        })

    action_counts: dict[str, int] = {}
    for row in results:
        action_counts[row["primary_action"]] = action_counts.get(row["primary_action"], 0) + 1
    return {
        "status": "USABILITY_REHEARSAL_PASSED",
        "journey_count": len(results),
        "ready_count": sum(row["status"] == "READY" for row in results),
        "protected_count": sum(row["status"] == "PROTECTED" for row in results),
        "guided_count": sum(row["status"] not in {"READY", "PROTECTED"} for row in results),
        "unresolved_count": sum(not row["steps"] or not row["message"] for row in results),
        "average_expected_steps": round(
            sum(row["expected_steps"] for row in results) / len(results), 2
        ),
        "action_counts": dict(sorted(action_counts.items())),
        "journeys": results,
        "automatic_contact_started": False,
    }
