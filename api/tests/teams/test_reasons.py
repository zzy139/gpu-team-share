"""Chinese pending reasons and per-type capacity status (有空闲 / 需启动约5分钟 / 暂无容量 / 全部在用)."""
import pytest

from api.teams import reasons
from api.teams.reasons import capacity_status, explain_pending, pending_text
from api.teams.slurm import parse_sinfo, parse_sinfo_ice

ICE = "(Code:InsufficientInstanceCapacity)Temporarily disabling node due to insufficient capacity"


@pytest.mark.parametrize("states,ice,expected", [
    ({"idle": 1, "allocated": 2}, False, "available"),
    ({"idle": 1, "down~": 3}, True, "available"),       # a powered-up idle node wins
    ({"idle~": 8}, False, "startable"),
    ({"idle~": 6, "down~": 2}, True, "no_capacity"),    # EC2 reported insufficient capacity
    ({"down~": 8}, True, "no_capacity"),
    ({"down~": 8}, None, "no_capacity"),                # no reason column: node-state heuristic
    ({"allocated": 2, "idle#": 1}, False, "busy"),      # all in use or starting for other jobs
    ({"mixed": 1}, False, "busy"),
    ({}, False, None),
    (None, None, None),
])
def test_capacity_status(states, ice, expected):
    assert capacity_status(states, ice) == expected
    if expected:
        assert reasons.STATUS_LABELS[expected]


def test_status_labels_are_the_form_texts():
    assert reasons.STATUS_LABELS == {"available": "有空闲", "startable": "需启动约5分钟", "no_capacity": "暂无容量",
                                     "busy": "全部在用，需排队"}


def _context(sinfo_lines):
    return {"types": {"g6e-2xlarge": "g6e.2xlarge", "g6-xlarge": "g6.xlarge", "g6e-xlarge": "g6e.xlarge"},
            "limits": {"g6e-2xlarge": 1, "g6-xlarge": 2, "g6e-xlarge": 1},
            "running": {"g6e-2xlarge": 0, "g6-xlarge": 1, "g6e-xlarge": 0},
            "nodes": parse_sinfo(sinfo_lines), "ice": parse_sinfo_ice(sinfo_lines)}


def test_job_26_sequence_configuring_then_pending_without_reason_under_ice():
    """Job 26 (team test, g6e.2xlarge): 节点启动中 -> 排队中 with reason None while EC2 has no capacity; the user
    saw "-" as 排队原因. Now every step has a Chinese explanation."""
    starting = _context(["g6e-2xlarge|mixed#|1|none", "g6e-2xlarge|idle~|7|none", "g6-xlarge|idle~|4|none",
                         "g6e-xlarge|idle~|8|none"])
    assert explain_pending("None", "g6e-2xlarge", "test-g6e-2xlarge", starting) == "正在等待机器启动（约 5 分钟）"
    failed = _context([f"g6e-2xlarge|down~|8|{ICE}", "g6-xlarge|idle~|4|none", f"g6e-xlarge|down~|8|{ICE}"])
    for reason in (None, "None", "", "BeginTime", "ReqNodeNotAvail,_UnavailableNodes:g6e-2xlarge-dy-1",
                   "Nodes_required_for_job_are_DOWN,_DRAINED_or_reserved_for_jobs_in_higher_priority_partitions"):
        text = explain_pending(reason, "g6e-2xlarge", "test-g6e-2xlarge", failed)
        assert text.startswith("g6e.2xlarge 暂无容量：AWS 目前没有这种机器可用，系统会自动重试"), reason
        assert "等待期间不产生费用" in text
        # alternatives: quota left and capacity (g6e.xlarge has quota but no capacity, so it is not offered)
        assert text.endswith("也可以取消后改用 g6.xlarge（剩余 1 台，需启动约5分钟）"), reason
    # requeued after the failed launch but the ICE window is over: the requeue explanation
    retry = _context(["g6e-2xlarge|idle~|8|none"])
    assert explain_pending("BeginTime", "g6e-2xlarge", "q", retry) == pending_text("BeginTime")
    assert "自动重新排队" in pending_text("BeginTime")


def test_pending_without_context_is_never_empty():
    assert pending_text(None) == pending_text("None") == "正在等待调度，机器启动约需 5 分钟"
    assert explain_pending(None, None, None, None) == pending_text(None)
    assert explain_pending("Priority", "g6e-2xlarge", "q", _context([f"g6e-2xlarge|down~|8|{ICE}"])) == \
        "前面有优先级更高的作业在排队"


def test_borrowed_job_is_not_told_about_team_quota():
    context = _context(["g6e-2xlarge|allocated|1|none"])
    assert explain_pending("QOSGrpNodeLimit", "g6e-2xlarge", "borrow", context) == pending_text("QOSGrpNodeLimit")


def test_alternatives_need_quota_and_capacity():
    context = _context(["g6e-2xlarge|down~|8|" + ICE, "g6-xlarge|idle|1|none", "g6e-xlarge|idle~|8|none"])
    assert reasons.alternatives(context, "g6e-2xlarge") == [
        {"instance_type": "g6.xlarge", "partition": "g6-xlarge", "remaining": 1, "status": "available"},
        {"instance_type": "g6e.xlarge", "partition": "g6e-xlarge", "remaining": 1, "status": "startable"}]
    full = {**context, "running": {"g6-xlarge": 2, "g6e-xlarge": 1}}
    assert reasons.alternatives(full, "g6e-2xlarge") == []
