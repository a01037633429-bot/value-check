# -*- coding: utf-8 -*-
"""밸류 체크 자동 계산 (역DCF-lite)
GitHub Actions에서 하루 2회 실행 → _automation/value-check.json 생성.
클라우드 루틴이 이 파일을 읽어 리서치 데스크(research/value-check)에 올린다.
원칙: 숫자 지어내기 금지(못 구하면 '확인 필요'), 계산은 코드로만, 매수/매도 추천 금지.
"""
import json, sys, datetime
from zoneinfo import ZoneInfo

import yfinance as yf

WACC = 0.10
TERM_G = 0.025

# (야후 티커, 표시 이름)
TICKERS = [
    ("NVDA", "엔비디아"), ("AMD", "AMD"), ("AVGO", "브로드컴"), ("TSM", "TSMC"),
    ("INTC", "인텔"), ("ASML", "ASML"), ("MU", "마이크론"), ("AMKR", "앰코"),
    ("3711.TW", "ASE(대만)"), ("BESI.AS", "BESI"), ("0522.HK", "ASMPT"), ("KLIC", "K&S"),
    ("VRT", "버티브"), ("MOD", "Modine"), ("NVT", "nVent"), ("ECL", "Ecolab"),
    ("SPXC", "SPX테크"), ("ETN", "이튼"), ("GEV", "GE버노바"), ("PWR", "Quanta"),
    ("ABB", "ABB"), ("CEG", "컨스텔레이션"), ("VST", "비스트라"), ("TLN", "Talen"),
    ("D", "도미니언"), ("SMR", "NuScale"), ("OKLO", "Oklo"), ("2802.T", "아지노모토"),
    ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"), ("066570.KS", "LG전자"),
    ("042700.KS", "한미반도체"), ("067310.KQ", "하나마이크론"), ("033640.KQ", "네패스"),
    ("131970.KQ", "두산테스나"), ("083450.KQ", "GST"), ("036200.KQ", "유니셈"),
    ("053080.KQ", "케이엔솔"), ("267260.KS", "HD현대일렉트릭"), ("298040.KS", "효성중공업"),
    ("010120.KS", "LS일렉트릭"), ("222800.KQ", "심텍"), ("353200.KQ", "대덕전자"),
    ("039030.KQ", "이오테크닉스"),
]


def pv(fcf0, g):
    s = sum(fcf0 * (1 + g) ** t / (1 + WACC) ** t for t in range(1, 11))
    tv = fcf0 * (1 + g) ** 10 * (1 + TERM_G) / ((WACC - TERM_G) * (1 + WACC) ** 10)
    return s + tv


def solve_g(cap, fcf0):
    lo, hi = -0.5, 3.0
    if pv(fcf0, lo) > cap:
        return lo
    if pv(fcf0, hi) < cap:
        return hi
    for _ in range(200):
        mid = (lo + hi) / 2
        if pv(fcf0, mid) < cap:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def analyze(symbol, name):
    try:
        tk = yf.Ticker(symbol)
        cap = None
        try:
            cap = tk.fast_info["market_cap"]
        except Exception:
            cap = (tk.info or {}).get("marketCap")
        cf = tk.cashflow
        if cap is None or cf is None or cf.empty or "Free Cash Flow" not in cf.index:
            return {"name": name, "status": "no_data"}
        # 시총 통화(주가)와 재무제표 통화가 다르면 환산 (예: TSM 시총 USD, 장부 TWD)
        try:
            price_cur = tk.fast_info["currency"]
            fin_cur = (tk.info or {}).get("financialCurrency") or price_cur
            if price_cur and fin_cur and price_cur != fin_cur:
                fx = yf.Ticker(f"{price_cur}{fin_cur}=X").fast_info["last_price"]
                cap = float(cap) * float(fx)
        except Exception:
            return {"name": name, "status": "no_data"}
        row = cf.loc["Free Cash Flow"].dropna()
        if row.empty:
            return {"name": name, "status": "no_data"}
        # 열은 최신순 정렬
        row = row.sort_index(ascending=False)
        fcf_latest = float(row.iloc[0])
        year_latest = row.index[0].year
        if fcf_latest <= 0:
            return {"name": name, "status": "neg_fcf"}
        req = solve_g(float(cap), fcf_latest)
        # 과거 성장률: 가장 오래된 양수 연도 → 최신
        past = None
        old = [(d.year, float(v)) for d, v in row.items() if float(v) > 0 and d.year < year_latest]
        if old:
            y0, v0 = old[-1]
            yrs = year_latest - y0
            if yrs >= 2:
                past = (fcf_latest / v0) ** (1 / yrs) - 1
        return {"name": name, "status": "ok", "req": req, "past": past}
    except Exception as e:
        return {"name": name, "status": "error", "err": str(e)[:80]}


