"""
fact_check.py - 팩트체크 모듈
──────────────────────────────
Google Fact Check Tools API를 사용하여
주장(claim)의 검증 결과를 조회합니다.
"""

import httpx
import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FactCheckResult:
    """팩트체크 결과"""
    claim: str            # 검증된 주장
    claimant: str         # 주장한 사람/매체
    rating: str           # 판정 결과 (예: "거짓", "대체로 사실" 등)
    publisher: str        # 검증 기관
    url: str              # 검증 기사 URL
    language: str = "ko"
    review_date: str = ""

    def to_dict(self):
        return asdict(self)


class FactChecker:
    """Google Fact Check Tools API 래퍼"""

    API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"

    def __init__(self, api_key: str = "", timeout: int = 10):
        """
        Args:
            api_key: Google API 키 (Fact Check Tools API 사용 설정 필요)
                     발급: https://console.cloud.google.com/apis/credentials
            timeout: API 요청 타임아웃 (초)
        """
        self.api_key = api_key
        self.timeout = timeout

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def check(
        self,
        query: str,
        language: str = "ko",
        max_results: int = 5,
    ) -> list[FactCheckResult]:
        """
        주장을 팩트체크 DB에서 검색합니다.

        Args:
            query: 검색할 주장/키워드
            language: 결과 언어 코드
            max_results: 최대 결과 수

        Returns:
            팩트체크 결과 리스트
        """
        if not self.is_available:
            logger.warning("Google API 키가 설정되지 않음 → 팩트체크 건너뜀")
            return []

        params = {
            "query": query[:200],  # 최대 200자
            "key": self.api_key,
            "languageCode": language,
            "pageSize": max_results,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(self.API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = []
            for claim_data in data.get("claims", []):
                text = claim_data.get("text", "")
                claimant = claim_data.get("claimant", "알 수 없음")

                for review in claim_data.get("claimReview", []):
                    results.append(FactCheckResult(
                        claim=text,
                        claimant=claimant,
                        rating=review.get("textualRating", "확인 불가"),
                        publisher=review.get("publisher", {}).get("name", "알 수 없음"),
                        url=review.get("url", ""),
                        language=review.get("languageCode", language),
                        review_date=review.get("reviewDate", ""),
                    ))

            logger.info(f"팩트체크 '{query[:30]}...' → {len(results)}건 확인")
            return results

        except httpx.HTTPError as e:
            logger.warning(f"팩트체크 API 오류: {e}")
            return []

    async def check_multiple(
        self,
        queries: list[str],
        language: str = "ko",
    ) -> list[FactCheckResult]:
        """여러 쿼리를 한 번에 팩트체크합니다."""
        all_results = []
        seen_claims = set()

        for query in queries:
            results = await self.check(query, language)
            for r in results:
                key = (r.claim, r.publisher)
                if key not in seen_claims:
                    seen_claims.add(key)
                    all_results.append(r)

        return all_results
