# 🔍 Fake News Tracker - API 연동 가이드

## 📋 목차
1. [프로젝트 구조](#1-프로젝트-구조)
2. [환경 설정](#2-환경-설정)
3. [API 키 발급 방법](#3-api-키-발급-방법)
4. [백엔드 실행](#4-백엔드-실행)
5. [프론트엔드 연동](#5-프론트엔드-연동)
6. [API 레퍼런스](#6-api-레퍼런스)
7. [배포 가이드](#7-배포-가이드)

---

## 1. 프로젝트 구조

```
fakenews-tracker/
├── backend/
│   ├── main.py              # FastAPI 메인 서버 (파이프라인 조율)
│   ├── crawler.py           # 웹 크롤링 & 텍스트 추출
│   ├── search.py            # 네이버/Google 검색 API 래퍼
│   ├── similarity.py        # TF-IDF 코사인 유사도 분석
│   ├── fact_check.py        # Google Fact Check API 래퍼
│   ├── graph_builder.py     # BFS 전파 그래프 구성
│   ├── scorer.py            # 신뢰도 점수 산출
│   ├── requirements.txt     # Python 의존성
│   └── .env.example         # 환경변수 템플릿
└── frontend/
    └── fake-news-tracker.jsx  # React 프론트엔드
```

### 분석 파이프라인 흐름

```
사용자 → URL 입력
         ↓
[1] crawler.py     → URL에서 HTML 파싱, 본문/제목/날짜 추출
         ↓
[2] crawler.py     → 본문에서 핵심 문장 3개 추출 (검색 쿼리용)
         ↓
[3] search.py      → 네이버 뉴스 API + Google CSE로 유사 기사 후보 수집
         ↓
[4] crawler.py     → 후보 기사들 일괄 크롤링
         ↓
[5] similarity.py  → TF-IDF 벡터화 → 코사인 유사도 행렬 계산
         ↓
[6] graph_builder  → 게시 시각 + 유사도 기반 BFS 전파 그래프 구성
         ↓
[7] fact_check.py  → Google Fact Check API로 팩트체크 대조
         ↓
[8] scorer.py      → 5개 시그널 가중합산 → 신뢰도 점수 (0~100)
         ↓
프론트엔드 → D3.js 네트워크 그래프 렌더링
```

---

## 2. 환경 설정

### 2.1 Python 환경 (3.10+)

```bash
cd backend

# 가상환경 생성
python -m venv venv
source venv/bin/activate   # macOS/Linux
# venv\Scripts\activate    # Windows

# 의존성 설치
pip install -r requirements.txt
```

### 2.2 KoNLPy 설치 (한국어 형태소 분석)

KoNLPy는 Java가 필요합니다:

```bash
# macOS
brew install java
export JAVA_HOME=$(/usr/libexec/java_home)

# Ubuntu/Debian
sudo apt-get install default-jdk

# 설치 확인
python -c "from konlpy.tag import Okt; print('KoNLPy OK')"
```

> KoNLPy 없이도 동작하지만, 한국어 텍스트 유사도 정확도가 크게 떨어집니다.

### 2.3 환경변수 설정

```bash
cp .env.example .env
# .env 파일을 편집하여 API 키 입력
```

---

## 3. API 키 발급 방법

### 3.1 네이버 검색 API (필수 권장)

**무료 한도: 25,000건/일**

1. https://developers.naver.com 접속 → 로그인
2. **Application → 애플리케이션 등록** 클릭
3. 애플리케이션 이름: `FakeNewsTracker`
4. **사용 API** 에서 다음을 체크:
   - ✅ 검색 (뉴스, 블로그)
5. **비로그인 오픈 API 서비스 환경** 에서:
   - 환경 추가: `WEB 설정`
   - 웹 서비스 URL: `http://localhost:3000` (개발용)
6. 등록 완료 후 **Client ID** 와 **Client Secret** 복사

```env
# .env 파일에 입력
NAVER_CLIENT_ID=발급받은_Client_ID
NAVER_CLIENT_SECRET=발급받은_Client_Secret
```

### 3.2 Google Fact Check API (권장)

**무료 한도: 10,000건/일**

1. https://console.cloud.google.com 접속 → 프로젝트 생성
2. 좌측 메뉴 → **API 및 서비스 → 라이브러리**
3. `Fact Check Tools API` 검색 → **사용 설정**
4. **API 및 서비스 → 사용자 인증 정보** → **API 키 만들기**
5. 생성된 API 키 복사

```env
GOOGLE_API_KEY=발급받은_Google_API_Key
```

> 보안 팁: API 키 제한 설정에서 `Fact Check Tools API`만 허용하세요.

### 3.3 Google Custom Search API (선택)

글로벌 뉴스 검색이 필요할 때 사용합니다. 네이버 API만으로도 한국어 뉴스 분석은 충분합니다.

**무료 한도: 100건/일** (유료: $5/1,000건)

1. https://programmablesearchengine.google.com 접속
2. **검색엔진 추가** → 전체 웹 검색 설정
3. 생성된 **검색엔진 ID (cx)** 복사
4. https://console.cloud.google.com → `Custom Search API` 사용 설정
5. API 키 생성

```env
GOOGLE_CSE_ID=발급받은_검색엔진_ID
GOOGLE_CSE_API_KEY=발급받은_API_Key
```

### API 비용 요약

| API | 무료 한도 | 초과 비용 | 필수 여부 |
|-----|-----------|-----------|-----------|
| 네이버 검색 | 25,000건/일 | 없음 (무료) | ⭐ 강력 권장 |
| Google Fact Check | 10,000건/일 | 없음 (무료) | 권장 |
| Google Custom Search | 100건/일 | $5/1,000건 | 선택 |

---

## 4. 백엔드 실행

### 4.1 개발 모드

```bash
cd backend
source venv/bin/activate

# 서버 시작 (자동 리로드)
uvicorn main:app --reload --port 8000
```

### 4.2 실행 확인

```bash
# 상태 확인
curl http://localhost:8000/api/health

# 응답 예시:
# {
#   "status": "ok",
#   "services": {
#     "naver_search": true,
#     "google_search": false,
#     "fact_check": true,
#     "crawler": true,
#     "similarity": true
#   }
# }
```

### 4.3 API 테스트

```bash
# URL 분석 요청
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url": "https://news.example.com/article/12345"}'
```

### 4.4 Swagger UI

브라우저에서 http://localhost:8000/docs 접속하면
대화형 API 문서를 확인할 수 있습니다.

---

## 5. 프론트엔드 연동

React 프론트엔드에서 백엔드 API를 호출하도록 수정합니다.

### 5.1 API 호출 함수 추가

```javascript
// api.js - 백엔드 API 호출 유틸리티

const API_BASE = "http://localhost:8000";

export async function analyzeUrl(url) {
  const response = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, max_candidates: 30 }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "분석 실패");
  }

  return response.json();
}

export async function checkHealth() {
  const response = await fetch(`${API_BASE}/api/health`);
  return response.json();
}

export async function getConfig() {
  const response = await fetch(`${API_BASE}/api/config`);
  return response.json();
}
```

### 5.2 React 컴포넌트에서 사용

```jsx
import { analyzeUrl } from "./api";

function App() {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleAnalyze = async (url) => {
    setLoading(true);
    setError(null);

    try {
      const data = await analyzeUrl(url);
      setResult(data);
      // data.graph.nodes → D3 네트워크 그래프에 전달
      // data.graph.edges → 엣지 데이터
      // data.trust_score → 신뢰도 점수 표시
      // data.fact_checks → 팩트체크 결과
      // data.timeline → 타임라인 표시
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
}
```

### 5.3 D3 그래프 데이터 매핑

백엔드 응답을 프론트엔드 그래프에 매핑하는 예시입니다:

```javascript
function mapToGraphData(apiResponse) {
  const { graph, trust_score } = apiResponse;

  // 노드 매핑
  const nodes = graph.nodes.map(node => ({
    id: node.id,
    name: node.name,
    type: node.platform_type,    // "blog", "news", "community", etc.
    trust: node.trust_score,
    url: node.url,
    time: node.pub_date,
  }));

  // 엣지 매핑
  const edges = graph.edges.map(edge => ({
    source: edge.source,
    target: edge.target,
    sim: edge.similarity,
  }));

  return { nodes, edges, originId: graph.origin_id };
}
```

---

## 6. API 레퍼런스

### POST /api/analyze

URL을 분석하여 전파 그래프, 신뢰도 점수 등을 반환합니다.

**요청:**
```json
{
  "url": "https://example.com/article/123",
  "max_candidates": 30
}
```

**응답:**
```json
{
  "success": true,
  "original": {
    "url": "https://example.com/article/123",
    "title": "기사 제목",
    "text": "본문 내용...",
    "author": "작성자",
    "date": "2025-01-15",
    "domain": "example.com"
  },
  "graph": {
    "nodes": [
      {
        "id": "node_0",
        "name": "출처 이름",
        "url": "https://...",
        "domain": "example.com",
        "platform_type": "news",
        "pub_date": "2025-01-15",
        "trust_score": 45.0,
        "depth": 0
      }
    ],
    "edges": [
      {
        "source": "node_0",
        "target": "node_1",
        "similarity": 0.85,
        "time_diff_hours": 4.2
      }
    ],
    "origin_id": "node_0",
    "total_reach": 152000,
    "max_depth": 3,
    "time_span_hours": 18.5
  },
  "trust_score": {
    "overall": 28.5,
    "source_reliability": 15.0,
    "fact_check_match": 30.0,
    "text_consistency": 45.0,
    "source_diversity": 25.0,
    "author_transparency": 20.0,
    "verdict": "허위 정보 가능성 있음",
    "verdict_level": "warning",
    "explanation": "원 출처의 신뢰도가 낮음..."
  },
  "fact_checks": [
    {
      "claim": "검증된 주장",
      "rating": "거짓",
      "publisher": "서울대 팩트체크센터",
      "url": "https://factcheck.snu.ac.kr/..."
    }
  ],
  "timeline": [
    {
      "order": 0,
      "node_id": "node_0",
      "name": "최초 게시",
      "platform": "blog",
      "pub_date": "2025-01-15T03:22:00",
      "depth": 0
    }
  ],
  "stats": {
    "total_candidates": 42,
    "similar_count": 13,
    "avg_similarity": 0.72,
    "crawl_success_rate": "28/42"
  }
}
```

### GET /api/health

```json
{
  "status": "ok",
  "services": {
    "naver_search": true,
    "google_search": false,
    "fact_check": true,
    "crawler": true,
    "similarity": true
  }
}
```

### GET /api/config

연동 상태 및 각 API의 문서 링크를 반환합니다.

---

## 7. 배포 가이드

### 7.1 Docker (권장)

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Java (KoNLPy 의존성)
RUN apt-get update && apt-get install -y default-jdk && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t fakenews-tracker .
docker run -p 8000:8000 --env-file .env fakenews-tracker
```

### 7.2 Railway / Render 배포

1. GitHub 레포지토리에 `backend/` 디렉토리 push
2. Railway (https://railway.app) 또는 Render (https://render.com) 에서 레포 연결
3. 환경변수에 `.env` 값들 설정
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### 7.3 프론트엔드 배포

React 앱은 Vercel 또는 Netlify에 배포하고,
`API_BASE` URL을 백엔드 배포 주소로 변경하세요.

```javascript
const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000";
```

---

## ⚡ 빠른 시작 요약

```bash
# 1. 코드 다운로드 후 백엔드 디렉토리로 이동
cd backend

# 2. 환경 설정
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 3. .env 파일에 최소한 네이버 API 키 입력
# NAVER_CLIENT_ID=...
# NAVER_CLIENT_SECRET=...

# 4. 서버 실행
uvicorn main:app --reload --port 8000

# 5. 브라우저에서 http://localhost:8000/docs 접속하여 테스트
```
