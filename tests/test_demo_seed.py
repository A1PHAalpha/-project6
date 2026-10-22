from collector import db
from test_cases.seed_demo import seed_demo
from webapp.api.v1.router import _session_detail_dto


def test_seed_demo_creates_exportable_session(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "original.db"))
    result = seed_demo(db_path=str(tmp_path / "demo.db"), reset=True)
    session = db.get_agent_session(result["session_id"])
    assert session is not None
    dto = _session_detail_dto(result["session_id"])
    assert dto["suggestion"]["canExecute"] is True
    assert dto["suggestion"]["targetDevice"] == "BR-A-R2"
    assert dto["evidence"]
