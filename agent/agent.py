"""
=============================================================================
슬기로운 자취생활 - AI Agent 메인 파이프라인 (agent.py)
=============================================================================
이 모듈은 사용자 질문을 받아 DB 조회를 통해 답변을 생성하는 전체 과정을 조율합니다.

[핵심 파이프라인]
  1. 질문 분류 (Classification): 질문의 의도를 파악하고 필요한 테이블과 조인 힌트를 추출합니다.
  2. SQL 실행 (Text-to-SQL): LLM을 활용해 쿼리를 생성하고, DB에서 실행 및 품질을 검증합니다.
  3. 응답 생성 (Selection/Info): 조회된 데이터를 기반으로 사용자 맞춤형 자연어 답변과 시각화 데이터를 구성합니다.

[실행 방법]
  - 패키지 설치: pip install langchain langchain-openai langchain-community sqlalchemy psycopg2-binary python-dotenv pyyaml
  - 터미널 환경: python agent.py "질문 내용"
  - 대화형 모드: python agent.py 실행 후 프롬프트에 질문 입력
=============================================================================
"""

import json
import time
import argparse
from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import SystemMessage, HumanMessage

from db import get_db, get_llm, get_schema_context, get_config, get_stage_model, get_store_codes_text
from schemas import ClassificationOutput, SelectionOutput
from sql_runner import run_text_to_sql
from prompts import CLASSIFICATION_PROMPT, SELECTION_PROMPT, INFO_ANSWER_PROMPT


