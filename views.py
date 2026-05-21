"""
=============================================================================
Django REST Framework API 엔드포인트 (views.py)
=============================================================================
작성된 AI Agent 파이프라인을 외부 프론트엔드(Web/App)에서 HTTP 요청으로 
호출할 수 있도록 연결해 주는 뷰 컨트롤러입니다.

[설치 및 설정]
  1. 이 파일들을 backend/apps/ai_agent/ 디렉토리에 복사
  2. urls.py에 경로 추가 (예: path("api/agent/query", agent_views.agent_query))
  3. settings.py의 INSTALLED_APPS에 "apps.ai_agent" 등록

[API 명세]
  - POST /api/agent/query
  - Request: {"question": "동국대 근처 월세 저렴한 곳"}
  - Response: Agent 분석 결과를 담은 JSON 페이로드 반환
=============================================================================
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from .agent import run_agent


@api_view(["POST"])
@permission_classes([AllowAny])
def agent_query(request):
    """
    POST /api/agent/query

    요청:
      { "question": "동국대 근처 월세 저렴한 동네 찾아줘" }

    응답 (recommendation):
      {
        "answer": "자연어 답변",
        "query_type": "recommendation",
        "route": "db",
        "neighborhoods": [
          {
            "rank": 1,
            "ldong_name": "장충동2가",
            "gu_name": "중구",
            "one_liner": "평균 월세 49만원 — 서울 평균 대비 25% 저렴",
            "data_summary": "평균월세 49만원"
          }
        ],
        "visualizations": [
          {
            "type": "bar",
            "title": "동네별 평균 월세 비교",
            "unit": "만원",
            "data": [
              {"label": "장충동2가", "value": 49.0, "is_baseline": false},
              {"label": "서울 평균", "value": 65.0, "is_baseline": true}
            ]
          }
        ],
        "elapsed_sec": 23.9
      }

    응답 (info):
      {
        "answer": "자연어 답변",
        "query_type": "info",
        "route": "db",
        "neighborhoods": [],
        "visualizations": [
          {
            "type": "table",
            "title": "동국대 근처 도서관 운영시간",
            "unit": "",
            "data": [
              {
                "label": "필동작은도서관",
                "columns": {"월": "10:00-21:00", "토": "10:00-14:00"}
              }
            ]
          }
        ],
        "elapsed_sec": 36.9
      }

    응답 (direct):
      {
        "answer": "안녕하세요! 서울 자취/동네 추천 서비스입니다.",
        "query_type": "none",
        "route": "direct",
        "neighborhoods": [],
        "visualizations": [],
        "elapsed_sec": 3.4
      }
    """
    question = request.data.get("question", "").strip()

    if not question:
        return Response(
            {"error": "질문을 입력해주세요."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(question) > 500:
        return Response(
            {"error": "질문이 너무 깁니다. 500자 이하로 입력해주세요."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = run_agent(question)

        # info 타입은 visualization 단수로 반환되므로 visualizations 배열로 통일
        visualizations = result.get("visualizations", [])
        if not visualizations:
            viz = result.get("visualization", {})
            if viz and viz.get("type", "none") != "none":
                visualizations = [viz]

        return Response({
            "answer": result.get("answer", ""),
            "query_type": result.get("query_type", "none"),
            "route": result.get("route", "direct"),
            "neighborhoods": result.get("neighborhoods", []),
            "visualizations": visualizations,
            "elapsed_sec": result.get("elapsed_sec", 0),
        })

    except Exception as e:
        return Response(
            {"error": f"Agent 실행 중 오류가 발생했습니다: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )