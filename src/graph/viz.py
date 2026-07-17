"""Интерактивная визуализация подграфа (drag / pan / zoom)."""

from __future__ import annotations

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