def run_agent(question: str) -> dict:
    """
    파이프라인:
      1단계: 질문 분류  — route / query_type / needed_tables / join_hint
      2단계: SQL 실행   — Text-to-SQL + YAML 힌트 + 재시도
      3단계: 응답 생성  — query_type별 분기 (config.yaml의 query_types 기반)
    """
    start = time.time()
    cfg = get_config()
    pipeline_cfg = cfg.get("pipeline", {})
    max_neighborhoods = pipeline_cfg.get("max_neighborhoods", 2)

    print(f"\n{'='*60}")
    print(f"질문: {question}")
    print(f"{'='*60}")

    schema_context = get_schema_context()

    # ── 1단계: 질문 분류 ──────────────────────────────────────────────────────
    print("\n[1단계] 질문 분류 중...")
    llm_cls = get_llm(get_stage_model("classification"))
    classification = llm_cls.with_structured_output(
        ClassificationOutput, method="function_calling"
    ).invoke([
        SystemMessage(content=CLASSIFICATION_PROMPT.format(
            schema_context=schema_context,
            store_codes=get_store_codes_text(),   # config.yaml에서 동적 주입
        )),
        HumanMessage(content=question),
    ])

    print(f"  route:         {classification.route}")
    print(f"  query_type:    {classification.query_type}")
    print(f"  needed_tables: {classification.needed_tables}")
    print(f"  join_hint:     {classification.join_hint}")

    if classification.route in {"direct", "blocked"}:
        return {
            "answer": classification.message,
            "neighborhoods": [],
            "visualization": {"type": "none"},
            "route": classification.route,
            "query_type": classification.query_type,
            "sql": None,
            "elapsed_sec": round(time.time() - start, 2),
        }

    # ── 2단계: SQL 실행 ───────────────────────────────────────────────────────
    print("\n[2단계] SQL 생성 및 실행 중...")
    sql_result = run_text_to_sql(
        question=question,
        needed_tables=classification.needed_tables,
        join_hint=classification.join_hint,
    )

    # ── 3단계: query_type별 분기 ──────────────────────────────────────────────
    query_types_cfg = cfg.get("query_types", {})
    qt_cfg = query_types_cfg.get(classification.query_type, {})
    steps = qt_cfg.get("steps", [])

    if "info_answer" in steps:
        print("\n[3단계] 정보 조회 답변 생성 중...")
        llm_info = get_llm(get_stage_model("info_answer"))
        answer_obj = llm_info.invoke([
            SystemMessage(content=INFO_ANSWER_PROMPT),
            HumanMessage(content=f"질문: {question}\nSQL 결과: {sql_result['result'] or '조회 결과 없음'}"),
        ])
        elapsed = round(time.time() - start, 2)
        print(f"\n[최종 답변]\n{answer_obj.content}")
        print(f"\n총 소요시간: {elapsed}초 | SQL 시도: {sql_result.get('attempts', 0)}회")

        return {
            "answer": answer_obj.content,
            "neighborhoods": [],
            "visualization": {"type": "table"},
            "route": "db",
            "query_type": classification.query_type,
            "sql": sql_result.get("sql"),
            "sql_attempts": sql_result.get("attempts", 0),
            "elapsed_sec": elapsed,
        }

    if "selection" in steps:
        print("\n[3단계] 동네 선정 중...")
        llm_sel = get_llm(get_stage_model("selection"))

        selection = llm_sel.with_structured_output(
            SelectionOutput, method="function_calling"
        ).invoke([
            SystemMessage(content=SELECTION_PROMPT.format(
                question=question,
                sql_result=sql_result["result"] or "조회 결과 없음",
                max_neighborhoods=max_neighborhoods,
            )),
            HumanMessage(content=question),
        ])

        # 보강 쿼리 실행
        if selection.additional_sql:
            print(f"\n[보강 쿼리]\n{selection.additional_sql}")
            try:
                db = get_db()
                extra = db.run(selection.additional_sql)
                print(f"[보강 결과] {extra[:200] if extra else '비어있음'}")

                if extra and extra.strip() not in ("", "[]"):
                    enriched = (
                        f"{sql_result['result']}"
                        f"\n\n[보강 데이터 — 비교 기준]\n"
                        f"SQL: {selection.additional_sql}\n"
                        f"결과: {extra}\n"
                        f"※ 한줄평, data_summary, visualization_data에 반드시 반영하세요"
                    )
                    selection = llm_sel.with_structured_output(
                        SelectionOutput, method="function_calling"
                    ).invoke([
                        SystemMessage(content=SELECTION_PROMPT.format(
                            question=question,
                            sql_result=enriched,
                            max_neighborhoods=max_neighborhoods,
                        )),
                        HumanMessage(content=question),
                    ])
            except Exception as e:
                print(f"[보강 쿼리 실패] {e}")

        elapsed = round(time.time() - start, 2)
        print(f"\n[최종 답변]\n{selection.answer}")
        print(f"\n총 소요시간: {elapsed}초 | SQL 시도: {sql_result.get('attempts', 0)}회")

        return {
            "answer": selection.answer,
            "neighborhoods": [n.model_dump() for n in selection.neighborhoods],
            "visualization": {
                "type": selection.visualization_type,
                "title": selection.visualization_title,
                "unit": selection.visualization_unit,
                "data": [d.model_dump() for d in selection.visualization_data],
                "reason": selection.visualization_reason,
            },
            "route": "db",
            "query_type": classification.query_type,
            "sql": sql_result.get("sql"),
            "sql_attempts": sql_result.get("attempts", 0),
            "elapsed_sec": elapsed,
        }

    # steps가 비어있는 경우 (direct/blocked가 아닌데 query_type 매핑 없는 경우)
    elapsed = round(time.time() - start, 2)
    return {
        "answer": "처리할 수 없는 요청입니다.",
        "neighborhoods": [],
        "visualization": {"type": "none"},
        "route": classification.route,
        "query_type": classification.query_type,
        "sql": None,
        "elapsed_sec": elapsed,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="슬기로운 자취생활 AI Agent")
    parser.add_argument("question", nargs="?", default=None)
    args = parser.parse_args()

    if args.question:
        result = run_agent(args.question)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("슬기로운 자취생활 AI Agent")
        print("종료: Ctrl+C\n")
        while True:
            try:
                q = input("질문: ").strip()
                if q:
                    result = run_agent(q)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
            except KeyboardInterrupt:
                print("\n종료합니다.")
                break