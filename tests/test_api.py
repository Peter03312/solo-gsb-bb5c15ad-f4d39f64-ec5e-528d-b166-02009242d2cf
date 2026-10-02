"""HTTP API 集成测试（每次测试使用独立临时数据库）。"""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path):
    os.environ["DB_PATH"] = str(tmp_path / "test.db")
    from app.main import app
    with TestClient(app) as c:
        yield c


def plan_payload(**kw):
    payload = {
        "completed_courses": [],
        "target_courses": ["CS401"],
        "start_semester": "2026-SPRING",
        "max_credits_per_semester": 12,
        "num_semesters": 6,
    }
    payload.update(kw)
    return payload


def test_seed_catalog_loaded(client):
    r = client.get("/catalog/current")
    assert r.status_code == 200
    body = r.json()
    assert body["version_id"] == 1
    assert len(body["courses"]) >= 10
    codes = {c["code"] for c in body["courses"]}
    assert {"CS101", "CS301", "CS401"} <= codes


def test_create_plan_binds_current_version(client):
    r = client.post("/plans", json=plan_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "proposed"
    assert body["catalog_version"] == 1
    result = body["result"]
    assert result["completion_semester"] == "2027-FALL"
    sems = [[c["code"] for c in s["courses"]] for s in result["semesters"]]
    assert sems == [["CS101", "CS101L"], ["CS201", "MATH103"], ["CS301"], ["CS401"]]
    # 可回看
    r2 = client.get(f"/plans/{body['plan_id']}")
    assert r2.status_code == 200
    assert r2.json()["catalog_version"] == 1


def test_invalid_inputs_rejected(client):
    assert client.post("/plans", json=plan_payload(target_courses=[])).status_code == 422
    assert client.post("/plans", json=plan_payload(max_credits_per_semester=0)).status_code == 422
    assert client.post("/plans", json=plan_payload(num_semesters=-1)).status_code == 422
    r = client.post("/plans", json=plan_payload(start_semester="2026春"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_INPUT"


def test_missing_course(client):
    r = client.post("/plans", json=plan_payload(target_courses=["NOPE101"]))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "COURSE_MISSING"


def test_requirement_conflict(client):
    r = client.post("/plans", json=plan_payload(target_courses=["CS999"]))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "REQUIREMENT_CONFLICT"


def test_no_solution(client):
    r = client.post("/plans", json=plan_payload(num_semesters=2))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_SOLUTION"


def _revise_catalog(client, note="修订"):
    current = client.get("/catalog/current").json()
    courses = current["courses"]
    courses[0]["credits"] += 1  # 任意改动
    r = client.post("/catalog/versions", json={"note": note, "courses": courses})
    assert r.status_code == 201
    return r.json()["version_id"]


def test_confirm_flow_and_catalog_change_rejection(client):
    plan_a = client.post("/plans", json=plan_payload()).json()
    plan_b = client.post("/plans", json=plan_payload(target_courses=["CS303"])).json()

    # 目录未变：确认成功并写入
    r = client.post(f"/plans/{plan_a['plan_id']}/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"

    # 修订目录 -> 新版本
    new_vid = _revise_catalog(client)
    assert new_vid == 2

    # 旧版本方案确认被拒绝，且不写入（状态保持 proposed）
    r = client.post(f"/plans/{plan_b['plan_id']}/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CATALOG_CHANGED"
    assert client.get(f"/plans/{plan_b['plan_id']}").json()["status"] == "proposed"

    # 新规划绑定新版本，可正常确认
    plan_c = client.post("/plans", json=plan_payload()).json()
    assert plan_c["catalog_version"] == 2
    r = client.post(f"/plans/{plan_c['plan_id']}/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"


def test_catalog_version_review(client):
    _revise_catalog(client)
    r = client.get("/catalog/versions/1")
    assert r.status_code == 200
    assert r.json()["version_id"] == 1
    assert client.get("/catalog/versions/999").status_code == 404


def test_invalid_catalog_rejected(client):
    bad = {"courses": [{"code": "X", "credits": 0, "semesters": ["SPRING"]}]}
    assert client.post("/catalog/versions", json=bad).status_code == 422
    dup = {"courses": [
        {"code": "X", "credits": 1, "semesters": ["SPRING"]},
        {"code": "X", "credits": 2, "semesters": ["FALL"]},
    ]}
    assert client.post("/catalog/versions", json=dup).status_code == 422


def test_plan_not_found(client):
    assert client.get("/plans/unknown").status_code == 404
    assert client.post("/plans/unknown/confirm").status_code == 404
