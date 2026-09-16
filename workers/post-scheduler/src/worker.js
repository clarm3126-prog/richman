/**
 * 발행 시각을 GitHub 밖에서 잰다.
 *
 * GitHub 예약은 7일을 재보니 중앙 147분, 최대 438분 늦게 떴다. 19:40 에
 * 걸어 둔 글이 새벽 두 시에 나간 날이 있었다. 러너 대기는 0분이라 우리 쪽
 * 문제가 아니라 GitHub 이 예약을 띄우는 순간 자체가 늦는 것이다. 크론 분을
 * 옮겨도 그대로였다.
 *
 * 그래서 시계를 밖에 둔다. Cloudflare 크론은 분 단위로 정확하다. 여기서
 * 시각을 재고, GitHub 에는 "지금 돌려라"는 신호만 보낸다.
 *
 *   Cloudflare 크론 (정확)  →  repository_dispatch  →  Actions 즉시 실행
 *
 * 무료 요금제는 Worker 하나에 크론 트리거 3개까지다. 그래서 트리거는 1분
 * 틱 하나만 두고, 어떤 글을 올릴지는 이 안에서 시각을 보고 고른다. 트리거를
 * 늘리지 않고도 시각을 얼마든지 추가할 수 있다.
 *
 * GitHub 쪽 크론은 지우지 않고 남겨 둔다. 이 Worker 가 멎어도 글이 나가야
 * 하기 때문이다. 늦게 떠서 겹치는 문제는 Actions 쪽 시간 창과 하루 한 번
 * 잠금이 막는다.
 */

const REPO = 'clarm3126-prog/richman';

/**
 * 언제 무엇을 올릴지. 시각은 KST 다.
 *
 * window 는 Actions 로 그대로 넘어간다. 이 Worker 가 제때 쏘더라도 GitHub
 * 이 실행을 늦게 만들 수 있어서, 받는 쪽에서 한 번 더 시각을 본다.
 *
 * days 는 일요일이 0 이다.
 */
const SCHEDULE = [
  { at: '08:09', mode: 'us_brief', days: [1, 2, 3, 4, 5], window: '07:30-10:30' },
  { at: '16:08', mode: 'kr_close', days: [1, 2, 3, 4, 5], window: '15:45-19:00' },
  { at: '18:40', mode: 'auto',     days: [1, 2, 3, 4, 5], window: '17:00-22:30' },
  { at: '19:40', mode: 'queue',    days: [1, 2, 3, 4, 5], window: '17:00-22:30' },
  // 주말은 장이 안 서서 자동 글이 없다. 대기열 글을 내보낸다.
  // 쓰레드 인사이트에서 활동이 가장 많은 자리가 일·금·토 오후 3~6시였다.
  { at: '16:08', mode: 'queue',    days: [0, 6],          window: '15:00-22:30' },
  // 금요일 16:08 은 국장 마감이 이미 쓰고 있어 한 시간 뒤로 둔다.
  { at: '17:18', mode: 'queue',    days: [5],             window: '17:00-22:30' },
];

/** UTC 시각을 KST 의 {요일, 시, 분} 으로 바꾼다. */
function kst(now) {
  const t = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  return {
    day: t.getUTCDay(),
    hhmm: String(t.getUTCHours()).padStart(2, '0') + ':' + String(t.getUTCMinutes()).padStart(2, '0'),
  };
}

function due(now) {
  const { day, hhmm } = kst(now);
  return SCHEDULE.filter((s) => s.at === hhmm && s.days.includes(day));
}

async function dispatch(job, env) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      // GitHub 은 User-Agent 가 없으면 403 을 준다.
      'User-Agent': 'stage2-post-scheduler',
    },
    body: JSON.stringify({
      event_type: 'post',
      client_payload: { mode: job.mode, window: job.window },
    }),
  });
  // 204 가 정상이다. 본문이 없다.
  return { mode: job.mode, status: r.status, ok: r.status === 204, body: r.status === 204 ? '' : await r.text() };
}

export default {
  async scheduled(event, env, ctx) {
    const jobs = due(new Date(event.scheduledTime));
    if (!jobs.length) return;
    ctx.waitUntil(
      Promise.all(jobs.map((j) => dispatch(j, env))).then((rs) => {
        for (const r of rs) console.log(`${r.mode} -> ${r.status}${r.ok ? '' : ' ' + r.body}`);
      })
    );
  },

  /**
   * 브라우저로 열어 보는 용도. 지금 KST 몇 시인지와 예정표를 보여준다.
   * 토큰은 쓰지 않으므로 아무것도 새지 않는다.
   *
   * ?fire=queue 를 붙이면 그 모드를 지금 한 번 쏜다. 배포 직후 한 번
   * 눌러 보라고 둔 것이다. 토큰이 없으면 아무 일도 일어나지 않는다.
   */
  async fetch(req, env) {
    const url = new URL(req.url);
    const now = new Date();
    const { day, hhmm } = kst(now);

    const fire = url.searchParams.get('fire');
    if (fire) {
      const job = SCHEDULE.find((s) => s.mode === fire);
      if (!job) return new Response(`모르는 모드: ${fire}\n`, { status: 400 });
      const r = await dispatch(job, env);
      return new Response(`${r.mode} 쏨 -> ${r.status} ${r.ok ? 'OK' : r.body}\n`, {
        status: r.ok ? 200 : 502,
      });
    }

    const lines = [
      `지금 KST ${'일월화수목금토'[day]} ${hhmm}`,
      '',
      '예정표',
      ...SCHEDULE.map((s) => `  ${s.at}  ${s.mode.padEnd(9)} ${s.days.map((d) => '일월화수목금토'[d]).join('')}  창 ${s.window}`),
      '',
      `지금 쏠 것: ${due(now).map((s) => s.mode).join(', ') || '없음'}`,
      '',
      '시험 발사: ?fire=queue',
    ];
    return new Response(lines.join('\n') + '\n', {
      headers: { 'content-type': 'text/plain; charset=utf-8' },
    });
  },
};
