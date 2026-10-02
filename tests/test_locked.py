"""锁定安排与不可排课学期：规划引擎单元测试 + HTTP 集成测试。"""
import os

import pytest
from fastapi.testclient import TestClient

from app.planner import Course, PlanError, build_plan


def C(code, credits, seasons=("SPRING", "FALL"), groups=(), coreqs=()):
    return Course(code, code, credits, frozenset(seasons),
                  tuple(tuple(g) for g in groups), tuple(coreqs))


def cat(*courses):
    return {c.code: c for c in courses}


def codes(result):
    return [[c["code"] for c in s["courses"]] for s in result["semesters"]]


# ---------------------------------------------------------------- 引擎：锁定

def test_locked_course_fixed_at_locked_semester():
    catalog = cat(C("A", 3), C("B", 3))
    r = build_plan(catalog, set(), ["A"], "2026-SPRING", 3, 4,
                   locked={"B": "2026-FALL"})
    # B 不在目标中但被锁定 -> 本次必修，且固定在 2026-FALL
    assert codes(r) == [["A"], ["B"]]
    assert r["completion_semester"] == "2026-FALL"


def test_locked_target_course_stays_at_locked_semester():
    catalog = cat(C("A", 3), C("B", 3))
    r = build_plan(catalog, set(), ["A", "B"], "2026-SPRING", 3, 4,
                   locked={"B": "2027-SPRING"})
    # 字典序规则下空学期靠前，A 被推迟到第 2 学期，B 固定第 3 学期
    assert codes(r) == [[], ["A"], ["B"]]
    assert r["completion_semester"] == "2027-SPRING"


def test_all_targets_completed_but_locked_still_scheduled():
    catalog = cat(C("A", 3), C("B", 3))
    r = build_plan(catalog, {"A"}, ["A"], "2026-SPRING", 10, 4,
                   locked={"B": "2026-SPRING"})
    assert codes(r) == [["B"]]


def test_locked_completed_course_rejected():
    catalog = cat(C("A", 3))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, {"A"}, ["A"], "2026-SPRING", 10, 4,
                   locked={"A": "2026-SPRING"})
    assert e.value.code == "INVALID_INPUT"
    assert "A" in e.value.message


def test_locked_missing_course():
    catalog = cat(C("A", 3))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   locked={"XXX": "2026-SPRING"})
    assert e.value.code == "COURSE_MISSING"
    assert "XXX" in e.value.message


def test_locked_semester_out_of_horizon():
    catalog = cat(C("A", 3), C("B", 3))
    with pytest.raises(PlanError) as e:  # 超出期限
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 2,
                   locked={"B": "2027-SPRING"})
    assert e.value.code == "INVALID_INPUT"
    with pytest.raises(PlanError) as e:  # 早于起始学期
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   locked={"B": "2025-FALL"})
    assert e.value.code == "INVALID_INPUT"
    with pytest.raises(PlanError) as e:  # 学期格式非法
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   locked={"B": "2026春"})
    assert e.value.code == "INVALID_INPUT"


def test_locked_season_conflict():
    catalog = cat(C("A", 3, seasons=("FALL",)))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   locked={"A": "2026-SPRING"})
    assert e.value.code == "REQUIREMENT_CONFLICT"
    assert "A" in e.value.message


def test_locked_prereq_must_finish_earlier():
    catalog = cat(C("A", 3), C("B", 3, groups=(("A",),)))
    r = build_plan(catalog, set(), ["B"], "2026-SPRING", 10, 4,
                   locked={"B": "2027-SPRING"})
    assert codes(r) == [[], ["A"], ["B"]]
    # 锁定第 1 学期，先修 A 来不及 -> 锁定与约束冲突
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["B"], "2026-SPRING", 10, 4,
                   locked={"B": "2026-SPRING"})
    assert e.value.code == "REQUIREMENT_CONFLICT"


def test_locked_coreq_block_moves_together():
    catalog = cat(C("A", 3, coreqs=("AL",)), C("AL", 1))
    r = build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   locked={"A": "2026-FALL"})
    # 同修课 AL 未修过，与 A 一起固定在锁定学期
    assert codes(r) == [[], ["A", "AL"]]


def test_coreq_members_locked_to_different_semesters_conflict():
    catalog = cat(C("A", 3, coreqs=("B",)), C("B", 3))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   locked={"A": "2026-SPRING", "B": "2026-FALL"})
    assert e.value.code == "REQUIREMENT_CONFLICT"


def test_lexicographic_tiebreak_still_applies_with_lock():
    catalog = cat(C("A", 3), C("B", 3), C("C", 3))
    r = build_plan(catalog, set(), ["A", "B"], "2026-SPRING", 3, 4,
                   locked={"C": "2027-SPRING"})
    # 每学期限 3 学分 -> 一学期一门；A、B 按字典序先后，C 固定第 3 学期
    assert codes(r) == [["A"], ["B"], ["C"]]


# ---------------------------------------------------------------- 引擎：不可排课学期

def test_unavailable_semester_gets_no_courses():
    catalog = cat(C("A", 3), C("B", 3))
    r = build_plan(catalog, set(), ["A", "B"], "2026-SPRING", 3, 4,
                   unavailable=["2026-SPRING"])
    assert codes(r) == [[], ["A"], ["B"]]
    assert r["completion_semester"] == "2027-SPRING"


def test_unavailable_semester_out_of_horizon():
    catalog = cat(C("A", 3))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 2,
                   unavailable=["2027-SPRING"])
    assert e.value.code == "INVALID_INPUT"
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   unavailable=["2026春"])
    assert e.value.code == "INVALID_INPUT"


