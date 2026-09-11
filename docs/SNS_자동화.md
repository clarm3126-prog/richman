# 쓰레드 / 인스타그램 자동 운영

종목노트 계정을 키우기 위한 자동화입니다. GitHub Actions가 돌리므로 서버는 필요 없습니다.

- **자동 글쓰기** — 매일 스크리닝 결과로 글을 만들어 쓰레드와 인스타에 올립니다.
- **댓글 자동 답장** — 15분마다 댓글을 확인해 키워드에 맞는 답장을 답니다.
- **DM 자동 발송** — 인스타에서 링크를 물어본 사람에게 DM으로 링크를 보냅니다.

---

## 1. 먼저 알아야 할 제약

### 쓰레드에는 DM API가 없습니다

Meta 공식 Threads API는 글쓰기·답글 읽기·답글 달기까지만 지원합니다.
그래서 쓰레드에서는 **답글에 링크를 직접 담아** 보냅니다.
DM 자동 발송은 인스타그램에서만 동작합니다.

### 인스타 DM은 "댓글을 단 사람"에게만 보낼 수 있습니다

쓰는 기능은 **비공개 답장(private reply)** 입니다.

- 내 게시물에 댓글을 단 사람에게만
- 그 댓글 1건당 **딱 1회**
- 댓글 작성 후 **7일 이내**

팔로워나 아무에게나 DM을 뿌리는 건 Meta 정책 위반이고 계정 정지 사유입니다.
그래서 이 코드에는 그런 기능이 아예 없습니다.

### 인스타는 텍스트만으로 글을 올릴 수 없습니다

반드시 이미지나 영상이 필요합니다.
그래서 스크리닝 결과를 1080×1080 카드 PNG로 그려서 올립니다.
카드는 `assets/cards/`에 커밋되고 GitHub Pages가 서빙하는 URL을 인스타에 넘깁니다.
(인스타가 이미지를 직접 내려받아야 해서 공개 URL이 필요합니다.)

| | 자동 글쓰기 | 댓글 읽기 | 댓글 답장 | DM |
|---|---|---|---|---|
| 쓰레드 | 텍스트 500자 | O | O | **X** (링크를 답글에) |
| 인스타 | 이미지 필수 | O | O | O (댓글당 1회) |

---

## 2. 앱 심사(App Review)가 필요한가?

**내 계정만 자동화하므로 대부분 필요 없습니다.**

- **쓰레드**: 필요 없습니다. 내가 앱 관리자이자 테스터이므로 개발 모드에서 네 가지 권한이 전부 동작합니다.
- **인스타**: 내가 소유한 프로페셔널 계정 하나만 다루므로 Standard Access로 동작합니다.
  다만 비공개 답장(DM)은 계정·앱 상태에 따라 막힐 수 있습니다.
  막히면 DM만 실패하고 **링크가 담긴 대체 답글이 대신 나갑니다.**
  실패하면 텔레그램으로 알림이 오니, 그때 App Review를 넣으면 됩니다.

---

## 3. 쓰레드 설정

### 3-1. 앱 만들기

1. https://developers.facebook.com/apps → **앱 만들기**
2. 유스케이스에서 **Threads API 액세스** 선택
3. 앱 이름은 아무거나 (예: `jongmok-note-social`)

### 3-2. 권한 추가

앱 대시보드 → **Threads API 액세스** → 권한에서 아래 네 개를 모두 추가합니다.

```
threads_basic            모든 엔드포인트에 필수
threads_content_publish  글 발행
threads_read_replies     답글 읽기
threads_manage_replies   답글 달기
```

### 3-3. 내 계정을 테스터로 추가

앱 대시보드 → **역할** → Threads 테스터에 내 쓰레드 계정 추가 →
쓰레드 앱 설정에서 초대 수락.

### 3-4. 토큰 발급

**Threads API 액세스 → 액세스 토큰 생성** 버튼을 누르면 장기 토큰이 바로 나옵니다.
같은 화면에 사용자 ID도 표시됩니다.

