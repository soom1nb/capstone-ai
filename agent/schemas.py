"""
=============================================================================
Pydantic 구조화 출력 스키마 정의 (schemas.py)
=============================================================================
LLM의 응답을 단순한 텍스트가 아닌, 시스템에서 처리할 수 있는 JSON 형태의
객체로 강제하기 위한 데이터 구조(Schema)를 정의합니다.

[주요 스키마]
  - ClassificationOutput: 1단계 의도 파악 결과 (라우팅, 쿼리 타입, 필요 테이블 등)
  - SelectionOutput: 3단계 동네 추천 결과 및 시각화(차트/지도) 데이터 셋
  - NeighborhoodItem, VisualizationData: UI에서 렌더링하기 편하도록 분리된 하위 모델
=============================================================================
"""
from pydantic import BaseModel, Field
from typing import Optional


# ── 1단계: 질문 분류 출력 ────────────────────────────────────────────────────
class ClassificationOutput(BaseModel):
    route: str = Field(
        description="'db'(DB 조회 가능) | 'direct'(DB 불필요, 바로 답변) | 'blocked'(제공 불가)"
    )
    message: str = Field(
        description="route가 direct/blocked일 때 사용자에게 바로 보여줄 답변"
    )
    query_type: str = Field(
        description=(
            "'recommendation': 동네 추천이 목적인 질문 | "
            "'info': 특정 시설/정보 조회가 목적인 질문 | "
            "'none': route가 direct/blocked인 경우"
        )
    )
    needed_tables: list[str] = Field(
        description="질문에 필요한 테이블 목록"
    )
    join_hint: str = Field(
        description="올바른 조인 경로 힌트"
    )


# ── 3단계: 시각화 데이터 ─────────────────────────────────────────────────────
class VisualizationData(BaseModel):
    label: str = Field(description="항목 레이블. 예: '장충동2가', '서울 평균'")
    value: float = Field(description="수치 값")
    is_baseline: bool = Field(default=False, description="True면 비교 기준선으로 표시")


# ── 3단계 (recommendation): 동네 선정 출력 ──────────────────────────────────
class NeighborhoodItem(BaseModel):
    rank: int = Field(description="추천 순위 (1 또는 2)")
    ldong_name: str = Field(description="법정동 또는 행정동 이름. 예: 장충동2가")
    gu_name: str = Field(description="자치구 이름. 예: 중구")
    one_liner: str = Field(description="수치 데이터 기반 동네 고유 특징. 두 동네에 동일 표현 금지")
    data_summary: str = Field(description="조회된 핵심 데이터 요약. 예: 평균월세 49만원 (서울 평균 65만원)")


class SelectionOutput(BaseModel):
    neighborhoods: list[NeighborhoodItem] = Field(
        description="조건을 만족하는 상위 동네 최대 2곳"
    )
    additional_sql: Optional[str] = Field(
        default=None,
        description="비교 기준(서울 평균 등) 보강이 필요할 때 추가로 실행할 SQL. 반드시 실제 존재하는 테이블만 사용"
    )
    visualization_type: str = Field(
        description="'map'(위치 기반) | 'bar'(수치 비교) | 'table'(목록/시간표) | 'none'"
    )
    visualization_title: str = Field(
        default="",
        description="시각화 제목. 예: '동국대 근처 동네별 평균 월세 비교'"
    )
    visualization_unit: str = Field(
        default="",
        description="수치 단위. 예: '만원', '개', '%'"
    )
    visualization_data: list[VisualizationData] = Field(
        default_factory=list,
        description="차트 데이터. 추천 동네 수치 + 보강 데이터(is_baseline=true) 포함"
    )
    visualization_reason: str = Field(description="이 시각화 형태를 선택한 이유")
    answer: str = Field(description="사용자에게 보여줄 최종 자연어 답변. 한국어로 작성")