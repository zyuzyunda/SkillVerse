"""Пульс рынка труда: обзор вакансий, трендов и кластеров без орг-слоя."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import GraphEdge, GraphNode, SkillCanonical, Vacancy
from src.db.session import SessionLocal

PERIOD_SPLIT = datetime(2025, 6, 1, tzinfo=timezone.utc)


@dataclass
class MarketSkillRow:
    skill_name: str
    skill_norm: str
    cluster: str
    role_group: str
    support: float
    trend: float
    support_early: Optional[float]
    support_late: Optional[float]
    count: int
    role_vacancies: int


def market_overview(session: Session) -> dict[str, Any]:
    n_vac = session.scalar(select(func.count()).select_from(Vacancy)) or 0
    by_role = dict(
        session.execute(
            select(Vacancy.role_group, func.count())
            .group_by(Vacancy.role_group)
            .order_by(func.count().desc())
        ).all()
    )
    early = (
        session.scalar(
            select(func.count()).select_from(Vacancy).where(Vacancy.published_at < PERIOD_SPLIT)
        )
        or 0
    )
    late = (
        session.scalar(
            select(func.count()).select_from(Vacancy).where(Vacancy.published_at >= PERIOD_SPLIT)
        )
        or 0
    )
    n_skills = (
        session.scalar(select(func.count()).select_from(GraphNode).where(GraphNode.node_type == "skill"))
        or 0
    )
    n_clusters = (
        session.scalar(
            select(func.count()).select_from(GraphNode).where(GraphNode.node_type == "cluster")
        )
        or 0
    )
    n_edges = (
        session.scalar(
            select(func.count())
            .select_from(GraphEdge)
            .where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
        )
        or 0
    )
    return {
        "vacancies": n_vac,
        "by_role": by_role,
        "period_early": early,
        "period_late": late,
        "canonical_skills": n_skills,
        "clusters": n_clusters,
        "role_skill_edges": n_edges,
        "period_labels": {
            "early": "мар–май 2025",
            "late": "июн–сен 2025",
            "split": PERIOD_SPLIT.date().isoformat(),
        },
    }


def load_market_skills(
    session: Session,
    *,
    role_group: Optional[str] = None,
    min_support: float = 0.1,
) -> list[MarketSkillRow]:
    q = select(GraphEdge).where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
    if role_group:
        q = q.where(GraphEdge.source_key == f"role:{role_group}")
    edges = session.scalars(q).all()
    canon = {c.name_norm: c for c in session.scalars(select(SkillCanonical)).all()}

    rows: list[MarketSkillRow] = []
    for e in edges:
        props = e.properties or {}
        support = float(props.get("support") or 0)
        if support < min_support:
            continue
        skill_norm = props.get("skill_norm") or e.target_key.replace("skill:", "", 1)
        skill = canon.get(skill_norm)
        early = props.get("support_early")
        late = props.get("support_late")
        rows.append(
            MarketSkillRow(
                skill_name=str(props.get("skill_name") or (skill.name if skill else skill_norm)),
                skill_norm=skill_norm,
                cluster=(skill.cluster_name if skill else None) or "Other",
                role_group=str(props.get("role_group") or e.source_key.replace("role:", "", 1)),
                support=round(support, 4),
                trend=round(float(props.get("trend") or 0), 4),
                support_early=None if early is None else round(float(early), 4),
                support_late=None if late is None else round(float(late), 4),
                count=int(props.get("count") or 0),
                role_vacancies=int(props.get("role_vacancies") or 0),
            )
        )
    rows.sort(key=lambda r: (r.support, r.trend), reverse=True)
    return rows


def top_skills_across_roles(
    rows: list[MarketSkillRow],
    *,
    top: int = 20,
) -> list[dict[str, Any]]:
    """Агрегат по навыку: max support и средний тренд по ролям."""
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        cur = agg.get(r.skill_norm)
        if cur is None:
            agg[r.skill_norm] = {
                "skill": r.skill_name,
                "cluster": r.cluster,
                "max_support": r.support,
                "avg_trend": r.trend,
                "roles": 1,
                "best_role": r.role_group,
            }
        else:
            cur["roles"] += 1
            cur["avg_trend"] = (cur["avg_trend"] * (cur["roles"] - 1) + r.trend) / cur["roles"]
            if r.support > cur["max_support"]:
                cur["max_support"] = r.support
                cur["best_role"] = r.role_group
                cur["skill"] = r.skill_name
                cur["cluster"] = r.cluster
    out = sorted(agg.values(), key=lambda x: (x["max_support"], x["avg_trend"]), reverse=True)
    return out[:top]


def rising_falling(
    rows: list[MarketSkillRow],
    *,
    top: int = 15,
    min_abs_trend: float = 0.08,
) -> tuple[list[MarketSkillRow], list[MarketSkillRow]]:
    rising = [r for r in rows if r.trend >= min_abs_trend]
    falling = [r for r in rows if r.trend <= -min_abs_trend]
    rising.sort(key=lambda r: (r.trend, r.support), reverse=True)
    falling.sort(key=lambda r: (r.trend, -r.support))
    return rising[:top], falling[:top]


def cluster_demand(
    rows: list[MarketSkillRow],
) -> list[dict[str, Any]]:
    """Спрос по кластеру: max support навыка × число сигналов."""
    by_cluster: dict[str, list[MarketSkillRow]] = defaultdict(list)
    for r in rows:
        if r.cluster == "Other":
            continue
        by_cluster[r.cluster].append(r)
    out = []
    for cluster, items in by_cluster.items():
        supports = [i.support for i in items]
        trends = [i.trend for i in items]
        out.append(
            {
                "cluster": cluster,
                "skills": len({i.skill_norm for i in items}),
                "max_support": round(max(supports), 4),
                "avg_support": round(sum(supports) / len(supports), 4),
                "avg_trend": round(sum(trends) / len(trends), 4),
                "top_skill": max(items, key=lambda x: x.support).skill_name,
            }
        )
    out.sort(key=lambda x: (x["max_support"], x["avg_trend"]), reverse=True)
    return out


def role_cluster_matrix(
    session: Session,
    *,
    min_support: float = 0.15,
) -> list[dict[str, Any]]:
    edges = session.scalars(
        select(GraphEdge).where(GraphEdge.edge_type == "ROLE_REQUIRES_CLUSTER")
    ).all()
    rows = []
    for e in edges:
        props = e.properties or {}
        support = float(props.get("support") or 0)
        if support < min_support:
            continue
        rows.append(
            {
                "role": props.get("role_group") or e.source_key.replace("role:", "", 1),
                "cluster": props.get("cluster") or e.target_key,
                "support": round(support, 4),
                "trend": round(float(props.get("trend") or 0), 4),
                "top_skill": props.get("top_skill") or "",
            }
        )
    return rows


def get_pulse(*, role_group: Optional[str] = None) -> dict[str, Any]:
    with SessionLocal() as session:
        overview = market_overview(session)
        all_rows = load_market_skills(session, role_group=None, min_support=0.1)
        role_rows = (
            load_market_skills(session, role_group=role_group, min_support=0.1)
            if role_group
            else all_rows
        )
        rising, falling = rising_falling(role_rows)
        return {
            "overview": overview,
            "role_group": role_group,
            "top_skills": top_skills_across_roles(all_rows if role_group is None else role_rows),
            "role_top": [
                {
                    "skill": r.skill_name,
                    "cluster": r.cluster,
                    "support": r.support,
                    "trend": r.trend,
                    "early": r.support_early,
                    "late": r.support_late,
                }
                for r in role_rows[:25]
            ],
            "rising": [
                {
                    "skill": r.skill_name,
                    "role": r.role_group,
                    "cluster": r.cluster,
                    "support": r.support,
                    "trend": r.trend,
                    "early": r.support_early,
                    "late": r.support_late,
                }
                for r in rising
            ],
            "falling": [
                {
                    "skill": r.skill_name,
                    "role": r.role_group,
                    "cluster": r.cluster,
                    "support": r.support,
                    "trend": r.trend,
                    "early": r.support_early,
                    "late": r.support_late,
                }
                for r in falling
            ],
            "clusters": cluster_demand(role_rows),
            "role_cluster": role_cluster_matrix(session),
        }
