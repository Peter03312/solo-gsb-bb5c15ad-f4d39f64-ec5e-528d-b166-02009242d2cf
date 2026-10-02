# 高校课程学期规划服务

基于 **Python + FastAPI + SQLite** 的课程学期规划服务：依据课程目录（学分、春秋开课学期、
先修条件、同修课程），为学生生成满足全部约束、**期限内最早完成**的完整排课方案；
目录支持版本化修订，方案绑定目录版本，可回看、可确认。

## 功能与规则

- **课程目录**：每门课记录学分、开课季节（`SPRING`/`FALL`，可两者）、先修条件与同修课程。
  - 先修条件按“组”声明：**每组任选一门，各组均须满足**；先修课必须安排在**更早学期**。
  - 同修课程（coreq）尚未修过时，必须与该课**同学期**安排。
- **规划输入**：已修课程、目标课程（非空）、起始学期（如 `2026-SPRING`）、
  每学期学分上限、规划学期数；可选**锁定安排**（课程+学期）与**不可排课学期**。
  学分与两个上限必须为正，非法输入一律拒绝（422/400）。
- **锁定安排（可选）**：`locked` 为 `{course, semester}` 列表，锁定课程视为**本次必修**
  （即使不在目标课程中），并必须安排在指定学期。锁定课程不得是已修课、不得重复锁定，
  学期须在规划期限内；锁定课程仍须满足开课季节、先修组、同修同学期与学分上限。
- **不可排课学期（可选）**：`unavailable_semesters` 列出的学期不安排任何课程
  （期限外的学期自动忽略）。
- **规划输出**：
  - 已修课程不重复排入；方案只包含目标课程、锁定课程及满足条件所需的课程。
  - 返回期限内**最早完成**的完整方案；完成学期相同者，按“各学期课程代码序列”
    （每学期内代码升序、再按学期先后组成序列）的**字典序**取最小者。
  - 课程缺失、条件冲突（先修循环、同修课无共同开课学期、锁定与约束冲突等）
    或期限内无解时，返回具体原因，**不返回部分课表**。
- **目录版本**：每次目录修订生成新版本；方案创建时绑定当前版本，可随时回看；
  确认方案时若目录已修订，则拒绝（409）且不写入。
- **执行中途滚动重排（草案）**：已确认方案执行过程中，如实际完成情况与计划不同或目录已修订，
  可凭**原方案编号、已结束学期数、截至该时点的累计已修课程**重新排课：
  - 已结束学期数必须 **大于 0 且小于原规划期限**；累计已修**不得撤销**原方案创建时声明的已修
    （必须是其超集，可包含计划外提前修完的课程）。
  - 从原起始学期顺延至下一学期起，沿用**原目标课程、原每学期学分上限和剩余学期数**，
    按**当前目录**求最早完成的完整未来课表；已修课程不重复排入。
  - 返回新课表以及与**原方案剩余学期**（跳过已结束学期）的课程差异（逐学期新增/移除 + 汇总），
    并作为**待确认草案**（`status="draft"`）关联原方案保存；原方案及其历史目录保持不变。
  - 草案沿用既有确认约定：`POST /plans/{draft_id}/confirm`，目录在草案生成后再修订则 409 拒绝。
  - 原方案不存在或未确认、目标缺失、条件冲突、期限内无解、撤销原已修或学期数越界时，
    返回具体原因且**不保存**；目录在计算至落库之间被修订时，拒绝旧版本结果（409，不保存）。

## 快速开始

### 方式一：Docker Compose（推荐，一键启动）

```bash
docker compose up --build
```

### 方式二：本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 访问地址

| 内容 | 地址 |
| --- | --- |
| 服务地址 | http://localhost:8000 |
| 交互式 API 文档 | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/health |

端口：容器内外均为 **8000**（Compose 映射 `8000:8000`，可在 `docker-compose.yml` 中修改）。

