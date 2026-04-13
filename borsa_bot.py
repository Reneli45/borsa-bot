"""
ULS + FIB — ULTRA OPTIMUM Sinyal Botu (v3.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Orijinal bota eklenen Ultra Optimum katmanları:
  ★ Supertrend(10, 2.0)        → EMA200 yerine dinamik trend filtresi
  ★ RS > BIST100 / SPY filtresi → Göreceli güç kontrolü
  ★ ADX eşiği 15 → 27          → Yatay piyasa gürültüsü azaltıldı
  ★ RSI 45-80 → 52-80          → Daha temiz momentum
  ★ MACD 12/26/9 → 8/17/9      → Daha hızlı momentum tespiti
  ★ Turtle sabit 40 → Adaptif  → Fib≥6: 20bar, Fib<6: 30bar
  ★ Fib eşiği 5/7 → 6/7        → Sadece olgunlaşmış trendler
  ★ Hacim SMA×1.2 → SMA×1.5    → Manipülatif kırılım engeli (zorunlu)
  ★ Stop 2.0 → 1.8×ATR         → Daha iyi R/R
  ★ TP 1.5/3.0/5.0 → 2.0/4.0/7.0
"""

import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import time
import schedule
import threading
import json
import os

# ══════════════════════════════════════════════
#  TELEGRAM AYARLARI
# ══════════════════════════════════════════════
TELEGRAM_TOKEN   = "8644118927:AAHwT1tHdfoEVZ-W8hpCJk9HJJT8iItul14"
TELEGRAM_CHAT_ID = "-1003848631204"
son_update_id    = 0
tarama_kilidi    = threading.Lock()
tarama_aktif     = False

def telegram_gonder(mesaj, chat_id=None):
    if chat_id is None:
        chat_id = TELEGRAM_CHAT_ID
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    veri = {"chat_id": chat_id, "text": mesaj, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=veri, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram hata: {e}")
        return False

def telegram_gonder_butonlu(mesaj, chat_id, ticker, giris, sl, tp1, sinyal_id):
    url      = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    lot      = max(1, int(1000 / giris)) if giris > 0 else 1
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ ONAYLA — Midas'ta Aç", "url": "https://getmidas.com"},
                {"text": "❌ REDDET", "callback_data": f"reddet_{sinyal_id}"}
            ],
            [
                {"text": f"📋 {ticker} | Giriş: {giris} | Lot: ~{lot}",
                 "callback_data": f"bilgi_{sinyal_id}"}
            ]
        ]
    }
    veri = {
        "chat_id": chat_id, "text": mesaj,
        "parse_mode": "HTML", "reply_markup": json.dumps(keyboard)
    }
    try:
        r = requests.post(url, json=veri, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Butonlu mesaj hata: {e}")
        return False

def telegram_mesajlari_al():
    global son_update_id
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": son_update_id + 1, "timeout": 10}, timeout=15)
        if r.status_code == 200:
            return r.json().get("result", [])
    except:
        pass
    return []

def telegram_callback_cevapla(callback_query_id, metin):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    veri = {"callback_query_id": callback_query_id, "text": metin, "show_alert": False}
    try:
        requests.post(url, data=veri, timeout=5)
    except:
        pass

# ══════════════════════════════════════════════
#  VERİTABANI
# ══════════════════════════════════════════════
DB_FILE = "borsa_db.json"

def db_oku():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"kullanicilar": {}, "sinyaller": [], "acik_pozisyonlar": {}}

