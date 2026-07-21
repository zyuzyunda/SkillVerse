"""LLM-извлечение навыков из вакансий (промпт по мотивам helper).

По умолчанию читает kept-секции (обязанности + требования) после Block 1;
если секций нет — fallback на description_text.

Пишет в vacancy_skills:
  - source=llm_extract  (required)
  - source=llm_optional (желательно / плюсом)

Не трогает hh_key_skills — обогащение = union на этапе сборки графа.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tqdm import tqdm

from src.config import settings
from src.db.models import Vacancy, VacancySkill
from src.db.session import Base, SessionLocal, engine
from src.market.hh_client import DESCRIPTION_SKILL_PATTERNS

SOURCE_REQUIRED = "llm_extract"
SOURCE_OPTIONAL = "llm_optional"
# маркер «LLM уже пробовали» — чтобы --resume не долбил вакансии без IT-скиллов
LLM_DONE_MARKER = "__llm_done__"
MIN_DESC_LEN = 50
# маленькая локальная Llama плохо держит длинный контекст
MAX_DESC_CHARS = 2_500
DEFAULT_SLEEP_SEC = 0.05
MAX_RETRIES = 1  # при timeout/ошибке сразу regex — не копить очередь в Ollama
OLLAMA_TIMEOUT_SEC = 30
OLLAMA_NUM_PREDICT = 256
OLLAMA_NUM_CTX = 2048

SYSTEM_PROMPT = """Ты извлекаешь hard skills из вакансий.
Ответь ТОЛЬКО валидным JSON без markdown.
Схема:
{"skills_required":["..."],"skills_optional":["..."]}
Правила:
- skills_required: обязательные технологии/библиотеки/инструменты (короткие имена)
- skills_optional: только «желательно» / «плюс»
- максимум 25 навыков в каждом списке
- без должностей и воды
"""

USER_PROMPT_TEMPLATE = """Заголовок: {title}

Описание:
{description}

JSON:"""

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.I)
_ARRAY_RE = re.compile(
    r'"(?:skills_required|skills|Skills)"\s*:\s*\[(.*?)\]',
    re.I | re.S,
)
_OPTIONAL_ARRAY_RE = re.compile(
    r'"(?:skills_optional|Skills_optional)"\s*:\s*\[(.*?)\]',
    re.I | re.S,
)
_STR_IN_ARRAY_RE = re.compile(r'"([^"\\]{1,80})"')


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _repair_json_text(text: str) -> str:
    """Чинит типичный мусор маленьких LLM: trailing commas, пропущенные ], кавычки."""
    fixed = _strip_fences(text)
    fixed = fixed.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    if "'" in fixed and fixed.count('"') < 2:
        fixed = fixed.replace("'", '"')
    # ["a", "b"\n  "skills_optional": → закрыть массив перед следующим ключом
    fixed = re.sub(
        r'"\s*\n\s*"(skills_optional|skills_required|experience|education|skills)"\s*:',
        r'"],\n"\1":',
        fixed,
        flags=re.I,
    )
    # пропущенная запятая между строками в массиве: "a"\n"b"
    fixed = re.sub(r'"\s*\n\s*"', '",\n"', fixed)
    fixed = re.sub(r",\s*}", "}", fixed)
    fixed = re.sub(r",\s*]", "]", fixed)
    if fixed.count("{") > fixed.count("}"):
        fixed += "}" * (fixed.count("{") - fixed.count("}"))
    if fixed.count("[") > fixed.count("]"):
        fixed += "]" * (fixed.count("[") - fixed.count("]"))
    return fixed


_JSON_KEY_NOISE = {
    "skills_required",
    "skills_optional",
    "skills",
    "experience",
    "education",
}


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        name = str(item).strip().strip(",")
        if not name or len(name) > 120:
            continue
        if name.lower().strip('"') in _JSON_KEY_NOISE:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out[:40]


def _skills_from_broken_json(raw: str) -> dict[str, list[str]]:
    """Последняя попытка: вытащить массивы regex'ом из почти-JSON."""
    req: list[str] = []
    opt: list[str] = []
    m = _ARRAY_RE.search(raw or "")
    if m:
        req = _STR_IN_ARRAY_RE.findall(m.group(1))
    m2 = _OPTIONAL_ARRAY_RE.search(raw or "")
    if m2:
        opt = _STR_IN_ARRAY_RE.findall(m2.group(1))
    if not req and not opt:
        raise ValueError("cannot recover skills from broken JSON")
    return {
        "skills_required": _as_str_list(req),
        "skills_optional": _as_str_list(opt),
    }


