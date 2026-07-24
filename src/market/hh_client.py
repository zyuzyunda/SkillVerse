"""Клиент hh.ru: официальный API (OAuth приложения) + HTML fallback."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import settings

HH_API_BASE = "https://api.hh.ru/"
HH_SITE = "https://hh.ru"
HH_OAUTH_TOKEN_URL = "https://hh.ru/oauth/token"
# токен приложения выдаётся редко — кэш на диск (в .gitignore)
HH_TOKEN_PATH = Path(__file__).resolve().parents[2] / ".hh_app_token"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# эвристика: если на странице нет key_skills — вытаскиваем из описания
DESCRIPTION_SKILL_PATTERNS: list[tuple[str, str]] = [
    (r"\bpython\b", "Python"),
    (r"\bsql\b", "SQL"),
    (r"\bpandas\b", "Pandas"),
    (r"\bnumpy\b", "NumPy"),
    (r"\bpytorch\b", "PyTorch"),
    (r"\btensorflow\b", "TensorFlow"),
    (r"\bkeras\b", "Keras"),
    (r"\bscikit[- ]?learn\b|\bsklearn\b", "scikit-learn"),
    (r"\bxgboost\b", "XGBoost"),
    (r"\blightgbm\b", "LightGBM"),
    (r"\bcatboost\b", "CatBoost"),
    (r"\bspark\b|\bpyspark\b", "Spark"),
    (r"\bairflow\b", "Airflow"),
    (r"\bmlflow\b", "MLflow"),
    (r"\bkafka\b", "Kafka"),
    (r"\bdocker\b", "Docker"),
    (r"\bkubernetes\b|\bk8s\b", "Kubernetes"),
    (r"\bfastapi\b", "FastAPI"),
    (r"\bflask\b", "Flask"),
    (r"\bdjango\b", "Django"),
    (r"\bpostgresql\b|\bpostgres\b", "PostgreSQL"),
    (r"\bclickhouse\b", "ClickHouse"),
    (r"\bredis\b", "Redis"),
    (r"\bmongodb\b", "MongoDB"),
    (r"\bellasticsearch\b", "Elasticsearch"),
    (r"\blangchain\b", "LangChain"),
    (r"\brag\b", "RAG"),
    (r"\bllm\b|\blarge language model", "LLM"),
    (r"\btransformers\b|\bhuggingface\b|\bhugging face\b", "Transformers"),
    (r"\bopenai\b|\bgpt-?4\b|\bgpt-?3\b", "GPT"),
    (r"\bbert\b", "BERT"),
    (r"\bnlp\b", "NLP"),
    (r"\bopencv\b|\bcomputer vision\b", "Computer Vision"),
    (r"\bdbt\b", "dbt"),
    (r"\btableau\b", "Tableau"),
    (r"\bpower\s*bi\b", "Power BI"),
    (r"\bgit\b", "Git"),
    (r"\blinux\b", "Linux"),
    (r"\baws\b", "AWS"),
    (r"\bgcp\b|\bgoogle cloud\b", "GCP"),
    (r"\bazure\b", "Azure"),
    (r"\bterraform\b", "Terraform"),
    (r"\bprometheus\b", "Prometheus"),
    (r"\bgrafana\b", "Grafana"),
    (r"\bjava\b", "Java"),
    (r"\bscala\b", "Scala"),
    (r"\bgolang\b", "Go"),
    (r"\bc\+\+\b", "C++"),
]

ZWSP = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
TAG_RE = re.compile(r"<[^>]+>")
ENTITY_RE = re.compile(
    r"&(?:nbsp|amp|quot|#x27|#39|laquo|raquo|mdash|ndash);"
)
ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&quot;": '"',
    "&#x27;": "'",
    "&#39;": "'",
    "&laquo;": "«",
    "&raquo;": "»",
    "&mdash;": "—",
    "&ndash;": "–",
}


class HHForbiddenError(RuntimeError):
    """API/сайт недоступен (антибот / VPN interstitial / OAuth)."""


def _normalize_user_agent(raw: str) -> str:
    """HH требует AppName/Version (email@domain)."""
    ua = (raw or "").strip()
    if not ua:
        return "SkillVerse/1.0 (contact@example.com)"
    # SkillVerse(email) → SkillVerse/1.0 (email)
    m = re.fullmatch(r"([A-Za-z0-9._-]+)\(([^)]+@[^)]+)\)", ua)
    if m:
        return f"{m.group(1)}/1.0 ({m.group(2)})"
    if re.search(r"\S+/\S+\s+\([^)]+@[^)]+\)", ua):
        return ua
    # уже есть email в скобках без версии
    m2 = re.fullmatch(r"([A-Za-z0-9._-]+)\s+\(([^)]+@[^)]+)\)", ua)
    if m2:
        return f"{m2.group(1)}/1.0 ({m2.group(2)})"
    return ua


def extract_skills_from_text(text: str) -> list[str]:
    if not text:
        return []
    blob = text.lower()
    found: list[str] = []
    for pattern, label in DESCRIPTION_SKILL_PATTERNS:
        if re.search(pattern, blob, flags=re.I) and label not in found:
            found.append(label)
    return found


def _strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_text(value: str) -> str:
    text = TAG_RE.sub(" ", value)
    text = ENTITY_RE.sub(lambda m: ENTITIES.get(m.group(0), " "), text)
    text = ZWSP.sub("", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).replace("\xa0", " ").strip()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    try:
        # JSON-LD: 2026-07-14T08:54:37.925+03:00
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            # +0300 → +03:00 for strptime %z on some platforms
            fixed = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
            return datetime.strptime(fixed if ":" in fixed[-5:] else raw, fmt)
        except ValueError:
            continue
    return None


def parse_search_vacancy_ids(html: str) -> list[str]:
    """ID вакансий из SERP-карточек (не из рекомендаций в сайдбаре)."""
    ids: list[str] = []
    seen: set[str] = set()
    blocks = html.split('data-qa="vacancy-serp__vacancy"')[1:]
    title_re = re.compile(
        r'data-qa="serp-item__title[^"]*"[^>]*href="(?:https?:)?//'
        r'(?:[a-z0-9-]+\.)?hh\.ru/vacancy/(\d+)',
        re.I,
    )
    fallback_re = re.compile(
        r'href="(?:https?:)?//(?:[a-z0-9-]+\.)?hh\.ru/vacancy/(\d+)',
        re.I,
    )
    for block in blocks:
        m = title_re.search(block) or fallback_re.search(block)
        if not m:
            continue
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        ids.append(vid)
    return ids


def parse_vacancy_html(html: str, vacancy_id: str) -> dict[str, Any]:
    """Собирает API-подобный dict из HTML карточки вакансии."""
    title = None
    tm = re.search(
        r'<h1[^>]*data-qa="vacancy-title"[^>]*>(.*?)</h1>',
        html,
        flags=re.S | re.I,
    )
    if tm:
        title = _clean_text(tm.group(1))

    employer_name = None
    em = re.search(
        r'data-qa="vacancy-company-name"[^>]*>(.*?)</(?:a|span|div)>',
        html,
        flags=re.S | re.I,
    )
    if em:
        employer_name = _clean_text(em.group(1))

    description_html = ""
    dm = re.search(
        r'data-qa="vacancy-description"[^>]*>(.*?)</div>\s*(?:</div>|</section>)',
        html,
        flags=re.S | re.I,
    )
    if dm:
        description_html = dm.group(1).strip()

    skills: list[str] = []
    for raw in re.findall(
        r'data-qa="skills-element"[^>]*>(.*?)</(?:span|div|li|p|button|a)>',
        html,
        flags=re.S | re.I,
    ):
        name = _clean_text(raw)
        if name and name not in skills:
            skills.append(name)

    area_name = None
    salary: dict[str, Any] | None = None
    published_at = None
    experience_name = None
    employment_name = None
    schedule_name = None

    ld = re.search(
        r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
        html,
        flags=re.S,
    )
    if ld:
        try:
            data = json.loads(ld.group(1))
            title = title or data.get("title")
            if data.get("description"):
                # JSON-LD description обычно полнее
                description_html = data["description"]
            org = data.get("hiringOrganization") or {}
            employer_name = employer_name or org.get("name")
            published_at = data.get("datePosted")
            loc = data.get("jobLocation") or {}
            addr = loc.get("address") if isinstance(loc, dict) else {}
            if isinstance(addr, dict):
                area_name = addr.get("addressLocality") or addr.get("addressRegion")
            base_salary = data.get("baseSalary")
            if isinstance(base_salary, dict):
                value = base_salary.get("value")
                currency = base_salary.get("currency")
                if isinstance(value, dict):
                    salary = {
                        "from": value.get("minValue"),
                        "to": value.get("maxValue"),
                        "currency": currency,
                        "gross": None,
                    }
                elif value is not None:
                    salary = {
                        "from": value,
                        "to": value,
                        "currency": currency,
                        "gross": None,
                    }
        except json.JSONDecodeError:
            pass

    if not skills:
        skills = extract_skills_from_text(
            _strip_html(description_html) if description_html else ""
        )

    # доп. поля из data-qa, если есть
    for qa, target in (
        ("vacancy-view-experience", "experience"),
        ("vacancy-view-employment-mode", "employment"),
        ("vacancy-view-work-schedule", "schedule"),
        ("vacancy-view-location", "area"),
    ):
        m = re.search(rf'data-qa="{qa}"[^>]*>(.*?)</(?:p|div|span)>', html, flags=re.S | re.I)
        if not m:
            continue
        text = _clean_text(m.group(1))
        if target == "experience":
            experience_name = text or experience_name
        elif target == "employment":
            employment_name = text or employment_name
        elif target == "schedule":
            schedule_name = text or schedule_name
        elif target == "area" and not area_name:
            area_name = text

    return {
        "id": str(vacancy_id),
        "name": title or f"Vacancy {vacancy_id}",
        "description": description_html,
        "key_skills": [{"name": s} for s in skills],
        "employer": {"id": None, "name": employer_name},
        "area": {"id": None, "name": area_name},
        "experience": {"id": None, "name": experience_name},
        "employment": {"id": None, "name": employment_name},
        "schedule": {"id": None, "name": schedule_name},
        "salary": salary,
        "published_at": published_at,
        "created_at": published_at,
        "archived": False,
        "alternate_url": f"{HH_SITE}/vacancy/{vacancy_id}",
        "_source": "html",
    }


class HHClient:
    def __init__(self, *, prefer_html: Optional[bool] = None) -> None:
        self.session = requests.Session()
        self.api_ua = _normalize_user_agent(settings.hh_user_agent)
        self.browser_ua = BROWSER_UA
        self.delay = settings.hh_request_delay_sec
        self._access_token: Optional[str] = (settings.hh_access_token or "").strip() or None
        self._api_vacancies_blocked: Optional[bool] = None
        # API first, если есть client credentials / token; иначе HTML
        if prefer_html is None:
            prefer_html = not self._has_api_credentials()
        self.prefer_html = prefer_html
        self._configure_browser_headers()

    def _has_api_credentials(self) -> bool:
        if self._access_token:
            return True
        if (settings.hh_access_token or "").strip():
            return True
        if _load_token_cache():
            return True
        return bool(
            (settings.hh_client_id or "").strip()
            and (settings.hh_client_secret or "").strip()
        )

    def _configure_browser_headers(self) -> None:
        self.session.headers.update(
            {
                "User-Agent": self.browser_ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache",
            }
        )

    def _sleep(self) -> None:
        time.sleep(self.delay)

    def ensure_access_token(self, *, force: bool = False) -> str:
        """Токен приложения: .env → кэш-файл → client_credentials."""
        if self._access_token and not force:
            return self._access_token

        if not force:
            env_token = (settings.hh_access_token or "").strip()
            if env_token:
                self._access_token = env_token
                return env_token
            cached = _load_token_cache()
            if cached:
                self._access_token = cached
                return cached

        client_id = (settings.hh_client_id or "").strip()
        client_secret = (settings.hh_client_secret or "").strip()
        if not client_id or not client_secret:
            if self._access_token:
                return self._access_token
            raise HHForbiddenError(
                "Нет HH_CLIENT_ID / HH_CLIENT_SECRET (или HH_ACCESS_TOKEN / .hh_app_token). "
                "Зарегистрируйте приложение на https://dev.hh.ru"
            )
        resp = requests.post(
            HH_OAUTH_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={
                "User-Agent": self.api_ua,
                "HH-User-Agent": self.api_ua,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            # токен уже выдан недавно — используем кэш / admin token
            if "refresh too early" in (resp.text or "") or resp.status_code == 403:
                cached = _load_token_cache() or (settings.hh_access_token or "").strip()
                if cached:
                    self._access_token = cached
                    return cached
                raise HHForbiddenError(
                    "Токен приложения уже выдан и ещё нельзя обновить. "
                    "Скопируйте access_token из https://dev.hh.ru/admin в HH_ACCESS_TOKEN "
                    f"или в файл {HH_TOKEN_PATH.name}. Ответ: {resp.text[:200]}"
                )
            raise HHForbiddenError(
                f"OAuth token failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        token = (data.get("access_token") or "").strip()
        if not token:
            raise HHForbiddenError(f"OAuth response without access_token: {data}")
        self._access_token = token
        _save_token_cache(token)
        return token

    def _check_vpn_interstitial(self, resp: requests.Response) -> None:
        if re.search(r"/vpnche{1,2}ck", resp.url or "", flags=re.I):
            raise HHForbiddenError(
                f"hh.ru VPN-check interstitial ({resp.url}). "
                "Сеть помечена как VPN/proxy — смените сеть или отключите VPN."
            )

    @retry(
        retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get_html(self, url: str, params: Optional[dict[str, Any]] = None) -> str:
        resp = self.session.get(url, params=params, timeout=45, allow_redirects=True)
        self._check_vpn_interstitial(resp)
        if resp.status_code in {403, 451}:
            raise HHForbiddenError(
                f"hh.ru HTML blocked HTTP {resp.status_code}: {resp.url}"
            )
        if resp.status_code == 429:
            time.sleep(8)
            raise requests.ConnectionError("hh.ru rate limited (429)")
        resp.raise_for_status()
        self._sleep()
        return resp.text

    def _api_headers(self) -> dict[str, str]:
        token = self.ensure_access_token()
        return {
            "User-Agent": self.api_ua,
            "HH-User-Agent": self.api_ua,
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def _api_get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = urljoin(HH_API_BASE, path.lstrip("/"))
        resp = self.session.get(
            url,
            params=params,
            timeout=30,
            headers=self._api_headers(),
        )
        if resp.status_code == 401:
            # токен протух / отозван — один раз обновим
            self.ensure_access_token(force=True)
            resp = self.session.get(
                url,
                params=params,
                timeout=30,
                headers=self._api_headers(),
            )
        if resp.status_code == 403:
            raise HHForbiddenError(f"api.hh.ru forbidden: {resp.text[:200]}")
        if resp.status_code == 400 and "bad_user_agent" in resp.text:
            raise HHForbiddenError(
                f"api.hh.ru bad_user_agent: {resp.text[:200]}. "
                "HH_USER_AGENT должен быть вида AppName/1.0 (email@domain.com)"
            )
        if resp.status_code == 429:
            time.sleep(5)
            raise requests.ConnectionError("api.hh.ru rate limited")
        resp.raise_for_status()
        self._sleep()
        return resp.json()

    def _api_vacancies_available(self) -> bool:
        if self._api_vacancies_blocked is not None:
            return not self._api_vacancies_blocked
        if not self._has_api_credentials():
            self._api_vacancies_blocked = True
            return False
        try:
            self._api_get(
                "vacancies",
                params={"text": "python", "area": 1, "per_page": 1, "page": 0},
            )
            self._api_vacancies_blocked = False
            return True
        except Exception as exc:
            print(f"api.hh.ru недоступен, HTML fallback: {exc}")
            self._api_vacancies_blocked = True
            return False

    def search_vacancy_ids(
        self,
        text: str,
        *,
        area: Optional[int] = None,
        search_field: str = "name",
        max_pages: Optional[int] = None,
        per_page: Optional[int] = None,
        date_from: Optional[str] = None,
    ) -> list[str]:
        area = area if area is not None else settings.hh_area
        max_pages = max_pages if max_pages is not None else settings.hh_max_pages_per_query
        per_page = per_page if per_page is not None else settings.hh_per_page
        date_from = (date_from if date_from is not None else settings.hh_date_from) or None
        if date_from:
            date_from = str(date_from).strip() or None

        use_api = (not self.prefer_html) and self._api_vacancies_available()
        if use_api:
            # API: максимум 100 на страницу, глубина ≤ 2000
            per_page = min(int(per_page), 100)
            max_pages = min(int(max_pages), max(1, 2000 // per_page))
            return self._search_ids_api(
                text, area, search_field, max_pages, per_page, date_from=date_from
            )
        return self._search_ids_html(text, area, search_field, max_pages, per_page)

    def _search_ids_api(
        self,
        text: str,
        area: int,
        search_field: str,
        max_pages: int,
        per_page: int,
        *,
        date_from: Optional[str] = None,
    ) -> list[str]:
        ids: list[str] = []
        for page in range(max_pages):
            if page * per_page >= 2000:
                break
            params: dict[str, Any] = {
                "text": text,
                "area": area,
                "page": page,
                "per_page": per_page,
                "search_field": search_field,
                "order_by": "publication_time",
            }
            if date_from:
                params["date_from"] = date_from
            data = self._api_get("vacancies", params=params)
            items = data.get("items") or []
            if not items:
                break
            ids.extend(str(item["id"]) for item in items if item.get("id"))
            pages = int(data.get("pages") or 0)
            if page + 1 >= pages:
                break
        return _dedupe(ids)

    def _search_ids_html(
        self,
        text: str,
        area: int,
        search_field: str,
        max_pages: int,
        per_page: int,
    ) -> list[str]:
        ids: list[str] = []
        # HTML SSR часто отдаёт ~20 карточек, даже если items_on_page больше
        stagnant_pages = 0
        for page in range(max_pages):
            if len(ids) >= 2000:
                break
            params: dict[str, Any] = {
                "text": text,
                "area": area,
                "page": page,
                "items_on_page": per_page,
                "order_by": "publication_time",
            }
            if search_field:
                params["search_field"] = search_field
            html = self._get_html(f"{HH_SITE}/search/vacancy", params=params)
            page_ids = parse_search_vacancy_ids(html)
            if not page_ids:
                break
            before = len(ids)
            ids.extend(page_ids)
            ids = _dedupe(ids)
            if len(ids) == before:
                stagnant_pages += 1
                if stagnant_pages >= 2:
                    break
            else:
                stagnant_pages = 0
        return ids

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        use_api = (not self.prefer_html) and self._api_vacancies_available()
        if use_api:
            try:
                raw = self._api_get(f"vacancies/{vacancy_id}")
                raw["_source"] = "api"
                return raw
            except HHForbiddenError:
                self._api_vacancies_blocked = True
        html = self._get_html(f"{HH_SITE}/vacancy/{vacancy_id}")
        return parse_vacancy_html(html, vacancy_id)


def _load_token_cache() -> Optional[str]:
    try:
        if HH_TOKEN_PATH.is_file():
            token = HH_TOKEN_PATH.read_text(encoding="utf-8").strip()
            return token or None
    except OSError:
        return None
    return None


def _save_token_cache(token: str) -> None:
    try:
        HH_TOKEN_PATH.write_text(token.strip() + "\n", encoding="utf-8")
        HH_TOKEN_PATH.chmod(0o600)
    except OSError as exc:
        print(f"warn: не удалось сохранить {HH_TOKEN_PATH.name}: {exc}")


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for vid in ids:
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


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
        if isinstance(item, str):
            name = item.strip()
        else:
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
        "salary_from": salary.get("from") if isinstance(salary, dict) else None,
        "salary_to": salary.get("to") if isinstance(salary, dict) else None,
        "salary_currency": salary.get("currency") if isinstance(salary, dict) else None,
        "salary_gross": salary.get("gross") if isinstance(salary, dict) else None,
        "published_at": _parse_dt(raw.get("published_at")),
        "created_at_hh": _parse_dt(raw.get("created_at")),
        "archived": bool(raw.get("archived")),
        "alternate_url": raw.get("alternate_url") or f"{HH_SITE}/vacancy/{raw['id']}",
        "search_query": search_query,
        "role_group": role_group,
        "data_source": "hh",
        "raw_json": raw,
        "key_skills": key_skills,
    }
