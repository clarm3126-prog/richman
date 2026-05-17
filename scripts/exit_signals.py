#!/usr/bin/env python3
"""매도 / 손절 시그널 검출 — watchlist + 최근 통과 미너비니/모멘텀 종목.

각 종목에 대해 매도 신호 평가:
- 🛑 위험 (즉시 매도 검토): MA50 break with volume, 신고가 후 분배일 5+
- ⚠️ 주의: MA21 break (단기 trail), 거래량 동반 큰 음봉
- ℹ️ 모니터: setup 약화, 신고가 후 거래량 감소

출력: data/exit_signals.json + Telegram 알림 (신규 위험/주의 신호만)
"""
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pytz

KST = pytz.timezone("Asia/Seoul")

sys.path.insert(0, str(Path(__file__).parent))
from screener import (
    fetch_all_stock_history,
    sma, send_telegram, DEDUP_RESET_DAYS, log_alert,
)


# ================================
# 매도 시그널 검출
# ================================

def evaluate_exit_signals(code, history, entry_price=None):
    """단일 종목 매도 시그널 평가.
    entry_price 제공 시 손절선/수익 trailing 추가 평가.
    Returns dict with severity + signals list.
    """
    if len(history) < 60:
        return {"eligible": False, "reason": f"history {len(history)} < 60"}

    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    volumes = [h["volume"] for h in history]
    opens = [h["open"] for h in history]

    cur_close = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else cur_close
    today_change = (cur_close - prev_close) / prev_close * 100 if prev_close > 0 else 0

    ma21 = sma(closes, 21)
    ma50 = sma(closes, 50)
    ma150 = sma(closes, 150)
    ma200 = sma(closes, 200)

    # 어제까지의 평균 (cross 감지용)
    closes_yesterday = closes[:-1]
    ma21_y = sma(closes_yesterday, 21) if len(closes_yesterday) >= 21 else None
    ma50_y = sma(closes_yesterday, 50) if len(closes_yesterday) >= 50 else None
    ma150_y = sma(closes_yesterday, 150) if len(closes_yesterday) >= 150 else None

    # 20일 평균 거래량
    vol_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    today_vol = volumes[-1] if volumes else 0
    vol_ratio = today_vol / vol_20d if vol_20d > 0 else 0

    # 60일 신고가 (최근 5일 내 신고가 갱신했는지)
    high_60d_excl_recent = max(highs[-60:-5]) if len(highs) >= 60 else 0
    recent_5d_high = max(highs[-5:])
    made_new_high_recently = recent_5d_high > high_60d_excl_recent

    signals = []

    # === 🛑 CRITICAL (즉시 매도 검토) ===

    # 1. MA50 break with volume (강한 종목이 큰 거래량으로 MA50 하향 돌파)
    if ma50_y and ma50:
        if prev_close >= ma50_y and cur_close < ma50 and vol_ratio >= 1.5 and ma200 and prev_close > ma200:
            signals.append({
                "severity": "critical",
                "type": "ma50_break_volume",
                "label": f"MA50 거래량 동반 하락 ({vol_ratio:.1f}x)",
                "detail": f"종가 {cur_close:,} < MA50 {ma50:,.0f}",
            })

    # 2. 큰 음봉 (-5% 이상 + 거래량 1.5x 이상) - 분배일
    if today_change <= -5 and vol_ratio >= 1.5:
        signals.append({
            "severity": "critical",
            "type": "big_red_distribution",
            "label": f"큰 음봉 ({today_change:.1f}%) + 거래량 {vol_ratio:.1f}x",
            "detail": "기관 분배 의심",
        })

    # 3. MA200 이탈 (가장 강한 추세 지지선 깨짐)
    if ma200 and ma50_y and ma200 < ma50_y:  # 원래는 상승 종목
        if cur_close < ma200 and prev_close >= ma200:
            signals.append({
                "severity": "critical",
                "type": "ma200_break",
                "label": f"MA200 이탈",
                "detail": f"종가 {cur_close:,} < MA200 {ma200:,.0f}",
            })

    # === ⚠️ WARNING (주의) ===

    # 4. MA21 break (단기 trail. +20%+ 수익 종목용)
    if ma21_y and ma21 and prev_close >= ma21_y and cur_close < ma21:
        signals.append({
            "severity": "warning",
            "type": "ma21_break",
            "label": f"MA21 이탈 (단기 약화)",
            "detail": f"종가 {cur_close:,} < MA21 {ma21:,.0f}",
        })

    # 5. 분배일 카운트 (최근 20일 중 음봉 + 평균 이상 거래량)
    # O'Neil 정의: -0.2% 이상 하락 + 평균 거래량 초과
    distribution_days = 0
    if len(closes) >= 21 and len(volumes) >= 21:
        for i in range(-20, 0):
            if i - 1 < -len(closes):
                continue
            day_change = (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if closes[i - 1] > 0 else 0
            # 하락 폭 -0.2% 이상 AND 거래량이 20일 평균 초과
            if day_change <= -0.2 and vol_20d > 0 and volumes[i] > vol_20d:
                distribution_days += 1
        if distribution_days >= 5:
            signals.append({
                "severity": "warning",
                "type": "distribution_days",
                "label": f"20일 분배일 {distribution_days}개",
                "detail": "기관 매도 누적",
            })

    # 6. 신고가 후 거래량 감소 (드라이업)
    if made_new_high_recently and len(volumes) >= 20:
        recent_5d_avg_vol = sum(volumes[-5:]) / 5
        prev_15d_avg_vol = sum(volumes[-20:-5]) / 15 if len(volumes) >= 20 else 0
        if prev_15d_avg_vol > 0 and recent_5d_avg_vol < prev_15d_avg_vol * 0.6:
            signals.append({
                "severity": "warning",
                "type": "post_breakout_dryup",
                "label": "신고가 후 거래량 감소",
                "detail": f"5일 평균 {recent_5d_avg_vol:,.0f} vs 15일 평균 {prev_15d_avg_vol:,.0f}",
            })

    # 7. Failed breakout (3일 내 신고가 후 다시 박스 안으로)
    if len(closes) >= 3 and len(highs) >= 23:
        prev_box_high = max(highs[-23:-3])
        broke_out = max(highs[-3:]) > prev_box_high * 1.02
        back_inside = cur_close < prev_box_high
        if broke_out and back_inside:
            signals.append({
                "severity": "warning",
                "type": "failed_breakout",
                "label": "Failed Breakout (가짜 돌파)",
                "detail": f"박스 상단 {prev_box_high:,.0f} 돌파 후 안으로 복귀",
            })

    # === ℹ️ MONITOR (모니터링) ===

    # 8. Death cross 임박 (MA50이 MA150 하향 접근)
    if ma50 and ma150 and ma50 > ma150:
        gap_pct = (ma50 - ma150) / ma150 * 100
        if gap_pct < 1:
            signals.append({
                "severity": "info",
                "type": "death_cross_imminent",
                "label": f"MA50/150 데스크로스 임박 (gap {gap_pct:.2f}%)",
                "detail": "추세 약화",
            })

    # === 💰 매입가 기반 시그널 (entry_price 제공 시) ===
    if entry_price and entry_price > 0:
        return_pct = (cur_close - entry_price) / entry_price * 100

        # 9. 손절선 -7% (Minervini의 매도 룰)
        if return_pct <= -7:
            signals.append({
                "severity": "critical",
                "type": "stop_loss_7pct",
                "label": f"손절선 도달 ({return_pct:.1f}%)",
                "detail": f"매입가 {entry_price:,}원 → 현재 {cur_close:,}원 (Minervini -7% 룰)",
            })

        # 10. 수익 +20% 후 MA21 trail
        if return_pct >= 20 and ma21 and ma21_y:
            if prev_close >= ma21_y and cur_close < ma21:
                signals.append({
                    "severity": "warning",
                    "type": "profit_trail_ma21",
                    "label": f"수익 trail (MA21 이탈, +{return_pct:.0f}%)",
                    "detail": f"매입 {entry_price:,} → 현재 {cur_close:,} · 이익 보존 검토",
                })

        # 11. 수익 +25% 후 MA50 trail (큰 수익은 더 긴 trail)
        if return_pct >= 50 and ma50 and ma50_y:
            if prev_close >= ma50_y and cur_close < ma50:
                signals.append({
                    "severity": "warning",
                    "type": "profit_trail_ma50",
                    "label": f"큰 수익 trail (MA50 이탈, +{return_pct:.0f}%)",
                    "detail": f"매입 {entry_price:,} → 현재 {cur_close:,}",
                })

    # 종합 severity
    if not signals:
        overall = "ok"
    elif any(s["severity"] == "critical" for s in signals):
        overall = "critical"
    elif any(s["severity"] == "warning" for s in signals):
        overall = "warning"
    else:
        overall = "info"

    result = {
        "eligible": True,
        "overall": overall,
        "signals": signals,
        "current_price": cur_close,
        "today_change": round(today_change, 2),
        "ma21": round(ma21) if ma21 else None,
        "ma50": round(ma50) if ma50 else None,
        "ma150": round(ma150) if ma150 else None,
        "ma200": round(ma200) if ma200 else None,
        "distribution_days_20d": distribution_days,
    }
    if entry_price and entry_price > 0:
        result["entry_price"] = entry_price
        result["return_pct"] = round((cur_close - entry_price) / entry_price * 100, 2)
    return result


def collect_target_codes():
    """평가 대상 종목 수집:
    1. data/watchlist.json - 보유(owned=true) + 관심
    2. data/screener_results.json - 최근 미너비니 통과 (top 30) [모니터링용, 알림 X]
    3. data/momentum_results.json - 최근 모멘텀 통과 (top 30) [모니터링용, 알림 X]

    각 항목에 owned/entry_price 정보 보존 (Telegram 발송 대상 결정용).
    """
    targets = {}  # {code: {name, source, owned, entry_price, entry_date}}

    # 1. Watchlist (owned 정보 포함)
    wl_path = Path("data/watchlist.json")
    if wl_path.exists():
        try:
            wl = json.loads(wl_path.read_text(encoding="utf-8"))
            for item in wl.get("watchlist", []):
                code = str(item.get("code", "")).zfill(6)
                if not code:
                    continue
                if code not in targets:
                    targets[code] = {
                        "name": item.get("name", code),
                        "source": [],
                        "owned": False,
                        "entry_price": None,
                        "entry_date": None,
                    }
                targets[code]["source"].append("watchlist")
                if item.get("owned"):
                    targets[code]["owned"] = True
                    targets[code]["entry_price"] = item.get("entry_price")
                    targets[code]["entry_date"] = item.get("entry_date")
        except Exception:
            pass

    # 2. Minervini results — 모니터링용 (Telegram 알림 X)
    sr_path = Path("data/screener_results.json")
    if sr_path.exists():
        try:
            sr = json.loads(sr_path.read_text(encoding="utf-8"))
            for r in (sr.get("results") or [])[:30]:
                code = r.get("code")
                if code:
                    if code not in targets:
                        targets[code] = {
                            "name": r.get("name", code),
                            "source": [],
                            "owned": False,
                            "entry_price": None,
                            "entry_date": None,
                        }
                    targets[code]["source"].append("minervini")
        except Exception:
            pass

    # 3. Momentum results — 모니터링용 (Telegram 알림 X)
    mr_path = Path("data/momentum_results.json")
    if mr_path.exists():
        try:
            mr = json.loads(mr_path.read_text(encoding="utf-8"))
            for r in (mr.get("results") or [])[:30]:
                code = r.get("code")
                if code:
                    if code not in targets:
                        targets[code] = {
                            "name": r.get("name", code),
                            "source": [],
                            "owned": False,
                            "entry_price": None,
                            "entry_date": None,
                        }
                    targets[code]["source"].append("momentum")
        except Exception:
            pass

    return targets


# ================================
# Telegram 알림
# ================================

def notify_exit_signals(results):
    """신규 critical/warning 시그널만 Telegram 발송. dedup."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("  TELEGRAM_BOT_TOKEN/CHAT_ID env not set")
        return

    alerted_path = Path("data/exit_alerted.json")
    alerted = {"signals": {}}  # {f"{code}_{type}": "YYYY-MM-DD"}
    if alerted_path.exists():
        try:
            raw = json.loads(alerted_path.read_text(encoding="utf-8"))
            alerted["signals"] = raw.get("signals", {})
        except Exception:
            pass
    today = datetime.now(KST)
    cutoff = today.timestamp() - DEDUP_RESET_DAYS * 86400
    active = set()
    for key, date_str in list(alerted["signals"].items()):
        try:
            ts = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
            if ts >= cutoff:
                active.add(key)
            else:
                del alerted["signals"][key]
        except Exception:
            pass

    new_critical = []
    new_warning = []
    for r in results:
        # ✅ Telegram은 보유 종목(owned=True)에만 발송
        if not r.get("owned"):
            continue
        code = r["code"]
        for sig in r.get("signals", []):
            if sig["severity"] not in ("critical", "warning"):
                continue
            key = f"{code}_{sig['type']}"
            if key in active:
                continue
            entry = {
                "code": code,
                "name": r["name"],
                "source": r.get("source", []),
                "price": r.get("current_price", 0),
                "change": r.get("today_change", 0),
                "label": sig["label"],
                "detail": sig["detail"],
                "return_pct": r.get("return_pct"),
                "entry_price": r.get("entry_price"),
                "_key": key,
            }
            if sig["severity"] == "critical":
                new_critical.append(entry)
            else:
                new_warning.append(entry)

    if not new_critical and not new_warning:
        print("  no new exit signals for owned stocks (skip telegram)")
        return

    today_str = today.strftime("%Y-%m-%d")
    lines = [f"🛑 *매도 시그널 (보유 종목)* — {today_str}\n"]

    def fmt_one(e):
        sign = "+" if e["change"] > 0 else ""
        ret_line = ""
        if e.get("return_pct") is not None and e.get("entry_price"):
            rsign = "+" if e["return_pct"] > 0 else ""
            ret_emoji = "🟢" if e["return_pct"] >= 0 else "🔴"
            ret_line = f"\n  {ret_emoji} 수익률 *{rsign}{e['return_pct']:.2f}%* (매입 {e['entry_price']:,}원)"
        return (
            f"• *{e['name']}* (`{e['code']}`)\n"
            f"  {e['price']:,}원 ({sign}{e['change']:.2f}%){ret_line}\n"
            f"  ⚡ {e['label']}\n"
            f"  💬 {e['detail']}"
        )

    if new_critical:
        lines.append(f"*🛑 위험 — {len(new_critical)}건*")
        for e in new_critical[:10]:
            lines.append(fmt_one(e))
        if len(new_critical) > 10:
            lines.append(f"... 외 {len(new_critical) - 10}건")
        lines.append("")
    if new_warning:
        lines.append(f"*⚠️ 주의 — {len(new_warning)}건*")
        for e in new_warning[:10]:
            lines.append(fmt_one(e))
        if len(new_warning) > 10:
            lines.append(f"... 외 {len(new_warning) - 10}건")

    msg = "\n".join(lines)
    ok, info = send_telegram(bot_token, chat_id, msg)
    if ok:
        for e in new_critical + new_warning:
            alerted["signals"][e["_key"]] = today_str
        alerted["last_sent"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        alerted_path.write_text(json.dumps(alerted, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ telegram sent: critical {len(new_critical)} new, warning {len(new_warning)} new")
        names = [e["name"] for e in (new_critical + new_warning)][:8]
        summary = f"위험 {len(new_critical)}건, 주의 {len(new_warning)}건 — {', '.join(names)}"
        log_alert("exit", "보유 종목 매도 시그널", summary)
    else:
        print(f"  ❌ telegram failed: {info}")


# ================================
# 메인
# ================================

def main():
    print(f"=== Exit Signals — {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} ===")

    targets = collect_target_codes()
    print(f"  target stocks: {len(targets)} (watchlist + minervini top30 + momentum top30)")
    if not targets:
        print("  no targets — exiting")
        return

    codes = list(targets.keys())
    print(f"\n[Naver] 60일 OHLC 수집...")
    histories = fetch_all_stock_history(codes, days=60)

    print("\n[평가] 매도 시그널...")
    results = []
    owned_count = 0
    for code, target in targets.items():
        history = histories.get(code)
        if not history or len(history) < 60:
            continue
        entry = target.get("entry_price") if target.get("owned") else None
        evaluation = evaluate_exit_signals(code, history, entry_price=entry)
        if not evaluation.get("eligible"):
            continue
        if target.get("owned"):
            owned_count += 1
        results.append({
            "code": code,
            "name": target["name"],
            "source": target["source"],
            "owned": target.get("owned", False),
            "entry_price": target.get("entry_price"),
            "entry_date": target.get("entry_date"),
            **evaluation,
        })
    print(f"  owned stocks evaluated: {owned_count}")

    # severity 순 정렬
    severity_order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    results.sort(key=lambda r: (severity_order.get(r["overall"], 99), r["name"]))

    critical_count = sum(1 for r in results if r["overall"] == "critical")
    warning_count = sum(1 for r in results if r["overall"] == "warning")
    print(f"  evaluated {len(results)} stocks: critical {critical_count}, warning {warning_count}")

    out_path = Path("data/exit_signals.json")
    out_path.write_text(json.dumps({
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "trading_day": datetime.now(KST).strftime("%Y%m%d"),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "results": results,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n✅ Saved exit_signals.json ({len(results)} stocks)")

    print("\n[Telegram] 신규 매도 시그널 알림...")
    notify_exit_signals(results)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
