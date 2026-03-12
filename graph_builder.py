"""
graph_builder.py - 전파 그래프 구성 모듈
────────────────────────────────────────
수집된 문서들의 게시 시각과 유사도 정보를 바탕으로
BFS 기반 전파 방향 그래프를 구성합니다.
"""

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from collections import deque
from urllib.parse import urlparse
import tldextract
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class GraphNode:
    """전파 그래프 노드 (문서/출처)"""
    id: str
    name: str            # 출처 이름 (도메인 or 매체명)
    url: str
    domain: str
    platform_type: str   # blog, news, community, sns, youtube, etc.
    text_preview: str    # 본문 앞 200자
    pub_date: str        # 게시 시각
    trust_score: float   # 개별 노드 신뢰도 (0~100)
    followers: int = 0   # 추정 영향력 (도메인 트래픽 기반)
    depth: int = 0       # 원 출처로부터의 거리

    def to_dict(self):
        return asdict(self)


@dataclass
class GraphEdge:
    """전파 그래프 엣지 (전파 경로)"""
    source: str          # 소스 노드 ID
    target: str          # 타겟 노드 ID
    similarity: float    # 텍스트 유사도 (0~1)
    time_diff_hours: float = 0  # 시간 차이

    def to_dict(self):
        return asdict(self)


@dataclass
class PropagationGraph:
    """완성된 전파 그래프"""
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    origin_id: str = ""
    total_reach: int = 0
    max_depth: int = 0
    time_span_hours: float = 0

    def to_dict(self):
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "origin_id": self.origin_id,
            "total_reach": self.total_reach,
            "max_depth": self.max_depth,
            "time_span_hours": round(self.time_span_hours, 1),
        }


# ── 플랫폼 분류 규칙 ────────────────────────────────────────
PLATFORM_RULES = {
    "blog": ["blog", "tistory", "brunch", "velog", "medium"],
    "news": ["news", "경제", "일보", "신문", "biz", "press", "reuters", "bbc"],
    "community": ["dcinside", "fmkorea", "mlbpark", "clien", "ruliweb", "theqoo", "instiz"],
    "cafe": ["cafe.naver"],
    "youtube": ["youtube", "youtu.be"],
    "sns": ["twitter", "x.com"],
    "facebook": ["facebook", "fb.com"],
    "instagram": ["instagram"],
    "tiktok": ["tiktok"],
    "telegram": ["t.me", "telegram"],
}


def classify_platform(url: str, domain: str) -> str:
    """URL/도메인에서 플랫폼 유형을 추정합니다."""
    url_lower = url.lower()
    domain_lower = domain.lower()

    for platform, keywords in PLATFORM_RULES.items():
        for kw in keywords:
            if kw in url_lower or kw in domain_lower:
                return platform

    # 도메인 분석으로 추가 판별
    ext = tldextract.extract(url)
    if ext.domain in ["naver", "daum", "kakao"]:
        if "blog" in url_lower:
            return "blog"
        if "cafe" in url_lower:
            return "cafe"
        if "news" in url_lower:
            return "news"

    return "website"


