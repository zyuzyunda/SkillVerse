"""Риски компетенций: SPOF, critical skills, coverage по подразделениям."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import Employee, EmployeeSkill, GraphEdge, SkillCanonical
from src.db.session import SessionLocal
from src.org.skill_gaps import _load_role_employees


@dataclass
class SkillRisk:
    canonical_id: int
    skill_name: str
    skill_norm: str
    cluster: str
    market_support: float
    trend: float
    holders: int
    employees_total: int
    coverage: float
    gap: float
    risk_score: float
    is_spof: bool
    is_critical: bool
    holder_names: list[str]


@dataclass
class ClusterCoverage:
    cluster: str
    department: str
    department_code: str
    holders: int
    employees_total: int
    coverage: float
    skills_covered: int
    skills_in_cluster: int


def compute_skill_risks(
    session: Session,
    *,
    role_group: str,
    department_code: Optional[str] = None,
    min_market_support: float = 0.15,
    spof_max_holders: int = 2,
    critical_max_coverage: float = 0.25,
) -> list[SkillRisk]:
    """
    SPOF: рыночный навык у ≤ spof_max_holders человек.
    Critical: высокий market + низкий coverage + мало holders.
    risk_score = support × (1+trend+) × (1−coverage) / max(holders, 0.5)
    """
    market = session.scalars(
        select(GraphEdge).where(
            GraphEdge.edge_type == "ROLE_REQUIRES_SKILL",
            GraphEdge.source_key == f"role:{role_group}",
        )
    ).all()
    employees = _load_role_employees(
        session, role_group=role_group, department_code=department_code
    )
    if not employees:
        return []

    canon = {c.id: c for c in session.scalars(select(SkillCanonical)).all()}
    holders_map: dict[int, list[str]] = defaultdict(list)
    for e in employees:
        for s in e.skills:
            holders_map[s.canonical_id].append(e.full_name)

    n = len(employees)
    risks: list[SkillRisk] = []
    for edge in market:
        props = edge.properties or {}
        support = float(props.get("support") or 0)
        if support < min_market_support:
            continue
        cid = props.get("canonical_id")
        skill_name = props.get("skill_name") or edge.target_key
        skill_norm = edge.target_key.replace("skill:", "", 1)
        if cid is None:
            match = next((c for c in canon.values() if c.name_norm == skill_norm), None)
            if not match:
                continue
            cid = match.id
            skill_name = match.name
        cid_int = int(cid)
        names = holders_map.get(cid_int, [])
        holders = len(names)
        coverage = holders / n
        gap = support - coverage
        trend = float(props.get("trend") or 0)
        trend_boost = 1.0 + max(0.0, trend)
        risk_score = (support * trend_boost * (1.0 - coverage)) / max(holders, 0.5)
        is_spof = 1 <= holders <= spof_max_holders
        is_critical = (
            support >= min_market_support
            and coverage <= critical_max_coverage
            and holders <= max(spof_max_holders + 1, 3)
        )
        skill = canon.get(cid_int)
        risks.append(
            SkillRisk(
                canonical_id=cid_int,
                skill_name=str(skill_name),
                skill_norm=skill_norm,
                cluster=(skill.cluster_name if skill else None) or "Other",
                market_support=round(support, 4),
                trend=round(trend, 4),
                holders=holders,
                employees_total=n,
                coverage=round(coverage, 4),
                gap=round(gap, 4),
                risk_score=round(risk_score, 4),
                is_spof=is_spof,
                is_critical=is_critical,
                holder_names=sorted(names),
            )
        )
    risks.sort(key=lambda r: (r.risk_score, r.gap, r.market_support), reverse=True)
    return risks


def compute_cluster_coverage(
    session: Session,
    *,
    role_group: Optional[str] = None,
    department_code: Optional[str] = None,
) -> list[ClusterCoverage]:
    """Coverage кластера: доля сотрудников с ≥1 навыком кластера."""
    employees = list(
        session.scalars(
            select(Employee)
            .options(
                selectinload(Employee.skills).selectinload(EmployeeSkill.canonical),
                selectinload(Employee.position),
                selectinload(Employee.department),
            )
            .where(Employee.is_active.is_(True))
        ).all()
    )
    if role_group:
        employees = [e for e in employees if e.position.role_group == role_group]
    if department_code:
        employees = [e for e in employees if e.department.code == department_code]
    if not employees:
        return []

    cluster_skills: dict[str, set[int]] = defaultdict(set)
    for c in session.scalars(select(SkillCanonical)).all():
        cluster_skills[c.cluster_name or "Other"].add(c.id)

    by_dept: dict[str, list[Employee]] = defaultdict(list)
    for e in employees:
        by_dept[e.department.code].append(e)

    rows: list[ClusterCoverage] = []
    for dept_code, emps in by_dept.items():
        dept_name = emps[0].department.name
        n = len(emps)
        for cluster, skill_ids in sorted(cluster_skills.items()):
            holders = 0
            covered_skills: set[int] = set()
            for emp in emps:
                emp_ids = {s.canonical_id for s in emp.skills}
                hit = emp_ids & skill_ids
                if hit:
                    holders += 1
                    covered_skills |= hit
            # не засоряем таблицу пустыми «хвостами»
            if holders == 0 and cluster == "Other":
                continue
            rows.append(
                ClusterCoverage(
                    cluster=cluster,
                    department=dept_name,
                    department_code=dept_code,
                    holders=holders,
                    employees_total=n,
                    coverage=round(holders / n, 4),
                    skills_covered=len(covered_skills),
                    skills_in_cluster=len(skill_ids),
                )
            )
    rows.sort(key=lambda r: (r.department, -r.coverage, r.cluster))
    return rows


def print_risks(role_group: str, top: int = 15) -> None:
    with SessionLocal() as session:
        risks = compute_skill_risks(session, role_group=role_group)
        if not risks:
            print(f"Нет данных для {role_group}")
            return
        print(f"\nРиски компетенций — {role_group} (n={risks[0].employees_total})")
        print(f"{'skill':<20} {'mkt':>5} {'hold':>4} {'cov':>5} {'risk':>6} flags")
        for r in risks[:top]:
            flags = []
            if r.is_spof:
                flags.append("SPOF")
            if r.is_critical:
                flags.append("CRIT")
            print(
                f"{r.skill_name:<20} {r.market_support:5.2f} {r.holders:4d} "
                f"{r.coverage:5.2f} {r.risk_score:6.3f} {','.join(flags)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="data_science")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    print_risks(args.role, top=args.top)


if __name__ == "__main__":
    main()
