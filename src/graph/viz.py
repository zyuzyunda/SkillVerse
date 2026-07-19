"""Интерактивная визуализация подграфа (drag / pan / zoom)."""

from __future__ import annotations

import math
from typing import Any, Optional

from pyvis.network import Network


# Современная палитра (светлый холст, насыщенные акценты)
THEME = {
    "bg": "#F8FAFC",
    "font": "#0F172A",
    "skill": {
        "background": "#14B8A6",
        "border": "#0F766E",
        "highlight": {"background": "#2DD4BF", "border": "#0F766E"},
    },
    "cluster": {
        "background": "#F97316",
        "border": "#C2410C",
        "highlight": {"background": "#FB923C", "border": "#C2410C"},
    },
    "default": {
        "background": "#94A3B8",
        "border": "#64748B",
        "highlight": {"background": "#CBD5E1", "border": "#64748B"},
    },
}

# Разные роли — разные цвета (без «AI-purple»)
ROLE_PALETTE = [
    "#0D9488",  # teal
    "#E11D48",  # rose
    "#2563EB",  # blue
    "#D97706",  # amber
    "#059669",  # emerald
    "#0891B2",  # cyan
    "#DB2777",  # pink
    "#4F46E5",  # indigo (мягкий, не violet-glow)
    "#65A30D",  # lime
    "#EA580C",  # orange
]

EDGE_COLORS = {
    "ROLE_REQUIRES_SKILL": {"color": "#94A3B8", "opacity": 0.55},
    "ROLE_REQUIRES_CLUSTER": {"color": "#FB923C", "opacity": 0.45},
    "CLUSTER_CONTAINS_SKILL": {"color": "#FDBA74", "opacity": 0.4},
    "SKILL_CO_OCCURS": {"color": "#CBD5E1", "opacity": 0.35},
}

TYPE_SIZES = {
    "role": 44,
    "cluster": 30,
    "skill": 18,
}

# Современные шкалы для plotly-чартов пульса
PLOTLY_SEQUENTIAL = ["#CCFBF1", "#5EEAD4", "#14B8A6", "#0F766E", "#134E4A"]
PLOTLY_DIVERGING = ["#E11D48", "#FDA4AF", "#F1F5F9", "#5EEAD4", "#0D9488"]
PLOTLY_HEAT = ["#F8FAFC", "#BAE6FD", "#38BDF8", "#0284C7", "#0C4A6E"]


def role_color(role_group: str, role_order: Optional[list[str]] = None) -> str:
    if role_order and role_group in role_order:
        idx = role_order.index(role_group)
    else:
        idx = abs(hash(role_group)) % len(ROLE_PALETTE)
    return ROLE_PALETTE[idx % len(ROLE_PALETTE)]


def _node_color(node: dict[str, Any], role_order: Optional[list[str]] = None) -> dict[str, Any]:
    ntype = node.get("node_type") or "skill"
    if ntype == "role":
        role = node.get("role_group") or (node.get("label") or "").strip()
        if node.get("id", "").startswith("role:"):
            role = node["id"].replace("role:", "", 1)
        base = role_color(role, role_order)
        return {
            "background": base,
            "border": base,
            "highlight": {"background": base, "border": "#0F172A"},
        }
    if ntype == "cluster":
        return THEME["cluster"]
    return THEME["skill"]


