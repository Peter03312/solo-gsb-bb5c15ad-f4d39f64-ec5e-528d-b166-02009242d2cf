"""高校课程学期规划服务 —— FastAPI 入口。"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import db
from .planner import Course, PlanError, build_plan
from .replan import replan_plan
from .schemas import CatalogIn, PlanIn, ReplanIn


@asynccontextmanager
async def lifespan(_):
    db.init_db()  # 建表；首次启动写入种子目录（版本 1）
    yield


app = FastAPI(
    title="高校课程学期规划服务",
    version="1.0.0",
    description="基于课程目录版本的学期排课规划：先修组、同修、开课季节、学分上限约束下的最早完成方案。",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def invalid_input_handler(_, exc):
    errors = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "INVALID_INPUT", "message": "非法输入", "errors": errors}},
    )


def _to_catalog(rows):
    return {
        r["code"]: Course(
            code=r["code"], name=r["name"], credits=r["credits"],
            seasons=frozenset(r["semesters"]),
            prereq_groups=tuple(tuple(g) for g in r["prereq_groups"]),
            coreqs=tuple(r["coreqs"]),
        )
        for r in rows
    }


def _plan_out(row):
    out = {
        "plan_id": row["id"],
        "status": row["status"],
        "catalog_version": row["version_id"],
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]),
        "created_at": row["created_at"],
    }
    source = row["source_plan_id"] if "source_plan_id" in row.keys() else None
    if source:
        out["source_plan_id"] = source
        out["replan"] = json.loads(row["replan_json"])
    return out


# ---------------------------------------------------------------- 基础

@app.get("/")
def root():
    return {"service": "高校课程学期规划服务", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- 课程目录

@app.post("/catalog/versions", status_code=201)
def create_catalog(payload: CatalogIn):
    """目录修订：全量提交课程集合，生成新版本并成为当前版本。"""
    courses = [
        {
            "code": c.code, "name": c.name, "credits": c.credits,
            "semesters": c.semesters, "prereq_groups": c.prereq_groups, "coreqs": c.coreqs,
        }
        for c in payload.courses
    ]
    with db.session() as conn:
        vid = db.create_catalog_version(conn, payload.note, courses)
        version = db.get_version(conn, vid)
    return {
        "version_id": vid,
        "note": version["note"],
        "course_count": len(courses),
        "created_at": version["created_at"],
    }


@app.get("/catalog/current")
def current_catalog():
    with db.session() as conn:
        version = db.get_current_version(conn)
        courses = db.load_courses(conn, version["id"])
    return {
        "version_id": version["id"],
        "note": version["note"],
        "created_at": version["created_at"],
        "courses": courses,
    }


@app.get("/catalog/versions/{version_id}")
def catalog_version(version_id: int):
    """查看历史目录版本（供回看方案所绑定的目录）。"""
    with db.session() as conn:
        version = db.get_version(conn, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"目录版本 {version_id} 不存在"})
        courses = db.load_courses(conn, version_id)
    return {
        "version_id": version["id"],
        "note": version["note"],
        "created_at": version["created_at"],
        "courses": courses,
    }


# ---------------------------------------------------------------- 规划方案

@app.post("/plans", status_code=201)
def create_plan(payload: PlanIn):
    """生成规划方案并绑定当前目录版本；失败时说明原因，不返回部分课表。

    可选锁定安排（locked：课程+学期）与不可排课学期（unavailable_semesters）：
    锁定课程视为本次必修并须安排在指定学期；不可排课学期不安排任何课程。
    不带这两个字段的请求行为与返回结构同既有版本一致。
    """
    with db.session() as conn:
        version = db.get_current_version(conn)
        catalog = _to_catalog(db.load_courses(conn, version["id"]))
        try:
            result = build_plan(
                catalog,
                completed=set(payload.completed_courses),
                targets=payload.target_courses,
                start_semester=payload.start_semester,
                max_credits=payload.max_credits_per_semester,
                num_semesters=payload.num_semesters,
                locked=[(l.course, l.semester) for l in payload.locked],
                unavailable=payload.unavailable_semesters,
            )
        except PlanError as exc:
            status = 400 if exc.code == "INVALID_INPUT" else 422
            raise HTTPException(status_code=status,
                                detail={"code": exc.code, "message": exc.message})
        # 兼容：未提供新字段时，保存的请求回显不含这两个键，与既有格式一致
        request_data = payload.model_dump()
        for key in ("locked", "unavailable_semesters"):
            if not request_data.get(key):
                request_data.pop(key, None)
        plan_id = uuid.uuid4().hex[:12]
        db.insert_plan(conn, plan_id, version["id"], request_data, result)
        row = db.get_plan(conn, plan_id)
    return _plan_out(row)


@app.get("/plans")
def list_plans():
    with db.session() as conn:
        rows = db.list_plans(conn)
    out = []
    for r in rows:
        item = {
            "plan_id": r["id"],
            "status": r["status"],
            "catalog_version": r["version_id"],
            "completion_semester": json.loads(r["result_json"]).get("completion_semester"),
            "created_at": r["created_at"],
        }
        if r["source_plan_id"]:
            item["source_plan_id"] = r["source_plan_id"]
        out.append(item)
    return out


@app.get("/plans/{plan_id}")
def get_plan(plan_id: str):
    """回看方案（含其绑定的目录版本号）。"""
    with db.session() as conn:
        row = db.get_plan(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"方案 {plan_id} 不存在"})
    return _plan_out(row)


@app.post("/plans/{plan_id}/confirm")
def confirm_plan(plan_id: str):
    """确认方案或重排草案：若目录自其生成后已修订，则拒绝且不写入。"""
    with db.session() as conn:
        row = db.get_plan(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"方案 {plan_id} 不存在"})
        if row["status"] == "confirmed":
            return _plan_out(row)  # 幂等：已确认的方案直接返回
        current = db.get_current_version(conn)
        if row["version_id"] != current["id"]:
            raise HTTPException(status_code=409, detail={
                "code": "CATALOG_CHANGED",
                "message": f"目录已修订（当前版本 {current['id']}，方案基于版本 "
                           f"{row['version_id']}），确认被拒绝，请重新规划",
            })
        db.set_plan_status(conn, plan_id, "confirmed")
        row = db.get_plan(conn, plan_id)
    return _plan_out(row)


@app.post("/plans/{plan_id}/replan", status_code=201)
def replan_plan_route(plan_id: str, payload: ReplanIn):
    """已确认方案执行中途，凭已结束学期数与累计已修课程按当前目录滚动重排。

    结果作为关联原方案的待确认草案（status=draft）保存；原方案及其历史目录不变。
    目录在计算至落库间被修订时，拒绝旧版本结果（409）且不保存。
    """
    with db.session() as conn:
        row = db.get_plan(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"方案 {plan_id} 不存在"})
        if row["status"] != "confirmed":
            raise HTTPException(status_code=409, detail={
                "code": "PLAN_NOT_CONFIRMED",
                "message": f"方案 {plan_id} 状态为 {row['status']}，仅已确认方案可滚动重排",
            })

        original_request = json.loads(row["request_json"])
        original_result = json.loads(row["result_json"])

        version = db.get_current_version(conn)
        catalog = _to_catalog(db.load_courses(conn, version["id"]))
        try:
            effective_request, result, diff = replan_plan(
                catalog,
                original_request=original_request,
                original_result=original_result,
                elapsed_semesters=payload.elapsed_semesters,
                completed=set(payload.completed_courses),
            )
        except PlanError as exc:
            status = 400 if exc.code == "INVALID_INPUT" else 422
            raise HTTPException(status_code=status,
                                detail={"code": exc.code, "message": exc.message})

        # 落库前复查：目录在计算期间被修订则拒绝旧版本结果，不保存
        current = db.get_current_version(conn)
        if current["id"] != version["id"]:
            raise HTTPException(status_code=409, detail={
                "code": "CATALOG_CHANGED",
                "message": f"目录在重排期间已修订（计算基于版本 {version['id']}，"
                           f"当前版本 {current['id']}），旧版本结果被拒绝，请重新发起重排",
            })

        draft_id = uuid.uuid4().hex[:12]
        replan_meta = {
            "source_plan_id": plan_id,
            "elapsed_semesters": payload.elapsed_semesters,
            "completed_courses": payload.completed_courses,
            "difference": diff,
        }
        db.insert_draft(conn, draft_id, version["id"], effective_request, result,
                        source_plan_id=plan_id, replan=replan_meta)
        draft = db.get_plan(conn, draft_id)
    return _plan_out(draft)
