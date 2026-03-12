"""
scorer.py - 신뢰도 점수 산출 모듈
──────────────────────────────────
여러 시그널을 가중 합산하여 0~100 사이의 신뢰도 점수를 계산합니다.
"""

import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrustScoreDetail:
    """신뢰도 점수 세부 내역"""
    overall: float              # 종합 점수 (0~100)
    source_reliability: float   # 출처 신뢰성
    fact_check_match: float     # 팩트체크 일치도
    text_consistency: float     # 텍스트 일관성 (원본 vs 복사 비율)
    source_diversity: float     # 인용 출처 다양성
    author_transparency: float  # 작성자 투명성
    verdict: str                # 판정 문구
    verdict_level: str          # "safe" | "caution" | "warning" | "danger"
    explanation: str            # 판정 근거 설명

    def to_dict(self):
        return asdict(self)


# ── 도메인 신뢰도 DB (실제 서비스에서는 외부 DB 연동) ──────
TRUSTED_DOMAINS = {
    # 주요 언론사 (높은 신뢰도)
    "yonhapnews.co.kr": 85, "yna.co.kr": 85,
    "hani.co.kr": 80, "khan.co.kr": 80,
    "donga.com": 78, "chosun.com": 78,
    "joongang.co.kr": 78, "joins.com": 78,
    "kmib.co.kr": 75, "mk.co.kr": 75,
    "hankyung.com": 75, "sbs.co.kr": 80,
    "kbs.co.kr": 85, "mbc.co.kr": 80,
    "bbc.com": 88, "reuters.com": 90,
    "apnews.com": 90,

    # 포털 (중간)
    "news.naver.com": 70, "news.daum.net": 70,

    # 팩트체크 기관 (최고 신뢰도)
    "factcheck.snu.ac.kr": 95,
    "snopes.com": 92, "factcheck.org": 92,
}

# 낮은 신뢰도 도메인 패턴
LOW_TRUST_PATTERNS = [
    "tistory", "blog", "cafe.naver", "dcinside",
    "fmkorea", "theqoo", "instiz", "ruliweb",
    "t.me", "telegram",
]