class PropagationGraphBuilder:
    """전파 그래프 빌더"""

    def __init__(self, similarity_threshold: float = 0.4):
        self.threshold = similarity_threshold

    def build(
        self,
        articles: list[dict],
        similarity_matrix: np.ndarray,
        original_index: int = 0,
    ) -> PropagationGraph:
        """
        수집된 기사들과 유사도 행렬로 전파 그래프를 구성합니다.

        Args:
            articles: 크롤링된 기사 데이터 리스트
                      각 항목: {url, title, text, date, domain}
            similarity_matrix: N×N 코사인 유사도 행렬
            original_index: 분석 대상 원문의 인덱스

        Returns:
            PropagationGraph 객체
        """
        n = len(articles)
        if n == 0:
            return PropagationGraph()

        # 1) 노드 생성
        nodes = []
        for i, art in enumerate(articles):
            domain = art.get("domain", urlparse(art.get("url", "")).netloc)
            nodes.append(GraphNode(
                id=f"node_{i}",
                name=self._get_source_name(art),
                url=art.get("url", ""),
                domain=domain,
                platform_type=classify_platform(art.get("url", ""), domain),
                text_preview=art.get("text", "")[:200],
                pub_date=art.get("date", ""),
                trust_score=0,  # scorer에서 나중에 계산
                depth=0,
            ))

        # 2) 게시 시각 파싱 & 정렬
        pub_times = []
        for art in articles:
            pt = self._parse_date(art.get("date", ""))
            pub_times.append(pt)

        # 3) 원 출처 추정: 가장 이른 게시 시각
        valid_times = [(i, t) for i, t in enumerate(pub_times) if t is not None]
        if valid_times:
            origin_idx = min(valid_times, key=lambda x: x[1])[0]
        else:
            origin_idx = original_index  # 시간 정보가 없으면 입력 URL을 원 출처로

        # 4) BFS로 전파 방향 그래프 구성
        edges = []
        visited = {origin_idx}
        queue = deque([origin_idx])
        depth_map = {origin_idx: 0}

        while queue:
            src = queue.popleft()
            src_time = pub_times[src]

            # 유사도가 높은 이웃 노드 탐색
            neighbors = []
            for tgt in range(n):
                if tgt == src or tgt in visited:
                    continue
                sim = similarity_matrix[src][tgt]
                if sim < self.threshold:
                    continue
                neighbors.append((tgt, sim))

            # 유사도 높은 순으로 정렬
            neighbors.sort(key=lambda x: x[1], reverse=True)

            for tgt, sim in neighbors:
                tgt_time = pub_times[tgt]

                # 시간 방향 검증: 소스가 타겟보다 먼저여야 함
                # 시간 정보가 없으면 유사도만으로 판단
                if src_time and tgt_time and tgt_time < src_time:
                    continue  # 타겟이 더 이전 → 이 방향은 아님

                time_diff = 0
                if src_time and tgt_time:
                    time_diff = (tgt_time - src_time).total_seconds() / 3600

                edges.append(GraphEdge(
                    source=f"node_{src}",
                    target=f"node_{tgt}",
                    similarity=round(sim, 4),
                    time_diff_hours=round(time_diff, 1),
                ))

                visited.add(tgt)
                depth = depth_map[src] + 1
                depth_map[tgt] = depth
                nodes[tgt].depth = depth
                queue.append(tgt)

        # 5) 메타데이터 계산
        max_depth = max(depth_map.values()) if depth_map else 0
        total_reach = sum(n.followers for n in nodes)

        earliest = min((t for t in pub_times if t), default=None)
        latest = max((t for t in pub_times if t), default=None)
        time_span = 0
        if earliest and latest:
            time_span = (latest - earliest).total_seconds() / 3600

        graph = PropagationGraph(
            nodes=nodes,
            edges=edges,
            origin_id=f"node_{origin_idx}",
            total_reach=total_reach,
            max_depth=max_depth,
            time_span_hours=time_span,
        )

        logger.info(
            f"전파 그래프 구성 완료: 노드 {len(nodes)}개, "
            f"엣지 {len(edges)}개, 최대 깊이 {max_depth}"
        )
        return graph

    def _get_source_name(self, article: dict) -> str:
        """기사에서 출처 이름을 추출합니다."""
        # 제목이 있으면 짧게 줄여서 사용
        title = article.get("title", "")
        if title:
            return title[:30] + ("…" if len(title) > 30 else "")

        # 없으면 도메인 사용
        domain = article.get("domain", "")
        if domain:
            ext = tldextract.extract(domain)
            return ext.domain

        return "알 수 없음"

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """다양한 형식의 날짜 문자열을 파싱합니다."""
        if not date_str:
            return None

        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y.%m.%d",
            "%Y/%m/%d",
            "%a, %d %b %Y %H:%M:%S %z",  # RFC 2822 (Naver API)
            "%Y%m%d",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip()[:26], fmt)
            except (ValueError, IndexError):
                continue

        return None