- `THREADS_USER_ID` — 숫자 ID
- `THREADS_ACCESS_TOKEN` — `TH...`로 시작하는 긴 문자열

토큰이 잘 되는지 확인:

```bash
curl "https://graph.threads.net/v1.0/me?fields=id,username&access_token=발급받은토큰"
```

---

## 4. 인스타그램 설정

### 4-1. 계정을 프로페셔널로 전환

인스타 앱 → 설정 → 계정 유형 → **프로페셔널 계정으로 전환** (크리에이터 또는 비즈니스).
개인 계정으로는 API가 아예 동작하지 않습니다.

### 4-2. 앱에 Instagram 추가

같은 Meta 앱(또는 새 앱)에서 **Instagram API 설정 → Instagram 로그인으로 설정**을 고릅니다.
(Facebook 페이지 연결 방식이 아니라 Instagram 로그인 방식이 더 간단합니다.)

권한:

```
instagram_business_basic
instagram_business_content_publish   글 올리기
instagram_business_manage_comments   댓글 읽기·답글
instagram_business_manage_messages   DM(비공개 답장)
```

### 4-3. 토큰 발급

**Instagram API 설정 → 액세스 토큰 생성**에서 내 계정을 선택하면 장기 토큰이 나옵니다.

- `IG_USER_ID` — Instagram 계정 ID (숫자)
- `IG_ACCESS_TOKEN` — `IG...`로 시작하는 긴 문자열

확인:

```bash
curl "https://graph.instagram.com/v25.0/me?fields=id,username&access_token=발급받은토큰"
```

---

## 5. GitHub Secrets 등록

저장소 → Settings → Secrets and variables → Actions → **New repository secret**

| 이름 | 값 | 필수 |
|---|---|---|
| `THREADS_USER_ID` | 쓰레드 사용자 ID | 쓰레드 쓰려면 |
| `THREADS_ACCESS_TOKEN` | 쓰레드 장기 토큰 | 쓰레드 쓰려면 |
| `IG_USER_ID` | 인스타 사용자 ID | 인스타 쓰려면 |
| `IG_ACCESS_TOKEN` | 인스타 장기 토큰 | 인스타 쓰려면 |
| `TELEGRAM_BOT_TOKEN` | 이미 등록됨 | 오류 알림용 |
| `TELEGRAM_CHAT_ID` | 이미 등록됨 | 오류 알림용 |
| `GH_PAT` | 토큰 자동 갱신용 (아래 참고) | 선택 |

한쪽 플랫폼 값만 넣으면 그쪽만 동작하고 나머지는 조용히 건너뜁니다.
쓰레드부터 켜보고 되면 인스타를 붙이는 걸 권합니다.

---

## 6. 토큰 만료 관리 — 이게 제일 중요합니다

Meta 장기 토큰은 **60일**이면 만료됩니다. 방치하면 어느 날 갑자기 전부 멈춥니다.

`Social Token Refresh` 워크플로우가 **매주 월요일**에 갱신해서 만료를 막습니다.
갱신한 새 토큰을 어디에 저장하느냐에 따라 두 가지 방식입니다.

### 방식 A — 완전 자동 (권장)

Fine-grained PAT을 만들어 `GH_PAT` 시크릿에 넣으면 워크플로우가
GitHub Secrets를 직접 덮어씁니다. 손댈 일이 없습니다.

1. https://github.com/settings/personal-access-tokens/new
2. Repository access → **Only select repositories** → `richman`
3. Repository permissions → **Secrets: Read and write**
4. 만료일은 1년 정도로
5. 나온 토큰을 `GH_PAT` 시크릿에 저장

### 방식 B — 반자동

`GH_PAT`가 없으면 새 토큰을 **텔레그램으로 보내줍니다.**
받아서 GitHub Secrets에 직접 붙여넣으면 됩니다.

