# 발행 시각 스케줄러

GitHub 예약이 중앙 147분, 최대 438분 늦게 뜬다. 시계를 밖에 두려고 만들었다.

```
Cloudflare 크론 (분 단위로 정확)
   ↓ repository_dispatch
GitHub Actions  →  Social Post 즉시 실행
```

## 까는 법

```bash
cd workers/post-scheduler
npx wrangler login
npx wrangler secret put GITHUB_TOKEN     # 아래에서 만든 토큰을 붙여넣는다
npx wrangler deploy
```

## 토큰 만들기

github.com/settings/personal-access-tokens → Fine-grained token

- Repository access : clarm3126-prog/richman 만
- Permissions       : Contents = Read and write  (repository_dispatch 에 필요)
- 만료               : 1년. 만료되면 글이 조용히 안 나가므로 달력에 적어 둘 것

## 확인

배포 주소를 브라우저로 열면 지금 KST 시각과 예정표가 나온다.
`?fire=queue` 를 붙이면 지금 한 번 쏜다.

## 시각을 바꾸려면

`src/worker.js` 의 `SCHEDULE` 만 고치고 다시 배포한다.
`window` 는 Actions 로 넘어가, 받는 쪽에서 한 번 더 시각을 본다.

## GitHub 크론은 왜 남겨 두나

이 Worker 가 멎어도 글은 나가야 한다. 둘 다 떠서 겹치는 일은 Actions 쪽
시간 창과 하루 한 번 잠금이 막는다.
