"""公开版硬性只读开关：访客筛选、URL参数和环境变量不能关闭它。"""
from pathlib import Path

_LOCKED = False


def activate():
    global _LOCKED
    _LOCKED = True


def enabled():
    # 发布包带标记，即使误选普通app.py也保持只读。
    return _LOCKED or (Path(__file__).resolve().parents[1] / '.public-demo').is_file()


def require_private(action):
    if enabled():
        raise ValueError('公开展示版禁止' + action)