def parse_extraction_json(raw: str) -> dict[str, list[str]]:
    candidates = [_strip_fences(raw), _repair_json_text(raw)]
    last_err: Optional[Exception] = None
    for blob in candidates:
        try:
            payload = json.loads(blob)
            if not isinstance(payload, dict):
                raise ValueError("expected JSON object")
            return {
                "skills_required": _as_str_list(
                    payload.get("skills_required")
                    or payload.get("skills")
                    or payload.get("Skills")
                ),
                "skills_optional": _as_str_list(
                    payload.get("skills_optional") or payload.get("Skills_optional")
                ),
            }
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    try:
        return _skills_from_broken_json(raw)
    except Exception:
        raise ValueError(f"JSON parse failed: {last_err}; raw={raw[:240]!r}") from last_err


def _api_key() -> str:
    return (settings.llm_api_key or settings.groq_api_key or "").strip()


def _is_forbidden(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "403" in msg or "forbidden" in msg


def _chat_openai_compatible(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: int = 120,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    import requests

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/zyuzyunda/gazyu",
        "X-Title": "competence-platform-skill-extract",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    if extra:
        payload.update(extra)
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


def _chat_groq(messages: list[dict[str, str]], *, temperature: float) -> str:
    key = settings.groq_api_key.strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY не задан")
    from groq import Groq

    client = Groq(api_key=key)
    completion = client.chat.completions.create(
        model=settings.llm_model,
        temperature=temperature,
        messages=messages,
    )
    return completion.choices[0].message.content or ""


def _stop_ollama_model() -> None:
    """Сбросить зависшую генерацию, чтобы не копить очередь (минуты на вакансию)."""
    import subprocess

    model = settings.ollama_model or "llama3.2:3b"
    try:
        subprocess.run(
            ["ollama", "stop", model],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        pass


def _is_timeout(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg or "read timed out" in msg


def _chat_ollama(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    timeout_sec: Optional[int] = None,
    num_predict: Optional[int] = None,
) -> str:
    """Локальная Llama: native /api/chat + format=json (стабильнее, чем /v1)."""
    import requests

    base = (settings.ollama_base_url or "http://127.0.0.1:11434").rstrip("/")
    model = settings.ollama_model or "llama3.2:3b"
    read_timeout = int(timeout_sec) if timeout_sec else OLLAMA_TIMEOUT_SEC
    predict = int(num_predict) if num_predict else OLLAMA_NUM_PREDICT
    resp = requests.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "keep_alive": "5m",
            "options": {
                "temperature": temperature,
                "num_predict": predict,
                "num_ctx": OLLAMA_NUM_CTX,
            },
        },
        timeout=(5, read_timeout),  # connect, read
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Ollama HTTP {resp.status_code}: {resp.text[:400]}. "
            f"Проверьте: ollama serve && ollama pull {model}"
        )
    data = resp.json()
    return (data.get("message") or {}).get("content") or ""


def _chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0,
    provider: Optional[str] = None,
    timeout_sec: Optional[int] = None,
    num_predict: Optional[int] = None,
) -> str:
    """
    Провайдеры:
      auto   — Groq, при 403 → Ollama (локальная Llama)
      groq   — только Groq
      ollama — только локальная Ollama
      openai — LLM_BASE_URL + LLM_API_KEY
    """
    mode = (provider or settings.llm_provider or "auto").strip().lower()

    if mode == "openai":
        base = (settings.llm_base_url or "").strip()
        if not base:
            raise RuntimeError("Для provider=openai задайте LLM_BASE_URL")
        return _chat_openai_compatible(
            messages,
            base_url=base,
            api_key=_api_key(),
            model=settings.llm_model,
            temperature=temperature,
        )

    if mode not in {"ollama", "groq", "auto"} and (settings.llm_base_url or "").strip():
        return _chat_openai_compatible(
            messages,
            base_url=settings.llm_base_url.strip(),
            api_key=_api_key(),
            model=settings.llm_model,
            temperature=temperature,
        )

    if mode == "ollama":
        return _chat_ollama(
            messages,
            temperature=temperature,
            timeout_sec=timeout_sec,
            num_predict=num_predict,
        )

    if mode == "groq":
        return _chat_groq(messages, temperature=temperature)

    if settings.groq_api_key.strip():
        try:
            return _chat_groq(messages, temperature=temperature)
        except Exception as exc:
            if _is_forbidden(exc):
                print(
                    "Groq Forbidden (403) — переключаюсь на локальную Ollama "
                    f"({settings.ollama_model})…"
                )
                return _chat_ollama(
                    messages,
                    temperature=temperature,
                    timeout_sec=timeout_sec,
                    num_predict=num_predict,
                )
            raise

    print(f"GROQ_API_KEY пуст — использую Ollama ({settings.ollama_model})")
    return _chat_ollama(
        messages,
        temperature=temperature,
        timeout_sec=timeout_sec,
        num_predict=num_predict,
    )


def mock_extract_skills(title: str, description: str) -> dict[str, list[str]]:
    """Офлайн-smoke: те же паттерны, что regex-fallback парсера (без API)."""
    blob = f"{title}\n{description}".lower()
    found: list[str] = []
    for pattern, label in DESCRIPTION_SKILL_PATTERNS:
        if re.search(pattern, blob, flags=re.I) and label not in found:
            found.append(label)
    return {"skills_required": found, "skills_optional": []}


def extract_skills_from_description(
    *,
    title: str,
    description: str,
    mock: bool = False,
    provider: Optional[str] = None,
    allow_regex_fallback: bool = True,
) -> dict[str, list[str]]:
    desc = (description or "").strip()
    if len(desc) > MAX_DESC_CHARS:
        desc = desc[:MAX_DESC_CHARS] + "…"

    if mock:
        return mock_extract_skills(title, desc)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(
                title=title or "",
                description=desc,
            ),
        },
    ]

    last_err: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            content = _chat_completion(
                messages, temperature=0, provider=provider
            )
            parsed = parse_extraction_json(content)
            if parsed["skills_required"] or parsed["skills_optional"]:
                return parsed
            last_err = ValueError("empty skills in model response")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if _is_timeout(exc):
                # иначе Ollama продолжает старую генерацию и следующий запрос ждёт минуты
                _stop_ollama_model()
                break
            msg = str(exc).lower()
            wait = DEFAULT_SLEEP_SEC * (2**attempt)
            if "429" in msg or "rate" in msg:
                wait = max(wait, 1.5 * (attempt + 1))
            time.sleep(wait)

    if allow_regex_fallback:
        fb = mock_extract_skills(title, desc)
        if fb["skills_required"]:
            print(
                f"  fallback regex skills ({len(fb['skills_required'])}) "
                f"after LLM error: {last_err}"
            )
            return fb
        # нет IT-скиллов в тексте — всё равно считаем обработанным (маркер ниже)
        print(f"  skip empty extract (timeout/no skills) vacancy error={last_err}")
        return {"skills_required": [], "skills_optional": []}
    raise RuntimeError(f"LLM extraction failed after {MAX_RETRIES} retries: {last_err}")