### 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | 容器内 `/data/app.db`；本地 `data/app.db` | SQLite 数据库文件路径 |

数据库在首次启动时自动初始化：建表，并写入种子课程目录（版本 1）。
Compose 通过命名卷 `planner-data` 持久化数据。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/catalog/versions` | 目录修订：全量提交课程集合，生成新版本 |
| GET | `/catalog/current` | 当前目录版本及全部课程 |
| GET | `/catalog/versions/{id}` | 查看历史目录版本（供回看） |
| POST | `/plans` | 生成规划方案（绑定当前目录版本；可选锁定安排与不可排课学期） |
| GET | `/plans` | 方案列表 |
| GET | `/plans/{id}` | 回看方案（含绑定的目录版本号） |
| POST | `/plans/{id}/confirm` | 确认方案/草案；目录已修订则 409 拒绝且不写入 |
| POST | `/plans/{id}/replan` | 已确认方案执行中途滚动重排，生成关联草案（201） |

### 目录课程字段

```json
{
  "code": "CS301",
  "name": "算法设计与分析",
  "credits": 3,
  "semesters": ["SPRING", "FALL"],
  "prereq_groups": [["CS201", "CS202"], ["MATH103"]],
  "coreqs": []
}
```

- `credits`：正整数；`semesters`：`SPRING`/`FALL` 至少其一。
- `prereq_groups`：每个内层数组为一组，组内任选一门，各组均须满足。
- `coreqs`：同修课程代码列表。

### 调用示例

生成规划：

```bash
curl -X POST http://localhost:8000/plans -H 'Content-Type: application/json' -d '{
  "completed_courses": [],
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6
}'
```

响应（201，节选）：

```json
{
  "plan_id": "64adff786f4c",
  "status": "proposed",
  "catalog_version": 1,
  "result": {
    "completion_semester": "2027-FALL",
    "total_credits": 16,
    "semesters": [
      {"semester": "2026-SPRING", "courses": [{"code": "CS101", ...}, {"code": "CS101L", ...}], "total_credits": 4},
      {"semester": "2026-FALL",   "courses": [{"code": "CS201", ...}, {"code": "MATH103", ...}], "total_credits": 6},
      {"semester": "2027-SPRING", "courses": [{"code": "CS301", ...}], "total_credits": 3},
      {"semester": "2027-FALL",   "courses": [{"code": "CS401", ...}], "total_credits": 3}
    ]
  }
}
```

确认 / 修订 / 回看：

```bash
curl -X POST http://localhost:8000/plans/<plan_id>/confirm      # 确认方案
curl -X POST http://localhost:8000/catalog/versions \
  -H 'Content-Type: application/json' -d @examples/catalog_v2.json  # 目录修订 -> 新版本