def subgraph_to_pyvis_html(
    data: dict[str, Any],
    *,
    height: str = "680px",
    role_order: Optional[list[str]] = None,
) -> str:
    """Собирает HTML с vis.js: узлы можно перетаскивать, холст — панорамировать."""
    roles = data.get("roles") or role_order or []
    if isinstance(roles, str):
        roles = [roles]
    role_order = list(roles) if roles else role_order

    net = Network(
        height=height,
        width="100%",
        bgcolor=THEME["bg"],
        font_color=THEME["font"],
        directed=False,
        cdn_resources="remote",
    )

    for node in data.get("nodes", []):
        ntype = node.get("node_type") or "skill"
        support = float(node.get("support") or 0)
        size = TYPE_SIZES.get(ntype, 16)
        if ntype == "skill":
            size = 14 + int(32 * support)
        title_parts = [node.get("label") or node["id"], f"тип: {ntype}"]
        if ntype == "role" and node.get("id", "").startswith("role:"):
            title_parts.append(f"роль: {node['id'].replace('role:', '', 1)}")
        if node.get("cluster"):
            title_parts.append(f"кластер: {node['cluster']}")
        if support:
            title_parts.append(f"support: {support:.0%}")

        shape = "dot"
        if ntype == "role":
            shape = "box"
        elif ntype == "cluster":
            shape = "ellipse"

        net.add_node(
            node["id"],
            label=node.get("label") or node["id"],
            title="<br>".join(title_parts),
            color=_node_color(node, role_order),
            size=size,
            shape=shape,
            borderWidth=2 if ntype != "skill" else 1,
            borderWidthSelected=3,
            font={"size": 14 if ntype == "role" else 12, "face": "Inter, Segoe UI, system-ui, sans-serif"},
        )

    for edge in data.get("edges", []):
        et = edge.get("edge_type") or ""
        weight = float(edge.get("weight") or edge.get("support") or 0.3)
        width = 1.0 + 3.5 * min(1.0, weight)
        ec = EDGE_COLORS.get(et, {"color": "#E2E8F0", "opacity": 0.4})
        net.add_edge(
            edge["source"],
            edge["target"],
            color={"color": ec["color"], "opacity": ec["opacity"]},
            width=width,
            title=et,
        )

    net.set_options(
        """
        {
          "nodes": {
            "font": {"size": 13, "face": "Inter, Segoe UI, system-ui, sans-serif", "color": "#0F172A"},
            "shadow": {"enabled": true, "size": 6, "x": 0, "y": 2, "color": "rgba(15,23,42,0.12)"}
          },
          "edges": {
            "smooth": {"type": "continuous", "roundness": 0.3},
            "selectionWidth": 2
          },
          "physics": {
            "enabled": true,
            "stabilization": {"enabled": true, "iterations": 140, "fit": true},
            "barnesHut": {
              "gravitationalConstant": -16000,
              "centralGravity": 0.2,
              "springLength": 145,
              "springConstant": 0.018,
              "damping": 0.48,
              "avoidOverlap": 0.55
            }
          },
          "interaction": {
            "dragNodes": true,
            "dragView": true,
            "zoomView": true,
            "hover": true,
            "tooltipDelay": 100,
            "navigationButtons": true,
            "keyboard": {"enabled": true}
          }
        }
        """
    )
    return net.generate_html(notebook=False)