def main():
    now = datetime.datetime.now(ZoneInfo("Asia/Seoul"))
    half = "오전" if now.hour < 12 else "오후"
    stamp = now.strftime("%m/%d")

    results = [analyze(s, n) for s, n in TICKERS]

    easy, hot, unknown = [], [], []
    for r in results:
        if r["status"] == "ok" and r["past"] is not None:
            gap = r["past"] - r["req"]
            (easy if gap > 0 else hot).append((gap, r))
        elif r["status"] == "ok":
            unknown.append((r["name"], "과거 현금흐름 비교 불가"))
        elif r["status"] == "neg_fcf":
            unknown.append((r["name"], "현금흐름 마이너스 — 역DCF 불가"))
        else:
            unknown.append((r["name"], "데이터 못 구함"))
    easy.sort(key=lambda x: -x[0])
    hot.sort(key=lambda x: -x[0])

    n_ok = len(easy) + len(hot)
    lines = [
        "## 🥄 이 카드 읽는 법 (10초)",
        "주가에는 '앞으로 이만큼 잘할 거야'라는 기대가 미리 들어있습니다. 중고차 가격에 '무사고 차량' 전제가 깔린 것처럼요. 그 기대(가격에 반영된 성장 요구)와 실제 성적(과거 현금창출 성장)을 비교합니다.",
        f"## 😌 기대가 소박하게 측정된 종목 — {n_ok}종 중 {len(easy)}종",
        "| 종목 | 시장의 기대 | 실제 성적 | 판정 |",
        "|---|---|---|---|",
    ]
    for gap, r in easy:
        lines.append(f"| {r['name']} | 매년 {r['req']*100:+.0f}% 요구 | 매년 {r['past']*100:+.0f}% 해옴 | 😌 성적보다 기대가 낮음 |")
    for gap, r in easy[:3]:
        lines.append(f"- [해석] {r['name']}: 최근 성적(연 {r['past']*100:+.0f}%)이 요구치(연 {r['req']*100:+.0f}%)보다 높음 — 다만 과거 속도가 유지된다는 보장은 없음")
    lines.append(f"## 🔥 기대가 큰 편으로 측정된 {len(hot)}종")
    lines.append(" · ".join(r["name"] for _, r in hot) if hot else "없음")
    if hot:
        _, top = hot[0]
        lines.append(f"- {top['name']}: 시장은 매년 {top['req']*100:+.0f}%를 요구하는데 실제는 {top['past']*100:+.0f}%였음 — 기대가 가격에 많이 실렸다는 뜻")
    if unknown:
        lines.append(f"확인 필요 {len(unknown)}종: " + " · ".join(f"{n}({why})" for n, why in unknown[:12]))
    lines += [
        "## ⚠️ 절대 오해 금지",
        "😌 = 사라는 뜻 아님 (성적이 꺾이면 기대가 낮아도 손해 — 가치함정 가능)",
        "🔥 = 팔라는 뜻 아님 (성장이 기대를 정말 따라잡을 수도 있음)",
        "계산: 역DCF-lite(WACC 10%) · 출처 야후파이낸스 · 근사치 — 판단용은 원본 재확인",
        "하루 2회 자동 갱신 (GitHub 서버 — 컴퓨터 꺼져도 동작) · 참고용 리서치",
    ]

    doc = {
        "name": "💰 밸류 체크 스코어보드",
        "updatedAt": f"{stamp} {half}",
        "sortKey": "999999999998",
        "cards": [{"mode": "가격", "date": now.strftime("%m/%d %H:%M"), "lines": lines}],
    }
    out = sys.argv[1] if len(sys.argv) > 1 else "_automation/value-check.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"OK {stamp} {half}: 😌{len(easy)} 🔥{len(hot)} ❓{len(unknown)} -> {out}")


if __name__ == "__main__":
    main()