def _already_extracted_ids(session: Session, vacancy_ids: list[int]) -> set[int]:
    if not vacancy_ids:
        return set()
    rows = session.execute(
        select(VacancySkill.vacancy_id)
        .where(
            VacancySkill.vacancy_id.in_(vacancy_ids),
            VacancySkill.source == SOURCE_REQUIRED,
        )
        .distinct()
    ).all()
    return {r[0] for r in rows}


def _replace_llm_skills(
    session: Session,
    vacancy: Vacancy,
    required: list[str],
    optional: list[str],
) -> None:
    existing = session.scalars(
        select(VacancySkill).where(
            VacancySkill.vacancy_id == vacancy.id,
            VacancySkill.source.in_([SOURCE_REQUIRED, SOURCE_OPTIONAL]),
        )
    ).all()
    for row in existing:
        session.delete(row)
    session.flush()

    seen: set[str] = set()
    for name in required:
        if name == LLM_DONE_MARKER:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        session.add(
            VacancySkill(
                vacancy_id=vacancy.id,
                skill_name=name,
                source=SOURCE_REQUIRED,
                skill_type="required",
            )
        )
    for name in optional:
        if name == LLM_DONE_MARKER:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        session.add(
            VacancySkill(
                vacancy_id=vacancy.id,
                skill_name=name,
                source=SOURCE_OPTIONAL,
                skill_type="optional",
            )
        )
    # всегда маркер — иначе --resume вечно ретраит «антенны» / пустые ответы
    session.add(
        VacancySkill(
            vacancy_id=vacancy.id,
            skill_name=LLM_DONE_MARKER,
            source=SOURCE_REQUIRED,
            skill_type="marker",
        )
    )


def text_for_extract(vacancy: Vacancy, *, from_sections: bool = True) -> tuple[str, str]:
    """Текст для LLM: секции Block 1, иначе полный description.

    Returns:
        (text, source) где source ∈ {"sections", "description"}.
    """
    if from_sections and (
        vacancy.section_responsibilities or vacancy.section_requirements
    ):
        from src.market.split_sections import kept_text

        kept = kept_text(vacancy).strip()
        if len(kept) >= MIN_DESC_LEN:
            return kept, "sections"
    desc = (vacancy.description_text or "").strip()
    return desc, "description"