curl http://localhost:8000/plans/<plan_id>                       # 回看方案（含绑定版本）
curl http://localhost:8000/catalog/versions/1                    # 回看历史目录
```

完整可运行示例脚本（含错误演示）：

```bash
bash examples/demo.sh
```

### 锁定安排与不可排课学期

`POST /plans` 接受两个可选字段（不带这两个字段的既有请求行为与返回结构不变）：

- `locked`：`{"course": "课程代码", "semester": "YYYY-SPRING|FALL"}` 列表。
  锁定课程视为本次必修（即使不在 `target_courses` 中），必须安排在指定学期；
  学期须在规划期限内，课程不得是已修课、不得重复锁定。
- `unavailable_semesters`：学期标签列表，这些学期不安排任何课程。

```bash
curl -X POST http://localhost:8000/plans -H 'Content-Type: application/json' -d '{
  "completed_courses": [],
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6,
  "locked": [{"course": "CS303", "semester": "2027-SPRING"}],
  "unavailable_semesters": ["2026-FALL"]
}'
```

注意锁定与其余约束共同生效：上例中 CS303 锁定在 2027-SPRING，要求其先修 CS201
排在更早学期，而 2026-FALL 不可排课，CS201 无处可排，因此该组合返回
`REQUIREMENT_CONFLICT`（锁定与约束冲突）。换成可行组合：

```bash
curl -X POST http://localhost:8000/plans -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6,
  "locked": [{"course": "CS401", "semester": "2028-FALL"}],
  "unavailable_semesters": ["2026-FALL"]
}'
```

响应（201，节选）：2026-FALL 为不可排课学期不排课，CS401 固定在锁定学期
2028-FALL；方案仍绑定当前目录版本，可确认、可回看。

```json
{
  "plan_id": "e5f1a2b3c4d6",
  "status": "proposed",
  "catalog_version": 1,
  "request": {
    "target_courses": ["CS401"],
    "locked": [{"course": "CS401", "semester": "2028-FALL"}],
    "unavailable_semesters": ["2026-FALL"]
  },
  "result": {
    "completion_semester": "2028-FALL",
    "total_credits": 16,
    "semesters": [
      {"semester": "2026-SPRING", "courses": [], "total_credits": 0},
      {"semester": "2026-FALL",   "courses": [], "total_credits": 0},
      {"semester": "2027-SPRING", "courses": [{"code": "CS101", ...}, {"code": "CS101L", ...}], "total_credits": 4},
      {"semester": "2027-FALL",   "courses": [{"code": "CS201", ...}, {"code": "MATH103", ...}], "total_credits": 6},
      {"semester": "2028-SPRING", "courses": [{"code": "CS301", ...}], "total_credits": 3},
      {"semester": "2028-FALL",   "courses": [{"code": "CS401", ...}], "total_credits": 3}
    ]
  }
}
```

单独锁定一门非目标课程（视为本次必修，即使不在 `target_courses` 中）：

```bash
curl -X POST http://localhost:8000/plans -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6,
  "locked": [{"course": "CS303", "semester": "2027-SPRING"}]
}'
# -> 201：CS303 排入 2027-SPRING，完成学期 2027-FALL
```

锁定相关失败均说明原因且不保存方案，例如：

```json
{"detail": {"code": "INVALID_INPUT", "message": "课程 CS303 被重复锁定"}}
{"detail": {"code": "INVALID_INPUT", "message": "锁定课程 CS101 已是已修课程，不得再锁定排课"}}
{"detail": {"code": "INVALID_INPUT", "message": "锁定课程 CS401 的学期 2030-FALL 不在规划期限（2026-SPRING 起共 6 个学期）内"}}
{"detail": {"code": "COURSE_MISSING", "message": "课程缺失：ABC123 不在当前目录版本中"}}
{"detail": {"code": "REQUIREMENT_CONFLICT", "message": "锁定课程 CS401 在锁定学期 2027-SPRING 不开课（该课及其同修课程仅 FALL 开课），锁定与约束冲突"}}
{"detail": {"code": "REQUIREMENT_CONFLICT", "message": "锁定课程 CS401 的学期 2027-FALL 为不可排课学期，锁定与约束冲突"}}
{"detail": {"code": "NO_SOLUTION", "message": "受锁定安排、开课学期、先修顺序或每学期学分上限 12 限制，4 个学期内无法排完所需课程"}}
```

### 执行中途滚动重排

仅可对**已确认**方案发起；重排结果以**草案**形式关联保存，原方案不变。
请求只需原方案编号、已结束学期数与累计已修课程（目标、学分上限、起始学期均取自原方案，
期限取剩余学期数，目录取当前版本）：

```bash
curl -X POST http://localhost:8000/plans/<plan_id>/replan \
  -H 'Content-Type: application/json' -d '{
    "elapsed_semesters": 2,
    "completed_courses": ["CS101", "CS101L", "CS201", "MATH103", "CS301"]
  }'
