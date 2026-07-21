"""Блок 1 · Агент 2: вырезка секций вакансии (правила / заголовки).

Оставляет обязанности + требования, отбрасывает «о компании / условия / бенефиты».
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from tqdm import tqdm

from src.db.models import Vacancy
from src.db.session import Base, SessionLocal, engine

MIN_DESC_LEN = 50
MAX_DESC_CHARS_LLM = 2_500
# секции длиннее списка скиллов — больше timeout/predict
LLM_TIMEOUT_SEC = 120
LLM_NUM_PREDICT = 768

SECTION_LLM_SYSTEM = """Ты размечаешь текст вакансии на секции.
Ответь ТОЛЬКО валидным JSON без markdown.
Схема:
{"responsibilities":"...","requirements":"...","discarded":"..."}
Правила:
- responsibilities: обязанности, задачи, что делать на работе
- requirements: требования к навыкам, опыту, образованию, «будет плюсом»
- discarded: о компании, условия, зарплата/бенефиты, процесс отклика, вода
- не выдумывай текст — только переноси/сокращай исходник
- если блока нет — пустая строка
"""

SECTION_LLM_USER = """Заголовок: {title}

Текст вакансии:
{description}

JSON:"""


# (kind, compiled pattern) — kind: responsibilities | requirements | discard
_HEADER_SPECS: list[tuple[str, re.Pattern[str]]] = [
    # responsibilities
    ("responsibilities", re.compile(r"(?i)^\s*(обязанност\w*|чем\s+(будешь|предстоит)\s+заниматься|чем\s+заниматься|что\s+нужно\s+делать|что\s+делать|задач[аи]\b|функционал\w*|основные\s+задачи|ваши\s+задачи)\s*[:.\-]?\s*$")),
    ("responsibilities", re.compile(r"(?i)^\s*(responsibilit\w*|what\s+you(?:'ll|\s+will)\s+do|key\s+responsibilit\w*|duties)\s*[:.\-]?\s*$")),
    # requirements
    ("requirements", re.compile(r"(?i)^\s*(требован\w*|мы\s+ждем(?:,\s*что\s+вы)?|мы\s+ждём(?:,\s*что\s+вы)?|ожидан\w*|необходим\w*\s+навык\w*|необходимые\s+компетенц\w*|наш\s+идеальный\s+кандидат|что\s+для\s+нас\s+важно|будет\s+плюсом|желательно|nice\s+to\s+have|will\s+be\s+a\s+plus)\s*[:.\-]?\s*$")),
    ("requirements", re.compile(r"(?i)^\s*(requirements?|we\s+expect|what\s+we\s+expect|must[- ]have|nice[- ]to[- ]have|qualifications?)\s*[:.\-]?\s*$")),
    # discard
    ("discard", re.compile(r"(?i)^\s*(услови\w*|мы\s+предлагаем|что\s+мы\s+предлагаем|о\s+компании|о\s+проекте|о\s+команде|бонус\w*|льгот\w*|как\s+откликнуть?ся|этапы\s+отбора|процесс\s+найма|о\s+нас)\s*[:.\-]?\s*$")),
    ("discard", re.compile(r"(?i)^\s*(benefits?|we\s+offer|about\s+(the\s+)?(company|team|project)|perks?|how\s+to\s+apply)\s*[:.\-]?\s*$")),
]

# якоря, которые вставляем как границы секций даже в сплошном тексте
_BOUNDARY_SPECS: list[tuple[str, re.Pattern[str]]] = [
    ("responsibilities", re.compile(
        r"(?i)(?P<header>\b(?:обязанности|основные\s+задачи|ваши\s+задачи|функционал|"
        r"чем\s+(?:будешь|предстоит)\s+заниматься|что\s+нужно\s+делать|"
        r"responsibilities|what\s+you(?:'ll|\s+will)\s+do)\s*[:.?!]*)"
    )),
    ("requirements", re.compile(
        r"(?i)(?P<header>\b(?:требования|"
        r"мы\s+ждём,\s*что\s+вы|мы\s+ждем,\s*что\s+вы|мы\s+ждём|мы\s+ждем|"
        r"ожидания|наш\s+идеальный\s+кандидат|будет\s+плюсом|желательно|"
        r"requirements|we\s+expect|nice\s+to\s+have|must[- ]have)\s*[:.?!]*)"
    )),
    ("discard", re.compile(
        r"(?i)(?P<header>\b(?:условия|мы\s+предлагаем|что\s+мы\s+предлагаем|"
        r"о\s+компании|о\s+проекте|о\s+команде|бонусы|льготы|"
        r"benefits|we\s+offer|about\s+(?:the\s+)?(?:company|team))\s*[:.?!]*)"
    )),
]


def _insert_boundaries(text: str) -> str:
    """Вставляет переносы перед типовыми заголовками в сплошном тексте hh."""
    spans: list[tuple[int, int, str]] = []
    for kind, pat in _BOUNDARY_SPECS:
        for m in pat.finditer(text):
            spans.append((m.start("header"), m.end("header"), kind))
    if not spans:
        return text
    spans.sort(key=lambda x: x[0])
    kept: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, kind in spans:
        if start < last_end:
            continue
        kept.append((start, end, kind))
        last_end = end
    out: list[str] = []
    cursor = 0
    for start, end, _kind in kept:
        out.append(text[cursor:start])
        out.append("\n")
        out.append(text[start:end].rstrip(".:!?") + ":")
        out.append("\n")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


@dataclass
class SectionSplitResult:
    responsibilities: str = ""
    requirements: str = ""
    discarded: str = ""
    method: str = "rules"
    headers_found: list[dict[str, Any]] = field(default_factory=list)
    kept_ratio: float = 0.0
    lengths: dict[str, int] = field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "headers_found": self.headers_found,
            "kept_ratio": round(self.kept_ratio, 4),
            "lengths": self.lengths,
            "split_at": datetime.now(timezone.utc).isoformat(),
        }


def ensure_section_columns() -> None:
    """create_all не добавляет колонки к существующей таблице — ALTER IF NOT EXISTS."""
    Base.metadata.create_all(bind=engine)
    alters = [
        "ALTER TABLE vacancies ADD COLUMN IF NOT EXISTS section_responsibilities TEXT",
        "ALTER TABLE vacancies ADD COLUMN IF NOT EXISTS section_requirements TEXT",
        "ALTER TABLE vacancies ADD COLUMN IF NOT EXISTS section_discarded TEXT",
        "ALTER TABLE vacancies ADD COLUMN IF NOT EXISTS section_meta JSONB DEFAULT '{}'::jsonb",
    ]
    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))


def _normalize_lines(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("ё", "е").replace("Ё", "Е")
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = _insert_boundaries(raw)
    lines = [ln.strip() for ln in raw.split("\n")]
    return [ln for ln in lines if ln]


def _match_header(line: str) -> Optional[str]:
    # strip trailing punctuation for matching short headers like "Обязанности:"
    probe = line.strip()
    for kind, pat in _HEADER_SPECS:
        if pat.match(probe):
            return kind
    # also allow header + rest on same line: "Требования: Python, SQL"
    m = re.match(
        r"(?i)^\s*(обязанности|требования|условия|мы предлагаем|"
        r"мы\s+ждём(?:,\s*что\s+вы)?|мы\s+ждем(?:,\s*что\s+вы)?|"
        r"чем будешь заниматься|что нужно делать|будет плюсом|"
        r"responsibilities|requirements|benefits|we offer)\s*:\s*(.+)$",
        probe,
    )
    if m:
        head = m.group(1).lower()
        if head.startswith("обязан") or "занимать" in head or "делать" in head or head.startswith("respons"):
            return "responsibilities"
        if head.startswith("треб") or "ждём" in head or "ждем" in head or "плюс" in head or head.startswith("requir"):
            return "requirements"
        return "discard"
    return None


def split_description(description: str) -> SectionSplitResult:
    """Rule-based split of vacancy description into keep / discard sections."""
    text = (description or "").strip()
    if len(text) < MIN_DESC_LEN:
        return SectionSplitResult(
            method="empty",
            kept_ratio=0.0,
            lengths={"full": len(text), "responsibilities": 0, "requirements": 0, "discarded": 0},
        )

    lines = _normalize_lines(text)
    # Find header positions
    headers: list[tuple[int, str, str]] = []  # (line_idx, kind, raw)
    for i, line in enumerate(lines):
        kind = _match_header(line)
        if kind:
            headers.append((i, kind, line))

    if not headers:
        # no structure — keep everything as requirements, mark weak quality
        return SectionSplitResult(
            responsibilities="",
            requirements=text,
            discarded="",
            method="fallback_full",
            headers_found=[],
            kept_ratio=1.0,
            lengths={
                "full": len(text),
                "responsibilities": 0,
                "requirements": len(text),
                "discarded": 0,
            },
        )

    buckets: dict[str, list[str]] = {
        "responsibilities": [],
        "requirements": [],
        "discard": [],
        "preamble": [],
    }
    # preamble = text before first header → discard (usually about company)
    first_idx = headers[0][0]
    if first_idx > 0:
        buckets["preamble"].extend(lines[:first_idx])

    for h_i, (start, kind, raw_header) in enumerate(headers):
        end = headers[h_i + 1][0] if h_i + 1 < len(headers) else len(lines)
        body_lines = lines[start + 1 : end]
        inline = re.match(
            r"(?i)^\s*(?:обязанности|требования|условия|мы предлагаем|мы ждём|мы ждем|"
            r"чем будешь заниматься|что нужно делать|будет плюсом|"
            r"responsibilities|requirements|benefits|we offer)\s*:\s*(.+)$",
            raw_header,
        )
        chunk: list[str] = []
        if inline and inline.group(1).strip():
            chunk.append(inline.group(1).strip())
        chunk.extend(body_lines)
        buckets[kind if kind != "discard" else "discard"].extend(chunk)

    resp = "\n".join(buckets["responsibilities"]).strip()
    req = "\n".join(buckets["requirements"]).strip()
    discarded_parts = buckets["preamble"] + buckets["discard"]
    discarded = "\n".join(discarded_parts).strip()

    method = "rules"
    if not resp and not req:
        req = text
        discarded = ""
        method = "fallback_full"

    kept_len = len(resp) + len(req)
    full_len = max(len(text), 1)
    kept_ratio = min(1.0, kept_len / full_len)

    return SectionSplitResult(
        responsibilities=resp,
        requirements=req,
        discarded=discarded,
        method=method,
        headers_found=[{"kind": k, "header": h, "line": i} for i, k, h in headers],
        kept_ratio=kept_ratio,
        lengths={
            "full": len(text),
            "responsibilities": len(resp),
            "requirements": len(req),
            "discarded": len(discarded),
        },
    )


def _parse_section_llm_json(raw: str) -> dict[str, str]:
    from src.market.extract_skills_llm import _repair_json_text, _strip_fences

    import json

    blob = _repair_json_text(raw)
    try:
        payload = json.loads(blob)
    except Exception:
        payload = json.loads(_strip_fences(raw))
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")

    def _s(key: str) -> str:
        val = payload.get(key)
        if val is None:
            return ""
        if isinstance(val, list):
            return "\n".join(str(x).strip() for x in val if str(x).strip())
        return str(val).strip()

    return {
        "responsibilities": _s("responsibilities"),
        "requirements": _s("requirements"),
        "discarded": _s("discarded"),
    }


def split_description_llm(
    description: str,
    *,
    title: str = "",
    provider: Optional[str] = None,
) -> SectionSplitResult:
    """LLM-разметка секций для текстов без явных заголовков."""
    from src.market.extract_skills_llm import _chat_completion

    text = (description or "").strip()
    if len(text) < MIN_DESC_LEN:
        return SectionSplitResult(method="empty", kept_ratio=0.0)

    desc = text if len(text) <= MAX_DESC_CHARS_LLM else text[:MAX_DESC_CHARS_LLM] + "…"
    messages = [
        {"role": "system", "content": SECTION_LLM_SYSTEM},
        {
            "role": "user",
            "content": SECTION_LLM_USER.format(title=title or "", description=desc),
        },
    ]
    content = _chat_completion(
        messages,
        temperature=0,
        provider=provider,
        timeout_sec=LLM_TIMEOUT_SEC,
        num_predict=LLM_NUM_PREDICT,
    )
    parsed = _parse_section_llm_json(content)
    resp = parsed["responsibilities"]
    req = parsed["requirements"]
    discarded = parsed["discarded"]
    if not resp and not req:
        # LLM пусто — оставляем fallback, не хуже исходника
        return SectionSplitResult(
            responsibilities="",
            requirements=text,
            discarded="",
            method="fallback_full",
            headers_found=[],
            kept_ratio=1.0,
            lengths={
                "full": len(text),
                "responsibilities": 0,
                "requirements": len(text),
                "discarded": 0,
            },
        )
    kept_len = len(resp) + len(req)
    full_len = max(len(text), 1)
    return SectionSplitResult(
        responsibilities=resp,
        requirements=req,
        discarded=discarded,
        method="llm",
        headers_found=[{"kind": "llm", "header": "llm_section_split", "line": -1}],
        kept_ratio=min(1.0, kept_len / full_len),
        lengths={
            "full": len(text),
            "responsibilities": len(resp),
            "requirements": len(req),
            "discarded": len(discarded),
        },
    )


def split_description_hybrid(
    description: str,
    *,
    title: str = "",
    llm_fallback: bool = False,
    provider: Optional[str] = None,
) -> SectionSplitResult:
    """Rules first; LLM только если method=fallback_full и llm_fallback=True."""
    result = split_description(description)
    if llm_fallback and result.method == "fallback_full":
        try:
            return split_description_llm(
                description, title=title, provider=provider
            )
        except Exception as exc:  # noqa: BLE001
            # сохраняем rules-fallback, помечаем ошибку в meta через headers
            result.headers_found = [
                {
                    "kind": "llm_error",
                    "header": str(exc)[:200],
                    "line": -1,
                }
            ]
            return result
    return result


def apply_split_to_vacancy(vacancy: Vacancy, result: SectionSplitResult) -> None:
    vacancy.section_responsibilities = result.responsibilities or None
    vacancy.section_requirements = result.requirements or None
    vacancy.section_discarded = result.discarded or None
    vacancy.section_meta = result.to_meta()


def kept_text(vacancy: Vacancy) -> str:
    """Текст для следующего блока (extract): обязанности + требования."""
    parts = [
        (vacancy.section_responsibilities or "").strip(),
        (vacancy.section_requirements or "").strip(),
    ]
    joined = "\n\n".join(p for p in parts if p)
    return joined or (vacancy.description_text or "")


def run_split(
    *,
    data_source: str = "hh",
    limit: Optional[int] = None,
    offset: int = 0,
    resume: bool = True,
    vacancy_id: Optional[int] = None,
    llm_fallback: bool = False,
    only_fallback: bool = False,
    provider: Optional[str] = None,
) -> dict[str, int]:
    ensure_section_columns()
    stats = {
        "candidates": 0,
        "processed": 0,
        "rules": 0,
        "fallback_full": 0,
        "llm": 0,
        "empty": 0,
        "skipped_resume": 0,
        "llm_fallback": int(llm_fallback),
    }

    with SessionLocal() as session:
        if vacancy_id is not None:
            vacs = list(session.scalars(select(Vacancy).where(Vacancy.id == vacancy_id)).all())
        else:
            q = (
                select(Vacancy)
                .where(
                    Vacancy.data_source == data_source,
                    Vacancy.description_text.is_not(None),
                    func.length(Vacancy.description_text) >= MIN_DESC_LEN,
                )
                .order_by(Vacancy.id)
                .offset(offset)
            )
            if only_fallback:
                q = q.where(Vacancy.section_meta["method"].astext == "fallback_full")
            elif resume:
                # ещё не размечены (нет method в meta)
                q = q.where(
                    (Vacancy.section_meta.is_(None))
                    | (~Vacancy.section_meta.has_key("method"))
                )
            if limit is not None:
                q = q.limit(limit)
            vacs = list(session.scalars(q).all())
            if resume and not only_fallback:
                # skipped_resume не считаем точно без полного scan — оставляем 0
                pass

        stats["candidates"] = len(vacs)
        for vac in tqdm(vacs, desc=f"split sections [{data_source}]"):
            result = split_description_hybrid(
                vac.description_text or "",
                title=vac.name or "",
                llm_fallback=llm_fallback,
                provider=provider,
            )
            apply_split_to_vacancy(vac, result)
            stats["processed"] += 1
            stats[result.method] = stats.get(result.method, 0) + 1
            session.commit()

    print("Section split done:", stats)
    return stats


def corpus_section_stats(session: Session, *, data_source: str = "hh") -> dict[str, Any]:
    total = session.scalar(
        select(func.count())
        .select_from(Vacancy)
        .where(
            Vacancy.data_source == data_source,
            Vacancy.description_text.is_not(None),
            func.length(Vacancy.description_text) >= MIN_DESC_LEN,
        )
    ) or 0
    split = session.scalar(
        select(func.count())
        .select_from(Vacancy)
        .where(
            Vacancy.data_source == data_source,
            Vacancy.section_meta.is_not(None),
            Vacancy.section_meta != {},
        )
    ) or 0
    # methods via raw SQL for JSONB
    rows = session.execute(
        text(
            """
            select coalesce(section_meta->>'method', 'none') as method, count(*)
            from vacancies
            where data_source = :src
              and description_text is not null
              and length(description_text) >= :min_len
            group by 1
            order by 2 desc
            """
        ),
        {"src": data_source, "min_len": MIN_DESC_LEN},
    ).fetchall()
    by_method = {r[0]: int(r[1]) for r in rows}
    avg_kept = session.execute(
        text(
            """
            select avg((section_meta->>'kept_ratio')::float)
            from vacancies
            where data_source = :src
              and section_meta ? 'kept_ratio'
            """
        ),
        {"src": data_source},
    ).scalar()
    return {
        "data_source": data_source,
        "with_description": total,
        "split_done": split,
        "by_method": by_method,
        "avg_kept_ratio": float(avg_kept or 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Split vacancy descriptions into sections (Block 1)")
    parser.add_argument("--source", default="hh")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Пропускать уже разрезанные (default: true)",
    )
    parser.add_argument("--vacancy-id", type=int, default=None)
    parser.add_argument(
        "--llm-fallback",
        action="store_true",
        help="Если rules → fallback_full, доразметить через LLM (Ollama/Groq)",
    )
    parser.add_argument(
        "--only-fallback",
        action="store_true",
        help="Только вакансии с method=fallback_full (удобно с --llm-fallback)",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "groq", "ollama", "openai"],
        default=None,
        help="LLM provider для fallback (default: LLM_PROVIDER / auto)",
    )
    args = parser.parse_args()
    run_split(
        data_source=args.source,
        limit=args.limit,
        offset=args.offset,
        resume=args.resume,
        vacancy_id=args.vacancy_id,
        llm_fallback=args.llm_fallback,
        only_fallback=args.only_fallback,
        provider=args.provider,
    )


if __name__ == "__main__":
    main()
