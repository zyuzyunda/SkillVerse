"""Правильная таксономия навыков: skill → cluster (rule-based)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Порядок важен: первое совпадение побеждает
CLUSTER_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "LLM & Generative AI",
        (
            "llm",
            "langchain",
            "rag",
            "gpt",
            "bert",
            "mistral",
            "qwen",
            "ollama",
            "lora",
            "prompt",
            "transformers",
            "huggingface",
            "openai",
            "anthropic",
            "vllm",
            "llama",
            "gemini",
        ),
    ),
    (
        "Computer Vision",
        ("opencv", "computer vision", "yolo", "detectron", "segmentation", "cv2"),
    ),
    (
        "NLP",
        ("nlp", "spacy", "nltk", "text mining", "named entity", "tokeniz"),
    ),
    (
        "ML Frameworks",
        (
            "pytorch",
            "tensorflow",
            "keras",
            "jax",
            "mxnet",
            "scikit-learn",
            "sklearn",
            "xgboost",
            "lightgbm",
            "catboost",
            "mlflow",
        ),
    ),
    (
        "Classical ML & Stats",
        (
            "machine learning",
            "deep learning",
            "feature engineering",
            "scipy",
            "statsmodels",
            "shap",
            "lime",
            "a/b testing",
            "ab testing",
        ),
    ),
    (
        "Orchestration & Pipelines",
        ("airflow", "dbt", "luigi", "prefect", "dagster", "nifi", "kettle", "informatica"),
    ),
    (
        "Big Data",
        (
            "spark",
            "pyspark",
            "hadoop",
            "hive",
            "hdfs",
            "kafka",
            "flink",
            "trino",
            "presto",
            "yarn",
            "avro",
            "parquet",
            "mapreduce",
        ),
    ),
    (
        "Data Warehouses & Databases",
        (
            "postgresql",
            "mysql",
            "clickhouse",
            "greenplum",
            "oracle",
            "mongodb",
            "redis",
            "cassandra",
            "snowflake",
            "bigquery",
            "redshift",
            "vertica",
            "teradata",
            "db2",
            "sql",
            "ms sql",
            "t-sql",
            "nosql",
            "sqlite",
            "elasticsearch",
            "opensearch",
        ),
    ),
    (
        "Data Processing",
        ("pandas", "numpy", "polars", "dask", "etl", "data processing"),
    ),
    (
        "MLOps & Infra",
        (
            "docker",
            "kubernetes",
            "k8s",
            "linux",
            "bash",
            "ci",
            "cd",
            "gitlab",
            "github",
            "jenkins",
            "terraform",
            "ansible",
            "prometheus",
            "grafana",
            "s3",
            "mlops",
        ),
    ),
    (
        "Cloud",
        ("aws", "gcp", "azure", "yandex cloud", "cloud"),
    ),
    (
        "BI & Visualization",
        (
            "tableau",
            "power bi",
            "matplotlib",
            "seaborn",
            "plotly",
            "excel",
            "superset",
            "metabase",
            "qlik",
            "bi",
        ),
    ),
    (
        "Web & APIs",
        ("fastapi", "flask", "django", "sqlalchemy", "requests", "api", "rest", "graphql"),
    ),
    (
        "Programming Languages",
        ("python", "java", "scala", "r", "c++", "golang", "go", "typescript", "javascript", "sql"),
    ),
    (
        "Version Control & Collaboration",
        ("git", "jira", "confluence"),
    ),
    (
        "Languages & Soft Skills",
        ("english", "английский", "коммуникац", "команд"),
    ),
]

DEFAULT_CLUSTER = "Other"


@dataclass(frozen=True)
class ClusterInfo:
    name: str
    code: str


def cluster_code(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def assign_cluster(name_norm: str, display_name: str = "") -> str:
    blob = f"{name_norm} {display_name}".lower()
    for cluster, keywords in CLUSTER_RULES:
        for kw in keywords:
            if kw in blob:
                # особый случай: голое "sql" уже покрыто warehouses; python отдельно
                if cluster == "Programming Languages" and kw == "sql":
                    continue
                return cluster
    return DEFAULT_CLUSTER


def all_clusters() -> list[ClusterInfo]:
    names = [c for c, _ in CLUSTER_RULES] + [DEFAULT_CLUSTER]
    # unique preserve order
    seen: set[str] = set()
    out: list[ClusterInfo] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        out.append(ClusterInfo(name=name, code=cluster_code(name)))
    return out
