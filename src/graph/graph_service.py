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
