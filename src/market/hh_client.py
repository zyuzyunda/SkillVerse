from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

HH_API_BASE = "https://api.hh.ru/"


def _strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    # 2024-01-15T12:30:00+0300
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


class HHClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.hh_user_agent,
                "Accept": "application/json",
                "HH-User-Agent": settings.hh_user_agent,
            }
        )
        self.delay = settings.hh_request_delay_sec

    def _sleep(self) -> None:
        time.sleep(self.delay)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=30), stop=stop_after_attempt(5))
    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = urljoin(HH_API_BASE, path.lstrip("/"))
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code == 403:
            raise requests.HTTPError(f"hh.ru forbidden: {resp.text[:300]}", response=resp)
        if resp.status_code == 429:
            time.sleep(5)
            resp.raise_for_status()
        resp.raise_for_status()
        self._sleep()
        return resp.json()

    def search_vacancy_ids(
        self,
        text: str,
        *,
        area: Optional[int] = None,
        search_field: str = "name",
        max_pages: Optional[int] = None,
        per_page: Optional[int] = None,
    ) -> list[str]:
        area = area if area is not None else settings.hh_area
        max_pages = max_pages if max_pages is not None else settings.hh_max_pages_per_query
        per_page = per_page if per_page is not None else settings.hh_per_page

        ids: list[str] = []
        for page in range(max_pages):
            # hh.ru: глубина выдачи <= 2000
            if page * per_page >= 2000:
                break
            data = self._get(
                "vacancies",
                params={
                    "text": text,
                    "area": area,
                    "page": page,
                    "per_page": per_page,
                    "search_field": search_field,
                    "order_by": "publication_time",
                },
            )
            items = data.get("items") or []
            if not items:
                break
            ids.extend(str(item["id"]) for item in items if item.get("id"))
            pages = int(data.get("pages") or 0)
            if page + 1 >= pages:
                break
        # dedupe, preserve order
        seen: set[str] = set()
        unique: list[str] = []
        for vid in ids:
            if vid not in seen:
                seen.add(vid)
                unique.append(vid)
        return unique

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return self._get(f"vacancies/{vacancy_id}")


def normalize_vacancy(raw: dict[str, Any], *, search_query: str, role_group: str) -> dict[str, Any]:
    salary = raw.get("salary") or {}
    employer = raw.get("employer") or {}
    area = raw.get("area") or {}
    experience = raw.get("experience") or {}
    employment = raw.get("employment") or {}
    schedule = raw.get("schedule") or {}
    description_html = raw.get("description") or ""

    key_skills = []
    for item in raw.get("key_skills") or []:
        name = (item.get("name") or "").strip()
        if name:
            key_skills.append(name)

    return {
        "hh_id": str(raw["id"]),
        "name": raw.get("name") or "",
        "description_html": description_html,
        "description_text": _strip_html(description_html) if description_html else None,
        "employer_id": str(employer["id"]) if employer.get("id") is not None else None,
        "employer_name": employer.get("name"),
        "area_id": str(area["id"]) if area.get("id") is not None else None,
        "area_name": area.get("name"),
        "experience_id": experience.get("id"),
        "experience_name": experience.get("name"),
        "employment_name": employment.get("name"),
        "schedule_name": schedule.get("name"),
        "salary_from": salary.get("from"),
        "salary_to": salary.get("to"),
        "salary_currency": salary.get("currency"),
        "salary_gross": salary.get("gross"),
        "published_at": _parse_dt(raw.get("published_at")),
        "created_at_hh": _parse_dt(raw.get("created_at")),
        "archived": bool(raw.get("archived")),
        "alternate_url": raw.get("alternate_url"),
        "search_query": search_query,
        "role_group": role_group,
        "raw_json": raw,
        "key_skills": key_skills,
    }
