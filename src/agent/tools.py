"""Tool-calling Agent.

Agent는 정형 Tool 결과만 설명하고 자체 숫자를 만들 수 없다.
Agent를 제거해도 전체 workflow가 완결되어야 한다.

Tool 목록 (이 9개 외 확장 금지)

답변 형식 고정
    [확인된 사실] / [모델 추정] / [불확실성] / [권고] / [근거]

평가지표
    unsupported-claim rate, tool grounding rate

TODO(P2): 왜 이 필지가 1순위인지를 데이터만으로 답변
"""

from __future__ import annotations

TOOLS = [
    "get_event_summary",
    "get_field_evidence",
    "get_satellite_observation",
    "get_forecast_risk",
    "get_impact_assessment",
    "get_shap_explanation",
    "run_resource_scenario",
    "get_policy_guidance",
    "generate_briefing",
]

ANSWER_SECTIONS = ["확인된 사실", "모델 추정", "불확실성", "권고", "근거"]
