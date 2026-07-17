"""Нормализация сырых названий навыков → canonical."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

# Явные синонимы: raw_norm → canonical display name
ALIAS_MAP: dict[str, str] = {
    # Python
    "python": "Python",
    "питон": "Python",
    "работа с python": "Python",
    "python3": "Python",
    "python 3": "Python",
    "python 3.x": "Python",
    "py": "Python",
    # SQL / DB
    "sql": "SQL",
    "работа с sql": "SQL",
    "t-sql": "T-SQL",
    "tsql": "T-SQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "postgre": "PostgreSQL",
    "pgsql": "PostgreSQL",
    "mysql": "MySQL",
    "my sql": "MySQL",
    "mariadb": "MySQL",
    "ms sql": "MS SQL",
    "mssql": "MS SQL",
    "ms sql server": "MS SQL",
    "sql server": "MS SQL",
    "clickhouse": "ClickHouse",
    "greenplum": "Greenplum",
    "oracle": "Oracle",
    "mongodb": "MongoDB",
    "mongo": "MongoDB",
    "redis": "Redis",
    "cassandra": "Cassandra",
    "nosql": "NoSQL",
    "nosql бд": "NoSQL",
    "sqlite": "SQLite",
    "snowflake": "Snowflake",
    "bigquery": "BigQuery",
    "redshift": "Redshift",
    "vertica": "Vertica",
    "teradata": "Teradata",
    "db2": "DB2",
    # ML / DL
    "pytorch": "PyTorch",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "tf": "TensorFlow",
    "keras": "Keras",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "catboost": "CatBoost",
    "transformers": "Transformers",
    "huggingface": "Transformers",
    "huggingface transformers": "Transformers",
    "opencv": "OpenCV",
    "scipy": "SciPy",
    "statsmodels": "Statsmodels",
    # Data stack
    "pandas": "Pandas",
    "numpy": "NumPy",
    "polars": "Polars",
    "spark": "Apache Spark",
    "pyspark": "PySpark",
    "apache spark": "Apache Spark",
    "pyspark (sql-api)": "PySpark",
    "hadoop": "Hadoop",
    "hadoop-стек": "Hadoop",
    "hive": "Hive",
    "hdfs": "HDFS",
    "airflow": "Airflow",
    "apache airflow": "Airflow",
    "dbt": "dbt",
    "kafka": "Kafka",
    "apache kafka": "Kafka",
    "flink": "Flink",
    "trino": "Trino",
    "presto": "Presto",
    # Infra / MLOps
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "mlflow": "MLflow",
    "gitlab": "GitLab",
    "github": "GitHub",
    "git": "Git",
    "linux": "Linux",
    "bash": "Bash",
    "grafana": "Grafana",
    "prometheus": "Prometheus",
    "s3": "S3",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    # Web / API
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "sqlalchemy": "SQLAlchemy",
    "requests": "Requests",
    "api": "API",
    # Viz / BI
    "matplotlib": "Matplotlib",
    "seaborn": "Seaborn",
    "plotly": "Plotly",
    "tableau": "Tableau",
    "power bi": "Power BI",
    "powerbi": "Power BI",
    "bi-инструменты": "BI",
    "excel": "Excel",
    # LLM / NLP / CV
    "llm": "LLM",
    "large language models": "LLM",
    "langchain": "LangChain",
    "lang chain": "LangChain",
    "rag": "RAG",
    "ollama": "Ollama",
    "gpt": "GPT",
    "bert": "BERT",
    "mistral": "Mistral",
    "qwen": "Qwen",
    "nlp": "NLP",
    "natural language processing": "NLP",
    "computer vision": "Computer Vision",
    "cv": "Computer Vision",
    "deep learning": "Deep Learning",
    "machine learning": "Machine Learning",
    "машинное обучение": "Machine Learning",
    "ml": "Machine Learning",
    # Soft / other
    "a/b тестирование": "A/B testing",
    "a/b testing": "A/B testing",
    "ab testing": "A/B testing",
    "english": "English",
    "английский язык": "English",
    "английский": "English",
    "jupyterlab": "Jupyter",
    "jupyter": "Jupyter",
    "jupyter notebook": "Jupyter",
}

# Не навыки (SQL-конструкции, общие фразы, шум из парсинга)
STOPLIST: set[str] = {
    "и",
    "or",
    "and",
    "etc",
    "другие",
    "прочее",
    "другое",
    "прочие",
    "join",
    "joinы",
    "joins",
    "физические виды join",
    "виды join",
    "подзапросы",
    "подзапрос",
    "оконные функции",
    "оконная функция",
    "агрегатные функции",
    "агрегатная функция",
    "chatgpt",
    "промптинг",
    "кастомизация",
    "яндекс",
    "so",
    "open-source модели",
    "cte",
    "сte",
    "select",
    "where",
    "group by",
    "order by",
    "having",
    "yql",
    "pl",
    "ddl",
    "dml",
    "crud",
    "основные библиотеки анализа данных",
    "основные библиотеки",
    "математическая статистика",
    "теория вероятностей",
    "статистика",
    "аналитическое мышление",
    "коммуникация",
    "обработка обратной связи",
    "умение работать в команде",
    "работа в команде",
    "ответственность",
    "многозадачность",
}

NOISE_PREFIXES = (
    "работа с ",
    "опыт работы с ",
    "опыт работы ",
    "знание ",
    "навыки ",
    "умение ",
    "владение ",
    "хорошее знание ",
    "уверенное знание ",
)

NOISE_PATTERNS = (
    re.compile(r"^опыт\b"),
    re.compile(r"^знание\b"),
    re.compile(r"^\d+(\.\d+)?$"),
    re.compile(r"^v?\d+(\.\d+)*$"),
)

MIN_SKILL_LEN = 2
MAX_SKILL_LEN = 60
FUZZY_THRESHOLD = 90


def normalize_text(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = text.replace("ё", "е")
    # типографские кавычки/апострофы
    text = text.replace("’", "").replace("'", "").replace("`", "")
    text = re.sub(r"[«»\"]", "", text)
    text = re.sub(r"[\u200b\u00a0]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for prefix in NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    # python 3.11 / python3 → python 3 / python3 (alias map добьёт)
    text = re.sub(r"\bpython[ ]*3(\.\d+)*\b", "python 3", text)
    text = re.sub(r"[.\s]+$", "", text)
    return text


def is_noise(raw: str) -> bool:
    norm = normalize_text(raw)
    if len(norm) < MIN_SKILL_LEN or len(norm) > MAX_SKILL_LEN:
        return True
    if norm.isdigit():
        return True
    if norm in STOPLIST:
        return True
    for pat in NOISE_PATTERNS:
        if pat.search(norm):
            return True
    # слишком общие однословные RU-фразы
    if re.search(r"\bjoin\b", norm):
        return True
    if " " not in norm and re.fullmatch(r"[а-я\-]+", norm):
        # одно слово кириллицей — часто шум, кроме явных из alias
        if norm not in ALIAS_MAP and normalize_text(ALIAS_MAP.get(norm, "")) != norm:
            # разрешим, если есть в значениях alias как norm
            alias_norms = {normalize_text(v) for v in ALIAS_MAP.values()}
            if norm not in alias_norms and len(norm) < 4:
                return True
    return False


def canonical_display_name(norm: str) -> str:
    """Человекочитаемое имя для нового canonical."""
    if norm in ALIAS_MAP:
        return ALIAS_MAP[norm]
    if re.fullmatch(r"[a-z0-9+#.\- /]+", norm):
        known_upper = {
            "sql",
            "nlp",
            "llm",
            "aws",
            "gcp",
            "api",
            "etl",
            "bi",
            "rag",
            "gpt",
            "bert",
            "hdfs",
            "s3",
            "dbt",
        }
        parts = []
        for token in norm.split():
            if token in known_upper:
                parts.append(token.upper())
            else:
                parts.append(token.capitalize())
        return " ".join(parts)
    return raw_preserve_case_from_norm(norm)


def raw_preserve_case_from_norm(norm: str) -> str:
    return norm[:1].upper() + norm[1:] if norm else norm


@dataclass
class ResolveResult:
    canonical_name: str
    name_norm: str
    match_method: str  # exact|alias|fuzzy|new


class SkillNormalizer:
    """Строит mapping raw → canonical с alias + fuzzy."""

    def __init__(self, fuzzy_threshold: int = FUZZY_THRESHOLD) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self._canonicals: dict[str, str] = {}
        fixed: dict[str, str] = {}
        for alias_norm, display in ALIAS_MAP.items():
            c_norm = normalize_text(display)
            self._canonicals[c_norm] = display
            fixed[normalize_text(alias_norm)] = c_norm
        self._alias_to_norm = fixed

    @property
    def canonical_names(self) -> list[str]:
        return list(self._canonicals.values())

    def resolve(self, raw: str) -> ResolveResult | None:
        if is_noise(raw):
            return None
        norm = normalize_text(raw)

        if norm in STOPLIST:
            return None

        if norm in self._alias_to_norm:
            c_norm = self._alias_to_norm[norm]
            display = self._canonicals.get(c_norm, canonical_display_name(c_norm))
            self._canonicals.setdefault(c_norm, display)
            return ResolveResult(display, c_norm, "alias")

        if norm in self._canonicals:
            return ResolveResult(self._canonicals[norm], norm, "exact")

        if self._canonicals:
            match = process.extractOne(
                norm,
                list(self._canonicals.keys()),
                scorer=fuzz.token_sort_ratio,
            )
            if match and match[1] >= self.fuzzy_threshold:
                c_norm = match[0]
                return ResolveResult(self._canonicals[c_norm], c_norm, "fuzzy")

        display = canonical_display_name(norm)
        c_norm = normalize_text(display)
        if c_norm in STOPLIST or is_noise(display):
            return None
        self._canonicals[c_norm] = display
        self._alias_to_norm[norm] = c_norm
        return ResolveResult(display, c_norm, "new")

    def register_raw_as_alias(self, raw: str, canonical_norm: str) -> None:
        norm = normalize_text(raw)
        if norm and not is_noise(raw) and norm not in STOPLIST:
            self._alias_to_norm[norm] = canonical_norm
