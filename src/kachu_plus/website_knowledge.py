from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

URL_PATTERN = re.compile(r"https?://[^\s<>'\")]+", re.IGNORECASE)
_PREFERRED_LINK_HINTS = (
    "關於",
    "about",
    "品牌",
    "產品",
    "product",
    "服務",
    "service",
    "療程",
    "menu",
    "菜單",
    "contact",
    "聯絡",
)
_WEBSITE_CATEGORIES = (
    "website_source_url",
    "website_brand_name",
    "website_summary",
    "website_highlight",
    "website_contact",
    "website_page",
)
_PRIORITY_BY_CATEGORY = {
    "website_summary": 0,
    "website_highlight": 1,
    "website_contact": 2,
    "core_value": 3,
    "pain_point": 4,
    "goal": 5,
    "basic_info": 6,
    "brand_material": 7,
    "website_page": 8,
    "website_brand_name": 9,
    "website_source_url": 10,
}


@dataclass(slots=True)
class WebsiteKnowledgeResult:
    source_url: str
    brand_name: str
    summary: str
    highlights: list[str]
    contact_points: list[str]
    page_urls: list[str]


@dataclass(slots=True)
class _PageSnapshot:
    url: str
    title: str
    description: str
    headings: list[str]
    paragraphs: list[str]
    links: list[str]


def contains_url(text: str) -> bool:
    return bool(URL_PATTERN.search(text))


def select_knowledge_highlights(entries: list[Any], *, limit: int) -> list[str]:
    ordered = sorted(
        entries,
        key=lambda entry: (
            _PRIORITY_BY_CATEGORY.get(getattr(entry, "category", ""), 99),
            -getattr(entry, "created_at").timestamp(),
        ),
    )
    seen: set[str] = set()
    highlights: list[str] = []
    for entry in ordered:
        content = getattr(entry, "content", "").strip()
        if not content or content in seen:
            continue
        seen.add(content)
        highlights.append(content)
        if len(highlights) >= limit:
            break
    return highlights


def format_website_ingestion_reply(result: WebsiteKnowledgeResult, *, onboarding: bool = False) -> str:
    lines = [
        "我先把官網重點吸收進來了。",
        f"品牌主軸：{result.brand_name or '已抓到網站內容'}",
        f"摘要：{result.summary}",
    ]
    for highlight in result.highlights[:3]:
        lines.append(f"• {highlight}")
    for contact in result.contact_points[:2]:
        lines.append(f"• {contact}")
    if onboarding:
        lines.append("如果哪裡不對，直接補充修正我；沒有的話你可以繼續傳資料，或傳「完成」。")
    else:
        lines.append("如果哪裡不對，直接補充修正我；之後我在寫貼文或回覆時會優先參考這些內容。")
    return "\n".join(lines)


