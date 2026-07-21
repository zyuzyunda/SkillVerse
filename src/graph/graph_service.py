"""Загрузка market-графа из Postgres в NetworkX + подграфы для UI."""

from __future__ import annotations

from typing import Any, Optional

import networkx as nx
from sqlalchemy import select

from src.db.models import GraphEdge, GraphNode
from src.db.session import SessionLocal


def load_networkx(
    *,
    edge_types: Optional[list[str]] = None,
) -> nx.Graph:
    """Неориентированный граф для визуализации/аналитики."""
    g = nx.Graph()
    with SessionLocal() as session:
        nodes = session.scalars(select(GraphNode)).all()
        for node in nodes:
            g.add_node(
                node.node_key,
                label=node.label,
                node_type=node.node_type,
                **(node.properties or {}),
            )

        q = select(GraphEdge)
        if edge_types:
            q = q.where(GraphEdge.edge_type.in_(edge_types))
        edges = session.scalars(q).all()
        for edge in edges:
            if edge.source_key not in g or edge.target_key not in g:
                continue
            g.add_edge(
                edge.source_key,
                edge.target_key,
                edge_type=edge.edge_type,
                weight=edge.weight,
                **(edge.properties or {}),
            )
    return g


def summary(g: nx.Graph) -> dict:
    roles = [n for n, d in g.nodes(data=True) if d.get("node_type") == "role"]
    skills = [n for n, d in g.nodes(data=True) if d.get("node_type") == "skill"]
    by_type: dict[str, int] = {}
    for _, _, d in g.edges(data=True):
        t = d.get("edge_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "roles": len(roles),
        "skills": len(skills),
        "edges_by_type": by_type,
    }


def role_subgraph(
    role_group: str,
    *,
    top_skills: int = 20,
    min_support: float = 0.15,
    include_clusters: bool = True,
    include_cooc: bool = True,
    top_cooc: int = 25,
) -> dict[str, Any]:
    """
    Подграф роли: role → top skills (+ clusters + сильнейшие co-occurrence).
    Возвращает nodes/edges + 2D layout для Plotly.
    """
    role_key = f"role:{role_group}"
    edge_types = ["ROLE_REQUIRES_SKILL"]
    if include_clusters:
        edge_types.extend(["ROLE_REQUIRES_CLUSTER", "CLUSTER_CONTAINS_SKILL"])
    if include_cooc:
        edge_types.append("SKILL_CO_OCCURS")

    g = load_networkx(edge_types=edge_types)
    if role_key not in g:
        return {"nodes": [], "edges": [], "positions": {}}

    # топ навыков роли по support
    skill_edges = []
    for _, tgt, data in g.edges(role_key, data=True):
        if data.get("edge_type") != "ROLE_REQUIRES_SKILL":
            continue
        support = float(data.get("support") or data.get("weight") or 0)
        if support < min_support:
            continue
        skill_edges.append((tgt, support, data))
    skill_edges.sort(key=lambda x: x[1], reverse=True)
    skill_edges = skill_edges[:top_skills]
    keep_skills = {s for s, _, _ in skill_edges}

    keep_clusters: set[str] = set()
    if include_clusters:
        for _, tgt, data in g.edges(role_key, data=True):
            if data.get("edge_type") == "ROLE_REQUIRES_CLUSTER":
                keep_clusters.add(tgt)
        # только кластеры, связанные с выбранными навыками
        linked = set()
        for ckey in keep_clusters:
            for _, sk, data in g.edges(ckey, data=True):
                if data.get("edge_type") == "CLUSTER_CONTAINS_SKILL" and sk in keep_skills:
                    linked.add(ckey)
        keep_clusters = linked

    sub = nx.Graph()
    sub.add_node(role_key, **g.nodes[role_key])

    for sk, support, data in skill_edges:
        nd = dict(g.nodes[sk])
        nd["support"] = support
        sub.add_node(sk, **nd)
        sub.add_edge(role_key, sk, **data)

    for ckey in keep_clusters:
        if ckey not in g:
            continue
        sub.add_node(ckey, **g.nodes[ckey])
        # role → cluster
        edata = g.get_edge_data(role_key, ckey) or {}
        if edata.get("edge_type") == "ROLE_REQUIRES_CLUSTER":
            sub.add_edge(role_key, ckey, **edata)
        for _, sk, data in g.edges(ckey, data=True):
            if data.get("edge_type") == "CLUSTER_CONTAINS_SKILL" and sk in keep_skills:
                if sk not in sub:
                    sub.add_node(sk, **g.nodes[sk])
                sub.add_edge(ckey, sk, **data)

    if include_cooc and keep_skills:
        cooc = []
        for u, v, data in g.edges(data=True):
            if data.get("edge_type") != "SKILL_CO_OCCURS":
                continue
            if u in keep_skills and v in keep_skills:
                cooc.append((u, v, float(data.get("weight") or 0), data))
        cooc.sort(key=lambda x: x[2], reverse=True)
        for u, v, _, data in cooc[:top_cooc]:
            if not sub.has_edge(u, v):
                sub.add_edge(u, v, **data)

    pos = nx.spring_layout(sub, seed=42, k=1.2 / max(sub.number_of_nodes() ** 0.5, 1), iterations=50)

    nodes_out = []
    for key, data in sub.nodes(data=True):
        x, y = pos[key]
        item = {
            "id": key,
            "label": data.get("label") or key,
            "node_type": data.get("node_type"),
            "cluster": data.get("cluster"),
            "x": float(x),
            "y": float(y),
            "support": float(data.get("support") or 0),
        }
        if data.get("node_type") == "role":
            item["role_group"] = role_group
        nodes_out.append(item)

    edges_out = []
    for u, v, data in sub.edges(data=True):
        edges_out.append(
            {
                "source": u,
                "target": v,
                "edge_type": data.get("edge_type"),
                "weight": float(data.get("weight") or 0),
                "support": float(data.get("support") or 0),
            }
        )

    return {
        "role": role_group,
        "roles": [role_group],
        "nodes": nodes_out,
        "edges": edges_out,
        "n_nodes": len(nodes_out),
        "n_edges": len(edges_out),
    }