class TrustScorer:
    """신뢰도 점수 계산기"""

    # 가중치 (합계 = 1.0)
    WEIGHTS = {
        "source_reliability": 0.30,
        "fact_check_match": 0.25,
        "text_consistency": 0.15,
        "source_diversity": 0.15,
        "author_transparency": 0.15,
    }

    def calculate(
        self,
        origin_domain: str,
        origin_author: str,
        avg_similarity: float,
        num_sources: int,
        fact_check_results: list,
        has_citations: bool = False,
    ) -> TrustScoreDetail:
        """
        종합 신뢰도 점수를 계산합니다.

        Args:
            origin_domain: 추정 원 출처 도메인
            origin_author: 원 출처 작성자
            avg_similarity: 평균 텍스트 유사도 (0~1)
            num_sources: 전파 노드 수
            fact_check_results: 팩트체크 API 결과 리스트
            has_citations: 원문에 인용 출처가 있는지

        Returns:
            TrustScoreDetail 객체
        """
        # 1) 출처 신뢰성 (0~100)
        source_score = self._score_source(origin_domain)

        # 2) 팩트체크 일치도 (0~100)
        fact_score = self._score_factcheck(fact_check_results)

        # 3) 텍스트 일관성 (유사도가 높으면 = 복붙이 많으면 → 낮은 점수)
        consistency_score = self._score_consistency(avg_similarity, num_sources)

        # 4) 인용 출처 다양성
        diversity_score = self._score_diversity(has_citations, num_sources)

        # 5) 작성자 투명성
        author_score = self._score_author(origin_author, origin_domain)

        # 가중 합산
        overall = (
            source_score * self.WEIGHTS["source_reliability"]
            + fact_score * self.WEIGHTS["fact_check_match"]
            + consistency_score * self.WEIGHTS["text_consistency"]
            + diversity_score * self.WEIGHTS["source_diversity"]
            + author_score * self.WEIGHTS["author_transparency"]
        )
        overall = round(max(0, min(100, overall)), 1)

        # 판정
        verdict, level, explanation = self._make_verdict(
            overall, source_score, fact_score, avg_similarity, origin_domain
        )

        return TrustScoreDetail(
            overall=overall,
            source_reliability=round(source_score, 1),
            fact_check_match=round(fact_score, 1),
            text_consistency=round(consistency_score, 1),
            source_diversity=round(diversity_score, 1),
            author_transparency=round(author_score, 1),
            verdict=verdict,
            verdict_level=level,
            explanation=explanation,
        )

    def _score_source(self, domain: str) -> float:
        """도메인 기반 출처 신뢰도"""
        domain_lower = domain.lower()

        # 신뢰 DB 확인
        for trusted_domain, score in TRUSTED_DOMAINS.items():
            if trusted_domain in domain_lower:
                return score

        # 낮은 신뢰도 패턴 확인
        for pattern in LOW_TRUST_PATTERNS:
            if pattern in domain_lower:
                return 15

        # 알 수 없는 도메인 → 기본값
        return 35

    def _score_factcheck(self, results: list) -> float:
        """팩트체크 결과 기반 점수"""
        if not results:
            return 50  # 팩트체크 결과 없음 → 중립

        # 판정 키워드 기반 점수 매핑
        negative_keywords = [
            "거짓", "false", "pants on fire", "mostly false",
            "오류", "사실아님", "가짜", "미확인", "unproven",
        ]
        positive_keywords = [
            "사실", "true", "mostly true", "확인", "correct",
        ]

        negative_count = 0
        positive_count = 0

        for r in results:
            rating = r.rating.lower() if hasattr(r, 'rating') else str(r).lower()
            if any(kw in rating for kw in negative_keywords):
                negative_count += 1
            elif any(kw in rating for kw in positive_keywords):
                positive_count += 1

        total = len(results)
        if negative_count > 0:
            return max(5, 50 - (negative_count / total * 50))
        elif positive_count > 0:
            return min(95, 50 + (positive_count / total * 45))

        return 45

    def _score_consistency(self, avg_similarity: float, num_sources: int) -> float:
        """
        텍스트 일관성 점수.
        유사도가 매우 높으면(복붙) → 낮은 신뢰도
        적절한 유사도면(인용+변형) → 괜찮음
        """
        if avg_similarity > 0.9:
            return 15  # 거의 복붙
        elif avg_similarity > 0.8:
            return 30
        elif avg_similarity > 0.6:
            return 55
        elif avg_similarity > 0.4:
            return 70
        else:
            return 80

    def _score_diversity(self, has_citations: bool, num_sources: int) -> float:
        """인용 출처 다양성 점수"""
        score = 30  # 기본

        if has_citations:
            score += 35

        # 전파 노드가 너무 많으면 (바이럴 확산) → 신뢰도 하락
        if num_sources > 10:
            score -= 15
        elif num_sources > 5:
            score -= 5

        return max(0, min(100, score))

    def _score_author(self, author: str, domain: str) -> float:
        """작성자 투명성 점수"""
        score = 20  # 기본 (작성자 정보 없음)

        if author and len(author) > 1:
            score = 60  # 작성자 있음

            # "기자" 키워드 → 언론사 기자로 추정
            if "기자" in author:
                score = 75

        # 익명 패턴
        anonymous_patterns = ["익명", "anonymous", "admin", "관리자", "운영자"]
        if any(p in (author or "").lower() for p in anonymous_patterns):
            score = 10

        return score

    def _make_verdict(
        self,
        overall: float,
        source_score: float,
        fact_score: float,
        avg_similarity: float,
        domain: str,
    ) -> tuple[str, str, str]:
        """판정 문구, 레벨, 설명을 생성합니다."""

        if overall >= 70:
            verdict = "신뢰할 수 있는 정보"
            level = "safe"
            explanation = (
                f"출처({domain})의 신뢰도가 높으며, "
                "팩트체크에서 문제가 확인되지 않았습니다."
            )
        elif overall >= 50:
            verdict = "주의가 필요한 정보"
            level = "caution"
            explanation = (
                "일부 출처의 신뢰도가 확인되었으나, "
                "추가 검증이 필요한 부분이 있습니다. "
                "다른 공신력 있는 매체의 보도를 확인해 주세요."
            )
        elif overall >= 30:
            verdict = "허위 정보 가능성 있음"
            level = "warning"
            reasons = []
            if source_score < 30:
                reasons.append(f"원 출처({domain})의 신뢰도가 낮음")
            if avg_similarity > 0.8:
                reasons.append("검증 없이 복사·붙여넣기로 전파된 패턴")
            if fact_score < 40:
                reasons.append("팩트체크 DB에서 부정적 결과 확인")
            explanation = ". ".join(reasons) + "." if reasons else "복합적 요인으로 신뢰도가 낮습니다."
        else:
            verdict = "허위 정보 가능성 높음"
            level = "danger"
            explanation = (
                f"원 출처({domain})의 신뢰도가 매우 낮으며, "
                "공신력 있는 기관의 인용 없이 확산되었습니다. "
                "팩트체크 결과와 전파 패턴을 종합할 때 "
                "허위 정보일 가능성이 높습니다."
            )

        return verdict, level, explanation
