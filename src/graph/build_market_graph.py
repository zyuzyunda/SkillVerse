"""Сборка market-графа: нормализация → canonical → рёбра → Postgres."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Optional

from math import log2

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from src.db.models import (
    CourseSkill,
    EmployeeSkill,
    GraphEdge,
    GraphNode,
    SkillAlias,
    SkillCanonical,
    Vacancy,
    VacancySkill,
)
from src.db.session import Base, SessionLocal, engine
from src.graph.clusters import assign_cluster, cluster_code
from src.graph.normalize import SkillNormalizer, normalize_text

# граница между «ранним» и «поздним» срезом (helper vs yandex_coach)
PERIOD_SPLIT = datetime(2025, 6, 1, tzinfo=timezone.utc)

SUPPORT_WEIGHT = 0.7
TREND_WEIGHT = 0.3

# фильтры качества
MIN_ROLE_VACANCIES = 5
MIN_SKILL_VACANCIES = 3
MIN_ROLE_SKILL_COUNT = 2
MIN_COOC_COUNT = 15
MIN_SKILL_FOR_COOC = 20
TOP_COOC_PER_SKILL = 8
MIN_SUPPORT = 0.02
MIN_PMI = 0.8
MAX_JACCARD = 0.75  # отсекаем почти-дубликаты / копипасту
MIN_JACCARD = 0.08

MARKET_EDGE_TYPES = (
    "ROLE_REQUIRES_SKILL",
    "SKILL_CO_OCCURS",
    "CLUSTER_CONTAINS_SKILL",
    "ROLE_REQUIRES_CLUSTER",
)
MARKET_NODE_TYPES = ("role", "skill", "cluster")


def _period(published_at: Optional[datetime]) -> str:
    if published_at is None:
        return "unknown"
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return "early" if published_at < PERIOD_SPLIT else "late"


def load_vacancy_skills(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Vacancy.id,
            Vacancy.role_group,
            Vacancy.published_at,
            VacancySkill.skill_name,
        ).join(VacancySkill, VacancySkill.vacancy_id == Vacancy.id)
    ).all()
    return [
        {
            "vacancy_id": r.id,
            "role_group": r.role_group,
            "published_at": r.published_at,
            "skill_name": r.skill_name,
        }
        for r in rows
    ]


def build_canonical_mapping(
    session: Session, raw_rows: list[dict[str, Any]]
) -> tuple[SkillNormalizer, dict[str, int], dict[int, set[str]]]:
    """
    Returns:
      normalizer,
      canonical_norm → id,
      vacancy_id → set(canonical_norm)
    """
    normalizer = SkillNormalizer()
    raw_freq: Counter[str] = Counter()
    for row in raw_rows:
        raw_freq[row["skill_name"]] += 1

    # сначала частые навыки → стабильнее fuzzy
    resolved: dict[str, tuple[str, str, str]] = {}  # raw → (display, norm, method)
    for raw, _ in raw_freq.most_common():
        result = normalizer.resolve(raw)
        if result is None:
            continue
        resolved[raw] = (result.canonical_name, result.name_norm, result.match_method)
        normalizer.register_raw_as_alias(raw, result.name_norm)

    # очистка старых справочников (org-skills ссылаются на canonical — снимаем сначала)
    session.execute(delete(EmployeeSkill))
    session.execute(delete(CourseSkill))
    session.execute(delete(SkillAlias))
    session.execute(delete(SkillCanonical))
    session.flush()

    # vacancy counts per canonical
    vac_by_canon: dict[str, set[int]] = defaultdict(set)
    vac_skills: dict[int, set[str]] = defaultdict(set)
    for row in raw_rows:
        raw = row["skill_name"]
        if raw not in resolved:
            continue
        _, c_norm, _ = resolved[raw]
        vac_by_canon[c_norm].add(row["vacancy_id"])
        vac_skills[row["vacancy_id"]].add(c_norm)

    canon_id: dict[str, int] = {}
    for c_norm, display in normalizer._canonicals.items():
        if c_norm not in vac_by_canon:
            continue
        if len(vac_by_canon[c_norm]) < MIN_SKILL_VACANCIES:
            continue
        cluster = assign_cluster(c_norm, display)
        obj = SkillCanonical(
            name=display,
            name_norm=c_norm,
            skill_type="skill",
            cluster_name=cluster,
            vacancy_count=len(vac_by_canon[c_norm]),
            metadata_json={"cluster_code": cluster_code(cluster)},
        )
        session.add(obj)
        session.flush()
        canon_id[c_norm] = obj.id

    # aliases
    seen_alias: set[str] = set()
    for raw, (display, c_norm, method) in resolved.items():
        if c_norm not in canon_id:
            continue
        alias_norm = normalize_text(raw)
        if not alias_norm or alias_norm in seen_alias:
            continue
        seen_alias.add(alias_norm)
        session.add(
            SkillAlias(
                alias=raw.strip(),
                alias_norm=alias_norm,
                canonical_id=canon_id[c_norm],
                match_method=method if method != "new" else "exact",
            )
        )

    session.flush()
    # filter vac_skills to kept canonicals
    filtered_vac_skills = {
        vid: {s for s in skills if s in canon_id} for vid, skills in vac_skills.items()
    }
    return normalizer, canon_id, filtered_vac_skills


def compute_role_skill_edges(
    raw_rows: list[dict[str, Any]],
    vac_skills: dict[int, set[str]],
    canon_id: dict[str, int],
    normalizer: SkillNormalizer,
) -> list[dict[str, Any]]:
    # vacancy meta
    vac_meta: dict[int, dict[str, Any]] = {}
    for row in raw_rows:
        vac_meta[row["vacancy_id"]] = {
            "role_group": row["role_group"],
            "period": _period(row["published_at"]),
        }

    role_vac_all: dict[str, set[int]] = defaultdict(set)
    role_vac_period: dict[tuple[str, str], set[int]] = defaultdict(set)
    role_skill_all: dict[tuple[str, str], set[int]] = defaultdict(set)
    role_skill_period: dict[tuple[str, str, str], set[int]] = defaultdict(set)

    for vid, skills in vac_skills.items():
        meta = vac_meta.get(vid)
        if not meta:
            continue
        role = meta["role_group"]
        period = meta["period"]
        role_vac_all[role].add(vid)
        if period in {"early", "late"}:
            role_vac_period[(role, period)].add(vid)
        for skill in skills:
            role_skill_all[(role, skill)].add(vid)
            if period in {"early", "late"}:
                role_skill_period[(role, period, skill)].add(vid)

    edges: list[dict[str, Any]] = []
    for (role, skill), vids in role_skill_all.items():
        n_role = len(role_vac_all[role])
        if n_role < MIN_ROLE_VACANCIES:
            continue
        count = len(vids)
        if count < MIN_ROLE_SKILL_COUNT:
            continue
        support = count / n_role
        if support < MIN_SUPPORT:
            continue

        early_role = len(role_vac_period.get((role, "early"), set()))
        late_role = len(role_vac_period.get((role, "late"), set()))
        early_cnt = len(role_skill_period.get((role, "early", skill), set()))
        late_cnt = len(role_skill_period.get((role, "late", skill), set()))

        support_early = (early_cnt / early_role) if early_role >= 5 else None
        support_late = (late_cnt / late_role) if late_role >= 5 else None

        if support_early is not None and support_late is not None:
            trend = support_late - support_early
        else:
            trend = 0.0

        # trend_norm: [-1,1] → [0,1]
        trend_norm = max(0.0, min(1.0, (trend + 1.0) / 2.0))
        weight = SUPPORT_WEIGHT * support + TREND_WEIGHT * trend_norm

        display = normalizer._canonicals.get(skill, skill)
        edges.append(
            {
                "source_key": f"role:{role}",
                "target_key": f"skill:{skill}",
                "edge_type": "ROLE_REQUIRES_SKILL",
                "weight": round(weight, 4),
                "properties": {
                    "role_group": role,
                    "skill_norm": skill,
                    "skill_name": display,
                    "support": round(support, 4),
                    "trend": round(trend, 4),
                    "count": count,
                    "role_vacancies": n_role,
                    "support_early": None if support_early is None else round(support_early, 4),
                    "support_late": None if support_late is None else round(support_late, 4),
                    "canonical_id": canon_id[skill],
                },
            }
        )
    return edges


def compute_cooccurrence_edges(
    vac_skills: dict[int, set[str]],
    canon_id: dict[str, int],
    normalizer: SkillNormalizer,
    *,
    role_by_vac: Optional[dict[int, str]] = None,
) -> list[dict[str, Any]]:
    """Co-occurrence на PMI + фильтры шума; опционально внутри role_group."""

    def build_for_subset(subset: dict[int, set[str]], role_label: str | None) -> list[dict[str, Any]]:
        pair_count: Counter[tuple[str, str]] = Counter()
        skill_count: Counter[str] = Counter()
        n_vac = 0
        for skills in subset.values():
            kept = sorted(
                s
                for s in skills
                if s in canon_id
            )
            if len(kept) < 2:
                continue
            n_vac += 1
            for s in kept:
                skill_count[s] += 1
            for a, b in combinations(kept, 2):
                pair_count[(a, b)] += 1

        if n_vac < 20:
            return []

        candidates: list[tuple[float, str, str, int, float, float]] = []
        for (a, b), cnt in pair_count.items():
            if cnt < MIN_COOC_COUNT:
                continue
            if skill_count[a] < MIN_SKILL_FOR_COOC or skill_count[b] < MIN_SKILL_FOR_COOC:
                continue
            union = skill_count[a] + skill_count[b] - cnt
            jaccard = cnt / union if union else 0.0
            if jaccard < MIN_JACCARD or jaccard > MAX_JACCARD:
                continue
            # PMI = log2( P(a,b) / (P(a)P(b)) )
            p_ab = cnt / n_vac
            p_a = skill_count[a] / n_vac
            p_b = skill_count[b] / n_vac
            if p_a <= 0 or p_b <= 0 or p_ab <= 0:
                continue
            pmi = log2(p_ab / (p_a * p_b))
            if pmi < MIN_PMI:
                continue
            # score для ранжирования: PMI * log(count)
            score = pmi * log2(1 + cnt)
            candidates.append((score, a, b, cnt, jaccard, pmi))

        candidates.sort(reverse=True)
        degree: Counter[str] = Counter()
        edges: list[dict[str, Any]] = []
        for score, a, b, cnt, jaccard, pmi in candidates:
            if degree[a] >= TOP_COOC_PER_SKILL or degree[b] >= TOP_COOC_PER_SKILL:
                continue
            degree[a] += 1
            degree[b] += 1
            props = {
                "count": cnt,
                "jaccard": round(jaccard, 4),
                "pmi": round(pmi, 4),
                "skill_a": normalizer._canonicals.get(a, a),
                "skill_b": normalizer._canonicals.get(b, b),
                "canonical_id_a": canon_id[a],
                "canonical_id_b": canon_id[b],
            }
            if role_label:
                props["role_group"] = role_label
            edges.append(
                {
                    "source_key": f"skill:{a}",
                    "target_key": f"skill:{b}",
                    "edge_type": "SKILL_CO_OCCURS",
                    "weight": round(float(pmi), 4),
                    "properties": props,
                }
            )
        return edges

    if role_by_vac:
        by_role: dict[str, dict[int, set[str]]] = defaultdict(dict)
        for vid, skills in vac_skills.items():
            role = role_by_vac.get(vid)
            if not role:
                continue
            by_role[role][vid] = skills
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for role, subset in by_role.items():
            for e in build_for_subset(subset, role):
                key = tuple(sorted((e["source_key"], e["target_key"])))
                prev = best.get(key)
                if prev is None or float(e["weight"]) > float(prev["weight"]):
                    best[key] = e
        return list(best.values())

    return build_for_subset(vac_skills, None)


def compute_cluster_edges(
    role_edges: list[dict[str, Any]],
    canon_id: dict[str, int],
    normalizer: SkillNormalizer,
    cluster_by_skill: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """CLUSTER_CONTAINS_SKILL + ROLE_REQUIRES_CLUSTER (max support по навыкам кластера)."""
    clusters_used: set[str] = set(cluster_by_skill.values())
    contain_edges: list[dict[str, Any]] = []
    for skill_norm, cluster in cluster_by_skill.items():
        if skill_norm not in canon_id:
            continue
        code = cluster_code(cluster)
        contain_edges.append(
            {
                "source_key": f"cluster:{code}",
                "target_key": f"skill:{skill_norm}",
                "edge_type": "CLUSTER_CONTAINS_SKILL",
                "weight": 1.0,
                "properties": {
                    "cluster": cluster,
                    "skill_name": normalizer._canonicals.get(skill_norm, skill_norm),
                    "canonical_id": canon_id[skill_norm],
                },
            }
        )

    # role → cluster: max support/trend среди навыков кластера
    best: dict[tuple[str, str], dict[str, Any]] = {}
    skill_counts: dict[tuple[str, str], int] = defaultdict(int)
    for e in role_edges:
        props = e["properties"] or {}
        skill_norm = props.get("skill_norm")
        if not skill_norm:
            continue
        cluster = cluster_by_skill.get(skill_norm)
        if not cluster:
            continue
        role = props["role_group"]
        key = (role, cluster)
        skill_counts[key] += 1
        support = float(props.get("support") or 0)
        prev = best.get(key)
        if prev is None or support > float(prev["properties"].get("support") or 0):
            best[key] = {
                "source_key": f"role:{role}",
                "target_key": f"cluster:{cluster_code(cluster)}",
                "edge_type": "ROLE_REQUIRES_CLUSTER",
                "weight": round(
                    SUPPORT_WEIGHT * support
                    + TREND_WEIGHT * max(0.0, min(1.0, (float(props.get("trend") or 0) + 1) / 2)),
                    4,
                ),
                "properties": {
                    "role_group": role,
                    "cluster": cluster,
                    "support": props.get("support"),
                    "trend": props.get("trend"),
                    "top_skill": props.get("skill_name"),
                    "skills_in_signal": 0,
                },
            }
    for key, edge in best.items():
        edge["properties"]["skills_in_signal"] = skill_counts[key]

    return contain_edges, list(best.values()), clusters_used


def persist_graph(
    session: Session,
    *,
    roles: set[str],
    canon_id: dict[str, int],
    normalizer: SkillNormalizer,
    edges: list[dict[str, Any]],
    cluster_by_skill: dict[str, str],
) -> None:
    # только market-слой — org-узлы/рёбра не трогаем
    session.execute(delete(GraphEdge).where(GraphEdge.edge_type.in_(MARKET_EDGE_TYPES)))
    session.execute(delete(GraphNode).where(GraphNode.node_type.in_(MARKET_NODE_TYPES)))
    session.flush()

    nodes: list[GraphNode] = []
    for role in sorted(roles):
        nodes.append(
            GraphNode(
                node_key=f"role:{role}",
                node_type="role",
                label=role,
                properties={"role_group": role},
            )
        )

    clusters_used = sorted(set(cluster_by_skill.values()))
    for cluster in clusters_used:
        code = cluster_code(cluster)
        skill_n = sum(1 for s, c in cluster_by_skill.items() if c == cluster and s in canon_id)
        nodes.append(
            GraphNode(
                node_key=f"cluster:{code}",
                node_type="cluster",
                label=cluster,
                properties={"cluster": cluster, "cluster_code": code, "skills_count": skill_n},
            )
        )

    for c_norm, cid in canon_id.items():
        cluster = cluster_by_skill.get(c_norm, "Other")
        nodes.append(
            GraphNode(
                node_key=f"skill:{c_norm}",
                node_type="skill",
                label=normalizer._canonicals.get(c_norm, c_norm),
                properties={
                    "canonical_id": cid,
                    "name_norm": c_norm,
                    "cluster": cluster,
                    "cluster_code": cluster_code(cluster),
                },
            )
        )
    session.add_all(nodes)
    session.flush()

    node_keys = {n.node_key for n in nodes}
    edge_objs = []
    for e in edges:
        if e["source_key"] not in node_keys or e["target_key"] not in node_keys:
            continue
        edge_objs.append(
            GraphEdge(
                source_key=e["source_key"],
                target_key=e["target_key"],
                edge_type=e["edge_type"],
                weight=e["weight"],
                properties=e["properties"],
            )
        )
    session.add_all(edge_objs)
    session.flush()


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    # миграция лёгкая: колонка vacancy_count могла отсутствовать
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE skills_canonical
                ADD COLUMN IF NOT EXISTS vacancy_count INTEGER DEFAULT 0
                """
            )
        )