def market_subgraph(
    role_groups: list[str],
    *,
    top_skills: int = 15,
    min_support: float = 0.18,
    include_clusters: bool = True,
    include_cooc: bool = False,
    top_cooc: int = 20,
) -> dict[str, Any]:
    """Подграф рынка для одной или нескольких ролей (общие навыки связывают роли)."""
    if not role_groups:
        return {"roles": [], "nodes": [], "edges": [], "n_nodes": 0, "n_edges": 0}

    merged_nodes: dict[str, dict[str, Any]] = {}
    merged_edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    for role in role_groups:
        part = role_subgraph(
            role,
            top_skills=top_skills,
            min_support=min_support,
            include_clusters=include_clusters,
            include_cooc=include_cooc,
            top_cooc=top_cooc,
        )
        for n in part["nodes"]:
            prev = merged_nodes.get(n["id"])
            if prev is None:
                nn = dict(n)
                if n.get("node_type") == "role":
                    nn["role_group"] = role
                merged_nodes[n["id"]] = nn
            elif float(n.get("support") or 0) > float(prev.get("support") or 0):
                prev["support"] = n["support"]
                if n.get("cluster"):
                    prev["cluster"] = n["cluster"]
        for e in part["edges"]:
            key = (e["source"], e["target"], e.get("edge_type") or "")
            key_rev = (e["target"], e["source"], e.get("edge_type") or "")
            if key not in merged_edges and key_rev not in merged_edges:
                merged_edges[key] = dict(e)
            else:
                existing = merged_edges.get(key) or merged_edges.get(key_rev)
                if existing and float(e.get("support") or e.get("weight") or 0) > float(
                    existing.get("support") or existing.get("weight") or 0
                ):
                    existing.update(e)

    return {
        "roles": list(role_groups),
        "nodes": list(merged_nodes.values()),
        "edges": list(merged_edges.values()),
        "n_nodes": len(merged_nodes),
        "n_edges": len(merged_edges),
    }


def list_market_roles() -> list[str]:
    g = load_networkx(edge_types=["ROLE_REQUIRES_SKILL"])
    roles = sorted(
        n.replace("role:", "", 1)
        for n, d in g.nodes(data=True)
        if d.get("node_type") == "role" and str(n).startswith("role:")
    )
    return roles


def list_market_clusters() -> list[str]:
    g = load_networkx(edge_types=["CLUSTER_CONTAINS_SKILL"])
    return sorted(
        str(d.get("label") or n.replace("cluster:", "", 1))
        for n, d in g.nodes(data=True)
        if d.get("node_type") == "cluster"
    )


