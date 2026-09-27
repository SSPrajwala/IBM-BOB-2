from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_task():
    resp = client.post("/tasks/", json={"title": "Write report", "owner": "asha"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Write report"


def test_list_tasks():
    resp = client.get("/tasks/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
