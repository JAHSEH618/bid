"""FastAPI dependencies — M0 占位。

M1 (#6 / D-EC) 实现最小版:`get_db`(§14.5)+ `get_current_user` dev/test stub
读 ``settings.bid_app_dev_user_id`` 或回退到 users 表第一个 admin 行。
M2 (#19 / D-DY) 完整版替换:JWT cookie / must_change_password / require_admin。
"""
from __future__ import annotations
