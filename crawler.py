"""
crawler.py - 웹 크롤링 & 텍스트 추출 모듈
──────────────────────────────────────────
trafilatura 기반으로 URL에서 본문, 제목, 작성일, 작성자 등을 추출합니다.
한국어 뉴스 사이트(네이버, 다음 등)에 최적화된 fallback 로직 포함.
"""

import httpx
import trafilatura
from trafilatura.settings import use_config
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urlparse
from dataclasses import dataclass, field, asdict
from typing import Optional
import logging
import re
import json

logger = logging.getLogger(__name__)

# trafilatura 설정: 한국어 최적화
TRAF_CONFIG = use_config()
TRAF_CONFIG.set("DEFAULT", "MIN_OUTPUT_SIZE", "100")
TRAF_CONFIG.set("DEFAULT", "MIN_EXTRACTED_SIZE", "50")


@dataclass
class ArticleData:
    """크롤링 결과 데이터 클래스"""
    url: str
    title: str = ""
    text: str = ""
    author: str = ""
    date: str = ""
    domain: str = ""
    description: str = ""
    language: str = "ko"
    success: bool = False
    error: str = ""
    crawled_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self):
        return asdict(self)


class ArticleCrawler:
    """웹 기사 크롤러"""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        }

    async def crawl(self, url: str) -> ArticleData:
        """URL에서 기사 데이터를 추출합니다."""
        # 네이버 블로그 → 모바일 URL로 변환 (JS 렌더링 우회)
        if "blog.naver.com" in url:
            url = url.replace("https://blog.naver.com", "https://m.blog.naver.com")
            url = url.replace("http://blog.naver.com", "https://m.blog.naver.com")

        article = ArticleData(url=url)
        article.domain = urlparse(url).netloc

        try:
            # 1) HTTP 요청
            html = await self._fetch_html(url)
            if not html:
                article.error = "HTML 다운로드 실패"
                return article

            # 2) trafilatura로 본문 추출 (1차 시도)
            result = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                output_format="json",
                target_language="ko",
                config=TRAF_CONFIG,
            )

            if result:
                result = json.loads(result)
                article.title = result.get("title", "")
                article.text = result.get("text", "")
                article.author = result.get("author", "")
                article.date = result.get("date", "")
                article.description = result.get("description", "")
                article.success = True

                # trafilatura가 author를 못 가져왔으면 직접 파싱
                if not article.author:
                    soup = BeautifulSoup(html, "html.parser")
                    author_tag = (
                        soup.find("meta", attrs={"name": "author"}) or
                        soup.find("meta", property="article:author") or
                        soup.find("em", class_="media_end_head_journalist_name") or
                        soup.find("span", class_="byline_s")
                    )
                    if author_tag:
                        article.author = author_tag.get("content") or author_tag.get_text(strip=True)

                # 그래도 없으면 본문 앞부분에서 "OOO 기자" 패턴 검색
                if not article.author and article.text:
                    match = re.search(r"([가-힣]{2,4}\s*기자)", article.text[:300])
                    if match:
                        article.author = match.group(1)
            else:
                # 3) BeautifulSoup fallback (네이버/다음 등 특수 구조)
                article = self._fallback_parse(html, article)

            # 4) 본문 정제
            if article.text:
                article.text = self._clean_text(article.text)
                article.success = bool(len(article.text) > 50)

        except Exception as e:
            logger.error(f"크롤링 실패 [{url}]: {e}")
            article.error = str(e)

        return article

    async def crawl_multiple(self, urls: list[str]) -> list[ArticleData]:
        """여러 URL을 동시에 크롤링합니다."""
        import asyncio
        tasks = [self.crawl(url) for url in urls]
        return await asyncio.gather(*tasks)

    async def _fetch_html(self, url: str) -> Optional[str]:
        """HTTP 요청으로 HTML을 다운로드합니다."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.headers,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPError as e:
            logger.warning(f"HTTP 오류 [{url}]: {e}")
            return None

    def _fallback_parse(self, html: str, article: ArticleData) -> ArticleData:
        """BeautifulSoup를 사용한 fallback 파싱 (네이버/다음 특화)"""
        soup = BeautifulSoup(html, "html.parser")

        # 제목 추출 순서: og:title → <title> → h1
        og_title = soup.find("meta", property="og:title")
        if og_title:
            article.title = og_title.get("content", "")
        elif soup.title:
            article.title = soup.title.get_text(strip=True)
        elif soup.h1:
            article.title = soup.h1.get_text(strip=True)

        # 본문 추출: 한국 뉴스 사이트 공통 셀렉터
        content_selectors = [
            "article",
            ".article-body",              # 조선일보
            ".news-article-body",         # 조선일보
            "[class*='article_body']",    # 조선 계열
            "#articleBodyContents",       # 네이버 뉴스
            "#articeBody",                # 네이버 뉴스 (구형)
            ".article_body",              # 다음 뉴스
            "#article-view-content-div",  # 언론사 공통
            ".news_end_body",
            "#newsEndContents",
            ".article-body",
            "div[itemprop='articleBody']",
            ".post-content",
            ".entry-content",
        ]

        text = ""
        for selector in content_selectors:
            el = soup.select_one(selector)
            if el:
                # 불필요한 요소 제거
                for tag in el.find_all(["script", "style", "iframe", "aside", "nav"]):
                    tag.decompose()
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 50:
                    break

        if not text:
            # 최후 수단: 모든 <p> 태그 합치기
            paragraphs = soup.find_all("p")
            text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)

        article.text = text

        # 날짜 추출
        date_meta = (
            soup.find("meta", property="article:published_time") or
            soup.find("meta", attrs={"name": "article:published_time"}) or
            soup.find("meta", property="og:regDate") or
            soup.find("meta", attrs={"name": "pubdate"}) or
            soup.find("meta", attrs={"name": "date"}) or
            soup.find("time", attrs={"datetime": True})
        )

        if date_meta:
            article.date = date_meta.get("content") or date_meta.get("datetime", "")

        if not article.date:
            import re
            date_in_url = re.search(r"(\d{4})(\d{2})(\d{2})", article.url)
            if date_in_url:
                y, m, d = date_in_url.groups()
                article.date = f"{y}-{m}-{d}T00:00:00"

        # 작성자 추출
        author_meta = (
            soup.find("meta", attrs={"name": "author"}) or
            soup.find("meta", property="article:author") or
            soup.find("meta", attrs={"name": "twitter:creator"}) or
            soup.find("span", class_="byline") or
            soup.find("em", class_="media_end_head_journalist_name") or  # 네이버 뉴스
            soup.find("span", class_="byline_s")  # 네이버 뉴스 구형
        )
        if author_meta:
            article.author = author_meta.get("content") or author_meta.get_text(strip=True)

        # "홍길동 기자" 패턴을 본문에서 직접 찾기 (최후 수단)
        if not article.author and article.text:
            import re
            match = re.search(r"([가-힣]{2,4}\s*기자)", article.text[:200])
            if match:
                article.author = match.group(1)

        # 설명 추출
        desc_meta = soup.find("meta", property="og:description") or \
                    soup.find("meta", attrs={"name": "description"})
        if desc_meta:
            article.description = desc_meta.get("content", "")

        # URL에서 날짜 추출 (예: /20260311/ 또는 /2026/03/11/)
        if not article.date:
            date_match = re.search(r"(\d{4})[/-]?(\d{2})[/-]?(\d{2})", article.url)
            if date_match:
                y, m, d = date_match.groups()
                article.date = f"{y}-{m}-{d}T00:00:00"

        return article

    def _clean_text(self, text: str) -> str:
        """텍스트 정제: 광고, 공백, 특수문자 등 제거"""
        # 연속 공백/줄바꿈 정리
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        # 공통 노이즈 패턴 제거
        noise_patterns = [
            r"Copyright\s*©.*",
            r"무단\s*전재.*금지.*",
            r"ⓒ.*(?:무단|전재|재배포).*",
            r"기자\s*[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+",
            r"\[.*?기자\]",
            r"▶.*",
            r"☞.*",
            r"관련기사.*",
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, "", text, flags=re.MULTILINE)

        return text.strip()


def extract_key_sentences(text: str, n: int = 3) -> list[str]:
    """
    본문에서 검색 쿼리로 사용할 핵심 문장을 추출합니다.
    가장 긴 문장(정보량이 많을 확률이 높음)을 우선 선택합니다.
    """
    sentences = re.split(r"[.!?。]\s+", text)
    sentences = [s.strip() for s in sentences if 20 < len(s.strip()) < 200]

    if not sentences:
        # 문장 분리 실패 시 텍스트를 적당히 나눔
        words = text.split()
        chunk_size = min(15, len(words))
        return [" ".join(words[:chunk_size])]

    # 길이순 정렬 → 상위 n개 선택
    sentences.sort(key=len, reverse=True)
    return sentences[:n]
