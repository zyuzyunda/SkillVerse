"""Синтетическая оргструктура и сотрудники с контролируемыми дефицитами навыков."""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Course,
    CourseSkill,
    Department,
    Employee,
    EmployeeSkill,
    GraphEdge,
    Position,
    SkillCanonical,
)
from src.db.session import Base, SessionLocal, engine

SEED = 42

DEPARTMENTS = [
    ("DS", "Отдел Data Science", None),
    ("MLPLAT", "ML Platform / MLOps", None),
    ("AIRESEARCH", "AI Research & Agents", None),
    ("ANALYTICS", "Продуктовая аналитика", None),
    ("LND", "Обучение и развитие", None),
]

POSITIONS = [
    # code, title, role_group, level, dept_code, headcount
    ("DS_JUN", "Junior Data Scientist", "data_science", "junior", "DS", 8),
    ("DS_MID", "Data Scientist", "data_science", "middle", "DS", 14),
    ("DS_SEN", "Senior Data Scientist", "data_science", "senior", "DS", 8),
    ("DS_LEAD", "Lead Data Scientist", "data_science", "lead", "DS", 2),
    ("ML_MID", "ML Engineer", "ml_engineering", "middle", "MLPLAT", 10),
    ("ML_SEN", "Senior ML Engineer", "ml_engineering", "senior", "MLPLAT", 6),
    ("MLOPS_MID", "MLOps Engineer", "mlops", "middle", "MLPLAT", 5),
    ("MLOPS_SEN", "Senior MLOps Engineer", "mlops", "senior", "MLPLAT", 3),
    ("LLM_MID", "LLM / AI Engineer", "llm_agents", "middle", "AIRESEARCH", 6),
    ("LLM_SEN", "Senior AI Engineer", "llm_agents", "senior", "AIRESEARCH", 3),
    ("NLP_MID", "NLP Engineer", "nlp", "middle", "AIRESEARCH", 4),
    ("CV_MID", "Computer Vision Engineer", "computer_vision", "middle", "AIRESEARCH", 3),
    ("RND_SEN", "Research Scientist", "rnd", "senior", "AIRESEARCH", 3),
    ("AN_MID", "Data Analyst (DS track)", "data_science", "middle", "ANALYTICS", 8),
]

# Базовые навыки почти у всех в роли (coverage высокий)
CORE_BY_ROLE: dict[str, list[str]] = {
    "data_science": ["Python", "SQL", "Pandas", "NumPy", "Git"],
    "ml_engineering": ["Python", "PyTorch", "Docker", "Git", "SQL"],
    "mlops": ["Python", "Docker", "Kubernetes", "Git", "Linux"],
    "llm_agents": ["Python", "PyTorch", "Git", "Docker"],
    "nlp": ["Python", "PyTorch", "NLP", "Git"],
    "computer_vision": ["Python", "PyTorch", "Computer Vision", "Git"],
    "rnd": ["Python", "PyTorch", "Machine Learning", "Git"],
}

# Рыночные навыки с намеренным дефицитом в компании (coverage низкий)
GAP_BY_ROLE: dict[str, list[str]] = {
    "data_science": ["Airflow", "ClickHouse", "MLflow", "Kafka", "dbt"],
    "ml_engineering": ["MLflow", "Kubernetes", "Airflow", "FastAPI"],
    "mlops": ["MLflow", "Airflow", "Terraform", "Prometheus"],
    "llm_agents": ["LLM", "LangChain", "Transformers", "FastAPI"],
    "nlp": ["Transformers", "LLM", "LangChain"],
    "computer_vision": ["OpenCV", "TensorFlow", "Docker"],
    "rnd": ["LLM", "Transformers", "MLflow"],
}

# Дополнительные навыки со средней вероятностью
OPTIONAL_BY_ROLE: dict[str, list[str]] = {
    "data_science": ["scikit-learn", "PySpark", "PostgreSQL", "Tableau", "Power BI"],
    "ml_engineering": ["TensorFlow", "Kafka", "PostgreSQL", "Linux"],
    "mlops": ["Kafka", "PostgreSQL", "Bash", "Grafana"],
    "llm_agents": ["NLP", "PostgreSQL", "Linux"],
    "nlp": ["scikit-learn", "Docker", "PostgreSQL"],
    "computer_vision": ["NumPy", "Linux", "Docker"],
    "rnd": ["NumPy", "scikit-learn", "SQL"],
}

# Навыки-SPOF: после генерации оставляем ровно 1 носителя на роль
SPOF_BY_ROLE: dict[str, list[str]] = {
    "data_science": ["ClickHouse", "dbt"],
    "ml_engineering": ["Terraform"],
    "mlops": ["Prometheus"],
    "llm_agents": ["LangChain"],
    "nlp": ["spaCy"],
    "computer_vision": ["OpenCV"],
    "rnd": ["MLflow"],
}

