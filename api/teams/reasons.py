# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Plain-language (Chinese) explanations of Slurm pending reasons and per-type capacity status for business users.

``reason_text`` maps a reason code to a sentence. ``explain_pending`` adds context when the team quota and the
node states are known: which instance type is full, which other type still has machines left, whether EC2 has no
capacity for the type right now (node reason ``InsufficientInstanceCapacity``) and whether machines are starting.
``capacity_status`` labels an instance type for the submit form: ``available`` (有空闲), ``startable``
(需启动约5分钟), ``no_capacity`` (暂无容量) or ``busy`` (全部在用，需排队).
"""
import re

AVAILABLE, STARTABLE, NO_CAPACITY, BUSY = "available", "startable", "no_capacity", "busy"
STATUS_LABELS = {AVAILABLE: "有空闲", STARTABLE: "需启动约5分钟", NO_CAPACITY: "暂无容量", BUSY: "全部在用，需排队"}

QUOTA_FULL = "团队在这个机型上的配额已用满；有机器空出来后作业会自动开始，也可以开启借用"
NOT_AVAILABLE = "作业需要的机器暂时不可用"
WAITING = "正在等待调度，机器启动约需 5 分钟"
STARTING = "正在等待机器启动（约 5 分钟）"
REQUEUED = "上次机器启动没有成功，作业已自动重新排队，系统会自动重试；等待期间不产生费用"
REASONS = {
    "QOSGrpNodeLimit": QUOTA_FULL,
    "QOSGrpNode": QUOTA_FULL,
    "QOSGrpGRES": QUOTA_FULL,
    "QOSGrpCpuLimit": QUOTA_FULL,
    "QOSMaxJobsPerUserLimit": "您在这个队列运行的作业数已达上限",
    "QOSMaxSubmitJobPerUserLimit": "您在这个队列提交的作业数已达上限",
    "AssocMaxSubmitJobLimit": "您的账号暂时不能提交新作业，请联系管理员",
    "Priority": "前面有优先级更高的作业在排队",
    "Resources": "正在等待空闲机器（机器正在启动或全部在用）",
    "QOSMaxWallDurationPerJobLimit": "运行时间上限超过了允许的最大值（借用作业最长 4 小时）",
    "ReqNodeNotAvail": NOT_AVAILABLE,
    "NodeDown": NOT_AVAILABLE,
    "BeginTime": REQUEUED,
    "Dependency": "正在等待它依赖的作业结束",
    "DependencyNeverSatisfied": "它依赖的作业失败了，这个作业不会运行；请取消后重新提交",
    "JobHeldUser": "已被您暂停",
    "JobHeldAdmin": "已被管理员暂停",
    "PartitionTimeLimit": "运行时间上限超过了队列允许的最大值",
    "PartitionNodeLimit": "申请的机器数超过了队列允许的数量",
    "InvalidQOS": "账号或 QOS 无效，请联系管理员",
    "InvalidAccount": "账号或 QOS 无效，请联系管理员",
    "Configuring": "机器正在启动，作业马上开始",
    # Slurm 25 prints this text instead of ReqNodeNotAvail, e.g. while EC2 reports InsufficientInstanceCapacity.
    "Nodes required for job are DOWN, DRAINED or reserved for jobs in higher priority partitions": NOT_AVAILABLE,
    "None": "",
}
QUOTA_REASONS = frozenset({"QOSGrpNodeLimit", "QOSGrpNode", "QOSGrpGRES", "QOSGrpCpuLimit"})
CAPACITY_REASONS = frozenset({"ReqNodeNotAvail", "NodeDown", "Resources",
                              "Nodes required for job are DOWN, DRAINED or reserved for jobs in higher priority "
                              "partitions"})
# No reason yet (just submitted, or back to PENDING after a failed node launch) or requeued (BeginTime)
WAITING_REASONS = frozenset({"", "None", "BeginTime", "Configuring"})
# sinfo states after a failed EC2 launch: powered down with a reason (``down~``) or powering down (``idle%``)
NO_CAPACITY_STATES = frozenset({"down~", "idle%", "down%", "down", "down*"})
LAUNCHABLE_STATES = frozenset({"idle", "idle~", "idle#"})


def _base(code):
    code = (code or "").strip()
    if code in REASONS:
        return code
    # e.g. "ReqNodeNotAvail,_UnavailableNodes:g6-dy-..." or "(Resources)"
    return re.split(r"[,(:\s]", code.strip("()"), maxsplit=1)[0]


def _normalize(code):
    """``scontrol show job`` / sacct print multi-word reasons with '_' for ' ' (Nodes_required_for_job_are_DOWN,_...)."""
    code = (code or "").strip()
    return code.replace("_", " ") if " " not in code and "_" in code and code.replace("_", " ") in REASONS else code


def reason_text(code):
    code = _normalize(code)
    if not code or code == "None":
        return ""
    if code in REASONS:
        return REASONS[code]
    base = _base(code)
    if base in REASONS:
        return REASONS[base]
    return f"其他原因：{code}"


def pending_text(code):
    """Sentence for a PENDING job without context: never empty (no reason yet = waiting for machines)."""
    return reason_text(code) or WAITING


def _no_capacity(states):
    """True when launches failed in the partition (nodes down~/idle%) and no node could start the job now."""
    if not states:
        return False
    failed = any(n and s.lower() in NO_CAPACITY_STATES for s, n in states.items())
    launchable = any(n and s.lower() in LAUNCHABLE_STATES for s, n in states.items())
    return failed and not launchable


def capacity_status(states, ice=None):
    """Status of one instance type (= partition) from its sinfo node states {state: count} and the ICE flag
    (a node reason contains InsufficientInstanceCapacity; None = reason not known).

    ``available``: a powered-up idle node; ``no_capacity``: EC2 has no capacity; ``startable``: powered-down
    nodes can be started (about 5 minutes); ``busy``: every node is allocated or starting. None without states.
    """
    if not states:
        return None
    counts = {s.lower(): n for s, n in states.items() if n}
    if counts.get("idle"):
        return AVAILABLE
    if ice or (ice is None and _no_capacity(states)):
        return NO_CAPACITY
    if counts.get("idle~"):
        return STARTABLE
    return BUSY


def alternatives(context, partition, limit=3):
    """Other instance types of the team with quota remaining whose status is available or startable:
    [{instance_type, partition, remaining, status}] sorted by status then instance type."""
    types = context.get("types") or {}
    limits, running = context.get("limits") or {}, context.get("running") or {}
    nodes, ice = context.get("nodes") or {}, context.get("ice") or {}
    result = []
    for other, other_limit in limits.items():
        remaining = other_limit - running.get(other, 0)
        if other == partition or remaining <= 0 or other not in types:
            continue
        status = capacity_status(nodes.get(other), ice.get(other) if other in ice else None)
        if status in (AVAILABLE, STARTABLE):
            result.append({"instance_type": types[other], "partition": other, "remaining": remaining,
                           "status": status})
    result.sort(key=lambda a: (a["status"] != AVAILABLE, a["instance_type"]))
    return result[:limit]


def format_alternatives(items):
    return "或".join(f"{a['instance_type']}（剩余 {a['remaining']} 台，{STATUS_LABELS[a['status']]}）" for a in items)


def no_capacity_text(itype):
    return (f"{itype} 暂无容量：AWS 目前没有这种机器可用，系统会自动重试，有容量时作业会自动开始；"
            "等待期间不产生费用")


def explain_pending(reason, partition, qos, context):
    """Contextual Chinese sentence for a pending job.

    ``context`` = {"types": {partition: instance_type}, "limits": {partition: machines},
    "running": {partition: machines}, "nodes": {partition: {state: count}}, "ice": {partition: bool}} for the
    job's team, or None.
    """
    reason = _normalize(reason)
    plain = pending_text(reason)
    if not context or not partition:
        return plain
    base = _base(reason) if reason else ""
    types = context.get("types") or {}
    itype = types.get(partition)
    if itype is None:
        return plain
    if base in QUOTA_REASONS and qos != "borrow":
        limits, running = context.get("limits") or {}, context.get("running") or {}
        options = []
        for other, other_limit in sorted(limits.items(), key=lambda item: types.get(item[0], item[0])):
            remaining = other_limit - running.get(other, 0)
            if other != partition and remaining > 0 and other in types:
                options.append(f"{types[other]}（剩余 {remaining} 台）")
        text = f"{itype} 团队配额已用满（{running.get(partition, 0)}/{limits.get(partition, 0)} 台）。"
        if options:
            return f"{text}可改用 {'或'.join(options[:2])}，或开启借用"
        return f"{text}请等待正在运行的作业结束，或开启借用"
    if base not in CAPACITY_REASONS and base not in WAITING_REASONS:
        return plain
    ice = context.get("ice") or {}
    states = (context.get("nodes") or {}).get(partition)
    status = capacity_status(states, ice.get(partition) if partition in ice else None)
    if status == NO_CAPACITY:
        text = no_capacity_text(itype)
        others = alternatives(context, partition, limit=2)
        if others:
            text += f"。也可以取消后改用 {format_alternatives(others)}"
        return text
    if base in ("", "None", "Configuring"):
        return STARTING if status in (BUSY, STARTABLE, None) else WAITING
    return plain
