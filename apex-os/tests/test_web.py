from apex.models import InboxItem, LearningSession, PlanItem


def test_requires_auth(db):
    from fastapi.testclient import TestClient

    from apex.main import create_app
    c = TestClient(create_app(start_scheduler=False))
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert c.get("/api/v1/status").status_code == 401


def test_bad_login_and_csrf(client):
    r = client.post("/plan", data={"title": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/login", data={"username": "owner", "password": "wrong password!"})
    assert "Invalid credentials" in r.text


def test_logging_and_pages(client, db):
    r = client.post("/log/german_session", data={"day": "2026-10-01", "minutes": "45", "skill": "speaking",
                                                  "mode": "active", "focus": "4", "notes": "private"})
    assert r.status_code == 200 and "Saved" in r.text
    s = db.query(LearningSession).one()
    assert s.language.skill == "speaking" and s.notes == "private"
    r = client.post("/log/sleep", data={"day": "2026-10-02", "bed_time": "23:30", "wake_time": "07:00"})
    assert r.status_code == 200
    r = client.post("/log/questions", data={"domain": "law", "area": "contract", "day": "2026-10-01",
                                             "total": "5", "correct": "9"})
    assert "cannot exceed" in r.text
    for p in ["/", "/brief", "/plan", "/inbox", "/actions", "/review", "/review/weekly", "/review/monthly",
              "/goals", "/german", "/law", "/career", "/experiments", "/radar", "/memory", "/settings",
              "/domain/attention", "/settings/export"]:
        assert client.get(p).status_code == 200, p


def test_demo_dashboard_and_accept_flow(client, db):
    from apex import demo
    from apex.web import today
    demo.seed(db, today())
    r = client.get("/")
    assert "TODAY'S #1 PRIORITY" in r.text and "WHY?" in r.text
    assert db.query(InboxItem).count() > 0
    from apex.models import Recommendation
    rec = db.query(Recommendation).filter_by(status="proposed").first()
    client.post(f"/recommendations/{rec.id}/accept")
    db.expire_all()
    assert db.query(PlanItem).filter_by(recommendation_id=rec.id).count() == 1
    client.get("/")  # re-plan must keep accepted recommendation
    db.expire_all()
    assert db.query(Recommendation).filter_by(id=rec.id).first().status == "accepted"


def test_api_log_and_csv_import(client, db):
    r = client.post("/api/v1/log/movement", json={"day": "2026-10-01", "steps": 9000})
    assert r.status_code == 201
    r = client.post("/api/v1/log/movement", json={"day": "bad", "steps": 1})
    assert r.status_code == 422
    csv = "day,total_min,phone_min\n2026-09-01,300,120\n2026-09-02,oops,1\n"
    r = client.post("/settings/import", data={"kind": "screen_time", "text": csv})
    assert "Imported+1" in str(r.url) or "Imported 1" in r.text


def test_wipe_requires_confirmation(client, db):
    client.post("/api/v1/log/movement", json={"day": "2026-10-01", "steps": 9000})
    client.post("/settings/wipe", data={"confirm": "yes"})
    from apex.models import Movement
    db.expire_all()
    assert db.query(Movement).count() == 1
    client.post("/settings/wipe", data={"confirm": "DELETE ALL MY DATA"})
    db.expire_all()
    assert db.query(Movement).count() == 0