LEVEL_CORE_P = {"junior": 0.75, "middle": 0.9, "senior": 0.95, "lead": 0.98}
LEVEL_OPTIONAL_P = {"junior": 0.25, "middle": 0.45, "senior": 0.6, "lead": 0.7}
LEVEL_GAP_P = {"junior": 0.05, "middle": 0.12, "senior": 0.2, "lead": 0.28}
LEVEL_PROF = {"junior": (0.4, 0.65), "middle": (0.55, 0.8), "senior": (0.7, 0.95), "lead": (0.8, 1.0)}

FIRST_NAMES = [
    "Анна", "Мария", "Елена", "Ольга", "Наталья", "Ирина", "Екатерина", "Татьяна",
    "Алексей", "Дмитрий", "Сергей", "Андрей", "Иван", "Максим", "Артём", "Никита",
    "Полина", "Дарья", "Алина", "Ксения", "Кирилл", "Павел", "Роман", "Егор",
]
LAST_NAMES = [
    "Иванова", "Петрова", "Смирнова", "Кузнецова", "Попова", "Васильева", "Соколова",
    "Иванов", "Петров", "Смирнов", "Кузнецов", "Попов", "Васильев", "Соколов",
    "Новикова", "Морозова", "Волкова", "Алексеева", "Лебедева", "Семёнова",
    "Новиков", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов",
]

COURSES = [
    ("AIRFLOW101", "Airflow для дата-пайплайнов", ["Airflow", "Python", "SQL"], 24),
    ("MLFLOW101", "MLflow и управление экспериментами", ["MLflow", "Python", "Docker"], 16),
    ("LLM101", "LLM и LangChain на практике", ["LLM", "LangChain", "Python", "Transformers"], 32),
    ("K8S_ML", "Kubernetes для ML-сервисов", ["Kubernetes", "Docker", "Linux"], 28),
    ("CLICKHOUSE", "Аналитика на ClickHouse", ["ClickHouse", "SQL"], 16),
    ("DBT101", "dbt: трансформации данных", ["dbt", "SQL", "PostgreSQL"], 20),
]


@dataclass
class SynthConfig:
    seed: int = SEED


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _canon_map(session: Session) -> dict[str, int]:
    """name / name_norm lower → canonical_id."""
    rows = session.scalars(select(SkillCanonical)).all()
    mapping: dict[str, int] = {}
    for row in rows:
        mapping[row.name.lower()] = row.id
        mapping[row.name_norm] = row.id
    return mapping


def _resolve_skill_ids(names: list[str], canon: dict[str, int]) -> list[int]:
    ids = []
    for name in names:
        cid = canon.get(name.lower())
        if cid is not None:
            ids.append(cid)
    return ids


def clear_org(session: Session) -> None:
    session.execute(delete(EmployeeSkill))
    session.execute(delete(CourseSkill))
    session.execute(delete(Employee))
    session.execute(delete(Course))
    session.execute(delete(Position))
    session.execute(delete(Department))
    # org graph edges only
    session.execute(
        delete(GraphEdge).where(
            GraphEdge.edge_type.in_(
                [
                    "EMPLOYEE_HAS_SKILL",
                    "EMPLOYEE_IN_DEPT",
                    "EMPLOYEE_HAS_POSITION",
                    "POSITION_MAPS_TO_ROLE",
                    "COURSE_TEACHES_SKILL",
                    "DEPT_CONTAINS_POSITION",
                ]
            )
        )
    )
    session.flush()


def seed_structure(session: Session) -> tuple[dict[str, Department], dict[str, Position]]:
    depts: dict[str, Department] = {}
    for code, name, parent in DEPARTMENTS:
        d = Department(code=code, name=name, parent_code=parent)
        session.add(d)
        session.flush()
        depts[code] = d

    positions: dict[str, Position] = {}
    for code, title, role_group, level, _dept, _n in POSITIONS:
        p = Position(code=code, title=title, role_group=role_group, level=level)
        session.add(p)
        session.flush()
        positions[code] = p
    return depts, positions


def seed_courses(session: Session, canon: dict[str, int]) -> list[Course]:
    courses = []
    for code, title, skills, hours in COURSES:
        c = Course(code=code, title=title, duration_hours=hours)
        session.add(c)
        session.flush()
        for cid in _resolve_skill_ids(skills, canon):
            session.add(CourseSkill(course_id=c.id, canonical_id=cid))
        courses.append(c)
    return courses