class WebsiteKnowledgeIngestionService:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    async def ingest_from_message(
        self,
        *,
        tenant_id: str,
        text: str,
        source_conversation_id: str = "",
    ) -> WebsiteKnowledgeResult | None:
        urls = self._extract_urls(text)
        if not urls:
            return None
        root_url = urls[0]
        snapshots = await self._fetch_site(root_url)
        result = self._build_result(root_url, snapshots)
        self._persist_result(tenant_id=tenant_id, result=result, source_conversation_id=source_conversation_id)
        return result

    def _extract_urls(self, text: str) -> list[str]:
        urls: list[str] = []
        for match in URL_PATTERN.findall(text):
            normalized = self._normalize_url(match)
            if normalized and normalized not in urls:
                urls.append(normalized)
        return urls

    async def _fetch_site(self, root_url: str) -> list[_PageSnapshot]:
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "KachuPlusBot/1.0 (+https://plus.kachu.tw)"},
            timeout=15.0,
        ) as client:
            root_snapshot = await self._fetch_page(client, root_url)
            page_urls = [root_snapshot.url]
            for candidate in root_snapshot.links:
                if len(page_urls) >= 4:
                    break
                if candidate not in page_urls:
                    page_urls.append(candidate)

            snapshots = [root_snapshot]
            for page_url in page_urls[1:]:
                try:
                    snapshots.append(await self._fetch_page(client, page_url))
                except httpx.HTTPError:
                    continue
            return snapshots

    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> _PageSnapshot:
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
        final_url = self._normalize_url(str(response.url)) or url
        return _PageSnapshot(
            url=final_url,
            title=self._extract_title(text),
            description=self._extract_meta_description(text),
            headings=self._extract_tag_text(text, "h1") + self._extract_tag_text(text, "h2"),
            paragraphs=self._extract_paragraphs(text),
            links=self._extract_candidate_links(text, final_url),
        )

    def _build_result(self, root_url: str, snapshots: list[_PageSnapshot]) -> WebsiteKnowledgeResult:
        first_page = snapshots[0]
        brand_name = first_page.title or (first_page.headings[0] if first_page.headings else "")
        summary = first_page.description or next(iter(first_page.paragraphs), "") or "已成功擷取網站內容。"
        highlights = self._build_highlights(snapshots)
        contacts = self._extract_contact_points(snapshots)
        return WebsiteKnowledgeResult(
            source_url=root_url,
            brand_name=brand_name[:120],
            summary=summary[:220],
            highlights=highlights,
            contact_points=contacts,
            page_urls=[snapshot.url for snapshot in snapshots],
        )

    def _persist_result(
        self,
        *,
        tenant_id: str,
        result: WebsiteKnowledgeResult,
        source_conversation_id: str = "",
    ) -> None:
        for category in _WEBSITE_CATEGORIES:
            self._repo.delete_knowledge_entries_by_category(tenant_id, category)

        self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="website_source_url",
            content=result.source_url,
            source_type="website",
            source_conversation_id=source_conversation_id,
        )
        if result.brand_name:
            self._repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="website_brand_name",
                content=result.brand_name,
                source_type="website",
                source_conversation_id=source_conversation_id,
            )
        self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="website_summary",
            content=result.summary,
            source_type="website",
            source_conversation_id=source_conversation_id,
        )
        for highlight in result.highlights[:4]:
            self._repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="website_highlight",
                content=highlight,
                source_type="website",
                source_conversation_id=source_conversation_id,
            )
        for contact in result.contact_points[:2]:
            self._repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="website_contact",
                content=contact,
                source_type="website",
                source_conversation_id=source_conversation_id,
            )
        for page_url in result.page_urls[:4]:
            self._repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="website_page",
                content=page_url,
                source_type="website",
                source_conversation_id=source_conversation_id,
            )

    def _extract_candidate_links(self, html_text: str, base_url: str) -> list[str]:
        base_host = urlparse(base_url).netloc
        matches = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html_text, re.IGNORECASE | re.DOTALL)
        scored: list[tuple[int, str]] = []
        for href, label in matches:
            candidate = self._normalize_url(urljoin(base_url, html.unescape(href)))
            if not candidate:
                continue
            parsed = urlparse(candidate)
            if parsed.netloc != base_host:
                continue
            label_text = self._clean_text(label)
            score = 0
            lowered = f"{label_text} {candidate}".lower()
            for hint in _PREFERRED_LINK_HINTS:
                if hint in lowered:
                    score += 10
            if "/collections" in lowered or "/products" in lowered:
                score += 4
            if score > 0:
                scored.append((score, candidate))

        ordered: list[str] = []
        for _, candidate in sorted(scored, key=lambda item: (-item[0], item[1])):
            if candidate not in ordered:
                ordered.append(candidate)
        return ordered[:3]

    def _extract_title(self, html_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        return self._clean_text(match.group(1) if match else "")

    def _extract_meta_description(self, html_text: str) -> str:
        match = re.search(
            r'<meta[^>]+name=[\"\']description[\"\'][^>]+content=[\"\'](.*?)[\"\'][^>]*>',
            html_text,
            re.IGNORECASE | re.DOTALL,
        )
        return self._clean_text(match.group(1) if match else "")

    def _extract_tag_text(self, html_text: str, tag: str) -> list[str]:
        pattern = rf"<{tag}[^>]*>(.*?)</{tag}>"
        return [self._clean_text(item) for item in re.findall(pattern, html_text, re.IGNORECASE | re.DOTALL) if self._clean_text(item)]

    def _extract_paragraphs(self, html_text: str) -> list[str]:
        raw_blocks = re.findall(r"<(?:p|li)[^>]*>(.*?)</(?:p|li)>", html_text, re.IGNORECASE | re.DOTALL)
        paragraphs: list[str] = []
        for block in raw_blocks:
            cleaned = self._clean_text(block)
            if 20 <= len(cleaned) <= 180 and cleaned not in paragraphs:
                paragraphs.append(cleaned)
        return paragraphs[:8]

    def _build_highlights(self, snapshots: list[_PageSnapshot]) -> list[str]:
        candidates: list[str] = []
        for snapshot in snapshots:
            for item in snapshot.headings[:2] + snapshot.paragraphs[:3]:
                normalized = item.strip()
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
        return candidates[:4]

    def _extract_contact_points(self, snapshots: list[_PageSnapshot]) -> list[str]:
        chunks: list[str] = []
        for snapshot in snapshots:
            chunks.extend([snapshot.title, snapshot.description, *snapshot.headings, *snapshot.paragraphs])
        flattened = "\n".join(chunk for chunk in chunks if chunk)
        contacts: list[str] = []
        for phone in re.findall(r"(?:\+886[-\s]?)?0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4}", flattened):
            normalized = phone.strip()
            if normalized not in contacts:
                contacts.append(f"聯絡電話：{normalized}")
        for line in flattened.splitlines():
            text = line.strip()
            if any(token in text for token in ("市", "縣", "區", "路", "街", "號")) and 6 <= len(text) <= 60:
                candidate = f"地址資訊：{text}"
                if candidate not in contacts:
                    contacts.append(candidate)
            if len(contacts) >= 2:
                break
        return contacts[:2]

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        cleaned = parsed._replace(fragment="", query="")
        normalized = urlunparse(cleaned)
        return normalized.rstrip("/")

    def _clean_text(self, raw: str) -> str:
        text = re.sub(r"<script.*?</script>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()