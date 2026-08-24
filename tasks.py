"""后台任务管理器 - 回填 / 导出共用

任务函数约定: def fn(*args, task=None, **kwargs)
- task 是一个 dict, 通过 TaskManager.report(task, current=.., total=.., msg=..) 更新进度
- 循环中检查 TaskManager.is_canceled(task) 实现优雅取消
- 任务异常时自动标记 error; 正常结束标记 done (pct=100)
"""
import logging
import threading
import uuid

logger = logging.getLogger(__name__)


class TaskManager:
    _lock = threading.Lock()
    _tasks = {}

    @classmethod
    def submit(cls, fn, *args, **kwargs):
        """提交后台任务, 返回 task_id"""
        tid = uuid.uuid4().hex[:12]
        entry = {
            "fn": fn, "args": args, "kwargs": kwargs,
            "status": "running", "pct": 0, "current": 0, "total": 0,
            "msg": "准备中", "cancel": False, "error": None,
        }
        with cls._lock:
            cls._tasks[tid] = entry
        threading.Thread(target=cls._run, args=(tid, entry), daemon=True).start()
        return tid

    @classmethod
    def _run(cls, tid, entry):
        try:
            entry["fn"](*entry["args"], task=entry, **entry["kwargs"])
        except Exception as e:
            logger.exception("task %s failed", tid)
            with cls._lock:
                if entry["status"] not in ("canceled",):
                    entry.update(status="error", error=str(e),
                                 msg=f"失败: {e}")
            return
        with cls._lock:
            if entry["status"] != "canceled":
                entry.update(status="done", pct=100)

    @classmethod
    def report(cls, task, current=None, total=None, msg=None, pct=None):
        """更新任务进度。task 可为 None（直接调用而非任务上下文时忽略）。"""
        if task is None:
            return
        if current is not None:
            task["current"] = current
        if total is not None:
            task["total"] = total
        if msg is not None:
            task["msg"] = msg
        if total and current is not None:
            task["pct"] = max(0, min(100, int(current * 100 / total)))
        elif pct is not None:
            task["pct"] = max(0, min(100, pct))

    @classmethod
    def progress(cls, tid):
        with cls._lock:
            t = cls._tasks.get(tid)
            if not t:
                return {"found": False, "running": False}
            return {
                "found": True,
                "running": t["status"] in ("running", "canceling"),
                "status": t["status"],
                "pct": t["pct"],
                "current": t["current"],
                "total": t["total"],
                "msg": t["msg"],
                "error": t.get("error"),
            }

    @classmethod
    def cancel(cls, tid):
        with cls._lock:
            t = cls._tasks.get(tid)
            if t and t["status"] == "running":
                t["cancel"] = True
                t["status"] = "canceling"
        return {"ok": True}

    @classmethod
    def is_canceled(cls, task):
        return bool(task and task.get("cancel", False))
