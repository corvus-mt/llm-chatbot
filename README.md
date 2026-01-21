# Kid Chatbot Web App

9세 아이를 위한 안전한 챗봇 웹앱의 최소 골격입니다. 테스트 기반 개발 흐름을 따르며, 우선 테스트용 엔드포인트부터 제공합니다.

## 개발 환경 셋업

```powershell
# Python 3.12 권장
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 테스트 실행

```powershell
# 가상환경을 활성화한 경우
pytest -q

# PATH에 pytest가 없으면 직접 실행
.\.venv\Scripts\pytest.exe -q
```

## 서버 실행

```powershell
uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000` 으로 접속하세요.

## 테스트용 엔드포인트

- `GET /api/ping`: 테스트용 응답과 페르소나 정보를 반환합니다.

## 다음 단계

- 상용 LLM API 연동을 위한 환경 변수/시크릿 관리
- 챗봇 대화 엔드포인트 설계 및 프론트엔드 UI 확장