def db_kaydet(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def kullanici_ekle(chat_id, isim):
    db  = db_oku()
    cid = str(chat_id)
    if cid not in db["kullanicilar"]:
        db["kullanicilar"][cid] = {
            "isim": isim, "katilim": datetime.now().strftime("%Y-%m-%d"), "aktif": True
        }
        db_kaydet(db)
        return True
    return False

def aktif_kullanicilar():
    db = db_oku()
    return [cid for cid, u in db["kullanicilar"].items() if u.get("aktif", True)]

def sinyal_kaydet(ticker, piyasa, giris, sl, tp1, tp2, tp3, atr, fib, skor):
    db  = db_oku()
    sid = len(db["sinyaller"]) + 1
    sinyal = {
        "id": sid, "tarih": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ticker": ticker, "piyasa": piyasa,
        "giris": giris, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "atr": atr, "fib": fib, "uls": skor,
        "durum": "ACIK", "sonuc": None, "kar_zarar": None, "kapanma": None
    }
    db["sinyaller"].append(sinyal)
    db["acik_pozisyonlar"][ticker] = sid
    db_kaydet(db)
    return sid

def pozisyon_kapat(ticker, durum, son_fiyat):
    db = db_oku()
    if ticker not in db["acik_pozisyonlar"]:
        return
    sid = db["acik_pozisyonlar"][ticker]
    for s in db["sinyaller"]:
        if s["id"] == sid:
            s["durum"]    = durum
            s["kapanma"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
            kar           = (son_fiyat - s["giris"]) / s["giris"] * 1000
            s["kar_zarar"] = round(kar, 2)
            s["sonuc"]    = "KAR" if kar > 0 else "ZARAR"
            break
    del db["acik_pozisyonlar"][ticker]
    db_kaydet(db)

def pozisyon_kapat_by_id(sinyal_id, durum, son_fiyat):
    db = db_oku()
    for s in db["sinyaller"]:
        if s["id"] == sinyal_id and s["durum"] == "ACIK":
            s["durum"]   = durum
            s["kapanma"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            if son_fiyat > 0:
                kar = (son_fiyat - s["giris"]) / s["giris"] * 1000
                s["kar_zarar"] = round(kar, 2)
                s["sonuc"]     = "KAR" if kar > 0 else "ZARAR"
            else:
                s["kar_zarar"] = 0
                s["sonuc"]     = "REDDEDILDI"
            ticker = s["ticker"]
            if ticker in db["acik_pozisyonlar"]:
                del db["acik_pozisyonlar"][ticker]
            break
    db_kaydet(db)

# ══════════════════════════════════════════════
#  TV UYUMLU HESAPLAMALAR
# ══════════════════════════════════════════════
def tv_ema(seri, periyot):
    return seri.ewm(span=periyot, adjust=False).mean()

def tv_rma(seri, periyot):
    alpha  = 1.0 / periyot
    result = seri.copy().astype(float) * np.nan
    if len(seri) < periyot:
        return result
    result.iloc[periyot - 1] = seri.iloc[:periyot].mean()
    for i in range(periyot, len(seri)):
        result.iloc[i] = alpha * seri.iloc[i] + (1 - alpha) * result.iloc[i - 1]
    return result

def tv_atr(df, periyot=20):
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()
    prev  = close.shift(1)
    tr    = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tv_rma(tr, periyot)

def tv_rsi(close, periyot=14):
    delta = close.diff()
    avg_g = tv_rma(delta.clip(lower=0), periyot)
    avg_l = tv_rma((-delta).clip(lower=0), periyot)
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def tv_dmi(df, periyot=14):
    high   = df["High"].squeeze()
    low    = df["Low"].squeeze()
    close  = df["Close"].squeeze()
    prev_c = close.shift(1)
    tr     = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    up     = high - high.shift(1)
    down   = low.shift(1) - low
    pdm    = up.where((up > down) & (up > 0), 0.0)
    mdm    = down.where((down > up) & (down > 0), 0.0)
    atr_w  = tv_rma(tr, periyot)
    pdi    = 100 * tv_rma(pdm, periyot) / atr_w
    mdi    = 100 * tv_rma(mdm, periyot) / atr_w
    dx     = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pdi, mdi, tv_rma(dx, periyot)

def tv_macd(close, fast=8, slow=17, signal=9):
    """★ Ultra Opt: 8/17/9 (orijinal 12/26/9)"""
    ml = tv_ema(close, fast) - tv_ema(close, slow)
    ms = tv_ema(ml, signal)
    return ml, ms, ml - ms

def tv_supertrend(df, periyot=10, faktor=2.0):
    """
    ★ YENİ: Supertrend hesabı
    Returns: (supertrend serisi, direction serisi)
    direction: +1 = fiyat altında (boğa), -1 = fiyat üstünde (ayı)
    Pine Script convention: stDir < 0 → AL
    """
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()
    atr   = tv_atr(df, periyot)
    hl2   = (high + low) / 2

    upper_band = hl2 + faktor * atr
    lower_band = hl2 - faktor * atr

    st     = pd.Series(np.nan, index=close.index)
    direction = pd.Series(1, index=close.index)  # 1=aşağı(ayı), -1=yukarı(boğa)

    for i in range(1, len(close)):
        # Lower band (destek)
        if lower_band.iloc[i] > lower_band.iloc[i-1] or close.iloc[i-1] < st.iloc[i-1]:
            lb = lower_band.iloc[i]
        else:
            lb = lower_band.iloc[i-1]

        # Upper band (direnç)
        if upper_band.iloc[i] < upper_band.iloc[i-1] or close.iloc[i-1] > st.iloc[i-1]:
            ub = upper_band.iloc[i]
        else:
            ub = upper_band.iloc[i-1]

        # Yön ve seviye
        prev_st  = st.iloc[i-1] if not np.isnan(st.iloc[i-1]) else lb
        prev_dir = direction.iloc[i-1]

        if prev_dir == 1:  # Önceki: ayı (st = upper)
            if close.iloc[i] > ub:
                direction.iloc[i] = -1  # Boğaya döndü
                st.iloc[i]        = lb
            else:
                direction.iloc[i] = 1
                st.iloc[i]        = ub
        else:  # Önceki: boğa (st = lower)
            if close.iloc[i] < lb:
                direction.iloc[i] = 1   # Ayıya döndü
                st.iloc[i]        = ub
            else:
                direction.iloc[i] = -1
                st.iloc[i]        = lb

    return st, direction

def hesapla_rs(close, bench_close, periyot=50):
    """
    ★ YENİ: Göreceli Güç (RS) hesabı
    Hisse/Benchmark oranı, SMA(50) üstünde mi?
    """
    ratio    = close / bench_close.reindex(close.index, method="ffill")
    ratio_ma = ratio.rolling(periyot).mean()
    return ratio, ratio_ma

# ══════════════════════════════════════════════
#  BENCHMARK VERİSİ (bir kez çek, cache'le)
# ══════════════════════════════════════════════
_benchmark_cache = {}

def benchmark_getir(sembol, period="2y"):
    global _benchmark_cache
    simdi = datetime.now()
    if sembol in _benchmark_cache:
        veri, zaman = _benchmark_cache[sembol]
        if (simdi - zaman).seconds < 3600:  # 1 saat cache
            return veri
    try:
        df = yf.download(sembol, period=period, interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) > 10:
            _benchmark_cache[sembol] = (df["Close"].squeeze(), simdi)
            return df["Close"].squeeze()
    except Exception as e:
        print(f"Benchmark hata ({sembol}): {e}")
    return None

# ══════════════════════════════════════════════
#  ★ ULTRA OPTIMUM HİSSE ANALİZİ
# ══════════════════════════════════════════════
def hisse_analiz_ultra(ticker):
    """
    Ultra Optimum sistem:
    1. Supertrend(10, 2.0) — boğa yönünde mi?
    2. RS > benchmark SMA  — piyasaya göre güçlü mü?
    3. ADX ≥ 27            — trend yeterince güçlü mü?
    4. RSI 52-80           — momentum sağlıklı mı?
    5. MACD(8,17,9) pozitif
    6. Adaptif Turtle kırılımı (Fib≥6→20bar, Fib<6→30bar)
    7. Hacim > SMA(20)×1.5
    8. Fibonacci EMA ≥ 6/7
    """
    try:
        piyasa  = "BIST" if ticker.endswith(".IS") else "ABD"
        min_cap = 0 if ticker.endswith(".IS") else 1_000_000_000

        # Piyasa değeri kontrolü
        try:
            bilgi  = yf.Ticker(ticker).info
            mktcap = bilgi.get("marketCap", 0) or 0
        except:
            mktcap = 0
        if min_cap > 0 and mktcap > 0 and mktcap < min_cap:
            return None, f"Küçük şirket ({mktcap/1e9:.1f}B)"

        # Fiyat verisi
        df = None
        for _ in range(3):
            try:
                df = yf.download(ticker, period="2y", interval="1d",
                                 progress=False, auto_adjust=True)
                if df is not None and len(df) >= 250:
                    break
                time.sleep(1)
            except:
                time.sleep(2)

        if df is None or len(df) < 250:
            return None, "Yeterli veri yok"

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        low    = df["Low"].squeeze()
        volume = df["Volume"].squeeze()

        # ── 1. SUPERTREND ★ ──────────────────────
        st_line, st_dir = tv_supertrend(df, periyot=10, faktor=2.0)
        stOK = bool(st_dir.iloc[-1] == -1)  # -1 = fiyat ST çizgisinin üstünde = boğa

        # ── 2. RS FİLTRESİ ★ ─────────────────────
        bench_sembol = "XU100.IS" if piyasa == "BIST" else "SPY"
        bench_close  = benchmark_getir(bench_sembol)
        if bench_close is not None and len(bench_close) > 50:
            rs_ratio, rs_ma = hesapla_rs(close, bench_close, periyot=50)
            rsStockOK = bool(rs_ratio.iloc[-1] > rs_ma.iloc[-1])
        else:
            rsStockOK = True  # Benchmark alınamazsa filtre geç

        # ── 3. ADX ≥ 27 ★ ────────────────────────
        _, _, adx_s = tv_dmi(df, 14)
        adx_val     = float(adx_s.iloc[-1])
        adxOK       = adx_val >= 27  # ★ (orijinal: 15)

        # ── 4. RSI 52-80 ★ ───────────────────────
        rsi_s   = tv_rsi(close, 14)
        rsi_val = float(rsi_s.iloc[-1])
        rsiOK   = 52 <= rsi_val <= 80  # ★ (orijinal: 45-80)

        # ── 5. MACD(8,17,9) ★ ────────────────────
        ml, ms, mh = tv_macd(close, fast=8, slow=17, signal=9)  # ★
        macdOK     = bool(ml.iloc[-1] > ms.iloc[-1]) and bool(mh.iloc[-1] > 0)

        # ── 6. FİBONACCİ EMA ★ ───────────────────
        fib_periyotlar = [5, 8, 13, 34, 55, 89, 144, 233]
        emalar = [float(tv_ema(close, p).iloc[-1]) for p in fib_periyotlar]
        fib    = sum(emalar[i] > emalar[i+1] for i in range(7))
        fibOK  = fib >= 6  # ★ (orijinal: 5)

        # ── 7. ADAPTİF TURTLE ★ ──────────────────
        turtle_len = 20 if fib >= 6 else 30  # ★ Adaptif (orijinal: sabit 40)
        exit_len   = 20
        don_hi     = high.rolling(turtle_len).max().shift(1)
        don_ex     = low.rolling(exit_len).min().shift(1)
        brkout     = bool(close.iloc[-1] > don_hi.iloc[-1])

        # ── 8. HACİM ★ ───────────────────────────
        vol_sma = volume.rolling(20).mean()
        volOK   = bool(volume.iloc[-1] > vol_sma.iloc[-1] * 1.5)  # ★ (orijinal: 1.2, kapalı)

        # ── ATR & SEVİYELER ──────────────────────
        N_s  = tv_atr(df, 20)
        N    = float(N_s.iloc[-1])
        fyt  = float(close.iloc[-1])
        risk = N * 1.8   # ★ Stop çarpanı (orijinal: 2.0)
        sl   = fyt - risk
        tp1  = fyt + risk * 2.0  # ★ (orijinal: 1.5)
        tp2  = fyt + risk * 4.0  # ★ (orijinal: 3.0)
        tp3  = fyt + risk * 7.0  # ★ (orijinal: 5.0)

        # ── MASTER SİNYAL ────────────────────────
        master = (stOK and rsStockOK and adxOK and rsiOK
                  and macdOK and brkout and volOK and fibOK)

        # Skor (kaç katman geçti)
        skor = sum([stOK, rsStockOK, adxOK, rsiOK, macdOK, brkout, volOK, fibOK])

        # Eksik katmanlar
        eksik = []
        if not stOK:      eksik.append("Supertrend")
        if not rsStockOK: eksik.append("RS")
        if not adxOK:     eksik.append(f"ADX({adx_val:.0f}<27)")
        if not rsiOK:     eksik.append(f"RSI({rsi_val:.0f})")
        if not macdOK:    eksik.append("MACD")
        if not brkout:    eksik.append(f"Turtle-{turtle_len}")
        if not volOK:     eksik.append("Hacim")
        if not fibOK:     eksik.append(f"Fib({fib}/7<6)")

        return {
            "ticker": ticker, "piyasa": piyasa, "master": master,
            "giris": round(fyt, 2), "sl": round(sl, 2),
            "tp1": round(tp1, 2), "tp2": round(tp2, 2), "tp3": round(tp3, 2),
            "rsi": round(rsi_val, 1), "adx": round(adx_val, 1),
            "fib": fib, "skor": skor, "atr": round(N, 2),
            "turtle_len": turtle_len, "mktcap": mktcap,
            "stOK": stOK, "rsOK": rsStockOK, "adxOK": adxOK,
            "rsiOK": rsiOK, "macdOK": macdOK, "brkout": brkout,
            "volOK": volOK, "fibOK": fibOK, "eksik": eksik
        }, None

    except Exception as e:
        return None, str(e)

def turtle_analiz_ultra(ticker):
    """Adaptif Turtle kırılım taraması"""
    try:
        piyasa  = "BIST" if ticker.endswith(".IS") else "ABD"
        min_cap = 0 if ticker.endswith(".IS") else 1_000_000_000
        try:
            bilgi  = yf.Ticker(ticker).info
            mktcap = bilgi.get("marketCap", 0) or 0
        except:
            mktcap = 0
        if min_cap > 0 and mktcap > 0 and mktcap < min_cap:
            return None, "Küçük şirket"

        df = None
        for _ in range(3):
            try:
                df = yf.download(ticker, period="2y", interval="1d",
                                 progress=False, auto_adjust=True)
                if df is not None and len(df) >= 50:
                    break
                time.sleep(1)
            except:
                time.sleep(2)

        if df is None or len(df) < 50:
            return None, "Yeterli veri yok"

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        low    = df["Low"].squeeze()

        # Fibonacci skor → adaptif turtle uzunluğu
        fib_periyotlar = [5, 8, 13, 34, 55, 89, 144, 233]
        emalar     = [float(tv_ema(close, p).iloc[-1]) for p in fib_periyotlar]
        fib        = sum(emalar[i] > emalar[i+1] for i in range(7))
        turtle_len = 20 if fib >= 6 else 30  # ★ Adaptif

        don_hi = high.rolling(turtle_len).max().shift(1)
        don_ex = low.rolling(20).min().shift(1)
        brkout = bool(close.iloc[-1] > don_hi.iloc[-1])

        if not brkout:
            return None, "Kırılım yok"

        N_s  = tv_atr(df, 20)
        N    = float(N_s.iloc[-1])
        fyt  = float(close.iloc[-1])
        risk = N * 1.8  # ★

        return {
            "ticker": ticker, "piyasa": piyasa,
            "giris": round(fyt, 2), "sl": round(fyt - risk, 2),
            "tp1": round(fyt + risk * 2.0, 2),
            "tp2": round(fyt + risk * 4.0, 2),
            "tp3": round(fyt + risk * 7.0, 2),
            "atr": round(N, 2), "fib": fib,
            "turtle_len": turtle_len,
            "don_hi": round(float(don_hi.iloc[-1]), 2),
            "don_ex": round(float(don_ex.iloc[-1]), 2),
            "mesafe": round((fyt - float(don_hi.iloc[-1])) / fyt * 100, 2),
            "mktcap": mktcap,
        }, None

    except Exception as e:
        return None, str(e)

# ══════════════════════════════════════════════
#  MESAJ FORMATLARI
# ══════════════════════════════════════════════
def sinyal_mesaji(s, sinyal_id=None):
    bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
    para   = "₺" if s["piyasa"] == "BIST" else "$"
    yildiz = "⭐" * s["fib"] + "☆" * (7 - s["fib"])
    cap    = f"{s['mktcap']/1e9:.1f}B {para}" if s.get("mktcap", 0) > 0 else "?"
    sid    = f"\n🔢 Sinyal ID: #{sinyal_id}" if sinyal_id else ""
    trt    = s.get("turtle_len", 20)
    return (
        f"{bayrak} <b>{s['ticker']}</b> [{s['piyasa']}] 🚀 AL SİNYALİ\n"
        f"💹 Piyasa Değeri: {cap}{sid}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Giriş: {para}{s['giris']}\n"
        f"🛑 S/L:   {para}{s['sl']}\n"
        f"🎯 TP1:  {para}{s['tp1']}  (×2R)\n"
        f"🎯 TP2:  {para}{s['tp2']}  (×4R)\n"
        f"🎯 TP3:  {para}{s['tp3']}  (×7R)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 RSI: {s['rsi']}  💪 ADX: {s['adx']}\n"
        f"🔢 ATR: {para}{s['atr']}  🐢 T-{trt}\n"
        f"⭐ Fib: {yildiz} {s['fib']}/7\n"
        f"✅ Skor: {s['skor']}/8 katman\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Ultra Optimum v3.0 | ST+RS+Fib6\n"
        f"💼 Sim: $1.000 / $100K portföy\n"
        f"⚠️ Yatırım tavsiyesi değildir."
    )

def detay_mesaji(s):
    isk    = lambda ok: "✅" if ok else "❌"
    bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
    para   = "₺" if s["piyasa"] == "BIST" else "$"
    sonuc  = "🚀 SİNYAL VAR!" if s["master"] else "❌ Sinyal yok"
    ekstra = f"\nGeçmeyen: {', '.join(s['eksik'])}" if not s["master"] and s["eksik"] else ""
    cap    = f"{s['mktcap']/1e9:.1f}B {para}" if s.get("mktcap", 0) > 0 else "?"
    trt    = s.get("turtle_len", 20)
    return (
        f"{bayrak} <b>{s['ticker']}</b> [{s['piyasa']}] — Ultra Optimum Analiz\n"
        f"💹 Piyasa Değeri: {cap}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{isk(s['stOK'])} Supertrend (★ yeni)\n"
        f"{isk(s['rsOK'])} RS > Benchmark (★ yeni)\n"
        f"{isk(s['adxOK'])} ADX: {s['adx']} (min:27 ★)\n"
        f"{isk(s['rsiOK'])} RSI: {s['rsi']} (52-80 ★)\n"
        f"{isk(s['macdOK'])} MACD(8/17/9) (★ hızlı)\n"
        f"{isk(s['brkout'])} Turtle T-{trt} Kırılım (★ adaptif)\n"
        f"{isk(s['volOK'])} Hacim >SMA×1.5 (★ zorunlu)\n"
        f"{isk(s['fibOK'])} Fibonacci: {s['fib']}/7 (min:6 ★)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Fiyat: {para}{s['giris']}\n"
        f"🛑 S/L:   {para}{s['sl']}\n"
        f"🎯 TP1:  {para}{s['tp1']}  (×2R)\n"
        f"🎯 TP2:  {para}{s['tp2']}  (×4R)\n"
        f"🎯 TP3:  {para}{s['tp3']}  (×7R)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{sonuc}{ekstra}"
    )

# ══════════════════════════════════════════════
#  İSTATİSTİK
# ══════════════════════════════════════════════
def istatistik_hesapla(gun_limit=None):
    db = db_oku()
    sinyaller = db["sinyaller"]
    if gun_limit:
        limit     = datetime.now() - timedelta(days=gun_limit)
        sinyaller = [s for s in sinyaller
                     if datetime.strptime(s["tarih"], "%Y-%m-%d %H:%M") >= limit]
    kapali = [s for s in sinyaller if s["durum"] != "ACIK"]
    acik   = [s for s in sinyaller if s["durum"] == "ACIK"]
    if not kapali:
        return None
    toplam   = len(kapali)
    kazanan  = len([s for s in kapali if s.get("sonuc") == "KAR"])
    kaybeden = len([s for s in kapali if s.get("sonuc") == "ZARAR"])
    net_pnl  = sum(s.get("kar_zarar", 0) or 0 for s in kapali)
    wr       = kazanan / toplam * 100 if toplam > 0 else 0
    return {
        "toplam": toplam, "kazanan": kazanan, "kaybeden": kaybeden,
        "acik": len(acik), "wr": round(wr, 1),
        "net_pnl": round(net_pnl, 2), "portfoy": round(100000 + net_pnl, 2)
    }

def istatistik_mesaji(donem, gun_limit=None):
    st = istatistik_hesapla(gun_limit)
    if not st:
        return f"📊 <b>{donem}</b>\n\nHenüz kapalı işlem yok."
    pnl_emoji = "📈" if st["net_pnl"] >= 0 else "📉"
    return (
        f"📊 <b>İSTATİSTİK — {donem}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Toplam İşlem: {st['toplam']}\n"
        f"✅ Kazanan: {st['kazanan']}\n"
        f"❌ Kaybeden: {st['kaybeden']}\n"
        f"🔄 Açık: {st['acik']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Kazanma Oranı: %{st['wr']}\n"
        f"{pnl_emoji} Net P&L: {'+'if st['net_pnl']>=0 else''}${st['net_pnl']:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💼 Portföy: ${st['portfoy']:,.2f}\n"
        f"📈 Başlangıç: $100,000.00\n"
        f"{'📈'if st['portfoy']>=100000 else'📉'} Değişim: "
        f"{'+'if st['portfoy']>=100000 else''}${st['portfoy']-100000:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Ultra Optimum v3.0 | $1.000/işlem"
    )

def acik_pozisyonlar_mesaji():
    db     = db_oku()
    aciklar= [s for s in db["sinyaller"] if s["durum"] == "ACIK"]
    if not aciklar:
        return "📋 <b>Açık Pozisyon Yok</b>"
    mesaj = f"📋 <b>AÇIK POZİSYONLAR ({len(aciklar)} adet)</b>\n━━━━━━━━━━━━━━━━━━━\n"
    for s in aciklar[-10:]:
        bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
        para   = "₺" if s["piyasa"] == "BIST" else "$"
        mesaj += (
            f"{bayrak} <b>{s['ticker']}</b>\n"
            f"  Giriş: {para}{s['giris']} | SL: {para}{s['sl']}\n"
            f"  TP1: {para}{s['tp1']} | Tarih: {s['tarih']}\n\n"
        )
    return mesaj

def gecmis_mesaji(adet=10):
    db     = db_oku()
    kapali = [s for s in db["sinyaller"] if s["durum"] != "ACIK"][-adet:]
    if not kapali:
        return "📋 <b>Geçmiş İşlem Yok</b>"
    mesaj = f"📋 <b>SON {len(kapali)} İŞLEM</b>\n━━━━━━━━━━━━━━━━━━━\n"
    for s in reversed(kapali):
        emoji  = "✅" if s.get("sonuc") == "KAR" else "❌"
        bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
        pnl    = s.get("kar_zarar", 0) or 0
        mesaj += f"{emoji} {bayrak} {s['ticker']} → {'+'if pnl>=0 else''}${pnl:.2f} ({s['tarih'][:10]})\n"
    return mesaj

# ══════════════════════════════════════════════
#  HİSSE LİSTELERİ
# ══════════════════════════════════════════════
BIST_HISSELER = list(dict.fromkeys([
    "AKBNK.IS","ARCLK.IS","ASELS.IS","BIMAS.IS","DOHOL.IS",
    "EKGYO.IS","ENKAI.IS","EREGL.IS","FROTO.IS","GARAN.IS",
    "HALKB.IS","ISCTR.IS","KCHOL.IS","KOZAL.IS","KRDMD.IS",
    "MGROS.IS","PETKM.IS","PGSUS.IS","SAHOL.IS","SASA.IS",
    "SISE.IS","TAVHL.IS","TCELL.IS","THYAO.IS","TKFEN.IS",
    "TOASO.IS","TTKOM.IS","TUPRS.IS","VAKBN.IS","YKBNK.IS",
    "AEFES.IS","AGESA.IS","AKSEN.IS","ALARK.IS","AYGAZ.IS",
    "BRYAT.IS","CCOLA.IS","CIMSA.IS","CLEBI.IS","GUBRF.IS",
    "ISGYO.IS","KARSN.IS","KOZAA.IS","LOGO.IS","MAVI.IS",
    "ODAS.IS","OTKAR.IS","SARKY.IS","SOKM.IS","TSKB.IS",
    "AGHOL.IS","AKGRT.IS","ALKIM.IS","ANELE.IS","ASUZU.IS",
    "AVGYO.IS","AYDEM.IS","BAGFS.IS","BIOEN.IS","BIZIM.IS",
    "CEMTS.IS","DEVA.IS","ENJSA.IS","ERBOS.IS","EUPWR.IS",
    "GESAN.IS","GLYHO.IS","HEKTS.IS","HLGYO.IS","ICBCT.IS",
    "INDES.IS","ISDMR.IS","IZENR.IS","KERVT.IS","KNFRT.IS",
    "KONTR.IS","MAALT.IS","MEGAP.IS","MERIT.IS","METRO.IS",
    "MPARK.IS","NATEN.IS","NETAS.IS","NTGAZ.IS","NUHCM.IS",
    "OFSYM.IS","ORGE.IS","OYAKC.IS","PAGYO.IS","PARSN.IS",
    "PENGD.IS","PNSUT.IS","POLHO.IS","PTOFS.IS","RNPOL.IS",
    "TATGD.IS","TBORG.IS","TEKTU.IS","TKNSA.IS","TMSN.IS",
    "TRGYO.IS","TTRAK.IS","TURSG.IS","ULKER.IS","VESTL.IS",
    "VKGYO.IS","YKGYO.IS","ZRGYO.IS","ALFAS.IS","BRSAN.IS",
    "EGEEN.IS","GSRAY.IS","BFREN.IS","BUCIM.IS","BURCE.IS",
    "DYOBY.IS","EMKEL.IS","FENER.IS","GRSEL.IS","GSDHO.IS",
    "HTTBT.IS","IPEKE.IS","ISFIN.IS","JANTS.IS","KUYAS.IS",
    "LKMNH.IS","MACKO.IS","MAGEN.IS","MEDTR.IS","MOBTL.IS",
    "ORCAY.IS","OZRDN.IS","PASEU.IS","PRZMA.IS","RAYSG.IS",
    "RGYAS.IS","RODRG.IS","SAFKR.IS","SELGD.IS","SEYKM.IS",
    "SILVR.IS","SNKRN.IS","SRVGY.IS","TLMAN.IS","TRCAS.IS",
    "ULUUN.IS","UMPAS.IS","USAK.IS","YAPRK.IS",
    "ADEL.IS","AFYON.IS","AGYO.IS","AKCNS.IS","AKFEN.IS",
    "AKSA.IS","AKSY.IS","ALCAR.IS","ALKA.IS","ALVES.IS",
    "ARENA.IS","ARTMS.IS","ATEKS.IS","ATLAS.IS","AYCES.IS",
    "AYEN.IS","BASGZ.IS","BINHO.IS","BNTAS.IS","BORSK.IS",
    "BURVA.IS","CANTE.IS","CEOEM.IS","CGCAM.IS","CMBTN.IS",
    "CRFSA.IS","DGATE.IS","DGGYO.IS","DITAS.IS","DOCO.IS",
    "DOGUB.IS","ECZYT.IS","EDIP.IS","EGPRO.IS","EMNIS.IS",
    "ENSRI.IS","EPLAS.IS","ERSU.IS","ESCOM.IS","ESEN.IS",
    "ETYAT.IS","EUREN.IS","FADE.IS","FMIZP.IS","FONET.IS",
    "GEDIK.IS","GEDZA.IS","GLBMD.IS","GLCVY.IS","GOLTS.IS",
    "GONUL.IS","GOODY.IS","GOZDE.IS","GRNYO.IS","GSDDE.IS",
    "GUNES.IS","HATEK.IS","HEDEF.IS","HRKET.IS","HUNER.IS",
    "HURGZ.IS","IDGYO.IS","IHLGM.IS","IHLAS.IS","IHYAY.IS",
    "IMASM.IS","INTEM.IS","INVEO.IS","ISATR.IS","ISBIR.IS",
    "ISKPL.IS","ISMEN.IS","ITTFH.IS","IZFAS.IS","IZINV.IS",
    "IZMDC.IS","KAPLM.IS","KAREL.IS","KATMR.IS","KAYSE.IS",
    "KBORU.IS","KENT.IS","KGYO.IS","KLGYO.IS","KLKIM.IS",
    "KLMSN.IS","KORDS.IS","KOTON.IS","KRDMA.IS","KRDMB.IS",
    "KRPLS.IS","KRSTL.IS","KRTEK.IS","KTLEV.IS","LIDER.IS",
    "LIDFA.IS","LINK.IS","LRSHO.IS","LUKSK.IS","MAKIM.IS",
    "MANAS.IS","MARTI.IS","MEKAG.IS","METEM.IS","METUR.IS",
    "MIPAZ.IS","MNDRS.IS","MOGAN.IS","MTRKS.IS","NIBAS.IS",
    "NILYT.IS","NTHOL.IS","NUGYO.IS","OBAMS.IS","OBASE.IS",
    "ONCSM.IS","OSMEN.IS","OSTIM.IS","OYLUM.IS","OZGYO.IS",
    "OZSUB.IS","PAMEL.IS","PAPIL.IS","PCILT.IS","PINSU.IS",
    "PKENT.IS","PSDTC.IS","QUAGR.IS","RALYH.IS","RTALB.IS",
    "RUBNS.IS","SAMAT.IS","SANEL.IS","SANFM.IS","SANKO.IS",
    "SAYAS.IS","SEKFK.IS","SEKUR.IS","SELVA.IS","SKBNK.IS",
    "SKYLP.IS","SMRTG.IS","SUMAS.IS","SURGY.IS","TDGYO.IS",
    "TGSAS.IS","TMPOL.IS","TURGG.IS","VAKFN.IS","VAKKO.IS",
    "VBTYZ.IS","YBTAS.IS","YYLGD.IS","ZOREN.IS","ACSEL.IS",
    "ADESE.IS","AKENR.IS","AKFGY.IS","AKMGY.IS","AKSGY.IS",
    "ALBRK.IS","ALGYO.IS","ALKLC.IS","ATAGY.IS","ATSYH.IS",
    "AZTEK.IS","CMENT.IS","DGNMO.IS","DMRGD.IS","DNISI.IS",
    "DURDO.IS","DZGYO.IS","EGSER.IS","EMPIN.IS","ESCAR.IS",
    "GARFA.IS","GLRYH.IS","IEYHO.IS","KCAER.IS","KZBGY.IS",
    "RGYO.IS"
]))

ABD_HISSELER = list(dict.fromkeys([
    "AAPL","MSFT","NVDA","AVGO","ORCL","CRM","ADBE","AMD","QCOM","TXN",
    "INTC","AMAT","MU","LRCX","KLAC","MRVL","SNPS","CDNS","ANSS","CTSH",
    "HPQ","IBM","CSCO","ACN","INTU","NOW","WDAY","TEAM","PANW","CRWD",
    "FTNT","DDOG","ZS","NET","OKTA","VEEV","PAYC","HUBS","TWLO","ZM",
    "DOCU","MDB","DKNG","RBLX","HPE","DELL","WDC","STX","NTAP","KEYS",
    "TRMB","EPAM","GDDY","CDW","FFIV","JNPR","CIEN","JPM","BAC","WFC",
    "GS","MS","BLK","AXP","V","MA","PYPL","C","USB","PNC",
    "TFC","COF","SCHW","ICE","CME","SPGI","MCO","BX","KKR","APO",
    "ARES","CB","AIG","MET","PRU","ALL","AFL","AMP","PFG","TROW",
    "STT","BK","NTRS","FIS","FI","GPN","DFS","SYF","ALLY","CFG",
    "HBAN","RF","KEY","FITB","MTB","ZION","CMA","JNJ","UNH","LLY",
    "PFE","ABBV","MRK","BMY","AMGN","GILD","REGN","VRTX","BIIB","MRNA",
    "ISRG","MDT","ABT","BSX","SYK","ZBH","BAX","BDX","EW","HOLX",
    "DXCM","PODD","ALNY","BMRN","HCA","CNC","CVS","CI","HUM","ELV",
    "MOH","IQV","DGX","LH","ZTS","IDXX","ICLR","MEDP","AMZN","TSLA",
    "HD","LOW","TGT","TJX","ROST","BURL","COST","WMT","SBUX","MCD",
    "YUM","CMG","DPZ","TXRH","NKE","LULU","SKX","CROX","DECK",
    "BKNG","EXPE","ABNB","UBER","MAR","HLT","GM","F","PG","KO",
    "PEP","PM","MO","KHC","GIS","K","CPB","CAG","MKC","CHD",
    "CLX","CL","EL","ULTA","WBA","DG","DLTR","XOM","CVX","COP",
    "EOG","SLB","HAL","BKR","MPC","VLO","PSX","OXY","DVN","APA",
    "HES","MRO","CTRA","KMI","WMB","OKE","LNG","GE","HON","MMM",
    "CAT","DE","BA","LMT","RTX","NOC","GD","UPS","FDX","CSX",
    "NSC","UNP","DAL","UAL","AAL","LUV","ALK","EMR","ETN","PH",
    "ROK","AME","XYL","ITW","DOV","SWK","TT","IR","CARR","OTIS",
    "TDG","HWM","WAB","GWW","FAST","LIN","APD","ECL","PPG","SHW",
    "NEM","FCX","AA","NUE","STLD","RS","ALB","AMT","PLD","CCI",
    "EQIX","SPG","O","PSA","EXR","AVB","EQR","VTR","WELL","VICI",
    "NEE","DUK","SO","D","AEP","EXC","XEL","ES","FE","ETR",
    "PCG","EIX","WEC","NRG","VST","GOOGL","GOOG","META","T","VZ",
    "TMUS","CHTR","CMCSA","DIS","NFLX","PARA","WBD","FOXA","SNAP","PINS",
    "SPOT","BRK-B","MSCI","NDAQ"
]))

# ══════════════════════════════════════════════
#  TARAMA FONKSİYONLARI
# ══════════════════════════════════════════════
gonderilen_sinyaller = set()

def tarama_yap(hisseler, baslik):
    global gonderilen_sinyaller
    simdi        = datetime.now().strftime("%d.%m.%Y %H:%M")
    sinyaller    = []
    kullanicilar = aktif_kullanicilar()

    print(f"\n{'='*50}")
    print(f"{baslik} Ultra Optimum taraması — {len(hisseler)} hisse")
    print(f"{'='*50}")

    for ticker in hisseler:
        print(f"  {ticker}...", end=" ", flush=True)
        sonuc, hata = hisse_analiz_ultra(ticker)
        if sonuc and sonuc["master"]:
            bugun   = datetime.now().strftime("%Y-%m-%d")
            anahtar = f"{ticker}_{bugun}"
            if anahtar not in gonderilen_sinyaller:
                sinyaller.append(sonuc)
                gonderilen_sinyaller.add(anahtar)
                print(f"★ SİNYAL! Fib:{sonuc['fib']}/7 ADX:{sonuc['adx']:.0f} RSI:{sonuc['rsi']:.0f}")
            else:
                print("(zaten gönderildi)")
        else:
            eksik = hata or (sonuc["eksik"] if sonuc else "?")
            print(f"- [{eksik}]" if isinstance(eksik, str) else f"- [{', '.join(eksik[:2])}]")
        time.sleep(0.3)

    print(f"\n{baslik}: {len(sinyaller)} sinyal bulundu")

    if not sinyaller:
        return

    ozet = (
        f"🚀 <b>{baslik} ULTRA OPT — {len(sinyaller)} YENİ SİNYAL</b>\n"
        f"⏰ {simdi}\n"
        f"🔥 Sistem: ST+RS+Fib6+ADX27+T-Adaptif"
    )
    for cid in kullanicilar:
        telegram_gonder(ozet, cid)
    time.sleep(1)

    for s in sinyaller:
        sid = sinyal_kaydet(
            s["ticker"], s["piyasa"], s["giris"],
            s["sl"], s["tp1"], s["tp2"], s["tp3"],
            s["atr"], s["fib"], s["skor"]
        )
        mesaj = sinyal_mesaji(s, sid)
        for cid in kullanicilar:
            telegram_gonder_butonlu(
                mesaj, cid, s["ticker"], s["giris"], s["sl"], s["tp1"], sid
            )
            time.sleep(0.3)
        time.sleep(0.5)

def turtle_tarama_yap(hisseler, baslik):
    simdi        = datetime.now().strftime("%d.%m.%Y %H:%M")
    sinyaller    = []
    kullanicilar = aktif_kullanicilar()

    for ticker in hisseler:
        sonuc, _ = turtle_analiz_ultra(ticker)
        if sonuc:
            sinyaller.append(sonuc)
        time.sleep(0.3)

    if not sinyaller:
        return

    ozet = f"🐢 <b>{baslik} TURTLE — {len(sinyaller)} KIRILIM</b>\n⏰ {simdi}"
    for cid in kullanicilar:
        telegram_gonder(ozet, cid)
    time.sleep(1)

    for s in sinyaller:
        bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
        para   = "₺" if s["piyasa"] == "BIST" else "$"
        trt    = s.get("turtle_len", 20)
        mesaj  = (
            f"🐢 {bayrak} <b>{s['ticker']}</b> — T-{trt} KIRILIM ★ Adaptif\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Giriş: {para}{s['giris']}\n"
            f"🛑 S/L:   {para}{s['sl']}\n"
            f"🎯 TP1:  {para}{s['tp1']}  (×2R)\n"
            f"🎯 TP2:  {para}{s['tp2']}  (×4R)\n"
            f"🎯 TP3:  {para}{s['tp3']}  (×7R)\n"
            f"📏 {trt}G Yüksek: {para}{s['don_hi']}\n"
            f"🚪 Çıkış 20G:   {para}{s['don_ex']}\n"
            f"📐 Mesafe: %{s['mesafe']}\n"
            f"⭐ Fib: {s['fib']}/7\n"
            f"⚠️ Yatırım tavsiyesi değildir."
        )
        for cid in kullanicilar:
            telegram_gonder(mesaj, cid)
            time.sleep(0.3)
        time.sleep(0.5)

# Zamanlayıcı sarmalayıcıları
def bist_tarama():
    global tarama_aktif
    with tarama_kilidi:
        if tarama_aktif:
            return
        tarama_aktif = True
    try:
        tarama_yap(BIST_HISSELER, "BIST")
    finally:
        tarama_aktif = False

def abd_tarama():
    global tarama_aktif
    with tarama_kilidi:
        if tarama_aktif:
            return
        tarama_aktif = True
    try:
        tarama_yap(ABD_HISSELER, "ABD")
    finally:
        tarama_aktif = False

def bist_turtle():
    global tarama_aktif
    with tarama_kilidi:
        if tarama_aktif:
            return
        tarama_aktif = True
    try:
        turtle_tarama_yap(BIST_HISSELER, "BIST")
    finally:
        tarama_aktif = False

def abd_turtle():
    global tarama_aktif
    with tarama_kilidi:
        if tarama_aktif:
            return
        tarama_aktif = True
    try:
        turtle_tarama_yap(ABD_HISSELER, "ABD")
    finally:
        tarama_aktif = False

# ══════════════════════════════════════════════
#  TELEGRAM DİNLEYİCİ
# ══════════════════════════════════════════════
def yardim_mesaji():
    return (
        "🤖 <b>ULS+FIB Ultra Optimum Bot v3.0</b>\n\n"
        "👤 Kayıt:\n"
        "/basla — Sisteme katıl\n\n"
        "📊 Analiz:\n"
        "THYAO / AAPL — Hisse analiz\n\n"
        "🔍 Tarama:\n"
        "/bist — BIST Ultra Opt tara\n"
        "/abd  — ABD Ultra Opt tara\n"
        "/tb   — BIST Turtle (adaptif)\n"
        "/ta   — ABD Turtle (adaptif)\n\n"
        "📈 İstatistik:\n"
        "/stat_hafta  — Haftalık\n"
        "/stat_ay     — Aylık\n"
        "/stat_3ay    — 3 Aylık\n"
        "/stat_yil    — Yıllık\n"
        "/stat_tum    — Tüm zamanlar\n\n"
        "💼 Portföy:\n"
        "/acik    — Açık pozisyonlar\n"
        "/gecmis  — Son 10 işlem\n"
        "/kapat THYAO 395.50\n\n"
        "🔥 Ultra Optimum: ST+RS+Fib6\n"
        "⏰ Otomatik: BIST 10-18:30 / ABD 23:30"
    )

def mesaji_isle(metin, chat_id, isim):
    cmd = metin.strip().upper()

    if cmd in ["/BASLA", "/START"]:
        yeni = kullanici_ekle(chat_id, isim)
        if yeni:
            telegram_gonder(
                f"✅ <b>Hoş geldin {isim}!</b>\n\n"
                f"Ultra Optimum Bot'a kayıt oldun.\n"
                f"🔥 Sistem: Supertrend + RS + Fib≥6\n\n"
                f"/yardim — Tüm komutlar", chat_id)
        else:
            telegram_gonder(f"✅ Zaten kayıtlısın {isim}!\n/yardim — Komutlar", chat_id)
        return

    if cmd in ["/YARDIM", "/HELP"]:
        telegram_gonder(yardim_mesaji(), chat_id)
        return

    if cmd == "/STAT_HAFTA":
        telegram_gonder(istatistik_mesaji("HAFTALIK", 7), chat_id)
        return
    if cmd == "/STAT_AY":
        telegram_gonder(istatistik_mesaji("AYLIK", 30), chat_id)
        return
    if cmd == "/STAT_3AY":
        telegram_gonder(istatistik_mesaji("3 AYLIK", 90), chat_id)
        return
    if cmd == "/STAT_YIL":
        telegram_gonder(istatistik_mesaji("YILLIK", 365), chat_id)
        return
    if cmd == "/STAT_TUM":
        telegram_gonder(istatistik_mesaji("TÜM ZAMANLAR"), chat_id)
        return

    if cmd == "/ACIK":
        telegram_gonder(acik_pozisyonlar_mesaji(), chat_id)
        return
    if cmd == "/GECMIS":
        telegram_gonder(gecmis_mesaji(), chat_id)
        return

    if cmd == "/BIST":
        telegram_gonder("🇹🇷 BIST Ultra Optimum taraması başlatılıyor...", chat_id)
        threading.Thread(target=bist_tarama, daemon=True).start()
        return
    if cmd == "/ABD":
        telegram_gonder("🇺🇸 ABD Ultra Optimum taraması başlatılıyor...", chat_id)
        threading.Thread(target=abd_tarama, daemon=True).start()
        return
    if cmd == "/TARA":
        telegram_gonder("🔍 Tüm piyasalar taranıyor...", chat_id)
        threading.Thread(target=bist_tarama, daemon=True).start()
        threading.Thread(target=abd_tarama,  daemon=True).start()
        return
    if cmd in ["/TB", "/TURTLE_BIST"]:
        telegram_gonder("🐢 BIST Adaptif Turtle başlatılıyor...", chat_id)
        threading.Thread(target=bist_turtle, daemon=True).start()
        return
    if cmd in ["/TA", "/TURTLE_ABD"]:
        telegram_gonder("🐢 ABD Adaptif Turtle başlatılıyor...", chat_id)
        threading.Thread(target=abd_turtle, daemon=True).start()
        return
    if cmd in ["/TT", "/TURTLE_TARA"]:
        telegram_gonder("🐢 Turtle taraması başlatılıyor...", chat_id)
        threading.Thread(target=bist_turtle, daemon=True).start()
        threading.Thread(target=abd_turtle,  daemon=True).start()
        return

    if cmd == "/LISTE":
        telegram_gonder(
            f"📊 <b>Tarama Listesi</b>\n\n"
            f"🇹🇷 BIST: {len(BIST_HISSELER)} hisse\n"
            f"🇺🇸 ABD: {len(ABD_HISSELER)} hisse\n"
            f"📊 Toplam: {len(BIST_HISSELER)+len(ABD_HISSELER)} hisse\n"
            f"🔥 Sistem: Ultra Optimum v3.0", chat_id)
        return

    if cmd.startswith("/KAPAT"):
        parcalar = metin.strip().split()
        if len(parcalar) < 3:
            telegram_gonder("Format: /kapat THYAO 395.50", chat_id)
            return
        tk = parcalar[1].upper()
        try:
            fk = float(parcalar[2])
        except:
            telegram_gonder("Geçersiz fiyat", chat_id)
            return
        dbx = db_oku()
        if tk not in dbx["acik_pozisyonlar"] and tk + ".IS" in dbx["acik_pozisyonlar"]:
            tk = tk + ".IS"
        if tk not in dbx["acik_pozisyonlar"]:
            telegram_gonder("Açık pozisyon yok: " + tk, chat_id)
            return
        sidx = dbx["acik_pozisyonlar"][tk]
        gf   = 0
        for sx in dbx["sinyaller"]:
            if sx["id"] == sidx:
                gf = sx["giris"]
                break
        pozisyon_kapat(tk, "KAPANDI", fk)
        kz  = (fk - gf) / gf * 1000 if gf > 0 else 0
        pnl = ("+" if kz >= 0 else "") + "$" + str(round(kz, 2))
        telegram_gonder(
            ("✅ KAR" if kz >= 0 else "❌ ZARAR") +
            f" | {tk} | Giriş:{gf} → Çıkış:{fk} | PnL:{pnl}", chat_id)
        return

    # Hisse analizi
    ticker = cmd.replace("/", "").strip()
    if len(ticker) < 2:
        return

    telegram_gonder(f"🔍 <b>{ticker}</b> Ultra Optimum analiz ediliyor...", chat_id)
    sonuc, hata = hisse_analiz_ultra(ticker)
    if sonuc is None and not ticker.endswith(".IS"):
        sonuc, hata = hisse_analiz_ultra(ticker + ".IS")
    if sonuc is None:
        telegram_gonder(
            f"❌ <b>{ticker}</b> bulunamadı.\n"
            f"BIST: THYAO veya THYAO.IS\n"
            f"ABD: AAPL, MSFT\n"
            f"Hata: {hata}", chat_id)
        return
    telegram_gonder(detay_mesaji(sonuc), chat_id)

def callback_isle(callback_query):
    cid  = callback_query["message"]["chat"]["id"]
    data = callback_query.get("data", "")
    qid  = callback_query["id"]
    if data.startswith("reddet_"):
        sid = int(data.replace("reddet_", ""))
        pozisyon_kapat_by_id(sid, "REDDEDILDI", 0)
        telegram_callback_cevapla(qid, "❌ Sinyal reddedildi")
        telegram_gonder(f"❌ Sinyal #{sid} reddedildi.", cid)
    elif data.startswith("bilgi_"):
        telegram_callback_cevapla(qid, "ℹ️ Sinyal detayı yukarıda")

def telegram_dinle():
    global son_update_id
    print("Telegram dinleniyor...")
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        r   = requests.get(url, params={"offset": -1}, timeout=10)
        if r.status_code == 200:
            sonuclar = r.json().get("result", [])
            if sonuclar:
                son_update_id = sonuclar[-1]["update_id"]
                print(f"Eski mesajlar atlandı. Son ID: {son_update_id}")
    except:
        pass

    while True:
        try:
            for m in telegram_mesajlari_al():
                son_update_id = m["update_id"]
                if "message" in m and "text" in m["message"]:
                    metin   = m["message"]["text"]
                    chat_id = m["message"]["chat"]["id"]
                    isim    = m["message"].get("from", {}).get("first_name", "Kullanıcı")
                    print(f"Mesaj [{chat_id}]: '{metin}'")
                    threading.Thread(
                        target=mesaji_isle,
                        args=(metin, chat_id, isim),
                        daemon=True
                    ).start()
                elif "callback_query" in m:
                    threading.Thread(
                        target=callback_isle,
                        args=(m["callback_query"],),
                        daemon=True
                    ).start()
            time.sleep(2)
        except Exception as e:
            print(f"Dinleme hatası: {e}")
            time.sleep(5)

# ══════════════════════════════════════════════
#  ANA PROGRAM
# ══════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("ULS + FIB — ULTRA OPTIMUM SİNYAL BOTU v3.0")
    print("=" * 60)
    print(f"★ Supertrend(10,2.0) + RS filtresi")
    print(f"★ ADX≥27 | RSI 52-80 | MACD(8/17/9)")
    print(f"★ Adaptif Turtle | Fib≥6/7 | Hacim×1.5")
    print(f"★ Stop×1.8 | TP ×2/4/7")
    print(f"BIST: {len(BIST_HISSELER)} hisse")
    print(f"ABD:  {len(ABD_HISSELER)} hisse")
    print("Durdurmak: CTRL+C\n")

    kullanici_ekle(TELEGRAM_CHAT_ID, "Admin")

    telegram_gonder(
        "✅ <b>Ultra Optimum Bot v3.0 Aktif!</b>\n\n"
        f"🇹🇷 BIST: {len(BIST_HISSELER)} hisse\n"
        f"🇺🇸 ABD: {len(ABD_HISSELER)} hisse\n\n"
        "🔥 <b>Yeni sistem:</b>\n"
        "★ Supertrend (EMA200 yerine)\n"
        "★ RS > Benchmark filtresi\n"
        "★ ADX≥27 | RSI 52-80\n"
        "★ MACD(8/17/9) hızlı\n"
        "★ Adaptif Turtle (T-20/T-30)\n"
        "★ Fibonacci ≥6/7\n"
        "★ Hacim ×1.5 zorunlu\n"
        "★ TP ×2R / ×4R / ×7R\n\n"
        "📊 Backtest: %88 WR | $134/sinyal\n\n"
        "/basla — Sisteme katıl\n"
        "/yardim — Tüm komutlar"
    )

    # Zamanlayıcı
    schedule.every().day.at("10:00").do(bist_tarama)
    schedule.every().day.at("12:00").do(bist_tarama)
    schedule.every().day.at("14:00").do(bist_tarama)
    schedule.every().day.at("16:00").do(bist_tarama)
    schedule.every().day.at("18:00").do(bist_tarama)
    schedule.every().day.at("18:30").do(bist_tarama)
    schedule.every().day.at("19:00").do(bist_turtle)
    schedule.every().day.at("23:30").do(abd_tarama)
    schedule.every().day.at("00:00").do(abd_turtle)

    threading.Thread(target=telegram_dinle, daemon=True).start()

    while True:
        schedule.run_pending()
        time.sleep(30)
