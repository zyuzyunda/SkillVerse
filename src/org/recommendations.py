"""Рекомендации: кого обучить, какие курсы, внутренняя мобильность."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import Course, CourseSkill, Employee, SkillCanonical
from src.db.session import SessionLocal
from src.org.skill_gaps import SkillGap, compute_skill_gaps


@dataclass
class TrainCandidate:
    employee_code: str
    full_name: str
    position: str
    department: str
    level: str
    experience_years: float
    missing_skills: list[str]
    score: float
    reason: str


@dataclass
class CourseRecommendation:
    course_code: str
    title: str
    duration_hours: int
    skills_covered: list[str]
    gap_score: float
    candidates_count: int
    reason: str


@dataclass
class MobilityCandidate:
    employee_code: str
    full_name: str
    current_position: str
    current_role: str
    target_role: str
    overlap: float
    matching_skills: list[str]
    missing_skills: list[str]
    reason: str


def _employee_skill_ids(emp: Employee) -> set[int]:
    return {s.canonical_id for s in emp.skills}


def recommend_training(
    session: Session,
    *,
    role_group: str,
    department_code: Optional[str] = None,
    top_skills: int = 5,
    top_people: int = 10,
    min_gap: float = 0.15,
) -> tuple[list[SkillGap], list[TrainCandidate]]:
    # навыки, которые реально можно закрыть курсами
    course_skill_ids: set[int] = set()
    for course in session.scalars(select(Course).options(selectinload(Course.skills))).all():
        for cs in course.skills:
            course_skill_ids.add(cs.canonical_id)

    all_gaps = [
        g
        for g in compute_skill_gaps(
            session, role_group=role_group, department_code=department_code
        )
        if g.gap >= min_gap
    ]

    def gap_priority(g: SkillGap) -> float:
        actionable = 1.5 if g.canonical_id in course_skill_ids else 1.0
        trend_boost = 1.0 + max(0.0, g.trend)
        return g.gap * trend_boost * actionable

    gaps = sorted(all_gaps, key=gap_priority, reverse=True)[:top_skills]
    if not gaps:
        return [], []

    target_ids = {g.canonical_id for g in gaps}
    gap_by_id = {g.canonical_id: g for g in gaps}

    employees = session.scalars(
        select(Employee)
        .options(
            selectinload(Employee.skills),
            selectinload(Employee.position),
            selectinload(Employee.department),
        )
        .where(Employee.is_active.is_(True))
    ).all()
    role_emps = [e for e in employees if e.position.role_group == role_group]
    if department_code:
        role_emps = [e for e in role_emps if e.department.code == department_code]

    candidates: list[TrainCandidate] = []
    for emp in role_emps:
        have = _employee_skill_ids(emp)
        missing = [gid for gid in target_ids if gid not in have]
        if not missing:
            continue
        level_boost = {"junior": 0.8, "middle": 1.0, "senior": 1.15, "lead": 1.1}.get(
            emp.position.level, 1.0
        )
        score = sum(gap_priority(gap_by_id[m]) for m in missing) * level_boost
        missing_names = [gap_by_id[m].skill_name for m in missing]
        candidates.append(
            TrainCandidate(
                employee_code=emp.employee_code,
                full_name=emp.full_name,
                position=emp.position.title,
                department=emp.department.name,
                level=emp.position.level,
                experience_years=emp.experience_years,
                missing_skills=missing_names,
                score=round(score, 3),
                reason=(
                    f"Не хватает {len(missing_names)} приоритетных навыков: "
                    + ", ".join(missing_names[:4])
                ),
            )
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return gaps, candidates[:top_people]


def recommend_courses(
    session: Session,
    *,
    role_group: str,
    department_code: Optional[str] = None,
    top: int = 5,
    min_gap: float = 0.1,
) -> list[CourseRecommendation]:
    gaps = [
        g
        for g in compute_skill_gaps(
            session, role_group=role_group, department_code=department_code
        )
        if g.gap >= min_gap
    ]
    if not gaps:
        return []
    gap_by_id = {g.canonical_id: g for g in gaps}
    gap_ids = set(gap_by_id)

    _, train_candidates = recommend_training(
        session,
        role_group=role_group,
        department_code=department_code,
        top_skills=8,
        top_people=50,
        min_gap=min_gap,
    )
    # сколько кандидатов потенциально закрывают курс
    courses = session.scalars(
        select(Course).options(selectinload(Course.skills))
    ).all()
    canon = {c.id: c for c in session.scalars(select(SkillCanonical)).all()}

    recs: list[CourseRecommendation] = []
    for course in courses:
        covered_ids = [cs.canonical_id for cs in course.skills if cs.canonical_id in gap_ids]
        if not covered_ids:
            continue
        gap_score = sum(gap_by_id[cid].gap for cid in covered_ids)
        skill_names = [canon[cid].name for cid in covered_ids if cid in canon]
        # кандидаты, у которых нет хотя бы одного навыка курса
        cand_count = 0
        for cand in train_candidates:
            if any(name in cand.missing_skills for name in skill_names):
                cand_count += 1
        recs.append(
            CourseRecommendation(
                course_code=course.code,
                title=course.title,
                duration_hours=course.duration_hours,
                skills_covered=skill_names,
                gap_score=round(gap_score, 3),
                candidates_count=cand_count,
                reason=(
                    f"Закрывает дефициты: {', '.join(skill_names)}. "
                    f"Потенциальных участников: {cand_count}."
                ),
            )
        )
    # сортируем курсы: сначала те, где есть реальные участники
    recs.sort(
        key=lambda r: (r.candidates_count > 0, r.gap_score * (1 + 0.1 * r.candidates_count)),
        reverse=True,
    )
    return recs[:top]


def recommend_mobility(
    session: Session,
    *,
    target_role: str,
    top: int = 8,
    min_overlap: float = 0.25,
) -> list[MobilityCandidate]:
    """Кто из других ролей ближе всего к целевой по навыкам рынка."""
    gaps = compute_skill_gaps(session, role_group=target_role, min_market_support=0.25)
    if not gaps:
        return []
    target_skills = gaps[:12]
    target_ids = {g.canonical_id: g for g in target_skills}

    employees = session.scalars(
        select(Employee)
        .options(
            selectinload(Employee.skills),
            selectinload(Employee.position),
            selectinload(Employee.department),
        )
        .where(Employee.is_active.is_(True))
    ).all()

    results: list[MobilityCandidate] = []
    for emp in employees:
        if emp.position.role_group == target_role:
            continue
        have = _employee_skill_ids(emp)
        matching = [target_ids[i].skill_name for i in target_ids if i in have]
        missing = [target_ids[i].skill_name for i in target_ids if i not in have]
        overlap = len(matching) / len(target_ids) if target_ids else 0
        if overlap < min_overlap:
            continue
        results.append(
            MobilityCandidate(
                employee_code=emp.employee_code,
                full_name=emp.full_name,
                current_position=emp.position.title,
                current_role=emp.position.role_group,
                target_role=target_role,
                overlap=round(overlap, 3),
                matching_skills=matching,
                missing_skills=missing[:6],
                reason=(
                    f"Совпадение с {target_role}: {overlap:.0%}. "
                    f"Есть: {', '.join(matching[:4]) or '—'}. "
                    f"Дорастить: {', '.join(missing[:3]) or '—'}."
                ),
            )
        )
    results.sort(key=lambda r: r.overlap, reverse=True)
    return results[:top]


def print_recommendations(role_group: str) -> None:
    with SessionLocal() as session:
        gaps, people = recommend_training(session, role_group=role_group)
        courses = recommend_courses(session, role_group=role_group)
        mobility = recommend_mobility(session, target_role=role_group)

        print(f"\n=== Рекомендации для {role_group} ===")
        print("\nТоп дефицитов:")
        for g in gaps:
            print(f"  - {g.skill_name}: gap={g.gap:.2f} (market={g.market_support:.2f}, org={g.org_coverage:.2f})")

        print("\nКого обучить:")
        for p in people:
            print(f"  - {p.full_name} ({p.position}): {p.reason} [score={p.score}]")

        print("\nКурсы:")
        for c in courses:
            print(f"  - {c.title}: {c.reason}")

        print("\nВнутренняя мобильность → роль:")
        for m in mobility:
            print(f"  - {m.full_name} ({m.current_position}): {m.reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="HR recommendations")
    parser.add_argument("--role", default="data_science")
    args = parser.parse_args()
    print_recommendations(args.role)


if __name__ == "__main__":
    main()
