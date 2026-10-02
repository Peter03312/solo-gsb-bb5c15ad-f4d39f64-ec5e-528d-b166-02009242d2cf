"""课程学期规划引擎。

排课规则：
- 先修条件按“组”声明：每组任选一门，所有组均须满足；被依赖的课程必须安排在更早学期。
- 同修课程（coreq）若尚未修过，必须与该课同学期安排。
- 已修课程不重复排入；方案只包含目标课程及满足条件所需课程。
- 目标：在规划期限（num_semesters）内最早完成；完成学期相同者，按“各学期课程
  代码序列”（每学期内代码升序、再按学期先后组成元组）的字典序取最小者。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations

SEASONS = ("SPRING", "FALL")
SEMESTER_RE = re.compile(r"^(\d{4})-(SPRING|FALL)$")

LEAF_CAP = 512         # 先修选课组合数上限，防止指数爆炸
NODE_BUDGET = 200_000  # 单个候选课程集合的排课搜索节点上限


class PlanError(Exception):
    """规划失败：code 供调用方判断，message 面向用户说明原因。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Course:
    code: str
    name: str
    credits: int
    seasons: frozenset          # {"SPRING"}、{"FALL"} 或两者
    prereq_groups: tuple        # tuple[tuple[str, ...], ...]，每组任选一门，各组均须满足
    coreqs: tuple               # tuple[str, ...]


# ---------------------------------------------------------------- 学期工具

def parse_semester(text):
    """解析 'YYYY-SPRING' / 'YYYY-FALL'，返回 (year, season)。"""
    m = SEMESTER_RE.match((text or "").strip().upper())
    if not m:
        raise PlanError(
            "INVALID_INPUT",
            f"学期格式非法：{text!r}，应为 YYYY-SPRING 或 YYYY-FALL，如 2026-SPRING",
        )
    return int(m.group(1)), m.group(2)


def season_at(start_season, index):
    """第 index 个学期（1 起）的季节。"""
    return SEASONS[(SEASONS.index(start_season) + index - 1) % 2]


def semester_label(start_year, start_season, index):
    """第 index 个学期（1 起）的绝对标签，如 2027-FALL。"""
    year = start_year + (SEASONS.index(start_season) + index - 1) // 2
    return f"{year}-{season_at(start_season, index)}"


# ---------------------------------------------------------------- 候选课程集合

def _enumerate_sets(catalog, completed, needed):
    """枚举满足全部先修组与同修闭包的候选课程集合。

    返回 (leaves, conflicts)：leaves 为 frozenset 列表；conflicts 为枚举过程中
    发现的条件冲突原因（供全部失败时向用户说明）。
    """
    leaves = []
    conflicts = []
    seen = set()

    def close_coreqs(courses):
        result = set(courses)
        stack = list(result)
        while stack:
            cur = stack.pop()
            for co in catalog[cur].coreqs:
                if co in completed or co in result:
                    continue
                if co not in catalog:
                    conflicts.append(f"课程 {cur} 的同修课程 {co} 不在目录中，条件冲突")
                    return None
                result.add(co)
                stack.append(co)
        return frozenset(result)

    def rec(courses):
        if len(leaves) >= LEAF_CAP:
            raise PlanError("TOO_COMPLEX", "先修条件的选课组合过多，超出规划复杂度限制")
        courses = close_coreqs(courses)
        if courses is None or courses in seen:
            return
        seen.add(courses)
        for code in sorted(courses):
            for group in catalog[code].prereq_groups:
                if any(o in completed or o in courses for o in group):
                    continue
                # 该组尚未满足：枚举可选课程（目录中不存在或已修的选项不可再选）
                options = sorted(o for o in group if o in catalog and o not in completed)
                if not options:
                    conflicts.append(
                        f"课程 {code} 的先修组 [{'、'.join(group)}] 在目录中无可选课程，条件冲突")
                    return
                for opt in options:
                    rec(courses | {opt})
                return
        leaves.append(courses)

    rec(frozenset(needed))
    return leaves, conflicts


# ---------------------------------------------------------------- 同修块