> 텔레그램 대화방에 토큰이 그대로 남습니다.
> 붙여넣은 뒤에는 그 메시지를 지우는 걸 권합니다. 가능하면 방식 A를 쓰세요.

---

## 7. 첫 실행 — 발송 없이 확인부터

Actions 탭에서 워크플로우를 열고 **Run workflow**를 누를 때
`dry_run`을 체크하면 실제로 아무것도 올라가지 않고 무엇을 보낼지만 로그에 찍힙니다.

1. **Social Post** → dry_run 체크 → 실행 → 로그에서 글 본문과 카드 확인
2. **Social Engage** → dry_run 체크 → 실행 → 어떤 댓글에 뭐라고 답할지 확인
3. 둘 다 멀쩡하면 dry_run 없이 다시 실행

로컬에서도 확인할 수 있습니다.

```bash
pip install requests pyyaml pillow
python scripts/social_post.py prepare --dry-run
```

---

## 8. 자동 실행 일정

| 워크플로우 | 시각 | 하는 일 |
|---|---|---|
| Social Post | 평일 KST 19:00 | 스크리너 자동 글 1건 (`--auto`) |
| Social Post | 평일 KST 21:00 | 대기열 글 1건 (`--queue-only`, 없으면 건너뜀) |
| Social Engage | 15분마다 | 댓글 답장 + DM |
| Social Insights | 매일 KST 22:00 | 발행한 글의 조회수·반응 수집 |
| Social Insights | 월요일 KST 09:00 | 수집 + 텔레그램 요약 |
| Social Token Refresh | 월요일 KST 09:00 | 토큰 갱신 |

발행이 두 번으로 나뉜 이유는, 수동 큐가 자동 생성보다 항상 먼저 쓰이기
때문입니다. 한 실행에서는 둘 중 하나만 나오므로, 큐에 글을 쌓아두면
그동안 스크리너 글이 한 건도 안 나갑니다. 시간을 갈라 둘 다 매일
나가게 했고, 큐가 비면 21:00 실행이 그냥 넘어가 하루 1건으로 돌아갑니다.

---

## 9. 문구와 규칙 고치기

전부 [`content/social.yml`](../content/social.yml) 한 파일에 있습니다. 코드는 건드릴 필요 없습니다.

### 답장 규칙

`engage.rules`를 **위에서부터** 훑어 처음 걸리는 규칙 하나만 적용합니다.

```yaml
- name: link_request
  keywords: ["링크", "주소", "어디서"]
  reply_threads: "{user} 여기예요 → {link}"      # 쓰레드용
  reply_instagram: "{user} DM 보내드렸어요."      # 인스타용
  reply_fallback: "{user} 프로필 링크에 있어요! {link}"   # DM 실패 시
  dm: "안녕하세요! {brand} → {link}"              # 인스타에서만
```

- `reply` 하나만 쓰면 양쪽에 같은 문구가 나갑니다.
- 문구를 목록으로 여러 개 주면 그중 하나를 무작위로 고릅니다. **같은 답장이 반복되면 스팸으로 보일 수 있으니 2~3개씩 넣어두는 걸 권합니다.**
- 자리표시자: `{user}` `{link}` `{brand}`
- 띄어쓰기는 무시하고 매칭합니다 ("링크 주세요" = "링크주세요").

### 답장하지 말아야 할 댓글

`skip_keywords`에 걸리면 아무 반응도 하지 않습니다.
시비나 홍보성 댓글에 자동 답장이 나가는 사고를 막는 안전장치입니다.

### 규칙에 안 걸린 댓글

`engage.default`가 적용됩니다. 아무 반응도 원하지 않으면 `reply`를 통째로 지우세요.

### 인스타 소개 카드 (캐러셀 2번째 장)

인스타에는 **2장짜리 캐러셀**로 올라갑니다.

```
1번째 장   그날의 종목 카드 (매일 새로 생성)
2번째 장   고정 소개 카드 (계정이 뭘 올리는 곳인지 + 종목노트 활용법)
```