def pipeline_kg_stats() -> dict[str, Any]:
    """KPI market-KG после пайплайна sections → extract → governance."""
    with SessionLocal() as session:
        from sqlalchemy import func

        from src.db.models import KgQuarantine, SkillCanonical

        n_canon = session.scalar(select(func.count()).select_from(SkillCanonical)) or 0
        n_role_skill = (
            session.scalar(
                select(func.count())
                .select_from(GraphEdge)
                .where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
            )
            or 0
        )
        n_cooc = (
            session.scalar(
                select(func.count())
                .select_from(GraphEdge)
                .where(GraphEdge.edge_type == "SKILL_CO_OCCURS")
            )
            or 0
        )
        n_quarantine = (
            session.scalar(
                select(func.count())
                .select_from(KgQuarantine)
                .where(KgQuarantine.item_type == "summary")
            )
            or 0
        )
        n_skills = (
            session.scalar(
                select(func.count())
                .select_from(GraphNode)
                .where(GraphNode.node_type == "skill")
            )
            or 0
        )
    return {
        "canonical_skills": int(n_canon),
        "skill_nodes": int(n_skills),
        "role_skill_edges": int(n_role_skill),
        "cooc_edges": int(n_cooc),
        "quarantine_summaries": int(n_quarantine),
    }


def personal_profile_subgraph(
    role_group: str,
    *,
    have_skills: list[str],
    gap_skills: list[str] | None = None,
    min_support: float = 0.10,
    top_skills: int = 28,
    include_clusters: bool = True,
    include_cooc: bool = True,
    top_cooc: int = 40,
    expand_cooc_neighbors: bool = True,
) -> dict[str, Any]:
    """Подграф целевой роли из KG с пометками have / gap / neighbor.

    Использует рёбра ROLE_REQUIRES_SKILL + SKILL_CO_OCCURS пайплайна.
    """
    gap_skills = gap_skills or []
    have_l = {s.strip().lower() for s in have_skills if s and s.strip()}
    gap_l = {s.strip().lower() for s in gap_skills if s and s.strip()} - have_l

    data = role_subgraph(
        role_group,
        top_skills=top_skills,
        min_support=min_support,
        include_clusters=include_clusters,
        include_cooc=include_cooc,
        top_cooc=top_cooc,
    )
    if not data.get("nodes"):
        return data

    # расширить соседями cooc вокруг «есть у меня»
    if include_cooc and expand_cooc_neighbors and have_l:
        g = load_networkx(edge_types=["SKILL_CO_OCCURS"])
        keep_ids = {n["id"] for n in data["nodes"]}
        have_ids = {
            n["id"]
            for n in data["nodes"]
            if n.get("node_type") == "skill"
            and (n.get("label") or "").lower() in have_l
        }
        extra_edges: list[dict[str, Any]] = []
        for hid in have_ids:
            if hid not in g:
                continue
            for nbr in g.neighbors(hid):
                ed = g.get_edge_data(hid, nbr) or {}
                if ed.get("edge_type") != "SKILL_CO_OCCURS":
                    continue
                if nbr not in keep_ids:
                    nd = dict(g.nodes[nbr])
                    data["nodes"].append(
                        {
                            "id": nbr,
                            "label": nd.get("label") or nbr,
                            "node_type": nd.get("node_type") or "skill",
                            "cluster": nd.get("cluster"),
                            "x": 0.0,
                            "y": 0.0,
                            "support": float(nd.get("support") or 0),
                            "status": "neighbor",
                        }
                    )
                    keep_ids.add(nbr)
                extra_edges.append(
                    {
                        "source": hid,
                        "target": nbr,
                        "edge_type": "SKILL_CO_OCCURS",
                        "weight": float(ed.get("weight") or 0),
                        "support": float(ed.get("support") or 0),
                    }
                )
        # дедуп рёбер
        seen = {(e["source"], e["target"], e.get("edge_type")) for e in data["edges"]}
        seen |= {(e["target"], e["source"], e.get("edge_type")) for e in data["edges"]}
        for e in extra_edges:
            key = (e["source"], e["target"], e.get("edge_type"))
            key_r = (e["target"], e["source"], e.get("edge_type"))
            if key in seen or key_r in seen:
                continue
            data["edges"].append(e)
            seen.add(key)

    nodes = []
    for n in data["nodes"]:
        nn = dict(n)
        if nn.get("node_type") == "skill":
            label = (nn.get("label") or "").lower()
            if label in have_l:
                nn["status"] = "have"
                nn["trend"] = 0.15  # зелёный в multiverse-палитре
                nn["bridge_roles"] = 1
            elif label in gap_l:
                nn["status"] = "gap"
                nn["trend"] = -0.15  # розовый
                nn["bridge_roles"] = 1
            else:
                nn.setdefault("status", "neighbor")
                nn.setdefault("trend", 0.0)
                nn.setdefault("bridge_roles", 1)
        nodes.append(nn)
    data = dict(data)
    data["nodes"] = nodes
    data["n_nodes"] = len(nodes)
    data["n_edges"] = len(data["edges"])
    data["pipeline"] = "sections→extract→governance"
    return data


