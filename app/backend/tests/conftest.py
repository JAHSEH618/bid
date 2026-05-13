"""Backend test fixtures bootstrap (PR-M7-3 副产物)。

为单元测试提供一组哑配置环境变量,让 ``bid_app.config.Settings`` 不在
``test_blackboard.py`` / ``test_redaction.py`` 等仅做磁盘 IO 的测试里
sys.exit(1)。集成测试(test_humanize_final 等)若需要真 PG,自己
通过 docker / .env 覆盖即可。
"""

from __future__ import annotations

import os

_FAKE_ENV = {
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test-pw",
    "POSTGRES_DB": "test",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "BID_APP_MASTER_KEY": "0" * 64,
    "JWT_SECRET": "0" * 64,
}

for key, value in _FAKE_ENV.items():
    os.environ.setdefault(key, value)