2번째 장 내용은 `post.about_card`에서 고칩니다.

```yaml
post:
  about_card:
    title: "이 계정은"
    sections:
      - heading: "매일 뭘 올리나"
        body: "장 마감 후 국내 전 종목을 훑어서..."
    footer: "종목 추천이 아닙니다..."
```

- 내용을 바꾸면 다음 발행 때 `assets/cards/about.png`가 새로 그려집니다.
- 바뀌지 않으면 같은 PNG가 나오므로 git이 커밋하지 않습니다.
- 본문이 길면 카드 안에서 자동으로 줄바꿈되고, 넘치면 뒤가 잘립니다. 섹션당 3~4줄이 적당합니다.
- `about_card`를 통째로 지우면 소개 카드 없이 종목 카드 1장만 올라갑니다.

### 직접 쓴 글 올리기

[`content/posts.yml`](../content/posts.yml)에 넣어두면 자동 생성보다 **먼저** 발행됩니다.

```yaml
queue:
  - text: |
      오늘부터 종목노트에 매도 시그널 기능이 붙었습니다.
      보유 종목 등록해두면 손절선 도달할 때 텔레그램으로 알려드려요.
    platforms: [threads]
    after: "2026-09-10"     # 이 날짜 이후에만 발행 (선택)
```

인스타에도 올리려면 `image: assets/cards/파일명.png`를 같이 적어야 합니다.

### 임시로 끄기

```yaml
post:
  enabled: false     # 자동 글쓰기만 끄기
engage:
  enabled: false     # 댓글 대응만 끄기
```

---

## 10. 안전장치

- 하루 발행 상한 `post.max_per_day` (기본 1건)
- 한 번 실행당 처리 상한 `engage.max_actions_per_run` (기본 20건)
- 같은 댓글에 두 번 답하지 않음 — 처리한 ID를 `data/social/handled.json`에 기록
- **내가 먼저 답글을 단 댓글은 건너뜀** — 기록에 없어도 실제 댓글창을 확인한다.
  쓰레드는 `/conversation`으로 대화 전체를, 인스타는 댓글의 `replies`를 읽어
  내 계정 답글이 이미 있으면 답글도 DM도 보내지 않는다.
- 내 계정이 단 댓글은 무시
- 오류가 나면 텔레그램으로 알림

**저장소가 Public이므로 댓글 작성자 아이디나 댓글 본문은 저장하지 않습니다.**
처리 여부 판단에 필요한 댓글 ID와 날짜만 남기고, 30일이 지나면 지웁니다.

---

## 11. 문제가 생기면

| 증상 | 원인 | 해결 |
|---|---|---|
| `자격증명 없음 - 건너뜀` | Secrets 미등록 | 5번 항목 확인 |
| `(#190) access token expired` | 토큰 만료 | Token Refresh 수동 실행, 안 되면 재발급 |
| 인스타 DM만 실패 | 권한 부족 | App Review 신청. 그동안은 대체 답글이 나감 |
| `이미지 URL이 아직 열리지 않음` | Pages 배포 지연 | 워크플로우 재실행 |
| `한글 폰트를 찾지 못했습니다` | fonts-nanum 미설치 | 워크플로우의 폰트 설치 단계 확인 |
| 글이 안 올라감 | 오늘 상한 도달 또는 데이터 오래됨 | 로그 확인 |

### 계정 정지를 피하려면

- 답장 문구를 여러 개 두고 돌려쓰기 (똑같은 문구 반복이 가장 위험)
- 하루 발행 1~2건 유지
- `skip_keywords` 관리
- **처음 일주일은 매일 실제로 나간 답장을 눈으로 확인**할 것

자동화가 사람 대신 말을 하는 것이므로, 초반에는 반드시 직접 검수하세요.

---

## 11-1. 성과 보기