def filtered_market_graph(
    *,
    roles: Optional[list[str]] = None,
    clusters: Optional[list[str]] = None,
    skill_query: str = "",
    min_support: float = 0.10,
    min_cooc_weight: float = 0.3,
    top_skills_per_role: int = 25,
    max_skills: int = 120,
    max_cooc: int = 80,
    include_role_skill: bool = True,
    include_clusters: bool = True,
    include_cooc: bool = True,
) -> dict[str, Any]:
    """Полный market-граф с фильтрами для UI.

    Берёт все роли (или выбранные), топ навыков по support, опционально
    кластеры и co-occurrence. Режет размер через max_skills / max_cooc.
    """
    edge_types = []
    if include_role_skill:
        edge_types.append("ROLE_REQUIRES_SKILL")
    if include_clusters:
        edge_types.extend(["ROLE_REQUIRES_CLUSTER", "CLUSTER_CONTAINS_SKILL"])
    if include_cooc:
        edge_types.append("SKILL_CO_OCCURS")
    if not edge_types:
        return {"roles": [], "nodes": [], "edges": [], "n_nodes": 0, "n_edges": 0}

    g = load_networkx(edge_types=edge_types)
    all_roles = [
        n.replace("role:", "", 1)
        for n, d in g.nodes(data=True)
        if d.get("node_type") == "role" and str(n).startswith("role:")
    ]
    selected_roles = [r for r in (roles or all_roles) if r in all_roles]
    if not selected_roles:
        return {"roles": [], "nodes": [], "edges": [], "n_nodes": 0, "n_edges": 0}

    q = (skill_query or "").strip().lower()
    cluster_filter = {c.lower() for c in (clusters or []) if c}

    # skill → best support across selected roles
    skill_best: dict[str, float] = {}
    skill_roles: dict[str, set[str]] = {}
    role_skill_edges: list[tuple[str, str, float, dict[str, Any]]] = []

    for role in selected_roles:
        role_key = f"role:{role}"
        if role_key not in g:
            continue
        scored: list[tuple[str, float, dict[str, Any]]] = []
        for _, tgt, data in g.edges(role_key, data=True):
            if data.get("edge_type") != "ROLE_REQUIRES_SKILL":
                continue
            support = float(data.get("support") or data.get("weight") or 0)
            if support < min_support:
                continue
            nd = g.nodes[tgt]
            label = str(nd.get("label") or tgt)
            cluster_name = str(nd.get("cluster") or "")
            if q and q not in label.lower() and q not in tgt.lower():
                continue
            if cluster_filter and cluster_name.lower() not in cluster_filter:
                continue
            scored.append((tgt, support, data))
        scored.sort(key=lambda x: x[1], reverse=True)
        for sk, support, data in scored[:top_skills_per_role]:
            role_skill_edges.append((role_key, sk, support, data))
            skill_best[sk] = max(skill_best.get(sk, 0.0), support)
            skill_roles.setdefault(sk, set()).add(role)

    # если нет ROLE_REQUIRES_SKILL (фильтр выключен) — берём навыки из кластеров/cooc
    if not include_role_skill and (include_clusters or include_cooc):
        for n, d in g.nodes(data=True):
            if d.get("node_type") != "skill":
                continue
            label = str(d.get("label") or n)
            cluster_name = str(d.get("cluster") or "")
            if q and q not in label.lower() and q not in str(n).lower():
                continue
            if cluster_filter and cluster_name.lower() not in cluster_filter:
                continue
            skill_best[n] = float(d.get("vacancy_count") or d.get("support") or 1.0)

    keep_skills = {
        sk
        for sk, _ in sorted(skill_best.items(), key=lambda x: x[1], reverse=True)[
            : max(1, max_skills)
        ]
    }

    keep_clusters: set[str] = set()
    if include_clusters and keep_skills:
        for n, d in g.nodes(data=True):
            if d.get("node_type") != "cluster":
                continue
            label = str(d.get("label") or n)
            if cluster_filter and label.lower() not in cluster_filter:
                continue
            # кластер связан хотя бы с одним keep skill
            linked = False
            for _, sk, data in g.edges(n, data=True):
                if data.get("edge_type") == "CLUSTER_CONTAINS_SKILL" and sk in keep_skills:
                    linked = True
                    break
            if linked or (cluster_filter and label.lower() in cluster_filter):
                keep_clusters.add(n)

    sub = nx.Graph()
    for role in selected_roles:
        role_key = f"role:{role}"
        if role_key in g:
            nd = dict(g.nodes[role_key])
            nd["role_group"] = role
            sub.add_node(role_key, **nd)

    for sk in keep_skills:
        if sk not in g:
            continue
        nd = dict(g.nodes[sk])
        nd["support"] = skill_best.get(sk, float(nd.get("support") or 0))
        sub.add_node(sk, **nd)

    for ckey in keep_clusters:
        if ckey in g:
            sub.add_node(ckey, **g.nodes[ckey])

    # рёбра role→skill
    for role_key, sk, support, data in role_skill_edges:
        if sk not in keep_skills or role_key not in sub:
            continue
        ed = dict(data)
        ed["support"] = support
        if not sub.has_edge(role_key, sk):
            sub.add_edge(role_key, sk, **ed)

    # кластеры
    if include_clusters:
        for role in selected_roles:
            role_key = f"role:{role}"
            if role_key not in sub:
                continue
            for _, ckey, data in g.edges(role_key, data=True):
                if data.get("edge_type") != "ROLE_REQUIRES_CLUSTER":
                    continue
                if ckey not in keep_clusters:
                    continue
                if not sub.has_edge(role_key, ckey):
                    sub.add_edge(role_key, ckey, **data)
        for ckey in keep_clusters:
            for _, sk, data in g.edges(ckey, data=True):
                if data.get("edge_type") != "CLUSTER_CONTAINS_SKILL":
                    continue
                if sk not in keep_skills:
                    continue
                if not sub.has_edge(ckey, sk):
                    sub.add_edge(ckey, sk, **data)

    # co-occurrence
    if include_cooc and keep_skills:
        cooc: list[tuple[str, str, float, dict[str, Any]]] = []
        for u, v, data in g.edges(data=True):
            if data.get("edge_type") != "SKILL_CO_OCCURS":
                continue
            if u not in keep_skills or v not in keep_skills:
                continue
            w = float(data.get("weight") or 0)
            cond = float(data.get("cond_prob") or 0)
            score = max(w, cond)
            if score < min_cooc_weight:
                continue
            cooc.append((u, v, score, data))
        cooc.sort(key=lambda x: x[2], reverse=True)
        for u, v, _, data in cooc[:max_cooc]:
            if not sub.has_edge(u, v):
                sub.add_edge(u, v, **data)

    # убрать изолированные skills без рёбер (кроме случая только skills)
    if include_role_skill or include_clusters or include_cooc:
        isolates = [
            n
            for n in list(sub.nodes())
            if sub.degree(n) == 0 and sub.nodes[n].get("node_type") == "skill"
        ]
        sub.remove_nodes_from(isolates)

    n_nodes = max(sub.number_of_nodes(), 1)
    pos = nx.spring_layout(
        sub,
        seed=42,
        k=1.4 / max(n_nodes**0.5, 1),
        iterations=60,
    )

    nodes_out = []
    for key, data in sub.nodes(data=True):
        x, y = pos.get(key, (0.0, 0.0))
        ntype = data.get("node_type")
        item: dict[str, Any] = {
            "id": key,
            "label": data.get("label") or key,
            "node_type": ntype,
            "cluster": data.get("cluster"),
            "x": float(x),
            "y": float(y),
            "support": float(data.get("support") or 0),
        }
        if ntype == "role":
            item["role_group"] = data.get("role_group") or key.replace("role:", "", 1)
        if ntype == "skill" and key in skill_roles:
            item["roles"] = sorted(skill_roles[key])
        nodes_out.append(item)

    edges_out = []
    for u, v, data in sub.edges(data=True):
        edges_out.append(
            {
                "source": u,
                "target": v,
                "edge_type": data.get("edge_type"),
                "weight": float(data.get("weight") or 0),
                "support": float(data.get("support") or 0),
                "cond_prob": float(data.get("cond_prob") or 0),
                "pmi": float(data.get("pmi") or 0),
                "count": int(data.get("count") or 0),
            }
        )

    return {
        "roles": selected_roles,
        "nodes": nodes_out,
        "edges": edges_out,
        "n_nodes": len(nodes_out),
        "n_edges": len(edges_out),
    }
