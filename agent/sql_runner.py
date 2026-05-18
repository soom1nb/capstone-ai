"""
=============================================================================
Text-to-SQL 실행 및 자동 복구 루프 (sql_runner.py)
=============================================================================
자연어 질문을 실제 SQL 쿼리로 변환하여 DB에서 실행하는 핵심 엔진입니다.
단순 1회성 실행에 그치지 않고, 오류나 낮은 품질의 결과를 스스로 교정합니다.

[동작 방식]
  1. 프롬프트 및 메타데이터(조인 힌트, 에러 기록)를 조합해 LLM에게 SQL 생성 요청
  2. DB에서 쿼리 실행
  3. 실행 결과가 나오면 `query_validator`를 호출하여 신뢰도 검증
  4. 검증 실패(오류 또는 낮은 신뢰도) 시 에러 기록을 누적하고 다시 1번으로 돌아가 재시도
=============================================================================
"""
import json
import time
from langchain_core.messages import SystemMessage, HumanMessage

from db import get_db, get_llm, get_filtered_schema_context, get_join_hints, get_config, get_stage_model
from prompts import SQL_GENERATION_PROMPT
from query_validator import validate_query_result


def run_text_to_sql(
    question: str,
    needed_tables: list[str],
    join_hint: str,
    max_retry: int | None = None,
) -> dict:
    """
    SQL을 생성하고 실행합니다.
    실행 후 검증 에이전트가 품질을 평가하고, 신뢰도가 낮으면 재시도합니다.
    """
    cfg = get_config()
    sql_cfg = cfg.get("sql", {})
    pipeline_cfg = cfg.get("pipeline", {})

    if max_retry is None:
        max_retry = sql_cfg.get("max_retry", 3)

    model_key = get_stage_model("sql_generation")
    llm = get_llm(model_key)
    db = get_db()
    filtered_schema = get_filtered_schema_context(needed_tables)
    yaml_hints = get_join_hints(needed_tables)
    error_history = []
    sql = ""

    for attempt in range(1, max_retry + 1):
        prompt = SQL_GENERATION_PROMPT.format(
            filtered_schema=filtered_schema,
            join_hint=join_hint,
            yaml_hints=yaml_hints,
            error_history=json.dumps(error_history, ensure_ascii=False, indent=2)
            if error_history else "없음",
            monthly_rent_min=pipeline_cfg.get("monthly_rent_min", 10),
            result_limit=sql_cfg.get("result_limit", 5),
        )

        t = time.time()
        raw = llm.invoke([
            SystemMessage(content=prompt),
            HumanMessage(content=question),
        ]).content.strip()

        sql = raw.replace("```sql", "").replace("```", "").strip()
        print(f"\n[SQL 시도 {attempt}/{max_retry}]\n{sql}")

        try:
            result = db.run(sql)
            elapsed = round(time.time() - t, 2)
            preview = result[:300] if result else "비어있음"
            print(f"[결과 ({elapsed}s)] {preview}")

            # ── 검증 에이전트 호출 ────────────────────────────────────────
            validation = validate_query_result(
                question=question,
                sql=sql,
                result=result,
            )

            print(f"[검증] 신뢰도: {validation.confidence:.1f} | 유효: {validation.is_valid}")
            if validation.issues:
                print(f"[검증] 문제: {', '.join(validation.issues)}")

            # 신뢰도 0.7 이상이면 통과
            if validation.is_valid and validation.confidence >= 0.7:
                return {
                    "sql": sql,
                    "result": result,
                    "attempts": attempt,
                    "confidence": validation.confidence,
                }

            # 신뢰도 낮으면 재시도
            error_history.append({
                "attempt": attempt,
                "sql": sql,
                "issues": validation.issues,
                "error": validation.retry_hint or "검증 실패 — 다른 접근으로 수정하세요",
            })

        except Exception as e:
            elapsed = round(time.time() - t, 2)
            print(f"[오류 ({elapsed}s)] {e}")
            error_history.append({
                "attempt": attempt,
                "sql": sql,
                "error": str(e),
            })

    # 최대 재시도 초과 — 마지막 결과라도 반환
    print(f"\n[SQL] {max_retry}회 시도 완료")
    return {
        "sql": sql,
        "result": result if "result" in dir() else "",
        "attempts": max_retry,
        "confidence": validation.confidence if "validation" in dir() else 0.0,
        "error": f"최대 재시도({max_retry}회) 초과",
        "error_history": error_history,
    }