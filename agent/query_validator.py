"""
=============================================================================
SQL 실행 결과 품질 검증 에이전트 (query_validator.py)
=============================================================================
SQL 쿼리가 성공적으로 실행되었더라도, 논리적인 오류나 비정상 데이터가 포함되어
있는지 확인하여 답변의 품질을 보장합니다.

[검증 프로세스]
  1. 1차 검증(Rule-based): 빈 결과, 너무 적은 후보(1건), 비정상 월세값(예: 1만원) 등 빠른 필터링
  2. 2차 검증(LLM-based): 프롬프트를 통해 신뢰도 점수(0.0~1.0) 평가 및 문제점 도출
  3. 재시도 피드백: 신뢰도가 낮을 경우 구체적인 재시도 힌트(retry_hint)를 생성하여 sql_runner에 전달
=============================================================================
"""

from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from db import get_llm, get_config, get_stage_model


# ── 검증 결과 스키마 ──────────────────────────────────────────────────────────
class ValidationResult(BaseModel):
    is_valid: bool = Field(
        description="결과가 신뢰할 수 있으면 True, 재시도가 필요하면 False"
    )
    confidence: float = Field(
        description="신뢰도 점수 0.0~1.0. 0.7 미만이면 재시도 권장"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="감지된 문제 목록. 예: ['월세 1만원 비정상값 포함', '결과 1건으로 너무 적음']"
    )
    retry_hint: str = Field(
        default="",
        description="재시도 시 SQL을 어떻게 수정해야 하는지 구체적인 힌트"
    )


VALIDATOR_PROMPT = """
당신은 SQL 쿼리 결과를 검증하는 전문가입니다.
아래 정보를 보고 결과의 신뢰도를 평가하세요.

[검증 기준]

결과가 비어있는 경우 (is_valid=False):
- 조인 경로가 잘못됐을 가능성 분석
- adong_code와 ldong_code 혼용 여부 확인
- LIKE 조건이 너무 좁은지 확인

결과가 있는 경우 이상값 감지:
- 월세: 10만원 미만이면 비정상 (monthly_rent < 10)
- 월세: 500만원 초과면 비정상
- 결과 건수가 1건이면 의심 (더 많은 후보가 있을 수 있음)
- 동네 이름 없이 구 이름만 반환된 경우
- 같은 동네가 중복으로 여러 번 반환된 경우

신뢰도 기준:
- 1.0: 결과 정상, 이상값 없음, 건수 충분
- 0.8: 결과 있으나 건수 부족 (1~2건)
- 0.5: 이상값 일부 포함
- 0.0: 결과 비어있음 또는 심각한 이상값

[입력 정보]
질문: {question}
실행된 SQL: {sql}
실행 결과: {result}
결과 건수: {result_count}
"""


def validate_query_result(
    question: str,
    sql: str,
    result: str,
) -> ValidationResult:
    """
    SQL 실행 결과를 검증합니다.

    Args:
        question: 사용자 원본 질문
        sql: 실행된 SQL
        result: DB 실행 결과 문자열

    Returns:
        ValidationResult: 신뢰도 점수 및 문제점/재시도 힌트 포함
    """
    cfg = get_config()
    pipeline_cfg = cfg.get("pipeline", {})
    monthly_rent_min = pipeline_cfg.get("monthly_rent_min", 10)

    # 결과 건수 계산
    result_count = 0
    if result and result.strip() not in ("", "[]"):
        try:
            parsed = eval(result)
            if isinstance(parsed, list):
                result_count = len(parsed)
        except Exception:
            result_count = 1 if result else 0

    # 빈 결과는 바로 반환 (LLM 호출 불필요)
    if not result or result.strip() in ("", "[]"):
        return ValidationResult(
            is_valid=False,
            confidence=0.0,
            issues=["결과 없음 — 조인 경로 또는 조건 재검토 필요"],
            retry_hint=(
                "다음을 확인하세요:\n"
                "1. adong_code와 ldong_code를 혼용하지 않았는지\n"
                "2. LIKE 조건이 너무 좁지 않은지 (LIKE '%동국대%' 형식 사용)\n"
                "3. adjacent_adong 결과를 rent_deal과 직접 조인하지 않았는지\n"
                "4. 대학 근처 월세는 univ_ldong → adjacent_ldong → rent_deal 경로 사용"
            ),
        )

    # 빠른 규칙 기반 검증 (LLM 호출 전)
    quick_issues = []

    if "monthly_rent" in sql.lower() or "월세" in question:
        try:
            parsed = eval(result)
            if isinstance(parsed, list):
                for row in parsed:
                    for val in row:
                        try:
                            v = float(str(val))
                            if 0 < v < monthly_rent_min:
                                quick_issues.append(f"비정상 월세값 감지: {v}만원")
                            if v > 500:
                                quick_issues.append(f"비정상 월세값 감지: {v}만원 (500만원 초과)")
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass

    if result_count == 1:
        quick_issues.append("결과 1건 — 후보가 너무 적음, LIMIT을 늘리거나 조건 완화 고려")

    # 빠른 검증에서 심각한 문제 발견 시 LLM 없이 반환
    if any("비정상 월세값" in i for i in quick_issues):
        return ValidationResult(
            is_valid=False,
            confidence=0.3,
            issues=quick_issues,
            retry_hint=f"monthly_rent > {monthly_rent_min} AND monthly_rent < 500 조건 추가 후 재시도",
        )

    # LLM 기반 정밀 검증
    llm = get_llm(get_stage_model("classification"))
    try:
        validation = llm.with_structured_output(
            ValidationResult, method="function_calling"
        ).invoke([
            SystemMessage(content=VALIDATOR_PROMPT.format(
                question=question,
                sql=sql,
                result=result[:500],  # 너무 길면 잘라서 전달
                result_count=result_count,
            )),
            HumanMessage(content="위 결과를 검증해주세요."),
        ])

        # 빠른 검증 결과와 병합
        if quick_issues:
            validation.issues = quick_issues + validation.issues
            validation.confidence = min(validation.confidence, 0.7)

        return validation

    except Exception as e:
        # LLM 검증 실패 시 결과는 있으므로 기본 통과
        return ValidationResult(
            is_valid=True,
            confidence=0.6,
            issues=[f"검증 에이전트 오류 (결과는 존재): {str(e)}"],
            retry_hint="",
        )