def build_market_graph(*, with_org: bool = True) -> dict[str, Any]:
    ensure_schema()

    with SessionLocal() as session:
        raw_rows = load_vacancy_skills(session)
        if not raw_rows:
            raise SystemExit("Нет данных в vacancy_skills — сначала загрузите вакансии.")

        print(f"Сырых связей vacancy↔skill: {len(raw_rows)}")
        normalizer, canon_id, vac_skills = build_canonical_mapping(session, raw_rows)
        print(f"Canonical skills: {len(canon_id)}")

        roles = {r["role_group"] for r in raw_rows}
        role_by_vac = {r["vacancy_id"]: r["role_group"] for r in raw_rows}
        role_edges = compute_role_skill_edges(raw_rows, vac_skills, canon_id, normalizer)
        cooc_edges = compute_cooccurrence_edges(
            vac_skills, canon_id, normalizer, role_by_vac=role_by_vac
        )
        cluster_by_skill = {
            c_norm: assign_cluster(c_norm, normalizer._canonicals.get(c_norm, c_norm))
            for c_norm in canon_id
        }
        contain_edges, role_cluster_edges, clusters_used = compute_cluster_edges(
            role_edges, canon_id, normalizer, cluster_by_skill
        )
        print(f"ROLE_REQUIRES_SKILL: {len(role_edges)}")
        print(f"SKILL_CO_OCCURS: {len(cooc_edges)}")
        print(f"Clusters: {len(clusters_used)}")
        print(f"CLUSTER_CONTAINS_SKILL: {len(contain_edges)}")
        print(f"ROLE_REQUIRES_CLUSTER: {len(role_cluster_edges)}")

        persist_graph(
            session,
            roles=roles,
            canon_id=canon_id,
            normalizer=normalizer,
            edges=role_edges + cooc_edges + contain_edges + role_cluster_edges,
            cluster_by_skill=cluster_by_skill,
        )
        session.commit()

        n_nodes = session.scalar(
            select(func.count())
            .select_from(GraphNode)
            .where(GraphNode.node_type.in_(MARKET_NODE_TYPES))
        ) or 0
        n_edges = session.scalar(
            select(func.count())
            .select_from(GraphEdge)
            .where(GraphEdge.edge_type.in_(MARKET_EDGE_TYPES))
        ) or 0
        stats = {
            "raw_links": len(raw_rows),
            "canonical_skills": len(canon_id),
            "roles": len(roles),
            "clusters": len(clusters_used),
            "role_skill_edges": len(role_edges),
            "cooc_edges": len(cooc_edges),
            "cluster_contains_edges": len(contain_edges),
            "role_cluster_edges": len(role_cluster_edges),
            "market_nodes": n_nodes,
            "market_edges": n_edges,
        }
        print("Market-граф готов:", stats)

    if with_org:
        # canonical ids обновились — пересобираем синтетику и org-слой
        from src.org.build_org_graph import build_org_graph
        from src.org.generate_synthetic import generate_org

        print("Пересборка org-слоя...")
        generate_org()
        build_org_graph()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build market knowledge graph into Postgres")
    parser.add_argument(
        "--skip-org",
        action="store_true",
        help="Не пересобирать синтетику/org после market-графа",
    )
    args = parser.parse_args()
    build_market_graph(with_org=not args.skip_org)


if __name__ == "__main__":
    main()
