"""Аналитика дефицитов: рынок vs покрытие сотрудников."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import Department, Employee, GraphEdge, SkillCanonical
from src.db.session import SessionLocal


@dataclass
class SkillGap:
    canonical_id: int
    skill_name: str
    skill_norm: str
    market_support: float
    org_coverage: float
    gap: float
    trend: float
    weight: float
    holders: int
    employees_total: int
    cluster: str = "Other"


@dataclass
class ClusterGap:
    cluster: str
    market_support: float
    org_coverage: float
    gap: float
    trend: float
    weight: float
    skills_in_signal: int
    top_skill: str
    holders: int
    employees_total: int


def _load_role_employees(
    session: Session,
    role_group: Optional[str] = None,
    department_code: Optional[str] = None,
) -> list[Employee]:
    q = (
        select(Employee)
        .options(
            selectinload(Employee.skills),
            selectinload(Employee.position),
            selectinload(Employee.department),
        )
        .where(Employee.is_active.is_(True))
    )
    employees = list(session.scalars(q).all())
    if role_group:
        employees = [e for e in employees if e.position.role_group == role_group]
    if department_code:
        employees = [e for e in employees if e.department.code == department_code]
    return employees


def compute_skill_gaps(
    session: Session,
    *,
    role_group: str,
    department_code: Optional[str] = None,
    min_market_support: float = 0.2,
) -> list[SkillGap]:
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
    holders: dict[int, int] = defaultdict(int)
    for e in employees:
        for s in e.skills:
            holders[s.canonical_id] += 1

    n = len(employees)
    gaps: list[SkillGap] = []
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
            cid = match.id if match else None
            if match:
                skill_name = match.name
        if cid is None:
            continue
        cid_int = int(cid)
        coverage = holders.get(cid_int, 0) / n
        gap = support - coverage
        gaps.append(
            SkillGap(
                canonical_id=cid_int,
                skill_name=str(skill_name),
                skill_norm=skill_norm,
                market_support=round(support, 4),
                org_coverage=round(coverage, 4),
                gap=round(gap, 4),
                trend=round(float(props.get("trend") or 0), 4),
                weight=round(float(edge.weight or 0), 4),
                holders=holders.get(cid_int, 0),
                employees_total=n,
                cluster=(canon.get(cid_int).cluster_name if canon.get(cid_int) else None)
                or "Other",
            )
        )
    gaps.sort(key=lambda g: (g.gap, g.trend, g.market_support), reverse=True)
    return gaps


def compute_cluster_gaps(
    session: Session,
    *,
    role_group: str,
    department_code: Optional[str] = None,
    min_market_support: float = 0.2,
) -> list[ClusterGap]:
    """Дефицит по кластерам: market = max support навыка; org = доля с ≥1 навыком кластера."""
    market = session.scalars(
        select(GraphEdge).where(
            GraphEdge.edge_type == "ROLE_REQUIRES_CLUSTER",
            GraphEdge.source_key == f"role:{role_group}",
        )
    ).all()
    employees = _load_role_employees(
        session, role_group=role_group, department_code=department_code
    )
    if not employees:
        return []

    # навыки кластера из рёбер CLUSTER_CONTAINS_SKILL
    contain = session.scalars(
        select(GraphEdge).where(GraphEdge.edge_type == "CLUSTER_CONTAINS_SKILL")
    ).all()
    skills_by_cluster: dict[str, set[int]] = defaultdict(set)
    for e in contain:
        props = e.properties or {}
        cluster = props.get("cluster")
        cid = props.get("canonical_id")
        if cluster and cid is not None:
            skills_by_cluster[str(cluster)].add(int(cid))

    n = len(employees)
    gaps: list[ClusterGap] = []
    for edge in market:
        props = edge.properties or {}
        support = float(props.get("support") or 0)
        if support < min_market_support:
            continue
        cluster = str(props.get("cluster") or edge.target_key)
        skill_ids = skills_by_cluster.get(cluster, set())
        holders = 0
        for emp in employees:
            emp_ids = {s.canonical_id for s in emp.skills}
            if emp_ids & skill_ids:
                holders += 1
        coverage = holders / n
        gaps.append(
            ClusterGap(
                cluster=cluster,
                market_support=round(support, 4),
                org_coverage=round(coverage, 4),
                gap=round(support - coverage, 4),
                trend=round(float(props.get("trend") or 0), 4),
                weight=round(float(edge.weight or 0), 4),
                skills_in_signal=int(props.get("skills_in_signal") or 0),
                top_skill=str(props.get("top_skill") or ""),
                holders=holders,
                employees_total=n,
            )
        )
    gaps.sort(key=lambda g: (g.gap, g.trend, g.market_support), reverse=True)
    return gaps


def list_role_groups(session: Session) -> list[str]:
    edges = session.scalars(
        select(GraphEdge.source_key).where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
    ).all()
    roles = sorted({k.replace("role:", "", 1) for k in edges})
    return roles


def list_departments(session: Session) -> list[tuple[str, str]]:
    rows = session.scalars(select(Department).order_by(Department.name)).all()
    return [(d.code, d.name) for d in rows]


def print_gaps(role_group: str, min_support: float = 0.25, top: int = 15) -> None:
    with SessionLocal() as session:
        gaps = compute_skill_gaps(
            session, role_group=role_group, min_market_support=min_support
        )
        if not gaps:
            print(f"Нет данных для role_group={role_group}")
            return
        n = gaps[0].employees_total
        print(f"\nДефициты для role_group={role_group} (n_employees={n})")
        print(f"{'skill':<22} {'market':>8} {'org':>8} {'gap':>8} {'trend':>8}")
        for g in gaps[:top]:
            print(
                f"{g.skill_name:<22} {g.market_support:8.2f} {g.org_coverage:8.2f} "
                f"{g.gap:8.2f} {g.trend:8.2f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="data_science")
    parser.add_argument("--min-support", type=float, default=0.25)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    print_gaps(args.role, min_support=args.min_support, top=args.top)


if __name__ == "__main__":
    main()