def _with_sticky_drag(html: str) -> str:
    """После стабилизации физика выключается — узлы остаются там, куда их перетащили."""
    snippet = """
<script type="text/javascript">
(function () {
  function bindSticky(net) {
    if (!net || net.__stickyBound) return;
    net.__stickyBound = true;
    net.once("stabilizationIterationsDone", function () {
      net.setOptions({ physics: { enabled: false } });
    });
    setTimeout(function () {
      try { net.setOptions({ physics: { enabled: false } }); } catch (e) {}
    }, 2500);
  }
  var _orig = window.drawGraph;
  if (typeof _orig === "function") {
    window.drawGraph = function () {
      var net = _orig.apply(this, arguments);
      bindSticky(window.network || net);
      return net;
    };
  } else {
    document.addEventListener("DOMContentLoaded", function () {
      var tries = 0;
      var t = setInterval(function () {
        tries += 1;
        if (window.network) {
          bindSticky(window.network);
          clearInterval(t);
        }
        if (tries > 40) clearInterval(t);
      }, 100);
    });
  }
})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", snippet + "</body>")
    return html + snippet


def render_subgraph_html(
    data: dict[str, Any],
    *,
    height: str = "680px",
    role_order: Optional[list[str]] = None,
) -> str:
    return _with_sticky_drag(
        subgraph_to_pyvis_html(data, height=height, role_order=role_order)
    )


# Мультивселенная: светлый «созвездие»-холст, навыки окрашены по тренду
MULTIVERSE_THEME = {
    "bg": "#EEF2F7",
    "font": "#0B1220",
    "skill_up": {
        "background": "#0D9488",
        "border": "#115E59",
        "highlight": {"background": "#2DD4BF", "border": "#0F766E"},
    },
    "skill_down": {
        "background": "#E11D48",
        "border": "#9F1239",
        "highlight": {"background": "#FB7185", "border": "#BE123C"},
    },
    "skill_flat": {
        "background": "#64748B",
        "border": "#334155",
        "highlight": {"background": "#94A3B8", "border": "#475569"},
    },
    "cluster": {
        "background": "#D97706",
        "border": "#92400E",
        "highlight": {"background": "#FBBF24", "border": "#B45309"},
    },
    "bridge": {
        "background": "#2563EB",
        "border": "#1E40AF",
        "highlight": {"background": "#60A5FA", "border": "#1D4ED8"},
    },
}


def _trend_skill_color(node: dict[str, Any]) -> dict[str, Any]:
    trend = float(node.get("trend") or 0)
    bridges = int(node.get("bridge_roles") or 1)
    if bridges >= 3:
        return MULTIVERSE_THEME["bridge"]
    if trend >= 0.04:
        return MULTIVERSE_THEME["skill_up"]
    if trend <= -0.04:
        return MULTIVERSE_THEME["skill_down"]
    return MULTIVERSE_THEME["skill_flat"]


def multiverse_to_pyvis_html(
    data: dict[str, Any],
    *,
    height: str = "760px",
    role_order: Optional[list[str]] = None,
) -> str:
    """Граф карьерных вселенных: роли = миры, навыки = пути/мосты, цвет = тренд."""
    roles = data.get("roles") or role_order or []
    if isinstance(roles, str):
        roles = [roles]
    role_order = list(roles) if roles else role_order

    net = Network(
        height=height,
        width="100%",
        bgcolor=MULTIVERSE_THEME["bg"],
        font_color=MULTIVERSE_THEME["font"],
        directed=False,
        cdn_resources="remote",
    )

    for node in data.get("nodes", []):
        ntype = node.get("node_type") or "skill"
        support = float(node.get("support") or 0)
        trend = float(node.get("trend") or 0)
        bridges = int(node.get("bridge_roles") or 1)
        size = TYPE_SIZES.get(ntype, 16)
        if ntype == "skill":
            size = 12 + int(28 * support) + (4 if bridges >= 3 else 0)
        elif ntype == "role":
            size = 48

        title_parts = [node.get("label") or node["id"], f"тип: {ntype}"]
        if ntype == "skill":
            title_parts.append(f"support: {support:.0%}")
            title_parts.append(f"тренд: {trend:+.1%}")
            if bridges > 1:
                title_parts.append(f"мост между {bridges} ролями")
        if node.get("cluster"):
            title_parts.append(f"кластер: {node['cluster']}")

        if ntype == "role":
            color = _node_color(node, role_order)
            shape = "box"
        elif ntype == "cluster":
            color = MULTIVERSE_THEME["cluster"]
            shape = "ellipse"
        else:
            color = _trend_skill_color(node)
            shape = "dot"

        net.add_node(
            node["id"],
            label=node.get("label") or node["id"],
            title="<br>".join(title_parts),
            color=color,
            size=size,
            shape=shape,
            borderWidth=3 if ntype == "role" else (2 if bridges >= 3 else 1),
            borderWidthSelected=4,
            font={
                "size": 15 if ntype == "role" else 12,
                "face": "IBM Plex Sans, Segoe UI, system-ui, sans-serif",
                "color": MULTIVERSE_THEME["font"],
            },
        )

    for edge in data.get("edges", []):
        et = edge.get("edge_type") or ""
        weight = float(edge.get("weight") or edge.get("support") or 0.3)
        width = 1.2 + 4.0 * min(1.0, weight)
        if et == "ROLE_REQUIRES_SKILL":
            ec = {"color": "#64748B", "opacity": 0.5}
        elif et == "SKILL_CO_OCCURS":
            ec = {"color": "#94A3B8", "opacity": 0.28}
        else:
            ec = EDGE_COLORS.get(et, {"color": "#CBD5E1", "opacity": 0.35})
        net.add_edge(
            edge["source"],
            edge["target"],
            color={"color": ec["color"], "opacity": ec["opacity"]},
            width=width,
            title=et,
        )

    net.set_options(
        """
        {
          "nodes": {
            "shadow": {"enabled": true, "size": 8, "x": 0, "y": 3, "color": "rgba(15,23,42,0.14)"}
          },
          "edges": {
            "smooth": {"type": "cubicBezier", "forceDirection": "none", "roundness": 0.45},
            "selectionWidth": 3
          },
          "physics": {
            "enabled": true,
            "stabilization": {"enabled": true, "iterations": 180, "fit": true},
            "barnesHut": {
              "gravitationalConstant": -22000,
              "centralGravity": 0.12,
              "springLength": 170,
              "springConstant": 0.014,
              "damping": 0.52,
              "avoidOverlap": 0.7
            }
          },
          "interaction": {
            "dragNodes": true,
            "dragView": true,
            "zoomView": true,
            "hover": true,
            "tooltipDelay": 80,
            "navigationButtons": true,
            "keyboard": {"enabled": true}
          }
        }
        """
    )
    return net.generate_html(notebook=False)


def render_multiverse_html(
    data: dict[str, Any],
    *,
    height: str = "760px",
    role_order: Optional[list[str]] = None,
) -> str:
    return _with_sticky_drag(
        multiverse_to_pyvis_html(data, height=height, role_order=role_order)
    )


def universes_map_to_pyvis_html(
    adjacent: list[dict[str, Any]],
    *,
    target_role: str,
    height: str = "560px",
) -> str:
    """
    Карта карьерных вселенных:
    центр — вы; основная вселенная — главный путь; вокруг — соседние миры.
    Размер и толщина связи ~ готовность.
    """
    net = Network(
        height=height,
        width="100%",
        bgcolor="#F1F5F9",
        font_color="#0B1220",
        directed=False,
        cdn_resources="remote",
    )

    net.add_node(
        "you",
        label="Вы",
        title="Ваш текущий профиль навыков",
        color={
            "background": "#0F172A",
            "border": "#020617",
            "highlight": {"background": "#334155", "border": "#0F172A"},
        },
        size=42,
        shape="dot",
        borderWidth=3,
        font={"size": 16, "face": "IBM Plex Sans, Segoe UI, system-ui, sans-serif", "color": "#F8FAFC"},
        x=0,
        y=0,
        fixed={"x": True, "y": True},
    )

    # раскладка по кругу: целевая ближе к центру, остальные дальше при низкой готовности
    others = [a for a in adjacent if not a.get("is_target") and a.get("role") != target_role]
    target = next(
        (a for a in adjacent if a.get("is_target") or a.get("role") == target_role),
        {"role": target_role, "readiness": 0.0, "covered": 0, "required": 0, "matching_top": [], "missing_top": [], "is_target": True},
    )
    nodes_orbit = [target] + others

    n = max(len(nodes_orbit), 1)
    for i, univ in enumerate(nodes_orbit):
        role = str(univ.get("role") or "")
        ready = float(univ.get("readiness") or 0)
        is_main = bool(univ.get("is_target") or role == target_role)
        covered = int(univ.get("covered") or 0)
        required = int(univ.get("required") or 0)
        matching = univ.get("matching_top") or []
        missing = univ.get("missing_top") or []

        # радиус: основной путь ближе, слабая готовность — дальше
        base_r = 180 if is_main else 280
        radius = base_r + int(140 * (1.0 - ready))
        angle = (2 * math.pi * i / n) - (math.pi / 2)
        if is_main:
            angle = -math.pi / 2  # сверху от «Вы»
            radius = 160 + int(80 * (1.0 - ready))

        size = 28 + int(36 * ready) if is_main else 18 + int(28 * ready)
        if is_main:
            color = {
                "background": "#0D9488",
                "border": "#115E59",
                "highlight": {"background": "#2DD4BF", "border": "#0F766E"},
            }
            label = f"★ {role}"
            path_label = "основной путь"
        else:
            # чем выше готовность — тем «теплее» сосед
            if ready >= 0.45:
                color = {
                    "background": "#2563EB",
                    "border": "#1E40AF",
                    "highlight": {"background": "#60A5FA", "border": "#1D4ED8"},
                }
            elif ready >= 0.25:
                color = {
                    "background": "#D97706",
                    "border": "#92400E",
                    "highlight": {"background": "#FBBF24", "border": "#B45309"},
                }
            else:
                color = {
                    "background": "#94A3B8",
                    "border": "#64748B",
                    "highlight": {"background": "#CBD5E1", "border": "#475569"},
                }
            label = role
            path_label = "соседняя вселенная"

        title = (
            f"<b>{role}</b><br>"
            f"{path_label}<br>"
            f"готовность: {ready:.0%}<br>"
            f"закрыто ядро: {covered}/{required}<br>"
            f"есть: {', '.join(matching[:5]) or '—'}<br>"
            f"не хватает: {', '.join(missing[:5]) or '—'}"
        )

        net.add_node(
            f"univ:{role}",
            label=label,
            title=title,
            color=color,
            size=size,
            shape="box" if is_main else "ellipse",
            borderWidth=4 if is_main else 2,
            font={
                "size": 14 if is_main else 12,
                "face": "IBM Plex Sans, Segoe UI, system-ui, sans-serif",
                "color": "#0B1220",
            },
            x=int(radius * math.cos(angle)),
            y=int(radius * math.sin(angle)),
            fixed={"x": False, "y": False},
        )

        edge_width = 2.5 + 8.0 * ready if is_main else 1.0 + 5.0 * ready
        edge_color = "#0D9488" if is_main else ("#2563EB" if ready >= 0.35 else "#94A3B8")
        net.add_edge(
            "you",
            f"univ:{role}",
            title=f"{path_label}: готовность {ready:.0%}",
            width=edge_width,
            color={"color": edge_color, "opacity": 0.75 if is_main else 0.45},
            label=f"{ready:.0%}",
            font={"size": 11, "color": "#475569", "strokeWidth": 0},
        )

    # слабые связи между соседними вселенными с похожей готовностью (ощущение мультивселенной)
    for i, a in enumerate(others):
        for b in others[i + 1 :]:
            ra, rb = float(a.get("readiness") or 0), float(b.get("readiness") or 0)
            if abs(ra - rb) > 0.15:
                continue
            overlap = len(set(a.get("matching_top") or []) & set(b.get("matching_top") or []))
            if overlap < 1 and (ra + rb) < 0.5:
                continue
            net.add_edge(
                f"univ:{a['role']}",
                f"univ:{b['role']}",
                width=0.8 + 0.4 * overlap,
                color={"color": "#CBD5E1", "opacity": 0.35},
                title=f"близкие миры · общих навыков в профиле: {overlap}",
            )

    net.set_options(
        """
        {
          "nodes": {
            "shadow": {"enabled": true, "size": 10, "x": 0, "y": 3, "color": "rgba(15,23,42,0.14)"}
          },
          "edges": {
            "smooth": {"type": "continuous", "roundness": 0.35},
            "selectionWidth": 2
          },
          "physics": {
            "enabled": true,
            "stabilization": {"enabled": true, "iterations": 120, "fit": true},
            "barnesHut": {
              "gravitationalConstant": -12000,
              "centralGravity": 0.35,
              "springLength": 160,
              "springConstant": 0.02,
              "damping": 0.55,
              "avoidOverlap": 0.8
            }
          },
          "interaction": {
            "dragNodes": true,
            "dragView": true,
            "zoomView": true,
            "hover": true,
            "tooltipDelay": 60,
            "navigationButtons": true,
            "keyboard": {"enabled": true}
          }
        }
        """
    )
    return net.generate_html(notebook=False)


def render_universes_map_html(
    adjacent: list[dict[str, Any]],
    *,
    target_role: str,
    height: str = "560px",
) -> str:
    return _with_sticky_drag(
        universes_map_to_pyvis_html(adjacent, target_role=target_role, height=height)
    )
