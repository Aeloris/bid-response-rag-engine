# -*- coding: utf-8 -*-
"""pytest 共享夹具。

- 确保仓库根目录在 sys.path（配合 pyproject 的 pythonpath 双保险）；
- settings 夹具：读取默认配置（config.yaml，provider=mock → 测试全程离线、确定性）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import get_settings  # noqa: E402


@pytest.fixture()
def settings():
    """进程共享配置对象（默认 provider=mock）。"""
    return get_settings()
