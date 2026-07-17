"""Готовые сценарии вопросов к LLM-ассистенту."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    question: str
    intent: str  # gaps|train|courses|mobility|market|overview


SCENARIOS: list[Scenario] = [
    Scenario(
        id="gaps_ds",
        title="Дефицитные компетенции DS",
        question="Какие компетенции являются дефицитными для Data Science в компании относительно рынка?",
        intent="gaps",
    ),
    Scenario(
        id="train_whom",
        title="Кого обучить в первую очередь",
        question="Кого рекомендуется обучить в первую очередь по роли data_science и почему?",
        intent="train",
    ),
    Scenario(
        id="courses",
        title="Какие курсы запустить",
        question="Какие внутренние курсы лучше запустить, чтобы закрыть дефицит компетенций?",
        intent="courses",
    ),
    Scenario(
        id="replace",
        title="Кто может заменить / усилить команду",
        question="Кто из сотрудников других ролей может усилить направление Data Science (внутренняя мобильность)?",
        intent="mobility",
    ),
    Scenario(
        id="market_trends",
        title="Что растёт на рынке",
        question="Какие компетенции наиболее востребованы рынком и какие из них растут по тренду?",
        intent="market",
    ),
    Scenario(
        id="llm_direction",
        title="Перспективное направление LLM/Agents",
        question="Какие сотрудники лучше соответствуют перспективному направлению LLM и AI Agents?",
        intent="mobility",
    ),
    Scenario(
        id="mlops_gaps",
        title="Дефициты MLOps",
        question="Где самые критичные разрывы между рынком и компетенциями для MLOps?",
        intent="gaps",
    ),
    Scenario(
        id="dept_plan",
        title="План развития подразделения",
        question="Сформируй краткий план развития компетенций для роли data_science на ближайший квартал.",
        intent="overview",
    ),
    Scenario(
        id="spof_risks",
        title="SPOF и критичные навыки",
        question="Какие навыки являются single point of failure или критичными рисками для data_science?",
        intent="gaps",
    ),
]


def get_scenario(scenario_id: str) -> Scenario | None:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    return None


def detect_intent(question: str) -> str:
    q = question.lower()
    if any(x in q for x in ("дефицит", "не хвата", "разрыв", "gap", "spof", "критич", "риск")):
        return "gaps"
    if any(x in q for x in ("мобильн", "замен", "перевест", "соответств", "перспектив")):
        return "mobility"
    if any(x in q for x in ("кого", "обучить", "кандида")):
        return "train"
    if any(x in q for x in ("курс", "обучен", "программ")):
        return "courses"
    if any(x in q for x in ("тренд", "востребован", "рынк")):
        return "market"
    return "overview"