def iter_candidate_vacancies(
    session: Session,
    *,
    data_source: str,
    limit: Optional[int],
    offset: int,
    resume: bool,
    require_sections: bool = False,
) -> list[Vacancy]:
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
    if require_sections:
        q = q.where(
            (Vacancy.section_responsibilities.is_not(None))
            | (Vacancy.section_requirements.is_not(None))
        )
    if limit is not None:
        q = q.limit(limit)
    vacancies = list(session.scalars(q).all())
    if not resume:
        return vacancies
    done = _already_extracted_ids(session, [v.id for v in vacancies])
    return [v for v in vacancies if v.id not in done]


def run_extraction(
    *,
    data_source: str = "hh",
    limit: Optional[int] = None,
    offset: int = 0,
    resume: bool = True,
    dry_run: bool = False,
    mock: bool = False,
    provider: Optional[str] = None,
    sleep_sec: float = DEFAULT_SLEEP_SEC,
    from_sections: bool = True,
    require_sections: bool = False,
) -> dict[str, int]:
    ensure_schema()
    resolved_provider = (provider or settings.llm_provider or "auto").strip().lower()
    stats = {
        "candidates": 0,
        "processed": 0,
        "skipped_empty": 0,
        "errors": 0,
        "skills_required": 0,
        "skills_optional": 0,
        "from_sections": 0,
        "from_description": 0,
        "mode": "mock" if mock else resolved_provider,
    }

    with SessionLocal() as session:
        vacancies = iter_candidate_vacancies(
            session,
            data_source=data_source,
            limit=limit,
            offset=offset,
            resume=resume,
            require_sections=require_sections,
        )
        stats["candidates"] = len(vacancies)
        if not vacancies:
            print(
                "Нет вакансий для LLM-извлечения "
                "(проверьте description_text / секции / --resume)."
            )
            return stats

        if not dry_run and not mock:
            if resolved_provider in {"groq", "openai"} and not _api_key():
                raise SystemExit(
                    "Задайте GROQ_API_KEY / LLM_API_KEY, либо --provider ollama / --mock"
                )

        for vacancy in tqdm(vacancies, desc=f"LLM extract [{data_source}]"):
            desc, text_source = text_for_extract(vacancy, from_sections=from_sections)
            if len(desc) < MIN_DESC_LEN:
                stats["skipped_empty"] += 1
                continue

            if dry_run:
                print(
                    f"[dry-run] id={vacancy.id} via={text_source} "
                    f"chars={len(desc)} title={(vacancy.name or '')[:50]!r}"
                )
                stats["processed"] += 1
                if text_source == "sections":
                    stats["from_sections"] += 1
                else:
                    stats["from_description"] += 1
                continue

            try:
                extracted = extract_skills_from_description(
                    title=vacancy.name,
                    description=desc,
                    mock=mock,
                    provider=resolved_provider,
                )
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                print(f"ERROR vacancy_id={vacancy.id}: {exc} — marking done")
                extracted = {"skills_required": [], "skills_optional": []}

            required = extracted["skills_required"]
            optional = extracted["skills_optional"]
            _replace_llm_skills(session, vacancy, required, optional)
            if text_source == "sections":
                stats["from_sections"] += 1
            else:
                stats["from_description"] += 1
            session.commit()

            stats["processed"] += 1
            stats["skills_required"] += len(required)
            stats["skills_optional"] += len(optional)
            if sleep_sec > 0 and not mock:
                time.sleep(sleep_sec)

    print("LLM extraction done:", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract skills from vacancy descriptions via LLM "
        "(Groq / Ollama Llama / OpenAI-compatible)"
    )
    parser.add_argument(
        "--source",
        default="hh",
        help="Vacancy.data_source (default: hh)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Макс. число вакансий")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Пропускать вакансии с уже записанным llm_extract (default: true)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Без API: извлечь навыки regex-словарём (только для smoke/CI)",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "groq", "ollama", "openai"],
        default=None,
        help="auto=Groq→Ollama при 403; ollama=локальная Llama (default из LLM_PROVIDER)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SEC,
        help="Пауза между запросами (сек)",
    )
    parser.add_argument(
        "--from-sections",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Текст из responsibilities+requirements (Block 1); иначе полный description (default: true)",
    )
    parser.add_argument(
        "--require-sections",
        action="store_true",
        help="Только вакансии, у которых уже есть section_*",
    )
    args = parser.parse_args()
    run_extraction(
        data_source=args.source,
        limit=args.limit,
        offset=args.offset,
        resume=args.resume,
        dry_run=args.dry_run,
        mock=args.mock,
        provider=args.provider,
        sleep_sec=args.sleep,
        from_sections=args.from_sections,
        require_sections=args.require_sections,
    )


if __name__ == "__main__":
    main()
