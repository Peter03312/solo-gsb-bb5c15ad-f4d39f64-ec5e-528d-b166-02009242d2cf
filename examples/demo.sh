#!/usr/bin/env bash
# 调用示例：先启动服务（docker compose up --build 或 uvicorn app.main:app）
# 用法：bash examples/demo.sh  [默认访问 http://localhost:8000，可用 BASE 覆盖]
BASE="${BASE:-http://localhost:8000}"
HERE="$(cd "$(dirname "$0")" && pwd)"

step() { echo; echo "== $* =="; }

step "1. 健康检查"
curl -s "$BASE/health"; echo

step "2. 当前课程目录（首次启动自动初始化为版本 1）"
curl -s "$BASE/catalog/current"; echo

step "3. 请求规划：目标 编译原理 CS401，2026 春开始，每学期≤12 学分，6 个学期"
PLAN_A=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": [],
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6
}')
echo "$PLAN_A"
PLAN_A_ID=$(echo "$PLAN_A" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")

step "4. 再规划一个方案 B（暂不确认）"
PLAN_B=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": ["CS101", "CS101L", "CS201"],
  "target_courses": ["CS303"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 4
}')
echo "$PLAN_B"
PLAN_B_ID=$(echo "$PLAN_B" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")

step "5. 确认方案 A（目录未变，应成功）"
curl -s -X POST "$BASE/plans/$PLAN_A_ID/confirm"; echo

step "6. 修订目录（生成版本 2）"
curl -s -X POST "$BASE/catalog/versions" -H 'Content-Type: application/json' \
  -d @"$HERE/catalog_v2.json"; echo

step "7. 确认方案 B（目录已变，应 409 拒绝且不写入）"
curl -s -w '\nHTTP %{http_code}\n' -X POST "$BASE/plans/$PLAN_B_ID/confirm"
echo "方案 B 状态（仍为 proposed）："
curl -s "$BASE/plans/$PLAN_B_ID"; echo

step "8. 基于新目录重新规划并确认"
PLAN_C=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": ["CS101", "CS101L", "CS201"],
  "target_courses": ["CS303"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 4
}')
echo "$PLAN_C"
PLAN_C_ID=$(echo "$PLAN_C" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")
curl -s -X POST "$BASE/plans/$PLAN_C_ID/confirm"; echo

step "9. 回看：方案绑定的目录版本"
curl -s "$BASE/catalog/versions/1"; echo

step "10. 滚动重排：方案 A 执行 3 个学期后，凭原编号/已结束学期数/累计已修按当前目录(v2)重排"
REPLAN_D=$(curl -s -X POST "$BASE/plans/$PLAN_A_ID/replan" -H 'Content-Type: application/json' -d '{
  "elapsed_semesters": 3,
  "completed_courses": ["CS101", "CS101L", "CS201", "MATH103", "CS301"]
}')
echo "$REPLAN_D"
DRAFT_D_ID=$(echo "$REPLAN_D" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")

step "11. 重排差异演示：仅执行 2 个学期，但额外提前修完 CS301（原方案第 3 学期课程）"
curl -s -X POST "$BASE/plans/$PLAN_A_ID/replan" -H 'Content-Type: application/json' -d '{
  "elapsed_semesters": 2,
  "completed_courses": ["CS101", "CS101L", "CS201", "MATH103", "CS301"]
}'; echo

step "12. 确认重排草案 D（基于当前目录 v2，应成功；不影响原方案 A）"
curl -s -X POST "$BASE/plans/$DRAFT_D_ID/confirm"; echo
echo "原方案 A 仍为 confirmed 且绑定 v1："
curl -s "$BASE/plans/$PLAN_A_ID"; echo

step "13. 错误示例：对未确认方案 B 重排（应 409 PLAN_NOT_CONFIRMED，不保存草案）"
curl -s -w '\nHTTP %{http_code}\n' -X POST "$BASE/plans/$PLAN_B_ID/replan" \
  -H 'Content-Type: application/json' -d '{"elapsed_semesters": 1, "completed_courses": []}'

step "14. 错误示例：已结束学期数越界（0 / 达到原期限 6）"
curl -s -X POST "$BASE/plans/$PLAN_A_ID/replan" -H 'Content-Type: application/json' \
  -d '{"elapsed_semesters": 0, "completed_courses": []}'; echo
curl -s -X POST "$BASE/plans/$PLAN_A_ID/replan" -H 'Content-Type: application/json' \
  -d '{"elapsed_semesters": 6, "completed_courses": []}'; echo

step "15. 错误示例：累计已修撤销了原方案声明的已修课程（422 COMPLETED_REVOKED）"
curl -s -X POST "$BASE/plans/$PLAN_C_ID/replan" -H 'Content-Type: application/json' \
  -d '{"elapsed_semesters": 1, "completed_courses": ["CS101"]}'; echo

step "16. 错误示例：剩余学期内无解（422 NO_SOLUTION，不保存草案）"
curl -s -X POST "$BASE/plans/$PLAN_A_ID/replan" -H 'Content-Type: application/json' \
  -d '{"elapsed_semesters": 5, "completed_courses": ["CS101", "CS101L"]}'; echo

step "17. 错误示例：课程缺失"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["ABC123"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6
}'; echo

step "18. 错误示例：条件冲突（CS999 与其同修课开课季节互斥）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS999"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6
}'; echo

step "19. 错误示例：期限内无解"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 2
}'; echo

step "20. 错误示例：非法输入（空目标 / 非正上限）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": [], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6
}'; echo
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS101"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 0, "num_semesters": 6
}'; echo

step "21. 锁定安排：锁定非目标课 CS303 到 2027-SPRING（视为本次必修，201）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "locked": [{"course": "CS303", "semester": "2027-SPRING"}]
}'; echo

step "22. 不可排课学期：2026-FALL 不安排课程（完成学期顺延，201）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "unavailable_semesters": ["2026-FALL"]
}'; echo

step "23. 锁定 + 不可排课组合：CS401 锁定到 2028-FALL 且 2026-FALL 不可排课（201）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "locked": [{"course": "CS401", "semester": "2028-FALL"}],
  "unavailable_semesters": ["2026-FALL"]
}'; echo

step "24. 错误示例：锁定到不可排课学期（422 REQUIREMENT_CONFLICT，不保存）"
curl -s -w '\nHTTP %{http_code}\n' -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "locked": [{"course": "CS401", "semester": "2027-FALL"}],
  "unavailable_semesters": ["2027-FALL"]
}'

step "25. 错误示例：锁定学期该课不开课（CS401 仅 FALL 开课，422 REQUIREMENT_CONFLICT）"
curl -s -w '\nHTTP %{http_code}\n' -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "locked": [{"course": "CS401", "semester": "2027-SPRING"}]
}'

step "26. 错误示例：重复锁定 / 锁定已修课 / 锁定学期超出期限（400 INVALID_INPUT）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "locked": [{"course": "CS303", "semester": "2027-SPRING"},
             {"course": "CS303", "semester": "2027-FALL"}]
}'; echo
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": ["CS101", "CS101L"],
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "locked": [{"course": "CS101", "semester": "2026-SPRING"}]
}'; echo
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6,
  "locked": [{"course": "CS401", "semester": "2030-FALL"}]
}'; echo

step "27. 错误示例：锁定导致期限内无解（422 NO_SOLUTION，不保存）"
curl -s -w '\nHTTP %{http_code}\n' -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 4,
  "locked": [{"course": "MATH103", "semester": "2027-SPRING"}]
}'
