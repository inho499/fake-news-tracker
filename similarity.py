"""
similarity.py - 텍스트 유사도 분석 모듈
──────────────────────────────────────────
TF-IDF 벡터화 + 코사인 유사도로 문서 간 유사도를 계산합니다.
한국어 형태소 분석(konlpy)을 지원하며, 미설치 시 기본 토크나이저로 동작.
"""

import re
import logging
from dataclasses import dataclass
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

logger = logging.getLogger(__name__)

# ── 한국어 토크나이저 설정 ──────────────────────────────────
# konlpy가 설치되어 있으면 Okt(Open Korea Text)를 사용
# 없으면 기본 공백+정규식 토크나이저로 fallback
try:
    from konlpy.tag import Okt
    _okt = Okt()

    def korean_tokenizer(text: str) -> list[str]:
        """Okt 형태소 분석기로 명사, 동사, 형용사만 추출"""
        morphs = _okt.pos(text, stem=True)
        # 명사(Noun), 동사(Verb), 형용사(Adjective)만 선택
        return [word for word, pos in morphs 
                if pos in ("Noun", "Verb", "Adjective") and len(word) > 1]

    TOKENIZER = korean_tokenizer
    logger.info("KoNLPy Okt 토크나이저 로드 완료")

except ImportError:
    logger.warning("KoNLPy 미설치 → 기본 토크나이저 사용 (정확도 하락 가능)")

    def basic_tokenizer(text: str) -> list[str]:
        """공백 기반 기본 토크나이저 (한국어에선 정확도 낮음)"""
        # 한글, 영문, 숫자만 남기고 공백으로 분리
        text = re.sub(r"[^\w\s가-힣]", " ", text)
        tokens = text.split()
        return [t for t in tokens if len(t) > 1]

    TOKENIZER = basic_tokenizer


# ── 한국어 불용어 ────────────────────────────────────────────
KOREAN_STOPWORDS = [
    "것", "수", "등", "이", "그", "저", "때", "년", "월", "일",
    "위", "중", "더", "또", "및", "를", "에", "의", "가", "은",
    "는", "로", "한", "할", "하다", "되다", "있다", "없다", "같다",
    "대한", "통해", "대해", "따라", "관련", "지난", "현재", "최근",
    "오늘", "어제", "내일", "아직", "이미", "모두", "각각", "매우",
    "정말", "진짜", "그리고", "하지만", "그러나", "때문", "에서",
]


@dataclass
class SimilarityResult:
    """유사도 분석 결과"""
    index: int              # 후보 문서 인덱스
    url: str                # 후보 문서 URL
    similarity: float       # 코사인 유사도 (0~1)
    shared_keywords: list   # 공통 핵심 키워드


class TextSimilarityAnalyzer:
    """TF-IDF 기반 텍스트 유사도 분석기"""

    def __init__(self, threshold: float = 0.4, max_features: int = 5000):
        """
        Args:
            threshold: 유사 문서로 판정할 최소 유사도 (0~1)
            max_features: TF-IDF 벡터 최대 차원수
        """
        self.threshold = threshold
        self.max_features = max_features
        self.vectorizer = None
        self.tfidf_matrix = None

    def analyze(
        self,
        original_text: str,
        candidate_texts: list[str],
        candidate_urls: list[str] = None,
    ) -> list[SimilarityResult]:
        """
        원문과 후보 문서들 사이의 유사도를 계산합니다.

        Args:
            original_text: 분석 대상 원문
            candidate_texts: 비교 대상 문서 텍스트 리스트
            candidate_urls: 비교 대상 URL 리스트

        Returns:
            유사도가 threshold 이상인 결과 리스트 (유사도 내림차순)
        """
        if not candidate_texts:
            return []

        if candidate_urls is None:
            candidate_urls = [f"candidate_{i}" for i in range(len(candidate_texts))]

        # 모든 텍스트 전처리
        all_texts = [self._preprocess(original_text)] + [
            self._preprocess(t) for t in candidate_texts
        ]

        # TF-IDF 벡터화
        self.vectorizer = TfidfVectorizer(
            tokenizer=TOKENIZER,
            stop_words=KOREAN_STOPWORDS,
            max_features=self.max_features,
            min_df=1,
            max_df=0.95,
            sublinear_tf=True,  # log(1 + tf) → 긴 문서 편향 방지
        )

        try:
            self.tfidf_matrix = self.vectorizer.fit_transform(all_texts)
        except ValueError as e:
            logger.error(f"TF-IDF 벡터화 실패: {e}")
            return []

        # 원문(인덱스 0) vs 나머지 문서의 코사인 유사도
        similarities = cosine_similarity(
            self.tfidf_matrix[0:1], self.tfidf_matrix[1:]
        )[0]

        # threshold 이상만 필터링
        results = []
        for i, sim in enumerate(similarities):
            if sim >= self.threshold:
                shared_kw = self._find_shared_keywords(0, i + 1, top_n=5)
                results.append(SimilarityResult(
                    index=i,
                    url=candidate_urls[i],
                    similarity=round(float(sim), 4),
                    shared_keywords=shared_kw,
                ))

        # 유사도 내림차순 정렬
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results

    def get_similarity_matrix(self, texts: list[str]) -> np.ndarray:
        """
        여러 문서 간 전체 유사도 행렬을 계산합니다.
        전파 그래프 엣지 가중치 계산에 사용됩니다.
        """
        processed = [self._preprocess(t) for t in texts]
        vectorizer = TfidfVectorizer(
            tokenizer=TOKENIZER,
            stop_words=KOREAN_STOPWORDS,
            max_features=self.max_features,
            sublinear_tf=True,
        )
        try:
            matrix = vectorizer.fit_transform(processed)
            return cosine_similarity(matrix)
        except ValueError:
            return np.zeros((len(texts), len(texts)))

    def _preprocess(self, text: str) -> str:
        """텍스트 전처리: 정규화, 노이즈 제거"""
        # URL 제거
        text = re.sub(r"https?://\S+", "", text)
        # 이메일 제거
        text = re.sub(r"\S+@\S+", "", text)
        # 특수문자 정리 (한글, 영문, 숫자, 공백만 남김)
        text = re.sub(r"[^\w\s가-힣a-zA-Z0-9]", " ", text)
        # 연속 공백 정리
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _find_shared_keywords(self, idx_a: int, idx_b: int, top_n: int = 5) -> list[str]:
        """두 문서 사이의 공통 핵심 키워드를 찾습니다."""
        if self.vectorizer is None or self.tfidf_matrix is None:
            return []

        feature_names = self.vectorizer.get_feature_names_out()
        vec_a = self.tfidf_matrix[idx_a].toarray().flatten()
        vec_b = self.tfidf_matrix[idx_b].toarray().flatten()

        # 두 문서 모두에서 TF-IDF > 0인 단어의 합산 점수
        shared_mask = (vec_a > 0) & (vec_b > 0)
        shared_scores = (vec_a + vec_b) * shared_mask

        top_indices = shared_scores.argsort()[-top_n:][::-1]
        keywords = [
            feature_names[i] for i in top_indices
            if shared_scores[i] > 0
        ]
        return keywords


def quick_similarity(text_a: str, text_b: str) -> float:
    """
    두 텍스트 간 빠른 유사도 계산 (단일 비교용).
    대량 비교에는 TextSimilarityAnalyzer.analyze()를 사용하세요.
    """
    analyzer = TextSimilarityAnalyzer(threshold=0.0)
    results = analyzer.analyze(text_a, [text_b])
    return results[0].similarity if results else 0.0
