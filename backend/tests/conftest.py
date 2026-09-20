"""测试环境：在导入 app.* 之前切换到临时文件 SQLite，无需真实 Postgres。

可重复执行（本地或容器内）：pytest -q
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.gettempdir(), "liftbay_pytest.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB}")
os.environ.setdefault("SEED_ON_EMPTY", "false")
os.environ.setdefault("READY_PROBE_TIMEOUT", "2")