어떤 글이 읽히는지 보려면 `data/social/insights.json`을 열면 됩니다.
매일 22:00에 쓰레드·인스타 인사이트 API로 최근 30일 글의 조회수·좋아요·
답글을 긁어와 덮어씁니다. 조회수는 시간이 지나며 늘어나므로 매번 다시
물어봅니다.

월요일 아침에는 텔레그램으로 요약이 옵니다. 최근 14일 글을 조회수 순으로
보여주고, 글 종류(screener / momentum / theme / manual)별 평균 조회수를
같이 냅니다. **종목 나열 글과 매매 기록 글 중 뭐가 읽히는지가 여기서
갈립니다.**

지금 당장 보고 싶으면 Actions에서 `Social Insights`를 수동 실행하세요.

지표 이름은 플랫폼마다 다르고(쓰레드 `replies` = 인스타 `comments`),
계정 상태나 미디어 종류에 따라 아예 안 주는 지표도 있습니다. 그래서
묶어서 요청했다가 실패하면 하나씩 다시 물어보고 받을 수 있는 것만
저장합니다. 일부 지표가 비어 있어도 정상입니다.

팔로워가 적은 초반에는 인스타 쪽 숫자가 안 나오거나 0으로 올 수
있습니다. 쓰레드 숫자를 먼저 보세요.

---

## 11-2. 시황 글 (16:00 국장 / 08:00 미장)

정해진 시각에 쓰레드로만 나가는 글입니다. 카드가 없어서 인스타에는
올리지 않습니다. 하루에 네 번 올리면 인스타 계정이 시끄러워지기도 하고요.

| 시각 | cron (UTC) | 내용 | 데이터 |
|---|---|---|---|
| 평일 16:00 | `0 7 * * 1-5` | 국장 마감 | `data/market.json` (15:55 갱신) |
| 평일 08:00 | `0 23 * * 0-4` | 미장 브리핑 | 야후 지수 + `data/us_results.json` |

미장 글의 요일이 `0-4`인 건 UTC 일요일 23:00이 KST 월요일 08:00이기
때문입니다. 국장 개장(09:00) 전에 나가야 해서 08:00으로 잡았습니다.

같은 종류가 하루에 두 번 나가지 않도록 막아 두었습니다. 그래서 워크플로를
다시 돌려도 중복 발행되지 않습니다. `post.max_per_day` 상한과는 따로
셉니다 (시황 글은 시각이 고정이라 상한에 걸릴 이유가 없습니다).

손으로 확인하려면:

```
python scripts/social_post.py prepare --dry-run --brief=kr_close
python scripts/social_post.py prepare --dry-run --brief=us_brief
```

### 뉴스 제목은 어떤 기준으로 고르나

기사 **제목과 매체 이름만** 씁니다. 본문은 가져오지 않고 주소도 붙이지
않습니다. 본문을 옮기면 저작권 침해이고, 쓰레드는 본문에 링크가 있으면
도달이 크게 깎이기 때문입니다.

최신순으로 앞에서 세 개를 집으면 시황과 상관없는 기사가 섞입니다.
그래서 `scripts/social/news.py`가 제목에 점수를 매기고 기준을 넘은 것만
씁니다.

1. **걸러내기** — `[그래픽]` `[특징주]` 같은 자동 생성 기사, "급등주"
   "유망주" "추천주" 같은 낚시성 제목은 제목만 보고 뺍니다 (`DROP`)
2. **시간** — 국장 글은 12시간, 미장 글은 18시간 안에 나온 기사만 봅니다
3. **점수** — 시장 전체를 말하는 단어에 가중치를 줍니다 (`CORE`).
   코스피·증시·환율·연준·나스닥은 3점, 기관·수급·유가는 2점 식입니다
4. **오늘 연결** — 그날 가장 많이 오른 테마와 그 대표주 이름이 제목에
   있으면 4점을 더합니다. 오늘 실제로 움직인 것과 이어지는 기사가
   시황 글에는 가장 잘 붙습니다
