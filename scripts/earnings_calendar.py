#!/usr/bin/env python3
"""실적 캘린더 — DART 잠정실적/분기보고서/사업보고서 공시 모니터링.

매일 1회 (장 마감 후) 실행.
- 최근 7일 + 향후 7일 공시 catch
- 잠정실적 공시: surprise 감지 (직전 분기 대비)
- 보유/관심 종목의 실적 발표 D-7 알림

출력: data/earnings_calendar.json
Telegram: 관심 종목 실적 발표 임박 + 잠정실적 surprise
"""
import io
import json
import os
import re
import sys
import time
import traceback
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests
from bs4 import BeautifulSoup

KST = pytz.timezone("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EarningsCalendar/1.0)"}

DART_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"

sys.path.insert(0, str(Path(__file__).parent))
from screener import send_telegram, DEDUP_RESET_DAYS, log_alert


def fetch_dart_disclosures(bgn_de, end_de, keyword=None, max_pages=5, pblntf_ty=None):
    """DART 공시검색 (특정 기간).
    pblntf_ty: 공시유형 필터 ('A'=정기공시 등). None이면 전체.
    """
    if not DART_KEY:
        return []
    out = []
    for page in range(1, max_pages + 1):
        url = f"{DART_BASE}/list.json"
        params = {
            "crtfc_key": DART_KEY,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": page,
            "page_count": 100,
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("status") != "000":
                break
            items = data.get("list", []) or []
            if not items:
                break
            if keyword:
                items = [it for it in items if keyword in (it.get("report_nm") or "")]
            out.extend(items)
            if len(items) < 100:
                break
            time.sleep(0.05)
        except Exception:
            break
    return out


def parse_earnings_disclosures(items, target_codes=None, surprise_only=False):
    """공시 list에서 실적 관련 추출.
    target_codes: set of stock_code — 필터 (None이면 전체)
    surprise_only: True면 잠정실적/영업(잠정)실적만 (정기보고서 제외)
                   = 실제 surprise 가능한 공시만
    """
    if surprise_only:
        # 잠정실적만 — 시장 반응이 큰 공시
        keywords = ["잠정실적", "영업(잠정)실적", "매출액또는손익구조30%이상변경"]
    else:
        keywords = ["잠정실적", "분기보고서", "반기보고서", "사업보고서", "영업(잠정)실적", "영업실적"]
    out = []
    for item in items:
        report_nm = item.get("report_nm", "") or ""
        stock_code = item.get("stock_code", "") or ""
        if not stock_code or not stock_code.isdigit():
            continue
        stock_code = stock_code.zfill(6)
        if target_codes and stock_code not in target_codes:
            continue
        if not any(k in report_nm for k in keywords):
            continue
        out.append({
            "code": stock_code,
            "name": item.get("corp_name", ""),
            "report_nm": report_nm,
            "rcept_dt": item.get("rcept_dt", ""),  # YYYYMMDD
            "rcept_no": item.get("rcept_no", ""),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no', '')}",
        })
    return out


# ================================
# 공시 본문 파싱 (잠정실적 매출/영업이익 추출)
# ================================

def parse_num(s):
    """문자열에서 숫자 추출 (콤마 제거, 음수 처리)."""
    if not s:
        return None
    s = str(s).strip().replace(",", "").replace(" ", "").replace("\xa0", "")
    if not s or s in ("-", "—", "–"):
        return None
    # 마이너스 부호 처리: △/▲ = positive 음수, (123) = -123
    neg = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
        neg = True
    if s.startswith("△") or s.startswith("▲"):
        s = s[1:]
        neg = True
    if s.startswith("-"):
        s = s[1:]
        neg = True
    try:
        v = float(s) if "." in s else int(s)
        return -v if neg else v
    except Exception:
        return None


def fetch_disclosure_body(rcept_no):
    """DART OpenAPI로 공시 본문 ZIP 받아서 HTML/XML 텍스트 반환."""
    if not DART_KEY or not rcept_no:
        return None
    url = f"{DART_BASE}/document.xml"
    params = {"crtfc_key": DART_KEY, "rcept_no": rcept_no}
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200 or len(r.content) < 100:
            return None
        z = zipfile.ZipFile(io.BytesIO(r.content))
        # 가장 큰 .xml 파일이 보통 본문
        biggest = None
        biggest_size = 0
        for info in z.infolist():
            if info.filename.lower().endswith((".xml", ".html", ".htm")):
                if info.file_size > biggest_size:
                    biggest_size = info.file_size
                    biggest = info.filename
        if not biggest:
            return None
        raw = z.read(biggest)
        # 인코딩 시도 (DART는 보통 utf-8 또는 euc-kr)
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  body fetch err {rcept_no}: {e}")
        return None


def parse_earnings_body(content):
    """잠정실적/손익구조 변경 공시 본문에서 매출/영업이익/당기순이익 추출.
    Returns dict with revenue/op_profit/net_profit + prev/yoy_change% if found.
    """
    if not content:
        return None
    try:
        soup = BeautifulSoup(content, "html.parser")
    except Exception:
        return None

    result = {}
    # 잠정실적 표 패턴: 항목 | 당기실적 | 전년동기실적 | 전기대비증감 | 전년동기대비증감
    target_keywords = {
        "revenue": ("매출액", "수익(매출액)", "매출"),
        "op_profit": ("영업이익", "영업손익"),
        "net_profit": ("당기순이익", "당기순손익", "지배기업의 소유주에게 귀속되는 당기순이익"),
    }

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            first = cells[0].get_text(strip=True).replace(" ", "")
            for field, kws in target_keywords.items():
                if any(kw.replace(" ", "") in first for kw in kws):
                    if field in result:
                        continue  # 이미 찾음 (첫 번째만)
                    # 숫자 cells 추출
                    nums = [parse_num(c.get_text()) for c in cells[1:]]
                    nums_valid = [n for n in nums if n is not None]
                    if nums_valid:
                        result[field] = nums_valid[0]  # 당기실적
                        if len(nums_valid) >= 2:
                            result[f"{field}_prev"] = nums_valid[1]  # 전년동기 또는 전기
                    break

    if not result:
        return None
    # 변화율 계산
    for field in ("revenue", "op_profit", "net_profit"):
        cur = result.get(field)
        prev = result.get(f"{field}_prev")
        if cur is not None and prev is not None and prev != 0:
            result[f"{field}_change_pct"] = round((cur - prev) / abs(prev) * 100, 1)
    return result


def fmt_amount(v):
    """숫자를 한국식 단위 (억/조)로 포맷. 입력 단위는 원 또는 백만원 추정."""
    if v is None:
        return "—"
    # 양수/음수 sign
    sign = "-" if v < 0 else ""
    a = abs(v)
    # 원 단위라고 가정. 조/억/만 변환
    if a >= 1_0000_0000_0000:  # 1조+
        return f"{sign}{a / 1_0000_0000_0000:.1f}조"
    elif a >= 1_0000_0000:  # 1억+
        return f"{sign}{a / 1_0000_0000:.0f}억"
    elif a >= 1_0000:  # 1만+ (이건 백만원 단위라고 보고 다시 변환)
        # 잠정실적 표는 보통 백만원 또는 천원 단위일 수도
        return f"{sign}{a:,}"
    else:
        return f"{sign}{a:,}"


def fetch_quarter_trend(stock_code, financials_cache):
    """캐시에서 종목의 최근 정식 분기 + YoY/QoQ 추세 요약."""
    fin = financials_cache.get(stock_code)
    if not fin or not fin.get("quarters"):
        return None
    q = fin["quarters"]
    sorted_keys = sorted(q.keys(), reverse=True)
    latest_q_key = None
    prev_q_key = None
    for k in sorted_keys:
        year, qname = k.split("_")
        if qname == "Y":
            continue
        if not latest_q_key:
            latest_q_key = k
        elif not prev_q_key:
            prev_q_key = k
            break
    if not latest_q_key:
        return None
    latest = q[latest_q_key]
    out = {
        "quarter": latest_q_key,
        "revenue": latest.get("매출액"),
        "op_profit": latest.get("영업이익"),
        "op_margin": latest.get("영업이익률"),
    }
    # YoY
    year, qname = latest_q_key.split("_")
    yoy_key = f"{int(year)-1}_{qname}"
    yoy = q.get(yoy_key)
    if yoy:
        if yoy.get("매출액", 0) > 0:
            out["revenue_yoy"] = round((latest.get("매출액", 0) - yoy["매출액"]) / yoy["매출액"] * 100, 1)
        if yoy.get("영업이익", 0) > 0:
            out["op_profit_yoy"] = round((latest.get("영업이익", 0) - yoy["영업이익"]) / yoy["영업이익"] * 100, 1)
    # QoQ
    prev = q.get(prev_q_key) if prev_q_key else None
    if prev:
        if prev.get("매출액", 0) > 0:
            out["revenue_qoq"] = round((latest.get("매출액", 0) - prev["매출액"]) / prev["매출액"] * 100, 1)
        if prev.get("영업이익", 0) > 0:
            out["op_profit_qoq"] = round((latest.get("영업이익", 0) - prev["영업이익"]) / prev["영업이익"] * 100, 1)
    return out


def load_dart_financials():
    """data/dart_financials.json cache 로드."""
    p = Path("data/dart_financials.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect_target_codes():
    """알림 대상 종목 = watchlist + 미너비니 strict (8/8 통과만).
    - 관심 종목: 사용자가 명시적으로 추가한 것
    - strict: 8개 조건 모두 통과한 최강 종목만 (보통 0~10개)
    노이즈 최소화. strong/momentum top 50은 제외 (너무 많아짐).
    """
    targets = set()
    # 1. Watchlist
    p = Path("data/watchlist.json")
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for item in data.get("watchlist", []):
                code = str(item.get("code", "")).zfill(6)
                if code:
                    targets.add(code)
        except Exception:
            pass
    # 2. Minervini strict only (8/8 통과)
    p = Path("data/screener_results.json")
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for r in (data.get("results") or []):
                code = r.get("code")
                if code and r.get("minervini_strict"):
                    targets.add(code)
        except Exception:
            pass
    return targets


def notify_earnings(my_disclosures, financials_cache=None):
    """관심 종목 잠정실적/실적 공시 → Telegram 알림.
    각 disclosure에 대해:
    1. 본문 파싱 시도 (잠정실적 매출/영업이익 추출)
    2. 실패 시 cache trend 사용 (최근 정식 분기 기준)
    3. 둘 다 실패 시 '확인 필요' 표시
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("  TELEGRAM_BOT_TOKEN/CHAT_ID env not set")
        return
    financials_cache = financials_cache or {}

    alerted_path = Path("data/earnings_alerted.json")
    alerted = {"items": {}}
    if alerted_path.exists():
        try:
            raw = json.loads(alerted_path.read_text(encoding="utf-8"))
            alerted["items"] = raw.get("items", {})
        except Exception:
            pass
    today = datetime.now(KST)
    cutoff = today.timestamp() - DEDUP_RESET_DAYS * 86400
    active = set()
    for key, date_str in list(alerted["items"].items()):
        try:
            ts = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
            if ts >= cutoff:
                active.add(key)
            else:
                del alerted["items"][key]
        except Exception:
            pass

    new_items = []
    for d in my_disclosures:
        key = d["rcept_no"]
        if key in active:
            continue
        new_items.append(d)

    if not new_items:
        print("  no new earnings disclosures (skip telegram)")
        return

    today_str = today.strftime("%Y-%m-%d")
    lines = [f"📊 *관심 종목 실적 공시* — {today_str}\n"]

    def fmt_change(pct):
        """변화율을 +12.5% 또는 -3.2% 형식으로."""
        if pct is None:
            return ""
        sign = "+" if pct >= 0 else ""
        emoji = "🟢" if pct >= 0 else "🔴"
        return f"{emoji} {sign}{pct}%"

    for d in new_items[:10]:
        emoji = "🚨" if "잠정" in d["report_nm"] or "변경" in d["report_nm"] else "📋"
        lines.append(f"{emoji} *{d['name']}* (`{d['code']}`)")
        lines.append(f"  {d['report_nm']}")
        lines.append(f"  접수: {d['rcept_dt']}")

        # 1. 본문 파싱 시도
        print(f"  parsing body for {d['code']} ({d['rcept_no']})...")
        body = fetch_disclosure_body(d['rcept_no'])
        parsed = parse_earnings_body(body) if body else None
        time.sleep(0.1)  # rate limit

        if parsed and (parsed.get('revenue') or parsed.get('op_profit')):
            # 본문에서 직접 추출 성공
            lines.append(f"  📊 *공시 본문 추출:*")
            if parsed.get('revenue') is not None:
                rev_chg = parsed.get('revenue_change_pct')
                lines.append(f"    매출액: `{fmt_amount(parsed['revenue'])}` {fmt_change(rev_chg)}")
            if parsed.get('op_profit') is not None:
                op_chg = parsed.get('op_profit_change_pct')
                lines.append(f"    영업이익: `{fmt_amount(parsed['op_profit'])}` {fmt_change(op_chg)}")
            if parsed.get('net_profit') is not None:
                np_chg = parsed.get('net_profit_change_pct')
                lines.append(f"    당기순이익: `{fmt_amount(parsed['net_profit'])}` {fmt_change(np_chg)}")
        else:
            # 2. Fallback: cache trend (최근 정식 분기)
            trend = fetch_quarter_trend(d['code'], financials_cache)
            if trend:
                lines.append(f"  📊 *최근 정식 분기 ({trend['quarter']}) 추세:*")
                if trend.get('revenue'):
                    yoy = trend.get('revenue_yoy')
                    qoq = trend.get('revenue_qoq')
                    extras = []
                    if yoy is not None:
                        extras.append(f"YoY {('+' if yoy >= 0 else '')}{yoy}%")
                    if qoq is not None:
                        extras.append(f"QoQ {('+' if qoq >= 0 else '')}{qoq}%")
                    extras_str = f" ({' / '.join(extras)})" if extras else ""
                    lines.append(f"    매출액: `{fmt_amount(trend['revenue'])}`{extras_str}")
                if trend.get('op_profit'):
                    yoy = trend.get('op_profit_yoy')
                    qoq = trend.get('op_profit_qoq')
                    extras = []
                    if yoy is not None:
                        extras.append(f"YoY {('+' if yoy >= 0 else '')}{yoy}%")
                    if qoq is not None:
                        extras.append(f"QoQ {('+' if qoq >= 0 else '')}{qoq}%")
                    extras_str = f" ({' / '.join(extras)})" if extras else ""
                    lines.append(f"    영업이익: `{fmt_amount(trend['op_profit'])}`{extras_str}")
                if trend.get('op_margin'):
                    lines.append(f"    영업이익률: {trend['op_margin']}%")
                lines.append(f"  ⚠️ 본 공시 잠정실적은 DART 링크에서 직접 확인 필요")
            else:
                # 3. 둘 다 실패
                lines.append(f"  ⚠️ *본문 파싱 실패 + 캐시 데이터 없음*")
                lines.append(f"  → DART 링크 직접 확인 필수")

        lines.append(f"  [DART 링크]({d['url']})")
        lines.append("")

    if len(new_items) > 10:
        lines.append(f"... 외 {len(new_items) - 10}건")

    msg = "\n".join(lines)
    ok, info = send_telegram(bot_token, chat_id, msg)
    if ok:
        for d in new_items:
            alerted["items"][d["rcept_no"]] = today_str
        alerted["last_sent"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        alerted_path.write_text(json.dumps(alerted, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ telegram sent: {len(new_items)} new earnings disclosures")
        names = [d["name"] for d in new_items][:8]
        log_alert("earnings", "관심 종목 실적 공시", f"{len(new_items)}건 — {', '.join(names)}")
    else:
        print(f"  ❌ telegram failed: {info}")


def main():
    print(f"=== Earnings Calendar — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    if not DART_KEY:
        print("  DART_API_KEY not set — skipping")
        return

    today = datetime.now(KST)
    end_de = today.strftime("%Y%m%d")
    # 잠정실적 surprise 알림 — 최근 7일 (시의성)
    bgn_de = (today - timedelta(days=7)).strftime("%Y%m%d")
    # 정기보고서 캘린더 — 최근 45일 (분기 실적 시즌 전체 cover)
    cal_bgn = (today - timedelta(days=45)).strftime("%Y%m%d")

    # 정기공시(A) = 분기/반기/사업보고서 — 45일, 유형 필터로 페이지 수 절감
    print(f"  fetching periodic reports {cal_bgn} ~ {end_de} (정기공시)...")
    periodic_items = fetch_dart_disclosures(cal_bgn, end_de, max_pages=100, pblntf_ty="A")
    print(f"  periodic disclosures: {len(periodic_items)}")

    # 최근 7일 전체 공시 — 잠정실적/손익구조 변경 catch
    print(f"  fetching recent disclosures {bgn_de} ~ {end_de}...")
    recent_items = fetch_dart_disclosures(bgn_de, end_de, max_pages=20)
    print(f"  recent disclosures: {len(recent_items)}")

    # 전체 실적 관련 공시 추출 — frontend calendar용 (정기보고서 45일 + 잠정실적 7일)
    earnings_periodic = parse_earnings_disclosures(periodic_items, surprise_only=False)
    earnings_recent = parse_earnings_disclosures(recent_items, surprise_only=False)
    seen = set()
    all_earnings = []
    for d in earnings_periodic + earnings_recent:
        if d["rcept_no"] in seen:
            continue
        seen.add(d["rcept_no"])
        all_earnings.append(d)
    all_earnings.sort(key=lambda x: x.get("rcept_dt", ""), reverse=True)
    print(f"  all earnings disclosures (frontend): {len(all_earnings)}")

    # 저장 (모든 실적 공시, frontend에서 필터링)
    out_path = Path("data/earnings_calendar.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "period": {"from": cal_bgn, "to": end_de},
        "disclosures": all_earnings,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved earnings_calendar.json ({len(all_earnings)} items)")

    # Telegram 알림 — surprise만 (잠정실적 한정), 관심 + strict만
    surprise_earnings = parse_earnings_disclosures(recent_items, surprise_only=True)
    print(f"  surprise disclosures (잠정실적): {len(surprise_earnings)}")
    target_codes = collect_target_codes()
    print(f"  target stocks (관심 + strict): {len(target_codes)}")
    my_disclosures = [d for d in surprise_earnings if d["code"] in target_codes]
    print(f"  matching surprise disclosures: {len(my_disclosures)}")

    # DART 재무 cache 로드 (fallback trend용)
    financials_cache = load_dart_financials()
    print(f"  loaded {len(financials_cache)} financial records (cache)")

    notify_earnings(my_disclosures, financials_cache)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
