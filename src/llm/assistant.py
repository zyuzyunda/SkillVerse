"""LLM-ассистент: Groq при наличии ключа, иначе детерминированный ответ по фактам."""

from __future__ import annotations

from typing import Optional

from src.config import settings
from src.llm.context import build_context, context_to_prompt_block
from src.llm.scenarios import detect_intent


SYSTEM_PROMPT = """Ты HR-аналитик AI-платформы управления компетенциями предприятия.
Отвечай по-русски, кратко и по делу (6–12 предложений или короткий маркированный список).
Используй ТОЛЬКО факты из блока CONTEXT. Не выдумывай сотрудников, курсы и проценты.
Если данных мало — так и скажи. В конце дай 1–2 конкретных следующих шага для HR.
"""


def _fallback_answer(question: str, ctx: dict, intent: str) -> str:
    role = ctx["role_group"]
    parts: list[str] = [f"**Роль:** `{role}`"]

    if intent in {"gaps", "market", "overview"}:
        parts.append("**Ключевые дефициты относительно рынка:**")
        for g in ctx["gaps"][:5]:
            parts.append(
                f"- {g['skill']}: рынок {g['market']:.0%}, в компании {g['org']:.0%}, "
                f"разрыв {g['gap']:.0%}"
                + (f", тренд +{g['trend']:.2f}" if g["trend"] > 0.2 else "")
            )
        if ctx["growing_skills"]:
            grow = ", ".join(g["skill"] for g in ctx["growing_skills"][:5])
            parts.append(f"**Растут на рынке:** {grow}.")

    if intent in {"train", "overview"}:
        parts.append("**Кого обучить в первую очередь:**")
        for t in ctx["trainees"][:4]:
            parts.append(f"- {t['name']} ({t['position']}): {', '.join(t['missing'][:3])}")

    if intent in {"courses", "overview"}:
        parts.append("**Курсы для закрытия дефицита:**")
        actionable = [c for c in ctx["courses"] if c["candidates"] > 0] or ctx["courses"]
        for c in actionable[:3]:
            parts.append(
                f"- {c['title']} — навыки: {', '.join(c['skills'])}; "
                f"потенциальных участников: {c['candidates']}"
            )

    if intent in {"mobility", "overview"}:
        parts.append("**Внутренняя мобильность / усиление команды:**")
        if not ctx["mobility"]:
            parts.append("- Подходящих кандидатов с достаточным overlap пока мало.")
        for m in ctx["mobility"][:4]:
            parts.append(
                f"- {m['name']} ({m['from']}): совпадение {m['overlap']:.0%}; "
                f"дорастить: {', '.join(m['missing'][:3]) or '—'}"
            )

    parts.append(
        "**Следующие шаги:** 1) согласовать приоритетный курс с наибольшим числом участников; "
        "2) назначить обучение топ-кандидатам и пересчитать покрытие через квартал."
    )
    parts.append("")
    parts.append("_Ответ сформирован по данным графа и модуля рекомендаций (без LLM)._")
    return "\n".join(parts)


def _groq_answer(question: str, ctx_block: str) -> str:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    completion = client.chat.completions.create(
        model=settings.llm_model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"CONTEXT:\n{ctx_block}\n\nQUESTION:\n{question}",
            },
        ],
    )
    return (completion.choices[0].message.content or "").strip()


def ask(
    question: str,
    *,
    role_group: Optional[str] = None,
    department_code: Optional[str] = None,
    use_llm: bool = True,
    intent: Optional[str] = None,
) -> dict:
    resolved_intent = intent or detect_intent(question)
    ctx = build_context(
        question, role_group=role_group, department_code=department_code
    )
    ctx_block = context_to_prompt_block(ctx)

    mode = "fallback"
    answer = _fallback_answer(question, ctx, resolved_intent)
    if use_llm and settings.groq_api_key:
        try:
            answer = _groq_answer(question, ctx_block)
            mode = "groq"
        except Exception as exc:  # noqa: BLE001
            answer = (
                _fallback_answer(question, ctx, resolved_intent)
                + f"\n\n_LLM недоступен ({exc.__class__.__name__}), показан ответ по фактам._"
            )
            mode = "fallback_error"

    return {
        "answer": answer,
        "mode": mode,
        "intent": resolved_intent,
        "role_group": ctx["role_group"],
        "context": ctx,
    }