5. **매체 분산** — 같은 매체는 한 줄만 씁니다
6. **미달 처리** — 3점을 넘은 기사가 2개 미만이면 **뉴스 항목을 통째로
   뺍니다.** 억지로 세 줄을 채우는 것보다 없는 게 낫습니다

6번이 핵심입니다. 의미 없는 기사가 섞이면 글 전체의 신뢰가 깎입니다.
기준을 바꾸고 싶으면 `news.py`의 `CORE`(가중치), `DROP`(제외 단어),
`MIN_SCORE`(기준 점수), `MIN_ITEMS`(최소 개수)를 고치면 됩니다.

고른 결과는 발행 전에 `--dry-run`으로 눈으로 확인할 수 있습니다.

### 카페·커뮤니티 글은 쓰지 않습니다

네이버 카페 글은 회원들의 저작물이고 대부분 무단 전재를 금지합니다.
요약해서 올리는 것도 2차적저작물이라 똑같이 침해입니다. API로 목록이
읽히더라도 쓰지 않습니다. 계정이 신고 한 번에 날아갈 수 있고, 무엇보다
"내가 직접 계산한 데이터"라는 이 계정의 전제가 무너집니다.

---

## 12. 남은 일 (2026-09-07 기준)

설정과 운영은 끝났고 자동으로 돌아가는 상태입니다. 아래는 나중에 손볼 것들입니다.

### 우선순위 높음 — 사용자가 늘기 전에

**1. ~~텔레그램 연결 코드가 공개 로그에 남습니다~~ (2026-09-10 처리 완료)**

저장소가 Public이라 Actions 로그도 공개됩니다. 그런데 연결 성공 로그에 코드와
chat_id를 그대로 찍었고, `link_code_to_chat()`이 코드 재사용을 막지 않아서
로그에서 코드를 본 사람이 그 코드를 봇에 보내면 **그 사용자의 알림이 자기
대화방으로 넘어갈 수** 있었습니다.

처리한 것:
- `telegram_link.py` · `user_alerts.py` · `user_exit_alerts.py` 로그에서
  코드와 chat_id를 뒤 3자리만 남기고 마스킹
- 연결과 동시에 `link_code`를 새 값으로 재발급 — 같은 PATCH 한 번에 처리하며,
  중복이면 다른 값으로 최대 5회까지 다시 뽑습니다. 이미 로그에 남아 있던
  과거 코드는 그 사용자가 다시 연결하는 순간 무효가 됩니다.
- 코드가 한 번 쓰고 바뀌므로, 같은 코드를 두 번 보낸 경우를 `already_linked()`로
  갈라내 "이미 연결되어 있습니다"로 답합니다.

**아직 남은 한 가지 — 과거 로그에 이미 찍힌 코드**

재발급은 앞으로의 연결에만 적용됩니다. 이미 연결을 마친 사용자의 코드는
DB에 예전 값 그대로 있고, 그 값이 옛 Actions 로그에 남아 있습니다.
그 사용자들은 앱에서 코드를 다시 볼 일이 없으니(연결되면 코드를 안 보여줍니다),
통째로 갈아 끼우면 됩니다. Supabase SQL Editor에서 **한 번만** 실행하세요.

```sql
update profiles
set link_code = upper(substring(replace(gen_random_uuid()::text, '-', '') from 1 for 6))
where telegram_chat_id is not null;
```

`link_code`에 unique 제약이 있으면 드물게 충돌로 실패할 수 있는데, 그냥 다시
실행하면 됩니다. 이걸 돌리고 나면 옛 로그의 코드는 전부 죽은 값이 됩니다.

**2. 히스토리 재작성 뒷정리**

2026-09-06에 `git filter-repo`로 개인 매매 데이터를 전체 이력에서 지우고
세 브랜치를 force push 했습니다. 하지만 옛 커밋 SHA로 직접 접근하면 아직
열립니다 (특히 `refs/pull/1/head` = `a64643d5`).