def test_locked_semester_marked_unavailable_conflicts():
    catalog = cat(C("A", 3))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4,
                   locked={"A": "2026-FALL"}, unavailable=["2026-FALL"])
    assert e.value.code == "REQUIREMENT_CONFLICT"


def test_locked_and_unavailable_leave_no_room_is_no_solution():
    catalog = cat(C("A", 3), C("B", 3))
    # 第 1 学期不可排课，A 锁定第 2 学期，B 只能同学期 -> 超出学分上限
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A", "B"], "2026-SPRING", 3, 2,
                   locked={"A": "2026-FALL"}, unavailable=["2026-SPRING"])
    assert e.value.code == "NO_SOLUTION"


# ---------------------------------------------------------------- HTTP API

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


def test_create_plan_with_locked_and_unavailable(client):
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS201", "semester": "2026-FALL"}],
        unavailable_semesters=["2027-SPRING"],
    ))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "proposed"
    assert body["catalog_version"] == 1
    # 请求回显包含新字段
    assert body["request"]["locked"] == [{"code": "CS201", "semester": "2026-FALL"}]
    assert body["request"]["unavailable_semesters"] == ["2027-SPRING"]
    result = body["result"]
    sems = [[c["code"] for c in s["courses"]] for s in result["semesters"]]
    # CS201 固定在 2026-FALL；2027-SPRING 不可排课；CS401 仅秋季 -> 2028-FALL 完成
    assert sems == [["CS101", "CS101L"], ["CS201"], [], ["MATH103"], ["CS301"], ["CS401"]]
    assert result["semesters"][2]["semester"] == "2027-SPRING"
    assert result["semesters"][2]["courses"] == []
    assert result["completion_semester"] == "2028-FALL"
    # 可回看
    r2 = client.get(f"/plans/{body['plan_id']}")
    assert r2.status_code == 200
    assert r2.json()["request"]["locked"] == [{"code": "CS201", "semester": "2026-FALL"}]


def test_locked_course_not_in_targets_is_required(client):
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS303", "semester": "2027-SPRING"}],
    ))
    assert r.status_code == 201, r.text
    sems = [[c["code"] for c in s["courses"]] for s in r.json()["result"]["semesters"]]
    # CS303 非目标课程，因锁定而成为必修并固定在 2027-SPRING
    assert sems == [["CS101", "CS101L"], ["CS201", "MATH103"], ["CS301", "CS303"], ["CS401"]]


def test_create_plan_without_new_fields_unchanged(client):
    r = client.post("/plans", json=plan_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    # 既有请求格式：不引入新字段；结果与既有行为一致
    assert "locked" not in body["request"]
    assert "unavailable_semesters" not in body["request"]
    sems = [[c["code"] for c in s["courses"]] for s in body["result"]["semesters"]]
    assert sems == [["CS101", "CS101L"], ["CS201", "MATH103"], ["CS301"], ["CS401"]]
    assert body["result"]["completion_semester"] == "2027-FALL"


def test_duplicate_lock_rejected(client):
    r = client.post("/plans", json=plan_payload(locked=[
        {"code": "CS201", "semester": "2026-FALL"},
        {"code": "CS201", "semester": "2027-SPRING"},
    ]))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_INPUT"


def test_locked_completed_course_rejected(client):
    r = client.post("/plans", json=plan_payload(
        completed_courses=["CS101", "CS101L"],
        locked=[{"code": "CS101", "semester": "2026-FALL"}],
    ))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_INPUT"


def test_locked_semester_out_of_horizon_rejected(client):
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS201", "semester": "2029-SPRING"}],
    ))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_INPUT"
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS201", "semester": "2025-FALL"}],
    ))
    assert r.status_code == 400


def test_locked_semester_bad_format_rejected(client):
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS201", "semester": "2026秋"}],
    ))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_INPUT"


def test_locked_missing_course_rejected(client):
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "NOPE101", "semester": "2026-FALL"}],
    ))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "COURSE_MISSING"


def test_locked_season_conflict_rejected(client):
    # CS401 仅秋季开课，锁定到春季学期
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS401", "semester": "2027-SPRING"}],
    ))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "REQUIREMENT_CONFLICT"


def test_locked_semester_unavailable_conflicts(client):
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS201", "semester": "2026-FALL"}],
        unavailable_semesters=["2026-FALL"],
    ))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "REQUIREMENT_CONFLICT"


def test_locked_prereq_cannot_finish_earlier_conflicts(client):
    # CS201 先修 CS101，锁定到第 1 学期 -> 先修来不及
    r = client.post("/plans", json=plan_payload(
        locked=[{"code": "CS201", "semester": "2026-SPRING"}],
    ))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "REQUIREMENT_CONFLICT"


def test_unavailable_semester_out_of_horizon_rejected(client):
    r = client.post("/plans", json=plan_payload(unavailable_semesters=["2029-SPRING"]))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_INPUT"


def test_no_solution_with_unavailable_and_saves_nothing(client):
    before = len(client.get("/plans").json())
    # 6 学期中 4 个学期不可排课，CS401 先修链无法在剩余 2 个学期内完成
    r = client.post("/plans", json=plan_payload(
        unavailable_semesters=["2026-FALL", "2027-SPRING", "2027-FALL", "2028-SPRING"],
    ))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_SOLUTION"
    assert len(client.get("/plans").json()) == before  # 不保存部分方案
