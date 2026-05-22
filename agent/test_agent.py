"""
=============================================================================
AI 에이전트 테스트 (test_agent.py)
=============================================================================
테스트 케이스를 실행하고 결과를 JSON 파일로 저장합니다.

[실행 방법]
  python test_agent.py                # 전체 테스트 실행
  python test_agent.py --case 0       # 특정 케이스만 실행 (인덱스 기준)
  python test_agent.py --output result.json  # 저장 파일명 지정
=============================================================================
"""

import argparse
import json
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from agent import run_agent

# ── 테스트 케이스 ─────────────────────────────────────────────────────────────
TEST_CASES = [
    # ── direct (DB 조회 불필요) ───────────────────────────────────────────────
    {
        "id": "direct_01",
        "category": "direct",
        "question": "안녕하세요",
        "expected_route": "direct",
        "expected_query_type": "none",
        "description": "인사말 — DB 조회 없이 바로 답변",
    },
    {
        "id": "direct_02",
        "category": "direct",
        "question": "자취할 때 필요한 물품 뭐가 있어?",
        "expected_route": "direct",
        "expected_query_type": "none",
        "description": "자취 관련 일반 상식 — DB 조회 없이 바로 답변",
    },

    # ── recommendation (동네 추천) ────────────────────────────────────────────
    {
        "id": "rec_01",
        "category": "recommendation",
        "question": "동국대 근처 월세 저렴하고 카페 많은 동네 찾아줘",
        "expected_route": "db",
        "expected_query_type": "recommendation",
        "description": "복합 조건 — 월세 + 카페 (차트 2개 기대)",
    },
    {
        "id": "rec_02",
        "category": "recommendation",
        "question": "2호선 역 근처에서 월세 저렴하고 버스 노선 많은 동네 찾아줘",
        "expected_route": "db",
        "expected_query_type": "recommendation",
        "description": "복합 조건 — 지하철 + 월세 + 버스",
    },


    # ── info (정보 조회) ──────────────────────────────────────────────────────
    {
        "id": "info_01",
        "category": "info",
        "question": "동국대 근처 도서관 운영시간 알려줘",
        "expected_route": "db",
        "expected_query_type": "info",
        "description": "도서관 운영시간 조회",
    },
    {
        "id": "info_03",
        "category": "info",
        "question": "동국대 근처 편의점 알려줘",
        "expected_route": "db",
        "expected_query_type": "info",
        "description": "편의점 목록 조회",
    },

    # ── blocked ───────────────────────────────────────────────────────────────────
    {
        "id": "blocked_01",
        "category": "blocked",
        "question": "집 주소 알려줘",
        "expected_route": "blocked",
        "expected_query_type": "none",
        "description": "개인정보 요청 — 차단",
    },
    {
        "id": "blocked_02",
        "category": "blocked",
        "question": "오늘 날씨 어때?",
        "expected_route": "blocked",
        "expected_query_type": "none",
        "description": "서비스 무관 질문 — 차단",
    },
]


# ── 결과 검증 ─────────────────────────────────────────────────────────────────
def validate_result(case: dict, result: dict) -> dict:
    """테스트 결과를 검증하고 pass/fail 판단"""
    checks = {}

    # route 검증
    checks["route"] = result.get("route") == case["expected_route"]

    # query_type 검증
    checks["query_type"] = result.get("query_type") == case["expected_query_type"]

    # answer 있는지 검증
    checks["has_answer"] = bool(result.get("answer", "").strip())

    # recommendation이면 neighborhoods 검증
    if case["expected_query_type"] == "recommendation":
        neighborhoods = result.get("neighborhoods", [])
        checks["has_neighborhoods"] = len(neighborhoods) > 0
        checks["neighborhoods_count"] = len(neighborhoods) <= 2

    # db 조회면 visualizations 검증
    if case["expected_route"] == "db":
        visualizations = result.get("visualizations", [])
        checks["visualizations_is_list"] = isinstance(visualizations, list)

    passed = all(checks.values())
    return {"passed": passed, "checks": checks}


# ── 테스트 실행 ───────────────────────────────────────────────────────────────
def run_tests(cases: list, output_file: str):
    print(f"\n{'='*60}")
    print(f"AI 에이전트 테스트 시작")
    print(f"총 {len(cases)}개 케이스")
    print(f"{'='*60}\n")

    results = []
    passed_count = 0
    failed_count = 0
    total_start = time.time()

    for i, case in enumerate(cases):
        print(f"[{i+1}/{len(cases)}] {case['id']} — {case['description']}")
        print(f"  질문: {case['question']}")

        try:
            result = run_agent(case["question"])
            validation = validate_result(case, result)

            status = "✓ PASS" if validation["passed"] else "✗ FAIL"
            print(f"  결과: {status}")
            print(f"  route: {result.get('route')} (기대: {case['expected_route']})")
            print(f"  query_type: {result.get('query_type')} (기대: {case['expected_query_type']})")
            print(f"  소요시간: {result.get('elapsed_sec')}초")

            if not validation["passed"]:
                failed_checks = [k for k, v in validation["checks"].items() if not v]
                print(f"  실패 항목: {failed_checks}")

            if validation["passed"]:
                passed_count += 1
            else:
                failed_count += 1

            results.append({
                "id": case["id"],
                "category": case["category"],
                "description": case["description"],
                "question": case["question"],
                "status": "PASS" if validation["passed"] else "FAIL",
                "validation": validation,
                "result": {
                    "answer": result.get("answer", ""),
                    "route": result.get("route"),
                    "query_type": result.get("query_type"),
                    "neighborhoods": result.get("neighborhoods", []),
                    "visualizations": result.get("visualizations", []),
                    "elapsed_sec": result.get("elapsed_sec"),
                    "sql_attempts": result.get("sql_attempts", 0),
                },
            })

        except Exception as e:
            print(f"  결과: ✗ ERROR — {e}")
            failed_count += 1
            results.append({
                "id": case["id"],
                "category": case["category"],
                "description": case["description"],
                "question": case["question"],
                "status": "ERROR",
                "error": str(e),
                "result": None,
            })

        print()

    total_elapsed = round(time.time() - total_start, 2)

    # 요약
    print(f"{'='*60}")
    print(f"테스트 완료")
    print(f"  통과: {passed_count}/{len(cases)}")
    print(f"  실패: {failed_count}/{len(cases)}")
    print(f"  총 소요시간: {total_elapsed}초")
    print(f"{'='*60}\n")

    # JSON 저장
    output = {
        "meta": {
            "timestamp": datetime.now().isoformat(),
            "total": len(cases),
            "passed": passed_count,
            "failed": failed_count,
            "total_elapsed_sec": total_elapsed,
        },
        "results": results,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"결과 저장 완료: {output_file}")
    return output


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI 에이전트 테스트")
    parser.add_argument(
        "--case",
        type=int,
        default=None,
        help="특정 케이스 인덱스만 실행 (0부터 시작)",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        choices=["direct", "recommendation", "info", "blocked"],
        help="특정 카테고리만 실행",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=f"test_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        help="결과 저장 파일명",
    )
    args = parser.parse_args()

    # 케이스 필터링
    cases = TEST_CASES
    if args.case is not None:
        cases = [TEST_CASES[args.case]]
    elif args.category:
        cases = [c for c in TEST_CASES if c["category"] == args.category]

    run_tests(cases, args.output)