class _Block:
    """同修课程合并成的排课单元：必须同学期修读。"""

    __slots__ = ("codes", "credits", "seasons", "members")

    def __init__(self, members, credits, seasons):
        self.members = members              # tuple[str, ...]
        self.codes = tuple(sorted(members))
        self.credits = credits
        self.seasons = seasons              # frozenset，成员开课季节的交集


def _build_blocks(catalog, courses):
    parent = {c: c for c in courses}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c in courses:
        for co in catalog[c].coreqs:
            if co in courses:
                a, b = find(c), find(co)
                if a != b:
                    parent[a] = b

    groups = {}
    for c in courses:
        groups.setdefault(find(c), []).append(c)

    blocks = []
    for members in groups.values():
        seasons = None
        credits = 0
        for m in members:
            credits += catalog[m].credits
            seasons = catalog[m].seasons if seasons is None else seasons & catalog[m].seasons
        if not seasons:
            raise PlanError(
                "REQUIREMENT_CONFLICT",
                f"同修课程 {'+'.join(sorted(members))} 没有共同开课学期，无法安排在同一学期",
            )
        blocks.append(_Block(tuple(sorted(members)), credits, frozenset(seasons)))
    blocks.sort(key=lambda b: b.codes)
    return blocks


def _block_requirements(catalog, completed, blocks):
    """每个块的先修要求：列表的每项是一个块编号集合，须至少一个块排在更早学期。"""
    block_of = {}
    for bi, b in enumerate(blocks):
        for c in b.members:
            block_of[c] = bi
    reqs = []
    for b in blocks:
        rb = []
        for c in b.members:
            for group in catalog[c].prereq_groups:
                if any(o in completed for o in group):
                    continue
                opts = frozenset(block_of[o] for o in group if o in block_of)
                if not opts:  # 防御：闭包阶段已保证每组有可选课程
                    return None
                rb.append(opts)
        reqs.append(rb)
    return reqs


# ---------------------------------------------------------------- 排课搜索

def _schedule(blocks, reqs, num_semesters, start_season, cap):
    """返回 (k, seq)：最少 k 个学期排完时字典序最小的方案；期限内无解返回 None。

    seq 为每学期课程代码（升序）组成的元组的元组，长度 k。
    """
    n = len(blocks)
    assign = [-1] * n
    counter = [0]

    def dfs(i, k, remaining):
        counter[0] += 1
        if counter[0] > NODE_BUDGET:
            raise PlanError("TOO_COMPLEX", "排课搜索超出复杂度限制，请减少目标课程或缩短学期数")
        if not remaining:
            return True
        if i > k:
            return False
        # 剪枝：每个剩余块在 i..k 内至少有一个匹配季节的学期
        for b in remaining:
            if not any(season_at(start_season, j) in blocks[b].seasons for j in range(i, k + 1)):
                return False
        # 剪枝：单季节块的学分不能超过剩余该季节学期的总容量
        for s in SEASONS:
            fixed = sum(blocks[b].credits for b in remaining if blocks[b].seasons == frozenset({s}))
            slots = sum(1 for j in range(i, k + 1) if season_at(start_season, j) == s)
            if fixed > cap * slots:
                return False
        avail = [
            b for b in remaining
            if season_at(start_season, i) in blocks[b].seasons
            and all(any(0 < assign[o] < i for o in opts) for opts in reqs[b])
        ]
        # 按“本学期课程代码序列”的字典序枚举本学期子集，首个可行即字典序最小
        candidates = []
        for r in range(len(avail) + 1):
            for combo in combinations(avail, r):
                if sum(blocks[b].credits for b in combo) <= cap:
                    key = tuple(sorted(c for b in combo for c in blocks[b].codes))
                    candidates.append((key, combo))
        candidates.sort(key=lambda x: x[0])
        for _, combo in candidates:
            for b in combo:
                assign[b] = i
            if dfs(i + 1, k, remaining - frozenset(combo)):
                return True
            for b in combo:
                assign[b] = -1
        return False

    for k in range(1, num_semesters + 1):
        for b in range(n):
            assign[b] = -1
        if dfs(1, k, frozenset(range(n))):
            seq = tuple(
                tuple(sorted(c for b in range(n) if assign[b] == i for c in blocks[b].codes))
                for i in range(1, k + 1)
            )
            return k, seq
    return None


