import json
import datetime
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="iddaa Kupon",
    page_icon="⚽",
    layout="centered",
)

DATA_FILE = Path(__file__).parent / "output" / "coupons_today.json"

# ── load data ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_data():
    if not DATA_FILE.exists():
        return None
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)

data = load_data()

# ── header ─────────────────────────────────────────────────────────────────
st.title("⚽ iddaa Günlük Kupon")

if data is None:
    st.error("Veri henüz yüklenmedi. GitHub Actions ilk çalıştırma bekleniyor.")
    st.stop()

date_str = data.get("date", "?")
generated_at = data.get("generated_at", "?")
total_events = data.get("total_events", 0)
events_with_stats = data.get("events_with_stats", 0)
coupons = data.get("coupons", [])

try:
    dt = datetime.datetime.fromisoformat(generated_at)
    generated_label = dt.strftime("%d %B %Y  %H:%M")
except Exception:
    generated_label = generated_at

st.markdown(f"### 📅 {date_str}")
st.caption(
    f"Son güncelleme: **{generated_label}** · "
    f"{total_events} maç bülteni · "
    f"{events_with_stats} maç form verisi"
)

if not coupons:
    st.warning("Bugün için uygun kupon bulunamadı.")
    st.stop()

st.divider()

# ── model info ─────────────────────────────────────────────────────────────
with st.expander("ℹ️ Model nasıl çalışıyor?"):
    st.markdown("""
Her maç için **Dixon-Coles Poisson modeli** kullanılır:

1. **Form verisi çekilir** — her takımın son 6 maçı (gol, form)
2. **Beklenen gol hesaplanır** — ev/deplasman takımının gücüne göre
3. **Olasılık hesaplanır** — 1/X/2, ÜST/ALT, KG (9×9 skor matrisi)
4. **Edge bulunur** — model olasılığı vs. bookmaker implied probability
5. **3 ayak seçilir** — en yüksek combined EV, farklı müsabakalar tercih edilir

EV (Beklenen Değer): 0.50 = kupon değeri %50 pozitif beklenti
*Küçük örneklem (6 maç/takım) = yüksek varyans. Zamanla kalibrasyon gelişir.*
    """)

st.divider()

# ── coupons ────────────────────────────────────────────────────────────────
OUTCOME_TR = {
    "home": "1 — Ev Sahibi Kazanır",
    "draw": "X — Beraberlik",
    "away": "2 — Deplasman Kazanır",
    "over": "ÜST 2.5 Gol",
    "under": "ALT 2.5 Gol",
    "btts_yes": "KG VAR",
    "btts_no": "KG YOK",
}

for coupon in coupons:
    rank = coupon["rank"]
    combined_odd = coupon["combined_odd"]
    win_prob = coupon["win_prob"]
    ev = coupon["expected_value"]
    legs = coupon["legs"]

    header = (
        f"🎯 **Kupon #{rank}** — "
        f"Oran: `{combined_odd:.2f}` · "
        f"Kazanma: `{win_prob:.1%}` · "
        f"EV: `{ev:+.2f}`"
    )

    with st.container(border=True):
        st.markdown(header)
        st.markdown("")

        for i, leg in enumerate(legs, 1):
            home = leg["home"]
            away = leg["away"]
            outcome_key = leg["outcome_key"]
            outcome_label = OUTCOME_TR.get(outcome_key, leg.get("outcome_label", outcome_key))
            odd = leg["odd"]
            fair_prob = leg["fair_prob"]
            match_time = leg["match_time"]

            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**Ayak {i}** &nbsp; `{match_time}` &nbsp; {home} — {away}")
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp; {outcome_label} &nbsp; **@ {odd:.2f}** &nbsp; *(fair: {fair_prob:.1%})*")
            with col2:
                ev_leg = round(fair_prob * odd - 1, 3)
                color = "green" if ev_leg > 0 else "red"
                st.markdown(f"<p style='color:{color};font-weight:bold;text-align:right'>EV {ev_leg:+.2f}</p>", unsafe_allow_html=True)

            if i < len(legs):
                st.markdown("---")

    st.markdown("")

# ── footer ─────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Veri kaynağı: iddaa.com · "
    "Model: Dixon-Coles Poisson · "
    "Günde 3 kez güncellenir (08:00 / 12:00 / 17:00 Türkiye saati)"
)