```

响应（201，节选）：

```json
{
  "plan_id": "a1b2c3d4e5f6",
  "status": "draft",
  "catalog_version": 2,
  "source_plan_id": "64adff786f4c",
  "request": {
    "completed_courses": ["CS101", "CS101L", "CS201", "CS301", "MATH103"],
    "target_courses": ["CS401"],
    "start_semester": "2027-SPRING",
    "max_credits_per_semester": 12,
    "num_semesters": 4
  },
  "result": {
    "completion_semester": "2027-FALL",
    "total_credits": 7,
    "semesters": [
      {"semester": "2027-SPRING", "courses": [], "total_credits": 0},
      {"semester": "2027-FALL", "courses": [{"code": "CS401", "credits": 4}], "total_credits": 4}
    ]
  },
  "replan": {
    "source_plan_id": "64adff786f4c",
    "elapsed_semesters": 2,
    "completed_courses": ["CS101", "CS101L", "CS201", "CS301", "MATH103"],
    "difference": {
      "semesters": [
        {"semester": "2027-SPRING", "added_courses": [], "removed_courses": ["CS301"]},
        {"semester": "2027-FALL", "added_courses": [], "removed_courses": []}
      ],
      "added_courses": [],
      "removed_courses": ["CS301"]
    }
  }
}
```

- `request`：本次未来课表的有效规划输入（起始学期已顺延、学期数为剩余数）。
- `replan.difference`：新课表与**原方案剩余学期**（原课表跳过前 2 个已结束学期后按学期对齐）
  的逐学期差异；`added_courses`/`removed_courses` 为跨学期去重汇总。
- 确认草案沿用原约定：`POST /plans/<draft_id>/confirm`；`GET /plans` 列表中草案带
  `source_plan_id` 字段。确认草案不影响原方案。

### 错误响应

失败时返回相应 HTTP 状态码与原因，不返回部分课表：

```json
{"detail": {"code": "NO_SOLUTION", "message": "受开课学期、先修顺序或每学期学分上限 12 限制，2 个学期内无法排完所需课程"}}
```

| code | HTTP | 含义 |
| --- | --- | --- |
| `INVALID_INPUT` | 400/422 | 非法输入：空目标、非正学分/上限、学期格式错误、重复锁定、锁定已修课、锁定学期超出规划期限等 |
| `COURSE_MISSING` | 422 | 课程缺失：目标或锁定课程不在当前目录版本中 |
| `REQUIREMENT_CONFLICT` | 422 | 条件冲突：先修循环、同修课无共同开课学期、同修课不在目录、锁定与开课季节/同修/不可排课约束冲突等 |
| `NO_SOLUTION` | 422 | 无解：期限内无法排完（锁定安排、不可排课学期、学分上限/开课季节/先修顺序限制） |
| `TOO_COMPLEX` | 422 | 组合或搜索超出内置复杂度上限 |
| `CATALOG_CHANGED` | 409 | 确认（或重排落库）时目录已修订，拒绝且不写入 |
| `PLAN_NOT_CONFIRMED` | 409 | 对未确认方案发起滚动重排 |
| `COMPLETED_REVOKED` | 422 | 累计已修撤销了原方案创建时声明的已修课程 |
| `NOT_FOUND` | 404 | 方案或目录版本不存在 |

## 项目结构

```
app/
  main.py       # FastAPI 路由与错误处理
  planner.py    # 规划引擎（先修组枚举、同修合并、锁定/不可排课约束、回溯排课、字典序选优）
  replan.py     # 执行中途滚动重排（学期顺延、剩余学期课程差异）
  schemas.py    # 请求模型与校验
  db.py         # SQLite 持久化（目录版本、方案、重排草案）
  seed_data.py  # 种子课程目录（版本 1）
tests/          # pytest 单元与 API 集成测试
examples/       # demo.sh 调用示例、catalog_v2.json 目录修订示例
Dockerfile / docker-compose.yml
```

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```
