"""
main.py - Fake News Tracker API 서버
─────────────────────────────────────
FastAPI 기반 REST API. 전체 분석 파이프라인을 조율합니다.

실행 방법:
    uvicorn main:app --reload --port 8000

API 엔드포인트:
    POST /api/analyze   - URL 분석 (전체 파이프라인)
    GET  /api/health    - 서버 상태 확인
    GET  /api/config    - API 연동 상태 확인
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from typing import Optional

from crawler import ArticleCrawler, extract_key_sentences
from search import NewsSearcher
from similarity import TextSimilarityAnalyzer
from fact_check import FactChecker
from graph_builder import PropagationGraphBuilder
from scorer import TrustScorer
from fastapi.responses import FileResponse

# ── 환경변수 로드 ──────────────────────────────────────────
load_dotenv()

# ── 로깅 설정 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fakenews-tracker")

# ── 서비스 인스턴스 ────────────────────────────────────────
crawler = ArticleCrawler(
    timeout=int(os.getenv("REQUEST_TIMEOUT", "15"))
)

searcher = NewsSearcher(
    naver_client_id=os.getenv("NAVER_CLIENT_ID", ""),
    naver_client_secret=os.getenv("NAVER_CLIENT_SECRET", ""),
    google_api_key=os.getenv("GOOGLE_CSE_API_KEY", ""),
    google_cse_id=os.getenv("GOOGLE_CSE_ID", ""),
)

fact_checker = FactChecker(
    api_key=os.getenv("GOOGLE_API_KEY", ""),
)

similarity_analyzer = TextSimilarityAnalyzer(
    threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.4")),
)

graph_builder = PropagationGraphBuilder(
    similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.4")),
)

trust_scorer = TrustScorer()


# ── FastAPI 앱 ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Fake News Tracker API 시작 ===")
    logger.info(f"네이버 API: {'✓ 연동됨' if searcher.has_naver else '✗ 미설정'}")
    logger.info(f"Google CSE: {'✓ 연동됨' if searcher.has_google else '✗ 미설정'}")
    logger.info(f"팩트체크 API: {'✓ 연동됨' if fact_checker.is_available else '✗ 미설정'}")
    yield
    logger.info("=== 서버 종료 ===")

app = FastAPI(
    title="Fake News Tracker API",
    description="가짜 뉴스 전파 경로 추적 시스템",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 설정
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 요청/응답 모델 ────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    url: str
    max_candidates: int = 30  # 최대 후보 검색 수

class AnalyzeResponse(BaseModel):
    success: bool
    original: dict
    graph: dict
    trust_score: dict
    fact_checks: list
    timeline: list
    stats: dict
    error: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    services: dict


# ── API 엔드포인트 ─────────────────────────────────────────
@app.get("/")
async def serve_frontend():
    """메인 웹페이지 제공"""
    return FileResponse("index.html")


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """서버 상태 및 API 연동 상태 확인"""
    return HealthResponse(
        status="ok",
        services={
            "naver_search": searcher.has_naver,
            "google_search": searcher.has_google,
            "fact_check": fact_checker.is_available,
            "crawler": True,
            "similarity": True,
        },
    )


@app.get("/api/config")
async def get_config():
    """현재 API 연동 설정 확인 (키 값은 노출하지 않음)"""
    return {
        "naver_api": {
            "connected": searcher.has_naver,
            "docs": "https://developers.naver.com/apps/#/register",
            "description": "네이버 검색 API (뉴스, 블로그 검색)",
        },
        "google_factcheck": {
            "connected": fact_checker.is_available,
            "docs": "https://console.cloud.google.com/apis/credentials",
            "description": "Google Fact Check Tools API",
        },
        "google_cse": {
            "connected": searcher.has_google,
            "docs": "https://programmablesearchengine.google.com/",
            "description": "Google Custom Search API (글로벌 검색)",
        },
    }


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_url(request: AnalyzeRequest):
    """
    URL을 분석하여 전파 경로, 신뢰도 점수 등을 반환합니다.

    ## 파이프라인
    1. URL 크롤링 (본문 추출)
    2. 핵심 문장 추출 → 검색 쿼리 생성
    3. 네이버/Google API로 유사 기사 검색
    4. 후보 기사들 크롤링
    5. TF-IDF 코사인 유사도 계산
    6. 전파 그래프 구성 (BFS)
    7. 팩트체크 API 대조
    8. 신뢰도 점수 산출
    """
    url = request.url.strip()
    logger.info(f"분석 시작: {url}")

    try:
        # ═══ STEP 1: 원문 크롤링 ═══
        logger.info("[1/7] 원문 크롤링 중...")
        original = await crawler.crawl(url)
        if not original.success:
            raise HTTPException(
                status_code=422,
                detail=f"URL 크롤링 실패: {original.error or '본문 추출 불가'}"
            )

        # ═══ STEP 2: 핵심 문장 추출 ═══
        logger.info("[2/7] 핵심 문장 추출 중...")
        key_sentences = extract_key_sentences(original.text, n=3)
        logger.info(f"  → 핵심 문장 {len(key_sentences)}개 추출")

        # ═══ STEP 3: 유사 기사 검색 ═══
        logger.info("[3/7] 유사 기사 검색 중...")
        search_results = await searcher.search(
            queries=key_sentences,
            max_per_query=request.max_candidates // 3,
        )

        # 블로그도 검색 (제목으로)
        if searcher.has_naver and original.title:
            blog_results = await searcher.search_naver_blog(
                original.title, display=10
            )
            search_results.extend(blog_results)

        logger.info(f"  → 후보 {len(search_results)}개 발견")

        if not search_results:
            # 검색 결과 없으면 원문만으로 결과 반환
            logger.warning("검색 결과 없음 → 단일 문서 분석으로 전환")
            score = trust_scorer.calculate(
                origin_domain=original.domain,
                origin_author=original.author,
                avg_similarity=0,
                num_sources=1,
                fact_check_results=[],
                has_citations=False,
            )
            return AnalyzeResponse(
                success=True,
                original=original.to_dict(),
                graph={"nodes": [], "edges": [], "origin_id": "", "total_reach": 0, "max_depth": 0, "time_span_hours": 0},
                trust_score=score.to_dict(),
                fact_checks=[],
                timeline=[],
                stats={"total_candidates": 0, "similar_count": 0, "avg_similarity": 0},
            )

        # ═══ STEP 4: 후보 기사 크롤링 ═══
        logger.info("[4/7] 후보 기사 크롤링 중...")
        candidate_urls = [r.url for r in search_results[:request.max_candidates]]
        candidate_articles = await crawler.crawl_multiple(candidate_urls)

        # 크롤링 성공한 것만 필터
        valid_candidates = [a for a in candidate_articles if a.success and len(a.text) > 50]
        logger.info(f"  → {len(valid_candidates)}/{len(candidate_urls)}개 크롤링 성공")

        if not valid_candidates:
            logger.warning("후보 크롤링 전부 실패")
            score = trust_scorer.calculate(
                origin_domain=original.domain,
                origin_author=original.author,
                avg_similarity=0,
                num_sources=1,
                fact_check_results=[],
            )
            return AnalyzeResponse(
                success=True,
                original=original.to_dict(),
                graph={"nodes": [], "edges": [], "origin_id": "", "total_reach": 0, "max_depth": 0, "time_span_hours": 0},
                trust_score=score.to_dict(),
                fact_checks=[],
                timeline=[],
                stats={"total_candidates": len(search_results), "similar_count": 0, "avg_similarity": 0},
            )

        # ═══ STEP 5: 텍스트 유사도 분석 ═══
        logger.info("[5/7] TF-IDF 유사도 분석 중...")

        # 원문 + 후보 전체 유사도 분석
        all_texts = [original.text] + [a.text for a in valid_candidates]
        all_articles = [original.to_dict()] + [a.to_dict() for a in valid_candidates]

        sim_results = similarity_analyzer.analyze(
            original_text=original.text,
            candidate_texts=[a.text for a in valid_candidates],
            candidate_urls=[a.url for a in valid_candidates],
        )

        # 전체 유사도 행렬 (그래프 구성용)
        sim_matrix = similarity_analyzer.get_similarity_matrix(all_texts)

        avg_sim = sum(r.similarity for r in sim_results) / max(len(sim_results), 1)
        logger.info(f"  → 유사 문서 {len(sim_results)}개 (평균 유사도 {avg_sim:.2f})")

        # ═══ STEP 6: 전파 그래프 구성 ═══
        logger.info("[6/7] 전파 그래프 구성 중...")
        graph = graph_builder.build(
            articles=all_articles,
            similarity_matrix=sim_matrix,
            original_index=0,
        )

        # ═══ STEP 7: 팩트체크 ═══
        logger.info("[7/7] 팩트체크 중...")
        fact_queries = [original.title] if original.title else key_sentences[:1]
        fact_results = await fact_checker.check_multiple(fact_queries)

        # ═══ 신뢰도 점수 계산 ═══
        has_citations = any(
            kw in original.text
            for kw in ["에 따르면", "에 의하면", "밝혔다", "보도했다", "발표했다"]
        )

        score = trust_scorer.calculate(
            origin_domain=original.domain,
            origin_author=original.author,
            avg_similarity=avg_sim,
            num_sources=len(graph.nodes),
            fact_check_results=fact_results,
            has_citations=has_citations,
        )

        # 그래프 노드에 trust_score 반영
        for node in graph.nodes:
            node.trust_score = trust_scorer._score_source(node.domain)

        # ═══ 타임라인 생성 ═══
        timeline = []
        sorted_nodes = sorted(
            graph.nodes,
            key=lambda n: n.pub_date if n.pub_date else "9999",
        )
        for i, node in enumerate(sorted_nodes):
            timeline.append({
                "order": i,
                "node_id": node.id,
                "name": node.name,
                "platform": node.platform_type,
                "pub_date": node.pub_date or "날짜 미상",
                "depth": node.depth,
            })

        logger.info(
            f"분석 완료: 신뢰도 {score.overall}% ({score.verdict_level}) "
            f"| 노드 {len(graph.nodes)}개 | 엣지 {len(graph.edges)}개"
        )

        return AnalyzeResponse(
            success=True,
            original=original.to_dict(),
            graph=graph.to_dict(),
            trust_score=score.to_dict(),
            fact_checks=[r.to_dict() for r in fact_results],
            timeline=timeline,
            stats={
                "total_candidates": len(search_results),
                "similar_count": len(sim_results),
                "avg_similarity": round(avg_sim, 4),
                "crawl_success_rate": f"{len(valid_candidates)}/{len(candidate_urls)}",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"분석 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"분석 중 오류 발생: {str(e)}")


# ── 메인 실행 ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
