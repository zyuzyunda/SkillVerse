"""
Поисковые запросы hh.ru для сегмента Data Science / ML / AI / Agents / RnD.
role_group используется для аналитики внутри платформы.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchQuery:
    text: str
    role_group: str
    search_field: str = "name"  # ищем в названии вакансии


# География фиксируется в settings (HH_AREA=113 — Россия).
SEARCH_QUERIES: list[SearchQuery] = [
    # Data Science
    SearchQuery("Data Scientist", "data_science"),
    SearchQuery("Дата-сайентист", "data_science"),
    SearchQuery("Дата сайентист", "data_science"),
    SearchQuery("Data Science", "data_science"),
    SearchQuery("DS Engineer", "data_science"),
    # ML Engineering
    SearchQuery("ML Engineer", "ml_engineering"),
    SearchQuery("Machine Learning Engineer", "ml_engineering"),
    SearchQuery("Инженер машинного обучения", "ml_engineering"),
    SearchQuery("ML разработчик", "ml_engineering"),
    SearchQuery("ML developer", "ml_engineering"),
    SearchQuery("ML инженер", "ml_engineering"),
    SearchQuery("MLE", "ml_engineering"),
    # AI / DL
    SearchQuery("AI Engineer", "ai_engineering"),
    SearchQuery("Deep Learning Engineer", "deep_learning"),
    SearchQuery("Deep Learning", "deep_learning"),
    # NLP / CV
    SearchQuery("NLP Engineer", "nlp"),
    SearchQuery("NLP", "nlp"),
    SearchQuery("Computer Vision Engineer", "computer_vision"),
    SearchQuery("CV Engineer", "computer_vision"),
    SearchQuery("Computer Vision", "computer_vision"),
    # LLM / Agents
    SearchQuery("LLM Engineer", "llm_agents"),
    SearchQuery("LLM", "llm_agents"),
    SearchQuery("Prompt Engineer", "llm_agents"),
    SearchQuery("AI Agent", "llm_agents"),
    SearchQuery("GenAI", "llm_agents"),
    SearchQuery("Generative AI", "llm_agents"),
    SearchQuery("LangChain", "llm_agents"),
    # MLOps
    SearchQuery("MLOps", "mlops"),
    SearchQuery("MLOps Engineer", "mlops"),
    SearchQuery("ML Platform", "mlops"),
    # RnD
    SearchQuery("Research Scientist", "rnd"),
    SearchQuery("AI Researcher", "rnd"),
    SearchQuery("Research Engineer", "rnd"),
    SearchQuery("R&D Engineer", "rnd"),
    SearchQuery("Исследователь искусственного интеллекта", "rnd"),
    SearchQuery("ML Researcher", "rnd"),
]
