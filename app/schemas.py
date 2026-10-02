"""请求体的 Pydantic 模型与校验。非法输入（空目标、非正学分/上限等）一律拒绝。"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Season = Literal["SPRING", "FALL"]


def _norm_code(code: str) -> str:
    return code.strip().upper()


class CourseIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = ""
    credits: int = Field(gt=0, description="学分必须为正整数")
    semesters: list[Season] = Field(min_length=1, description="开课季节：SPRING/FALL")
    prereq_groups: list[list[str]] = Field(
        default_factory=list, description="先修条件：每组任选一门，各组均须满足")
    coreqs: list[str] = Field(default_factory=list, description="同修课程")

    @field_validator("code")
    @classmethod
    def norm_code(cls, v):
        return _norm_code(v)

    @field_validator("semesters")
    @classmethod
    def dedupe_semesters(cls, v):
        return sorted(set(v))

    @field_validator("prereq_groups")
    @classmethod
    def check_groups(cls, v):
        groups = []
        for g in v:
            if not g:
                raise ValueError("先修组不能为空")
            groups.append(sorted({_norm_code(x) for x in g}))
        return groups

    @field_validator("coreqs")
    @classmethod
    def check_coreqs(cls, v):
        return sorted({_norm_code(x) for x in v})


class CatalogIn(BaseModel):
    """目录修订：全量提交一套课程，形成新版本。"""

    note: str = ""
    courses: list[CourseIn] = Field(min_length=1)

    @field_validator("courses")
    @classmethod
    def unique_codes(cls, v):
        codes = [c.code for c in v]
        if len(codes) != len(set(codes)):
            raise ValueError("课程代码重复")
        return v


class LockedPlacement(BaseModel):
    """锁定安排：课程必须安排在指定学期（学期须在规划期限内）。"""

    course: str = Field(min_length=1, max_length=32, description="锁定课程代码")
    semester: str = Field(description="锁定学期，如 2026-FALL")

    @field_validator("course")
    @classmethod
    def norm_course(cls, v):
        return _norm_code(v)


class PlanIn(BaseModel):
    completed_courses: list[str] = Field(default_factory=list, description="已修课程")
    target_courses: list[str] = Field(min_length=1, description="目标课程，非空")
    start_semester: str = Field(description="起始学期，如 2026-SPRING")
    max_credits_per_semester: int = Field(gt=0, description="每学期学分上限，必须为正")
    num_semesters: int = Field(gt=0, description="规划学期数，必须为正")
    locked: list[LockedPlacement] = Field(
        default_factory=list,
        description="锁定安排：课程视为本次必修并须安排在指定学期（即使不在目标课程中）")
    unavailable_semesters: list[str] = Field(
        default_factory=list, description="不可排课学期：这些学期不安排任何课程")

    @field_validator("completed_courses", "target_courses")
    @classmethod
    def norm_codes(cls, v, info):
        codes = sorted({_norm_code(x) for x in v if x.strip()})
        if info.field_name == "target_courses" and not codes:
            raise ValueError("目标课程不能为空")
        return codes


class ReplanIn(BaseModel):
    """方案执行中途的滚动重排输入。

    - elapsed_semesters：已结束学期数，须大于 0 且小于原规划期限；
    - completed_courses：截至该时点的累计已修课程，
      不得撤销原方案创建时声明的已修课程（须为其超集）。
    """

    elapsed_semesters: int = Field(description="已结束学期数（0 < x < 原规划学期数）")
    completed_courses: list[str] = Field(
        default_factory=list, description="截至该时点的累计已修课程（须包含原方案声明的已修）")

    @field_validator("elapsed_semesters")
    @classmethod
    def elapsed_positive(cls, v):
        if v <= 0:
            raise ValueError("已结束学期数必须大于 0")
        return v

    @field_validator("completed_courses")
    @classmethod
    def norm_codes(cls, v):
        return sorted({_norm_code(x) for x in v if x.strip()})
