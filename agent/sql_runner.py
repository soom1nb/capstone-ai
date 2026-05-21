"""
=============================================================================
Text-to-SQL 실행 및 자동 복구 루프 (sql_runner.py)
=============================================================================
"""
import json
import time
from langchain_core.messages import SystemMessage, HumanMessage

from db import get_db, get_llm, get_filtered_schema_context, get_join_hints, get_config, get_stage_model, get_store_codes_text
from prompts import SQL_GENERATION_PROMPT


def run_text_to_sql(
    question: str,
    needed_tables: list[str],
    join_hint: str,
    sql_plans: list = None,
    max_retry: int | None = None,
) -> dict:
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

    # sql_plans가 없으면 단일 SQL 실행
    if not sql_plans:
        return _run_single_sql(
            question, llm, db, filtered_schema,
            yaml_hints, join_hint, pipeline_cfg,
            sql_cfg, max_retry
        )

    # sql_plans가 있으면 각 계획마다 SQL 실행
    all_results = []
    for idx, plan in enumerate(sql_plans):
        print(f"\n[SQL 계획] {plan.get('description', '')}")
        result = _run_single_sql(
            question=question,
            llm=llm,
            db=db,
            filtered_schema=filtered_schema,
            yaml_hints=yaml_hints,
            join_hint=join_hint,
            pipeline_cfg=pipeline_cfg,
            sql_cfg=sql_cfg,
            max_retry=max_retry,
            extra_hint=plan.get("sql_hint", ""),
            plan_label=f"계획{idx+1}/{len(sql_plans)}",
        )
        all_results.append({
            "description": plan.get("description", ""),
            "sql": result.get("sql"),
            "result": result.get("result"),
            "attempts": result.get("attempts", 0),
            "confidence": result.get("confidence", 0),
        })

    combined_result = "\n\n---\n\n".join(
        f"[{r['description']}]\n{r['result']}"
        for r in all_results
        if r.get("result")
    )
    total_attempts = sum(r.get("attempts", 0) for r in all_results)

    return {
        "sql": [r["sql"] for r in all_results],
        "result": combined_result,
        "attempts": total_attempts,
        "confidence": min(r.get("confidence", 0) for r in all_results),
        "sql_details": all_results,
    }


def _run_single_sql(
    question, llm, db, filtered_schema,
    yaml_hints, join_hint, pipeline_cfg,
    sql_cfg, max_retry, extra_hint="", plan_label=""
) -> dict:
    error_history = []
    sql = ""
    result = ""

    for attempt in range(1, max_retry + 1):
        prompt = SQL_GENERATION_PROMPT.format(
            filtered_schema=filtered_schema,
            join_hint=join_hint,
            yaml_hints=yaml_hints,
            error_history=json.dumps(error_history, ensure_ascii=False, indent=2)
            if error_history else "없음",
            monthly_rent_min=pipeline_cfg.get("monthly_rent_min", 10),
            result_limit=sql_cfg.get("result_limit", 5),
            store_codes=get_store_codes_text(),
            extra_hint=extra_hint,
        )

        t = time.time()
        raw = llm.invoke([
            SystemMessage(content=prompt),
            HumanMessage(content=question),
        ]).content.strip()

        sql = raw.replace("```sql", "").replace("```", "").strip()
        label = f"{plan_label} " if plan_label else ""
        print(f"\n[SQL {label}시도 {attempt}/{max_retry}]\n{sql}")

        try:
            result = db.run(sql)
            elapsed = round(time.time() - t, 2)
            # 결과가 너무 길면 자르기
            if result and len(result) > 3000:
                result = result[:3000] + "\n... (결과 일부 생략)"
            preview = result[:300] if result else "비어있음"
            print(f"[결과 ({elapsed}s)] {preview}")

            # 결과 있으면 바로 반환
            if result and result.strip() not in ("", "[]"):
                return {
                    "sql": sql,
                    "result": result,
                    "attempts": attempt,
                    "confidence": 1.0,
                }

            # 결과 비어있으면 재시도
            error_history.append({
                "attempt": attempt,
                "sql": sql,
                "error": "결과 없음 — 조인 경로 또는 조건 재검토 필요",
            })

        except Exception as e:
            elapsed = round(time.time() - t, 2)
            print(f"[오류 ({elapsed}s)] {e}")
            error_history.append({
                "attempt": attempt,
                "sql": sql,
                "error": str(e),
            })

    # 최대 재시도 초과
    print(f"\n[SQL] {max_retry}회 시도 완료")
    return {
        "sql": sql,
        "result": result,
        "attempts": max_retry,
        "confidence": 0.0,
    }