GitHub Support(https://support.github.com/contact)에 캐시된 뷰와 stale ref
제거를 요청해야 완전히 사라집니다. 요청 문구는 아래 항목 참고.

```
Repository: clarm3126-prog/richman
I rewrote history with git filter-repo to remove files containing personal
financial data and force-pushed all branches. Please permanently remove the
cached views and stale references to the old commits, including
refs/pull/1/head.
Removed paths: data/trade_journal.json, data/watchlist.json, data/alerts_config.json
```

### 우선순위 보통

**3. 실적 공시 알림이 사실상 멈춰 있습니다**

`earnings_calendar.py`의 알림 대상은 "관심종목 + 미너비니 strict"인데,
`data/watchlist.json`을 지우면서 관심종목 쪽이 사라졌습니다. 이 스크립트에는
Supabase 대체 경로가 없어서 (목표가는 `user_alerts.py`, 매도 시그널은
`user_exit_alerts.py`가 커버) **strict 통과 종목만 남았고 현재 0개입니다.**

Supabase watchlist를 읽도록 바꾸면 되살아납니다. 30분 정도 작업입니다.

**4. 스크리닝 결과는 소유자에게만 갑니다**

`screener.py` / `momentum_screener.py` / `exit_signals.py` /
`earnings_calendar.py` / `fetch_prices.py` 모두 `TELEGRAM_CHAT_ID` 고정입니다.
다른 사용자는 **본인 관심종목 알림과 본인 보유종목 시그널만** 받습니다.

앱의 핵심 가치가 스크리닝 결과인데 텔레그램으로는 안 나가므로, 재방문을
늘리려면 선택지가 있습니다.
- 현행 유지 (웹사이트 방문 유도)
- 연결된 전 사용자에게 발송 (알림 피로 + 텔레그램 초당 30건 제한 주의)
- 앱에 "매일 스크리닝 결과 받기" 토글 추가

**5. `GH_PAT` 등록 여부 확인**

없으면 60일 뒤 Meta 토큰 만료로 자동화가 통째로 멈춥니다.
`Social Token Refresh` 워크플로우 로그를 보면 알 수 있습니다.
없어도 새 토큰이 텔레그램으로 오긴 하지만 손으로 넣어야 합니다.

### 운영 메모

- **인스타 계정이 갓 전환된 상태라** subcode 2207051(스팸 의심 차단)이 종종
  납니다. 게시가 실제로 됐는지 확인하는 로직이 들어 있어 헛알림은 안 갑니다.
  손으로도 게시물을 올리고 평범한 활동을 섞으면 완화됩니다.
- **쓰레드 링크 요청은 직접 DM으로 보내야 합니다.** 쓰레드에 DM API가 없어
  봇이 못 보냅니다. 대상 명단이 텔레그램으로 옵니다.
- 첫 주에는 실제로 나간 답글을 매일 눈으로 확인하세요.

## 13. 파일 구성

```
scripts/
  social_post.py            글 발행 (prepare / publish)
  social_engage.py          댓글 답장 + DM
  social_refresh_token.py   토큰 갱신
  social/
    config.py         설정 로딩
    store.py          상태 저장
    compose.py        글 본문 작성
    card.py           카드 PNG 생성
    matcher.py        댓글 키워드 매칭
    threads_api.py    Threads API
    instagram_api.py  Instagram API
    notify.py         텔레그램 알림

content/
  social.yml    설정과 문구 (여기만 고치면 됨)
  posts.yml     직접 쓴 글 대기열

scripts/
  social_post.py       발행 (--auto / --queue-only)
  social_engage.py     댓글 대응
  social_insights.py   성과 수집
  make_blog_cards.py   블로그 매매 기록 카드 생성 (필요할 때 직접 실행)

data/social/    상태 (자동 생성)
assets/cards/   카드 이미지 (60일 지나면 자동 삭제)
```
