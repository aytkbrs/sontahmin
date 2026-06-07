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
@st.cache_data(ttl=60)
def load_data():
    if not DATA_FILE.exists():
        return None
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)

col_refresh, _ = st.columns([1, 5])
if col_refresh.button("↺ Yenile"):
    load_data.clear()

data = load_data()

# ── header ─────────────────────────────────────────────────────────────────
st.title("⚽ iddaa Günlük Kupon")

if data is None:
    st.error("Veri henüz yüklenmedi. GitHub Actions ilk çalıştırma bekleniyor.")
    st.stop()

date_str = data.get("date", "?")
generated_at = data.get("generated_at", "?")
last_checked = data.get("last_checked", generated_at)
total_events = data.get("total_events", 0)
events_with_stats = data.get("events_with_stats", 0)
coupons = data.get("coupons", [])

try:
    dt = datetime.datetime.fromisoformat(generated_at)
    generated_label = dt.strftime("%d %B %Y  %H:%M")
except Exception:
    generated_label = generated_at

try:
    dt_checked = datetime.datetime.fromisoformat(last_checked)
    checked_label = dt_checked.strftime("%H:%M")
except Exception:
    checked_label = last_checked

accumulation = data.get("accumulation", {})
total_runs = accumulation.get("total_prematch_runs", 0)
total_labels = accumulation.get("total_result_labels", 0)
total_training = accumulation.get("total_training_rows", 0)

st.markdown(f"### 📅 {date_str}")
st.caption(
    f"Kupon üretildi: **{generated_label}** · "
    f"Son kontrol: **{checked_label}** · "
    f"{total_events} maç · "
    f"{events_with_stats} form verisi"
)

pinnacle_covered = accumulation.get("pinnacle_covered_today", 0)
tracked_teams = accumulation.get("tracked_teams", 0)
team_match_history = accumulation.get("team_match_history", 0)

col_a, col_b, col_c, col_d, col_e = st.columns(5)
col_a.metric("Toplam Gün", total_runs, help="Birikmüş prematch bülteni sayısı")
col_b.metric("Sonuç Etiketi", total_labels, help="Tamamlanmış ve etiketlenmiş maç sayısı")
col_c.metric("Takım Profili", tracked_teams, help=f"DB'de takip edilen takım sayısı ({team_match_history} toplam maç)")
col_d.metric("Training Satırı", total_training, help="Modeli kalibre etmek için kullanılabilir veri")
col_e.metric("Pinnacle Kapsama", pinnacle_covered, help="Bugün Pinnacle oranı bulunan maç sayısı")

if not coupons:
    st.warning("Bugün için uygun kupon bulunamadı.")
    st.stop()

st.divider()

# ── model info ─────────────────────────────────────────────────────────────
with st.expander("ℹ️ Model nasıl çalışıyor? (Zamanla öğrenir)"):
    st.markdown("""
Her maç için **Dixon-Coles Poisson modeli** kullanılır:

1. **Form verisi çekilir** — her takımın son 6 maçı (gol, form) — iddaa istatistik API'si
2. **Beklenen gol hesaplanır** — ev/deplasman takımının gücüne göre (Bayesian blend)
3. **Olasılık hesaplanır** — 1/X/2, ÜST/ALT, KG (9×9 skor matrisi, DC düzeltmesi)
4. **Oran kayması (drift) hesaplanır** — aynı maç bir önceki günkü oranla karşılaştırılır; piyasa bizimle aynı yönde mi hareket ediyor?
5. **Edge bulunur** — Poisson olasılığı vs. bookmaker implied probability
6. **3 ayak seçilir** — en yüksek combined EV + drift onayı, farklı müsabakalar tercih edilir

**Zamanla nasıl güçlenir:**
- Her gün DB'ye yeni bir bülten snapshot'ı eklenir
- Tamamlanan maçlar otomatik etiketlenir (1/X/2, ÜST/ALT, KG)
- Birikmiş veriyle model kalibrasyonu yapılabilir
- Oran kayması sinyali ancak tarihsel veri biriktikçe güçlenir

*Drift > 0 → piyasa da bizimle aynı yönde hareket ediyor (güçlü sinyal)*
*Drift < 0 → piyasa karşı yönde hareket ediyor (contrarian)*
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

            drift = leg.get("drift", 0.0)
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**Ayak {i}** &nbsp; `{match_time}` &nbsp; {home} — {away}")
                drift_tag = ""
                if drift > 0.01:
                    drift_tag = f" &nbsp; 📈 `drift {drift:+.3f}`"
                elif drift < -0.01:
                    drift_tag = f" &nbsp; 📉 `drift {drift:+.3f}`"
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp; {outcome_label} &nbsp; **@ {odd:.2f}** &nbsp; *(fair: {fair_prob:.1%})*{drift_tag}")
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
    "Saatte 1 kez güncellenir · Kapalı marketler otomatik filtredir"
)
