"""
search.py - 유사 기사 검색 모듈
──────────────────────────────────
네이버 검색 API, Google Custom Search API를 사용하여
원문과 유사한 기사 후보를 수집합니다.
"""

import httpx
import logging
from dataclasses import dataclass, asdict
from urllib.parse import quote_plus
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """검색 결과 항목"""
    title: str
    url: str
    description: str
    source: str        # "naver" | "google"
    pub_date: str = ""

    def to_dict(self):
        return asdict(self)


class NewsSearcher:
    """뉴스 검색 API 래퍼"""

    def __init__(
        self,
        naver_client_id: str = "",
        naver_client_secret: str = "",
        google_api_key: str = "",
        google_cse_id: str = "",
        timeout: int = 10,
    ):
        self.naver_id = naver_client_id
        self.naver_secret = naver_client_secret
        self.google_key = google_api_key
        self.google_cse = google_cse_id
        self.timeout = timeout

    @property
    def has_naver(self) -> bool:
        return bool(self.naver_id and self.naver_secret)

    @property
    def has_google(self) -> bool:
        return bool(self.google_key and self.google_cse)

    async def search(
        self,
        queries: list[str],
        max_per_query: int = 10,
    ) -> list[SearchResult]:
        """
        여러 검색 쿼리로 뉴스를 검색합니다.
        네이버 → Google 순으로 시도하며 결과를 합칩니다.

        Args:
            queries: 검색 쿼리 리스트 (핵심 문장들)
            max_per_query: 쿼리당 최대 결과 수

        Returns:
            중복 제거된 검색 결과 리스트
        """
        all_results: list[SearchResult] = []
        seen_urls = set()

        for query in queries:
            # 쿼리가 너무 길면 앞부분만 사용
            q = query[:100] if len(query) > 100 else query

            # 1) 네이버 검색
            if self.has_naver:
                naver_results = await self._search_naver(q, max_per_query)
                for r in naver_results:
                    if r.url not in seen_urls:
                        seen_urls.add(r.url)
                        all_results.append(r)

            # 2) Google 검색
            if self.has_google:
                google_results = await self._search_google(q, max_per_query)
                for r in google_results:
                    if r.url not in seen_urls:
                        seen_urls.add(r.url)
                        all_results.append(r)

        logger.info(f"총 {len(all_results)}개 검색 결과 수집 (쿼리 {len(queries)}개)")
        return all_results

    async def _search_naver(self, query: str, display: int = 10) -> list[SearchResult]:
        """
        네이버 검색 API (뉴스)
        문서: https://developers.naver.com/docs/serviceapi/search/news/news.md

        무료 한도: 25,000건/일
        """
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": self.naver_id,
            "X-Naver-Client-Secret": self.naver_secret,
        }
        params = {
            "query": query,
            "display": min(display, 100),
            "sort": "date",  # 날짜순 (sim: 정확도순)
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = []
            for item in data.get("items", []):
                # 네이버 API는 HTML 태그를 포함하므로 제거
                title = self._strip_html(item.get("title", ""))
                desc = self._strip_html(item.get("description", ""))

                results.append(SearchResult(
                    title=title,
                    url=item.get("originallink") or item.get("link", ""),
                    description=desc,
                    source="naver",
                    pub_date=item.get("pubDate", ""),
                ))

            logger.debug(f"네이버 검색 '{query[:30]}...' → {len(results)}건")
            return results

        except httpx.HTTPError as e:
            logger.warning(f"네이버 검색 실패: {e}")
            return []

    async def _search_google(self, query: str, num: int = 10) -> list[SearchResult]:
        """
        Google Custom Search API
        문서: https://developers.google.com/custom-search/v1/introduction

        무료 한도: 100건/일 (유료: $5/1000건)
        """
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "key": self.google_key,
            "cx": self.google_cse,
            "num": min(num, 10),
            "lr": "lang_ko",  # 한국어 우선
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = []
            for item in data.get("items", []):
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    description=item.get("snippet", ""),
                    source="google",
                ))

            logger.debug(f"Google 검색 '{query[:30]}...' → {len(results)}건")
            return results

        except httpx.HTTPError as e:
            logger.warning(f"Google 검색 실패: {e}")
            return []

    async def search_naver_blog(self, query: str, display: int = 10) -> list[SearchResult]:
        """네이버 블로그 검색 (뉴스 외 블로그 전파 추적용)"""
        if not self.has_naver:
            return []

        url = "https://openapi.naver.com/v1/search/blog.json"
        headers = {
            "X-Naver-Client-Id": self.naver_id,
            "X-Naver-Client-Secret": self.naver_secret,
        }
        params = {"query": query, "display": min(display, 100), "sort": "date"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

            return [
                SearchResult(
                    title=self._strip_html(item.get("title", "")),
                    url=item.get("link", ""),
                    description=self._strip_html(item.get("description", "")),
                    source="naver_blog",
                    pub_date=item.get("postdate", ""),
                )
                for item in data.get("items", [])
            ]
        except httpx.HTTPError as e:
            logger.warning(f"네이버 블로그 검색 실패: {e}")
            return []

    @staticmethod
    def _strip_html(text: str) -> str:
        """HTML 태그 제거"""
        import re
        return re.sub(r"<[^>]+>", "", text).strip()
