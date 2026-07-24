"""
Поисковые запросы hh.ru для сегмента Data / Analytics / ML / AI / Agents / RnD.
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
    # Data Analytics
    SearchQuery("Data Analyst", "data_analytics"),
    SearchQuery("Аналитик данных", "data_analytics"),
    SearchQuery("Дата аналитик", "data_analytics"),
    SearchQuery("Data Analytics", "data_analytics"),
    SearchQuery("Junior Data Analyst", "data_analytics"),
    SearchQuery("Senior Data Analyst", "data_analytics"),
    SearchQuery("Аналитик DWH", "data_analytics"),
    SearchQuery("SQL аналитик", "data_analytics"),
    # Product / Marketing analytics
    SearchQuery("Product Analyst", "product_analytics"),
    SearchQuery("Продуктовый аналитик", "product_analytics"),
    SearchQuery("Product Analytics", "product_analytics"),
    SearchQuery("Маркетинговый аналитик", "product_analytics"),
    SearchQuery("Marketing Analyst", "product_analytics"),
    SearchQuery("Web Analyst", "product_analytics"),
    SearchQuery("Веб-аналитик", "product_analytics"),
    # BI
    SearchQuery("BI Analyst", "bi_analytics"),
    SearchQuery("BI-аналитик", "bi_analytics"),
    SearchQuery("BI аналитик", "bi_analytics"),
    SearchQuery("Business Intelligence", "bi_analytics"),
    SearchQuery("BI Developer", "bi_analytics"),
    SearchQuery("Power BI", "bi_analytics"),
    SearchQuery("Tableau", "bi_analytics"),
    # Business / Systems analyst (смежные)
    SearchQuery("Бизнес-аналитик", "business_analytics"),
    SearchQuery("Business Analyst", "business_analytics"),
    SearchQuery("Системный аналитик", "business_analytics"),
    SearchQuery("Systems Analyst", "business_analytics"),
    # Data Engineering
    SearchQuery("Data Engineer", "data_engineering"),
    SearchQuery("Дата инженер", "data_engineering"),
    SearchQuery("Инженер данных", "data_engineering"),
    SearchQuery("ETL Developer", "data_engineering"),
    SearchQuery("DWH Engineer", "data_engineering"),
    SearchQuery("Data Platform Engineer", "data_engineering"),
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
