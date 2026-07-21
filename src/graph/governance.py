"""Блок 3 · KG Governance: журнал решений SAA / CRA / Evaluator."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from src.db.models import KgQuarantine, Vacancy, VacancySkill
from src.graph.normalize import STOPLIST, is_noise, normalize_text

# лимит детальных строк на reason (summary всегда пишется отдельно)
MAX_DETAIL_ROWS = 200


class GovernanceLedger:
    """Собирает решения агентов 5–7 за один прогон сборки графа."""

    def __init__(self, *, sources: str = "hh") -> None:
        self.sources = sources
        self.built_at = datetime.now(timezone.utc)
        self._rows: list[dict[str, Any]] = []
        self.counts: Counter[str] = Counter()

    def add(
        self,
        *,
        agent: str,
        reason: str,
        item_type: str,
        item_key: str,
        decision: str = "reject",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        self.counts[f"{agent}:{reason}"] += 1
        if item_type != "summary" and self.counts[f"{agent}:{reason}:detail"] >= MAX_DETAIL_ROWS:
            return
        if item_type != "summary":
            self.counts[f"{agent}:{reason}:detail"] += 1
        self._rows.append(
            {
                "agent": agent,
                "decision": decision,
                "reason": reason,
                "item_type": item_type,
                "item_key": (item_key or "")[:500],
                "detail": detail or {},
                "sources": self.sources,
                "built_at": self.built_at,
            }
        )

    def add_summary(self, *, agent: str, reason: str, count: int, **extra: Any) -> None:
        if count <= 0:
            return
        self.add(
            agent=agent,
            reason=reason,
            item_type="summary",
            item_key=f"*{reason}",
            decision="reject",
            detail={"count": count, **extra},
        )

    def persist(self, session: Session) -> int:
        session.execute(delete(KgQuarantine).where(KgQuarantine.sources == self.sources))
        for row in self._rows:
            session.add(KgQuarantine(**row))
        session.flush()
        return len(self._rows)


def ensure_quarantine_table() -> None:
    from src.db.session import engine

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS kg_quarantine (
                    id SERIAL PRIMARY KEY,
                    agent VARCHAR(32) NOT NULL,
                    decision VARCHAR(16) NOT NULL DEFAULT 'reject',
                    reason VARCHAR(64) NOT NULL,
                    item_type VARCHAR(32) NOT NULL,
                    item_key VARCHAR(512) NOT NULL,
                    detail JSONB DEFAULT '{}'::jsonb,
                    sources VARCHAR(64) NOT NULL DEFAULT 'hh',
                    built_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kg_quarantine_agent ON kg_quarantine (agent)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kg_quarantine_reason ON kg_quarantine (reason)"
            )
        )


def reject_reason_for_raw(raw: str) -> Optional[str]:
    """Почему SAA/CRA отбросил сырой навык до canonical."""
    if is_noise(raw):
        return "noise"
    norm = normalize_text(raw)
    if not norm:
        return "empty"
    if norm in STOPLIST:
        return "stoplist"
    return None


def count_optional_excluded(session: Session, *, sources: Optional[list[str]]) -> int:
    q = (
        select(func.count())
        .select_from(VacancySkill)
        .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
        .where(VacancySkill.source == "llm_optional")
    )
    if sources:
        q = q.where(Vacancy.data_source.in_(sources))
    return int(session.scalar(q) or 0)


def corpus_governance_stats(session: Session, *, sources: str = "hh") -> dict[str, Any]:
    rows = session.execute(
        select(KgQuarantine.agent, KgQuarantine.reason, func.count())
        .where(
            KgQuarantine.sources == sources,
            KgQuarantine.item_type != "summary",
        )
        .group_by(KgQuarantine.agent, KgQuarantine.reason)
    ).all()
    by_reason = {f"{a}:{r}": int(n) for a, r, n in rows}
    summaries = session.scalars(
        select(KgQuarantine).where(
            KgQuarantine.sources == sources,
            KgQuarantine.item_type == "summary",
        )
    ).all()
    built = session.scalar(
        select(func.max(KgQuarantine.built_at)).where(KgQuarantine.sources == sources)
    )
    total = session.scalar(
        select(func.count())
        .select_from(KgQuarantine)
        .where(KgQuarantine.sources == sources, KgQuarantine.item_type != "summary")
    ) or 0
    return {
        "sources": sources,
        "built_at": built.isoformat() if built else None,
        "detail_rows": int(total),
        "by_reason": by_reason,
        "summaries": [
            {
                "agent": s.agent,
                "reason": s.reason,
                "count": (s.detail or {}).get("count"),
                "detail": s.detail or {},
            }
            for s in summaries
        ],
    }
