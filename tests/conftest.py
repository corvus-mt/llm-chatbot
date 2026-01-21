import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest

from app.routes import api as api_module


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    db_path = tmp_path / "chat_state.db"
    monkeypatch.setattr(api_module, "STATE_DB_PATH", db_path)
    api_module._ensure_state_db()
    api_module._reset_state()
    return db_path