def _deadlocked_codes(blocks, reqs):
    """忽略季节与学分限制仍无法排入的课程（先修循环/不可满足），用于说明原因。"""
    assigned = set()
    progress = True
    while progress:
        progress = False
        for b in range(len(blocks)):
            if b in assigned:
                continue
            if all(any(o in assigned for o in opts) for opts in reqs[b]):
                assigned.add(b)
                progress = True
    return sorted(c for b in range(len(blocks)) if b not in assigned for c in blocks[b].codes)


# ---------------------------------------------------------------- 主入口

def build_plan(catalog, completed, targets, start_semester, max_credits, num_semesters):
    """生成完整方案；失败抛出 PlanError（不返回部分课表）。

    catalog:   {code: Course}
    completed: 已修课程代码集合
    targets:   目标课程代码列表（非空）
    """
    start_year, start_season = parse_semester(start_semester)
    targets = list(dict.fromkeys(t.strip().upper() for t in targets))
    completed = {c.strip().upper() for c in completed}

    missing = [t for t in targets if t not in catalog]
    if missing:
        raise PlanError("COURSE_MISSING", f"课程缺失：{'、'.join(missing)} 不在当前目录版本中")

    needed = set(targets) - completed
    if not needed:
        return {
            "completion_semester": None,
            "total_credits": 0,
            "semesters": [],
            "message": "目标课程均已完成，无需排课",
        }

    leaves, conflicts = _enumerate_sets(catalog, completed, needed)
    best = None
    no_solution_notes = []

    for courses in leaves:
        try:
            blocks = _build_blocks(catalog, courses)
        except PlanError as exc:
            conflicts.append(exc.message)
            continue
        reqs = _block_requirements(catalog, completed, blocks)
        if reqs is None:
            conflicts.append("先修条件无法满足，条件冲突")
            continue
        heavy = next((b for b in blocks if b.credits > max_credits), None)
        if heavy is not None:
            no_solution_notes.append(
                f"同修课程 {'+'.join(heavy.codes)} 学分合计 {heavy.credits} "
                f"超过每学期学分上限 {max_credits}")
            continue
        sched = _schedule(blocks, reqs, num_semesters, start_season, max_credits)
        if sched is None:
            # 放宽学期数与学分上限再试一次，用于区分“条件冲突”与“期限内无解”
            relaxed = _schedule(blocks, reqs, max(num_semesters, 2 * len(blocks) + 2),
                                start_season, sum(b.credits for b in blocks))
            if relaxed is None:
                conflicts.append("先修条件存在循环依赖或无法满足，涉及课程："
                                 + "、".join(_deadlocked_codes(blocks, reqs)))
            else:
                no_solution_notes.append(
                    f"受开课学期、先修顺序或每学期学分上限 {max_credits} 限制，"
                    f"{num_semesters} 个学期内无法排完所需课程")
            continue
        if best is None or sched < best:
            best = sched

    if best is None:
        if no_solution_notes:
            raise PlanError("NO_SOLUTION", no_solution_notes[0])
        raise PlanError("REQUIREMENT_CONFLICT",
                        conflicts[0] if conflicts else "先修/同修条件冲突，无法生成方案")

    k, seq = best
    semesters = []
    for i, codes in enumerate(seq, start=1):
        courses_in_sem = [
            {"code": c, "name": catalog[c].name, "credits": catalog[c].credits} for c in codes
        ]
        semesters.append({
            "semester": semester_label(start_year, start_season, i),
            "courses": courses_in_sem,
            "total_credits": sum(c["credits"] for c in courses_in_sem),
        })
    return {
        "completion_semester": semester_label(start_year, start_season, k),
        "total_credits": sum(s["total_credits"] for s in semesters),
        "semesters": semesters,
    }