def assign_skills(
    rng: random.Random,
    *,
    role_group: str,
    level: str,
    canon: dict[str, int],
) -> list[tuple[int, float]]:
    core = CORE_BY_ROLE.get(role_group, ["Python", "Git"])
    optional = OPTIONAL_BY_ROLE.get(role_group, [])
    gaps = GAP_BY_ROLE.get(role_group, [])
    p_core = LEVEL_CORE_P[level]
    p_opt = LEVEL_OPTIONAL_P[level]
    p_gap = LEVEL_GAP_P[level]
    lo, hi = LEVEL_PROF[level]

    picked: dict[int, float] = {}

    def maybe_add(names: list[str], prob: float) -> None:
        for name in names:
            cid = canon.get(name.lower())
            if cid is None:
                continue
            if rng.random() <= prob:
                picked[cid] = round(rng.uniform(lo, hi), 2)

    maybe_add(core, p_core)
    maybe_add(optional, p_opt)
    maybe_add(gaps, p_gap)

    # гарантируем хотя бы Python, если есть в справочнике
    py = canon.get("python")
    if py and py not in picked:
        picked[py] = round(rng.uniform(lo, hi), 2)

    return list(picked.items())


def seed_employees(
    session: Session,
    rng: random.Random,
    depts: dict[str, Department],
    positions: dict[str, Position],
    canon: dict[str, int],
) -> list[Employee]:
    employees: list[Employee] = []
    idx = 1
    exp_by_level = {"junior": (0.5, 2.0), "middle": (2.0, 5.0), "senior": (5.0, 9.0), "lead": (8.0, 14.0)}

    for code, _title, role_group, level, dept_code, headcount in POSITIONS:
        for _ in range(headcount):
            lo, hi = exp_by_level[level]
            emp = Employee(
                employee_code=f"E{idx:04d}",
                full_name=_name(rng),
                department_id=depts[dept_code].id,
                position_id=positions[code].id,
                experience_years=round(rng.uniform(lo, hi), 1),
                is_active=True,
            )
            session.add(emp)
            session.flush()
            for cid, prof in assign_skills(rng, role_group=role_group, level=level, canon=canon):
                session.add(
                    EmployeeSkill(
                        employee_id=emp.id,
                        canonical_id=cid,
                        proficiency=prof,
                        source="synthetic",
                    )
                )
            employees.append(emp)
            idx += 1

    _apply_spof_skills(session, rng, employees, positions, canon)
    return employees


def _apply_spof_skills(
    session: Session,
    rng: random.Random,
    employees: list[Employee],
    positions: dict[str, Position],
    canon: dict[str, int],
) -> None:
    """Гарантируем SPOF: навык только у одного человека в роли."""
    pos_by_id = {p.id: p for p in positions.values()}
    by_role: dict[str, list[Employee]] = defaultdict(list)
    for e in employees:
        role = pos_by_id[e.position_id].role_group
        by_role[role].append(e)

    for role, skill_names in SPOF_BY_ROLE.items():
        pool = by_role.get(role) or []
        if not pool:
            continue
        # предпочитаем senior/lead
        ranked = sorted(
            pool,
            key=lambda e: {"lead": 3, "senior": 2, "middle": 1, "junior": 0}.get(
                pos_by_id[e.position_id].level, 0
            ),
            reverse=True,
        )
        for skill_name in skill_names:
            cid = canon.get(skill_name.lower())
            if cid is None:
                continue
            # снять навык у всех в роли
            for emp in pool:
                session.execute(
                    delete(EmployeeSkill).where(
                        EmployeeSkill.employee_id == emp.id,
                        EmployeeSkill.canonical_id == cid,
                    )
                )
            # оставить одному
            keeper = ranked[0] if ranked else rng.choice(pool)
            session.add(
                EmployeeSkill(
                    employee_id=keeper.id,
                    canonical_id=cid,
                    proficiency=0.85,
                    source="synthetic_spof",
                )
            )
        session.flush()


def generate_org(config: SynthConfig | None = None) -> dict:
    config = config or SynthConfig()
    rng = random.Random(config.seed)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        canon = _canon_map(session)
        if not canon:
            raise SystemExit("Сначала соберите market-граф (нужен skills_canonical).")

        clear_org(session)
        depts, positions = seed_structure(session)
        courses = seed_courses(session, canon)
        employees = seed_employees(session, rng, depts, positions, canon)
        session.commit()

        stats = {
            "departments": len(depts),
            "positions": len(positions),
            "employees": len(employees),
            "courses": len(courses),
            "employee_skills": session.scalar(select(func.count()).select_from(EmployeeSkill)),
        }
        print("Синтетика готова:", stats)
        return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic org data")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    generate_org(SynthConfig(seed=args.seed))


if __name__ == "__main__":
    main()
