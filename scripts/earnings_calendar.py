#!/usr/bin/env python3
"""실적 캘린더 — DART 잠정실적/분기보고서/사업보고서 공시 모니터링.

매일 1회 (장 마감 후) 실행.
- 최근 7일 + 향후 7일 공시 catch
- 잠정실적 공시: surprise 감지 (직전 분기 대비)
- 보유/관심 종목의 실적 발표 D-7 알림

출력: data/earnings_calendar.json
Telegram: 관심 종목 실적 발표 임박 + 잠정실적 surprise
"""
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EarningsCalendar/1.0)"}

DART_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"

sys.path.insert(0, str(Path(__file__).parent))
from screener import send_telegram, DEDUP_RESET_DAYS


def fetch_dart_disclosures(bgn_de, end_de, keyword=None, max_pages=5):
    """DART 공시검색 (특정 기간)."""
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


def notify_earnings(my_disclosures):
    """관심 종목 잠정실적/실적 공시 → Telegram 알림."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("  TELEGRAM_BOT_TOKEN/CHAT_ID env not set")
        return

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
    for d in new_items[:15]:
        emoji = "🚨" if "잠정" in d["report_nm"] else "📋"
        lines.append(f"{emoji} *{d['name']}* (`{d['code']}`)")
        lines.append(f"  {d['report_nm']}")
        lines.append(f"  접수: {d['rcept_dt']}")
        lines.append(f"  [DART 링크]({d['url']})")
        lines.append("")
    if len(new_items) > 15:
        lines.append(f"... 외 {len(new_items) - 15}건")

    msg = "\n".join(lines)
    ok, info = send_telegram(bot_token, chat_id, msg)
    if ok:
        for d in new_items:
            alerted["items"][d["rcept_no"]] = today_str
        alerted["last_sent"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        alerted_path.write_text(json.dumps(alerted, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ telegram sent: {len(new_items)} new earnings disclosures")
    else:
        print(f"  ❌ telegram failed: {info}")


def main():
    print(f"=== Earnings Calendar — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    if not DART_KEY:
        print("  DART_API_KEY not set — skipping")
        return

    today = datetime.now(KST)
    bgn_de = (today - timedelta(days=7)).strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")

    print(f"  fetching disclosures {bgn_de} ~ {end_de}...")
    items = fetch_dart_disclosures(bgn_de, end_de, max_pages=10)
    print(f"  total disclosures: {len(items)}")

    # 전체 실적 관련 공시 추출 — frontend calendar용 (정기보고서 포함)
    all_earnings = parse_earnings_disclosures(items, surprise_only=False)
    print(f"  all earnings disclosures (frontend): {len(all_earnings)}")

    # 저장 (모든 실적 공시, frontend에서 필터링)
    out_path = Path("data/earnings_calendar.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "period": {"from": bgn_de, "to": end_de},
        "disclosures": all_earnings,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved earnings_calendar.json ({len(all_earnings)} items)")

    # Telegram 알림 — surprise만 (잠정실적 한정), 관심 + strict만
    surprise_earnings = parse_earnings_disclosures(items, surprise_only=True)
    print(f"  surprise disclosures (잠정실적): {len(surprise_earnings)}")
    target_codes = collect_target_codes()
    print(f"  target stocks (관심 + strict): {len(target_codes)}")
    my_disclosures = [d for d in surprise_earnings if d["code"] in target_codes]
    print(f"  matching surprise disclosures: {len(my_disclosures)}")

    notify_earnings(my_disclosures)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
