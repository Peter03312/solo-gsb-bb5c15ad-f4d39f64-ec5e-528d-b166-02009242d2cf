"""规划引擎单元测试（使用内联目录，不依赖种子数据）。"""
import pytest

from app.planner import Course, PlanError, build_plan


def C(code, credits, seasons=("SPRING", "FALL"), groups=(), coreqs=()):
    return Course(code, code, credits, frozenset(seasons),
                  tuple(tuple(g) for g in groups), tuple(coreqs))


def cat(*courses):
    return {c.code: c for c in courses}


def codes(result):
    return [[c["code"] for c in s["courses"]] for s in result["semesters"]]


def test_prereq_chain_and_coreq_same_semester():
    catalog = cat(
        C("CS101", 3, coreqs=("CS101L",)), C("CS101L", 1),
        C("CS201", 3, groups=(("CS101",),)),
    )
    r = build_plan(catalog, set(), ["CS201"], "2026-SPRING", 10, 4)
    # 同修课同学期，先修课在更早学期
    assert codes(r) == [["CS101", "CS101L"], ["CS201"]]
    assert r["completion_semester"] == "2026-FALL"
    assert r["total_credits"] == 7


def test_season_constraint_delays_course():
    catalog = cat(C("A", 3, seasons=("FALL",)))
    r = build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 4)
    assert codes(r) == [[], ["A"]]
    assert r["completion_semester"] == "2026-FALL"


def test_group_choice_and_lexicographic_tiebreak():
    catalog = cat(
        C("CS101", 3, coreqs=("CS101L",)), C("CS101L", 1),
        C("CS201", 3, groups=(("CS101",),)),
        C("CS202", 3, groups=(("MATH101",),)),
        C("MATH101", 4), C("MATH103", 3),
        C("CS301", 3, groups=(("CS201", "CS202"), ("MATH103",))),
    )
    r = build_plan(catalog, set(), ["CS301"], "2026-SPRING", 10, 6)
    # 两条先修路径均 3 学期完成；按各学期代码序列字典序，CS101 路径 < MATH101 路径
    assert codes(r) == [["CS101", "CS101L"], ["CS201", "MATH103"], ["CS301"]]
    assert r["completion_semester"] == "2027-SPRING"


def test_completed_courses_not_rescheduled():
    catalog = cat(
        C("CS101", 3, coreqs=("CS101L",)), C("CS101L", 1),
        C("CS201", 3, groups=(("CS101",),)),
    )
    r = build_plan(catalog, {"CS101", "CS101L"}, ["CS201"], "2026-SPRING", 10, 4)
    assert codes(r) == [["CS201"]]


def test_all_targets_completed_returns_empty_plan():
    catalog = cat(C("A", 3))
    r = build_plan(catalog, {"A"}, ["A"], "2026-SPRING", 10, 4)
    assert r["semesters"] == []
    assert r["completion_semester"] is None


def test_missing_course():
    catalog = cat(C("A", 3))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["XXX"], "2026-SPRING", 10, 4)
    assert e.value.code == "COURSE_MISSING"
    assert "XXX" in e.value.message


def test_coreq_season_conflict():
    catalog = cat(
        C("X", 2, seasons=("SPRING",), coreqs=("XL",)),
        C("XL", 2, seasons=("FALL",)),
    )
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["X"], "2026-SPRING", 10, 4)
    assert e.value.code == "REQUIREMENT_CONFLICT"
    assert "X" in e.value.message and "XL" in e.value.message


def test_prereq_cycle_is_conflict():
    catalog = cat(
        C("A", 3, groups=(("B",),)),
        C("B", 3, groups=(("A",),)),
    )
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026-SPRING", 10, 6)
    assert e.value.code == "REQUIREMENT_CONFLICT"
    assert "A" in e.value.message and "B" in e.value.message


def test_coreq_block_exceeds_credit_cap():
    catalog = cat(C("CS101", 3, coreqs=("CS101L",)), C("CS101L", 1))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["CS101"], "2026-SPRING", 3, 4)
    assert e.value.code == "NO_SOLUTION"
    assert "4" in e.value.message and "3" in e.value.message


def test_deadline_too_short_is_no_solution():
    catalog = cat(C("A", 3), C("B", 3, groups=(("A",),)), C("C", 3, groups=(("B",),)))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["C"], "2026-SPRING", 10, 2)
    assert e.value.code == "NO_SOLUTION"
    r = build_plan(catalog, set(), ["C"], "2026-SPRING", 10, 3)
    assert codes(r) == [["A"], ["B"], ["C"]]


def test_credit_cap_forces_spread():
    catalog = cat(C("A", 3), C("B", 3))
    r = build_plan(catalog, set(), ["A", "B"], "2026-SPRING", 3, 4)
    assert codes(r) == [["A"], ["B"]]  # 字典序：A 在前


def test_invalid_semester_format():
    catalog = cat(C("A", 3))
    with pytest.raises(PlanError) as e:
        build_plan(catalog, set(), ["A"], "2026春", 10, 4)
    assert e.value.code == "INVALID_INPUT"
