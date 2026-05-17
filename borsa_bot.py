"""
BIST MASTER SİNYAL BOTU v4.0
══════════════════════════════════════════════
Pine Script'ten taşınan 7 gösterge sistemi:
  ★ EMA 20/50/200     → Trend dizilimi
  ★ RSI (14)          → Momentum 50-70
  ★ MACD (12/26/9)    → Histogram genişliyor
  ★ Hacim / OBV       → Akıllı para takibi
  ★ Bollinger Bantları → Fiyat pozisyonu
  ★ Supertrend (10,3) → Dinamik trend filtresi
  ★ Ichimoku          → Kumo + TK + Chikou

AL  sinyali : 5/7 ve üzeri onay
ÇIK sinyali : 3/7 ve altı onay (açık pozisyonlar için)

Tarama zamanları:
  BIST: 10:30 / 12:30 / 14:30 / 16:30 / 18:30 (her 2 saatte 1)
  ABD : 16:30 (açılış ortası) / 23:00 (kapanış)
  ÇIK taraması: Her 2 saatte 1 (açık pozisyonlar)
"""

import requests
import json
import time
import threading
import schedule
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os

# ══════════════════════════════════════════════
# TELEGRAM AYARLARI
# ══════════════════════════════════════════════
TELEGRAM_TOKEN   = "8644118927:AAHwT1tHdfoEVZ-W8hpCJk9HJJT8iItul14"
TELEGRAM_CHAT_ID = "-1003848631204"

son_update_id = 0
tarama_aktif  = False
tarama_kilidi = threading.Lock()

def telegram_gonder(mesaj, chat_id=None):
    cid = chat_id or TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": cid, "text": mesaj,
            "parse_mode": "HTML"
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram hata: {e}")
        return False

def telegram_gonder_butonlu(mesaj, chat_id, ticker, giris, sl, tp1, sinyal_id):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    lot  = max(1, int(1000 / giris)) if giris > 0 else 1
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
            sonuclar = r.json().get("result", [])
            if sonuclar:
                son_update_id = sonuclar[-1]["update_id"]
            return sonuclar
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
# VERİTABANI
# ══════════════════════════════════════════════
DB_FILE = "bist_master_db.json"

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
            "isim": isim,
            "katilim": datetime.now().strftime("%Y-%m-%d"),
            "aktif": True
        }
        db_kaydet(db)
        return True
    return False

def aktif_kullanicilar():
    db = db_oku()
    return [cid for cid, u in db["kullanicilar"].items() if u.get("aktif", True)]

def sinyal_kaydet(ticker, piyasa, giris, sl, tp1, tp2, tp3, atr, skor):
    db     = db_oku()
    sinyal = {
        "id"       : len(db["sinyaller"]) + 1,
        "tarih"    : datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ticker"   : ticker,
        "piyasa"   : piyasa,
        "giris"    : giris,
        "sl"       : sl,
        "tp1"      : tp1,
        "tp2"      : tp2,
        "tp3"      : tp3,
        "atr"      : atr,
        "skor"     : skor,
        "durum"    : "ACIK",
        "sonuc"    : None,
        "kar_zarar": None,
        "kapanma"  : None
    }
    db["sinyaller"].append(sinyal)
    db["acik_pozisyonlar"][ticker] = sinyal["id"]
    db_kaydet(db)
    return sinyal["id"]

def pozisyon_kapat(ticker, durum, son_fiyat):
    db  = db_oku()
    sid = db["acik_pozisyonlar"].get(ticker)
    if not sid:
        return
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
# TEKNİK HESAPLAMALAR — PINE SCRIPT UYUMLU
# ══════════════════════════════════════════════

def tv_ema(seri, periyot):
    return seri.ewm(span=periyot, adjust=False).mean()

def tv_rma(seri, periyot):
    """Pine Script'in rma() fonksiyonu — ATR ve DMI için"""
    alpha  = 1.0 / periyot
    result = seri.copy().astype(float) * np.nan
    if len(seri) < periyot:
        return result
    result.iloc[periyot - 1] = seri.iloc[:periyot].mean()
    for i in range(periyot, len(seri)):
        result.iloc[i] = alpha * seri.iloc[i] + (1 - alpha) * result.iloc[i - 1]
    return result

def tv_atr(df, periyot=14):
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()
    prev  = close.shift(1)
    tr    = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tv_rma(tr, periyot)

def tv_rsi(close, periyot=14):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = tv_rma(gain, periyot)
    avg_l = tv_rma(loss, periyot)
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def tv_macd(close, fast=12, slow=26, signal=9):
    ml = tv_ema(close, fast) - tv_ema(close, slow)
    ms = tv_ema(ml, signal)
    return ml, ms, ml - ms

def tv_bb(close, periyot=20, mult=2.0):
    mid   = close.rolling(periyot).mean()
    std   = close.rolling(periyot).std(ddof=0)
    upper = mid + mult * std
    lower = mid - mult * std
    return mid, upper, lower

def tv_supertrend(df, periyot=10, faktor=3.0):
    """Pine Script Supertrend — st_dir: 1=boğa, -1=ayı"""
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()
    atr   = tv_atr(df, periyot)
    hl2   = (high + low) / 2

    final_upper = pd.Series(np.nan, index=close.index)
    final_lower = pd.Series(np.nan, index=close.index)
    st          = pd.Series(np.nan, index=close.index)
    direction   = pd.Series(1, index=close.index)

    for i in range(1, len(close)):
        bu = hl2.iloc[i] + faktor * atr.iloc[i]
        bl = hl2.iloc[i] - faktor * atr.iloc[i]

        # Upper band
        prev_u = final_upper.iloc[i-1] if not np.isnan(final_upper.iloc[i-1]) else bu
        final_upper.iloc[i] = bu if (bu < prev_u or close.iloc[i-1] > prev_u) else prev_u

        # Lower band
        prev_l = final_lower.iloc[i-1] if not np.isnan(final_lower.iloc[i-1]) else bl
        final_lower.iloc[i] = bl if (bl > prev_l or close.iloc[i-1] < prev_l) else prev_l

        # Yön
        prev_st  = st.iloc[i-1] if not np.isnan(st.iloc[i-1]) else final_lower.iloc[i]
        prev_dir = direction.iloc[i-1]

        if prev_dir == 1:  # Önceki: boğa (st = lower)
            if close.iloc[i] < final_lower.iloc[i]:
                direction.iloc[i] = -1
                st.iloc[i]        = final_upper.iloc[i]
            else:
                direction.iloc[i] = 1
                st.iloc[i]        = final_lower.iloc[i]
        else:              # Önceki: ayı (st = upper)
            if close.iloc[i] > final_upper.iloc[i]:
                direction.iloc[i] = 1
                st.iloc[i]        = final_lower.iloc[i]
            else:
                direction.iloc[i] = -1
                st.iloc[i]        = final_upper.iloc[i]

    return st, direction

def tv_ichimoku(df, conv=9, base=26, span_b=52, disp=26):
    """Ichimoku hesaplama — Pine Script uyumlu"""
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()

    def donchian(h, l, n):
        return (h.rolling(n).max() + l.rolling(n).min()) / 2

    tenkan = donchian(high, low, conv)
    kijun  = donchian(high, low, base)
    spanA  = (tenkan + kijun) / 2
    spanB  = donchian(high, low, span_b)

    # Kumo: disp bar önceki spanA ve spanB
    kumo_top = pd.concat([spanA.shift(disp), spanB.shift(disp)], axis=1).max(axis=1)
    kumo_bot = pd.concat([spanA.shift(disp), spanB.shift(disp)], axis=1).min(axis=1)

    return tenkan, kijun, spanA, spanB, kumo_top, kumo_bot

# ══════════════════════════════════════════════
# ANA ANALİZ FONKSİYONU — 7 GÖSTERGE
# Pine Script BIST Master v4.0 ile birebir uyumlu
# ══════════════════════════════════════════════

def hisse_analiz(ticker):
    """
    7 göstergeyi hesaplar, her biri 0 veya 1 puan verir.
    Toplam skor 0-7 arasında.
    AL  : skor >= 5
    ÇIK : skor <= 3
    """
    try:
        piyasa  = "BIST" if ticker.endswith(".IS") else "ABD"
        min_cap = 0 if ticker.endswith(".IS") else 1_000_000_000

        try:
            bilgi  = yf.Ticker(ticker).info
            mktcap = bilgi.get("marketCap", 0) or 0
        except:
            mktcap = 0
        if min_cap > 0 and mktcap > 0 and mktcap < min_cap:
            return None, f"Küçük şirket"

        # Veri çek — Ichimoku için en az 300 bar gerekli
        df = None
        for _ in range(3):
            try:
                df = yf.download(ticker, period="2y", interval="1d",
                                 progress=False, auto_adjust=True)
                if df is not None and len(df) >= 300:
                    break
                time.sleep(1)
            except:
                time.sleep(2)

        if df is None or len(df) < 300:
            return None, "Yeterli veri yok"

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        low    = df["Low"].squeeze()
        volume = df["Volume"].squeeze()

        # ── 1. EMA 20/50/200 ─────────────────────────────
        ema20  = tv_ema(close, 20)
        ema50  = tv_ema(close, 50)
        ema200 = tv_ema(close, 200)
        cond_ema = bool(
            close.iloc[-1] > ema20.iloc[-1] and
            ema20.iloc[-1] > ema50.iloc[-1] and
            ema50.iloc[-1] > ema200.iloc[-1]
        )

        # ── 2. RSI (14) ───────────────────────────────────
        rsi_val  = tv_rsi(close, 14)
        rsi_last = float(rsi_val.iloc[-1])
        cond_rsi = bool(50 < rsi_last < 70)

        # ── 3. MACD (12/26/9) ─────────────────────────────
        ml, ms, mh = tv_macd(close, 12, 26, 9)
        cond_macd  = bool(
            ml.iloc[-1] > ms.iloc[-1] and
            mh.iloc[-1] > 0 and
            mh.iloc[-1] > mh.iloc[-2]      # Histogram genişliyor
        )

        # ── 4. Hacim / OBV ────────────────────────────────
        vol_ma = volume.rolling(20).mean()
        # OBV hesabı (Pine Script uyumlu)
        obv_step = pd.Series(np.where(
            close > close.shift(1), volume,
            np.where(close < close.shift(1), -volume, 0)
        ), index=close.index)
        obv = obv_step.cumsum()
        cond_vol = bool(
            obv.iloc[-1] > obv.iloc[-2] and
            obv.iloc[-2] > obv.iloc[-3] and
            volume.iloc[-1] > vol_ma.iloc[-1]
        )

        # ── 5. Bollinger Bantları ─────────────────────────
        bb_mid, bb_upper, bb_lower = tv_bb(close, 20, 2.0)
        bb_w   = (bb_upper - bb_lower) / bb_mid
        bb_pct = bb_w.rank(pct=True) * 100          # Percentrank yaklaşımı
        bb_sq  = bool(bb_pct.iloc[-1] < 20)
        cond_bb = bool(
            close.iloc[-1] > bb_mid.iloc[-1] and
            (not bb_sq or close.iloc[-1] > bb_upper.iloc[-2])
        )

        # ── 6. Supertrend (10, 3.0) ───────────────────────
        st_line, st_dir = tv_supertrend(df, periyot=10, faktor=3.0)
        cond_st = bool(st_dir.iloc[-1] == 1)   # 1 = boğa

        # ── 7. Ichimoku ───────────────────────────────────
        tenkan, kijun, spanA, spanB, kumo_top, kumo_bot = tv_ichimoku(df)
        price_above_kumo = bool(close.iloc[-1] > kumo_top.iloc[-1])
        tk_bull          = bool(tenkan.iloc[-1] > kijun.iloc[-1])
        chikou_bull      = bool(close.iloc[-1] > close.iloc[-27])  # 26 bar geri
        ich_score        = sum([price_above_kumo, tk_bull, chikou_bull])
        cond_ich         = ich_score >= 2

        # ── MASTER SKOR ───────────────────────────────────
        skor = sum([cond_ema, cond_rsi, cond_macd, cond_vol,
                    cond_bb, cond_st, cond_ich])

        # ── ATR ile stop/hedef seviyeleri ─────────────────
        atr_val = tv_atr(df, 14)
        N       = float(atr_val.iloc[-1])
        fyt     = float(close.iloc[-1])
        sl      = fyt - N * 2.0
        tp1     = fyt + N * 2.0
        tp2     = fyt + N * 4.0
        tp3     = fyt + N * 7.0

        eksik = []
        if not cond_ema:  eksik.append("EMA")
        if not cond_rsi:  eksik.append(f"RSI({rsi_last:.0f})")
        if not cond_macd: eksik.append("MACD")
        if not cond_vol:  eksik.append("Hacim/OBV")
        if not cond_bb:   eksik.append("Bollinger")
        if not cond_st:   eksik.append("Supertrend")
        if not cond_ich:  eksik.append(f"Ichimoku({ich_score}/3)")

        return {
            "ticker"   : ticker,
            "piyasa"   : piyasa,
            "skor"     : skor,
            "al"       : skor >= 5,
            "cik"      : skor <= 3,
            "giris"    : round(fyt, 2),
            "sl"       : round(sl, 2),
            "tp1"      : round(tp1, 2),
            "tp2"      : round(tp2, 2),
            "tp3"      : round(tp3, 2),
            "atr"      : round(N, 2),
            "rsi"      : round(rsi_last, 1),
            "mktcap"   : mktcap,
            "cond_ema" : cond_ema,
            "cond_rsi" : cond_rsi,
            "cond_macd": cond_macd,
            "cond_vol" : cond_vol,
            "cond_bb"  : cond_bb,
            "cond_st"  : cond_st,
            "cond_ich" : cond_ich,
            "ich_score": ich_score,
            "bb_squeeze": bb_sq,
            "eksik"    : eksik
        }, None

    except Exception as e:
        return None, str(e)

# ══════════════════════════════════════════════
# MESAJ FORMATLARI
# ══════════════════════════════════════════════

def onay_satiri(ok, isim):
    return ("✅" if ok else "❌") + " " + isim

def al_mesaji(s, sinyal_id=None):
    bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
    para   = "₺" if s["piyasa"] == "BIST" else "$"
    cap    = f"{s['mktcap']/1e9:.1f}B {para}" if s.get("mktcap", 0) > 0 else "?"
    sid    = f"\n🔢 Sinyal ID: #{sinyal_id}" if sinyal_id else ""
    win    = 91 if s["skor"]==7 else 78 if s["skor"]==6 else 62 if s["skor"]==5 else 48
    return (
        f"{bayrak} <b>{s['ticker']}</b> [{s['piyasa']}] 🚀 AL SİNYALİ{sid}\n"
        f"💹 Piyasa Değeri: {cap}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Onay: <b>{s['skor']}/7</b>  |  🎯 Kazanma: ~%{win}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{onay_satiri(s['cond_ema'],  'EMA 20/50/200')}\n"
        f"{onay_satiri(s['cond_rsi'],  f'RSI ({s[\"rsi\"]})')}\n"
        f"{onay_satiri(s['cond_macd'], 'MACD Histogram')}\n"
        f"{onay_satiri(s['cond_vol'],  'Hacim / OBV')}\n"
        f"{onay_satiri(s['cond_bb'],   'Bollinger BB')}\n"
        f"{onay_satiri(s['cond_st'],   'Supertrend')}\n"
        f"{onay_satiri(s['cond_ich'],  f'Ichimoku ({s[\"ich_score\"]}/3)')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Giriş: {para}{s['giris']}\n"
        f"🛑 S/L:   {para}{s['sl']}\n"
        f"🎯 TP1:  {para}{s['tp1']}  (+2R)\n"
        f"🎯 TP2:  {para}{s['tp2']}  (+4R)\n"
        f"🎯 TP3:  {para}{s['tp3']}  (+7R)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 BIST Master v4.0\n"
        f"⚠️ Yatırım tavsiyesi değildir."
    )

def cik_mesaji(s, giris_fiyat=None):
    bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
    para   = "₺" if s["piyasa"] == "BIST" else "$"
    pnl_str = ""
    if giris_fiyat:
        pnl = (s["giris"] - giris_fiyat) / giris_fiyat * 100
        pnl_str = f"\n📊 Tahmini PnL: {'+'if pnl>=0 else''}{pnl:.1f}%"
    return (
        f"{bayrak} <b>{s['ticker']}</b> ⛔ ÇIK SİNYALİ\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Onay: <b>{s['skor']}/7</b> — Eşik altına düştü (≤3)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{onay_satiri(s['cond_ema'],  'EMA 20/50/200')}\n"
        f"{onay_satiri(s['cond_rsi'],  f'RSI ({s[\"rsi\"]})')}\n"
        f"{onay_satiri(s['cond_macd'], 'MACD Histogram')}\n"
        f"{onay_satiri(s['cond_vol'],  'Hacim / OBV')}\n"
        f"{onay_satiri(s['cond_bb'],   'Bollinger BB')}\n"
        f"{onay_satiri(s['cond_st'],   'Supertrend')}\n"
        f"{onay_satiri(s['cond_ich'],  f'Ichimoku ({s[\"ich_score\"]}/3)')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Mevcut Fiyat: {para}{s['giris']}{pnl_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⛔ Pozisyonu kapat!"
    )

def detay_mesaji(s):
    bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
    para   = "₺" if s["piyasa"] == "BIST" else "$"
    durum  = "🚀 AL SİNYALİ" if s["al"] else ("⛔ ÇIK" if s["cik"] else "⏳ BEKLE")
    cap    = f"{s['mktcap']/1e9:.1f}B {para}" if s.get("mktcap", 0) > 0 else "?"
    return (
        f"{bayrak} <b>{s['ticker']}</b> [{s['piyasa']}]\n"
        f"💹 Piyasa Değeri: {cap}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Skor: <b>{s['skor']}/7</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{onay_satiri(s['cond_ema'],  'EMA 20/50/200')}\n"
        f"{onay_satiri(s['cond_rsi'],  f'RSI ({s[\"rsi\"]})')}\n"
        f"{onay_satiri(s['cond_macd'], 'MACD Histogram')}\n"
        f"{onay_satiri(s['cond_vol'],  'Hacim / OBV')}\n"
        f"{onay_satiri(s['cond_bb'],   'Bollinger BB')}\n"
        f"{onay_satiri(s['cond_st'],   'Supertrend')}\n"
        f"{onay_satiri(s['cond_ich'],  f'Ichimoku ({s[\"ich_score\"]}/3)')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Fiyat: {para}{s['giris']}\n"
        f"🛑 S/L:   {para}{s['sl']}\n"
        f"🎯 TP1:  {para}{s['tp1']}\n"
        f"🎯 TP2:  {para}{s['tp2']}\n"
        f"🎯 TP3:  {para}{s['tp3']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{durum}"
        + (f"\nEksik: {', '.join(s['eksik'])}" if not s['al'] and s['eksik'] else "")
    )

# ══════════════════════════════════════════════
# İSTATİSTİK
# ══════════════════════════════════════════════

def istatistik_hesapla(gun_limit=None):
    db        = db_oku()
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
        "toplam"  : toplam,
        "kazanan" : kazanan,
        "kaybeden": kaybeden,
        "acik"    : len(acik),
        "wr"      : round(wr, 1),
        "net_pnl" : round(net_pnl, 2),
        "portfoy" : round(100000 + net_pnl, 2)
    }

def istatistik_mesaji(donem, gun_limit=None):
    st = istatistik_hesapla(gun_limit)
    if not st:
        return f"📊 <b>{donem}</b>\n\nHenüz kapalı işlem yok."
    pnl_emoji = "📈" if st["net_pnl"] >= 0 else "📉"
    durum = "KARDA" if st["net_pnl"] >= 0 else "ZARARDA"
    return (
        f"📊 <b>İSTATİSTİK — {donem}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Toplam: {st['toplam']} işlem\n"
        f"✅ WIN:  {st['kazanan']} işlem\n"
        f"❌ LOSS: {st['kaybeden']} işlem\n"
        f"🔄 Açık: {st['acik']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Kazanma Oranı: %{st['wr']}\n"
        f"{pnl_emoji} Net P&L: {'+'if st['net_pnl']>=0 else''}${st['net_pnl']:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💼 Portföy: ${st['portfoy']:,.2f} / $100.000\n"
        f"{'📈' if st['portfoy']>=100000 else '📉'} Sistem <b>{durum}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 BIST Master v4.0"
    )

def acik_pozisyonlar_mesaji():
    db      = db_oku()
    aciklar = [s for s in db["sinyaller"] if s["durum"] == "ACIK"]
    if not aciklar:
        return "📋 <b>Açık Pozisyon Yok</b>"
    mesaj = f"📋 <b>AÇIK POZİSYONLAR ({len(aciklar)} adet)</b>\n━━━━━━━━━━━━━━━━━━━\n"
    for s in aciklar[-10:]:
        bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
        para   = "₺" if s["piyasa"] == "BIST" else "$"
        mesaj += (
            f"{bayrak} <b>{s['ticker']}</b> | {para}{s['giris']} → SL:{para}{s['sl']}\n"
            f"   TP1:{para}{s['tp1']} | {s['tarih']} | Skor:{s['skor']}/7\n\n"
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
# HİSSE LİSTELERİ
# ══════════════════════════════════════════════
BIST_HISSELER = list(dict.fromkeys([
    "AKBNK.IS","ARCLK.IS","ASELS.IS","BIMAS.IS","DOHOL.IS",
    "EKGYO.IS","ENKAI.IS","EREGL.IS","FROTO.IS","GARAN.IS",
    "HALKB.IS","ISCTR.IS","KCHOL.IS","KOZAL.IS","KRDMD.IS",
    "MGROS.IS","PETKM.IS","SAHOL.IS","SISE.IS","TAVHL.IS",
    "TCELL.IS","THYAO.IS","TKFEN.IS","TOASO.IS","TTKOM.IS",
    "TUPRS.IS","VAKBN.IS","VESTL.IS","YKBNK.IS","AKSEN.IS",
    "ALARK.IS","ALCTL.IS","ALFAS.IS","ALGYO.IS","ALKIM.IS",
    "ANELE.IS","AEFES.IS","AGESA.IS","AGHOL.IS","AGYO.IS",
    "AHGAZ.IS","AHSGY.IS","AKFGY.IS","AKFYE.IS","AKGRT.IS",
    "AKMGY.IS","AKSA.IS","AKSEL.IS","AKSGY.IS","ALBRK.IS",
    "ALCAR.IS","ALDGI.IS","ANGEN.IS","ARSAN.IS","ARZUM.IS",
    "ASUZU.IS","ATLAS.IS","AVGYO.IS","AVHOL.IS","AVOD.IS",
    "AVTUR.IS","AYCES.IS","AYES.IS","AZTEK.IS","CMENT.IS",
    "DGNMO.IS","DMRGD.IS","DNISI.IS","DURDO.IS","DZGYO.IS",
    "EGSER.IS","EMPIN.IS","ESCAR.IS","GARFA.IS","GLRYH.IS",
    "IEYHO.IS","KCAER.IS","KZBGY.IS","OBAMS.IS","OSTIM.IS",
    "PKENT.IS","QUAGR.IS","RALYH.IS","RUBNS.IS","SAMAT.IS",
    "SANKO.IS","SEKUR.IS","SKBNK.IS","SUMAS.IS","SURGY.IS",
    "TGSAS.IS","TURGG.IS","VAKFN.IS","VAKKO.IS","ZOREN.IS","RGYO.IS"
]))

ABD_HISSELER = list(dict.fromkeys([
    "AAPL","MSFT","NVDA","AVGO","ORCL","CRM","ADBE","AMD","QCOM","TXN",
    "MU","AMAT","LRCX","KLAC","MRVL","PANW","CRWD","ZS","FTNT","NET",
    "SNOW","DDOG","MDB","GTLB","PLTR","UBER","LYFT","ABNB","SHOP","SQ",
    "PYPL","COIN","HOOD","SOFI","AFRM","UPST","LC","JPM","BAC","WFC",
    "GS","MS","C","BLK","SCHW","AXP","V","MA","SPGI","MCO","ICE","CME",
    "LLY","ABBV","MRK","JNJ","ABT","MDT","SYK","BSX","EW","ISRG","VRTX",
    "REGN","BIIB","GILD","MRNA","PFE","BMY","AMGN","XOM","CVX","COP",
    "EOG","SLB","HAL","BKR","PSX","VLO","MPC","WMB","KMI","OKE",
    "AMZN","TSLA","HD","LOW","TGT","TJX","COST","WMT","SBUX","MCD",
    "BKNG","EXPE","MAR","HLT","DIS","NFLX","META","GOOGL","GOOG",
    "INTC","CSCO","IBM","HPQ","DELL","EMR","HON","GE","MMM","CAT",
    "DE","BA","LMT","RTX","NOC","GD","TSMC","TSM"
]))

# ══════════════════════════════════════════════
# TARAMA FONKSİYONLARI
# ══════════════════════════════════════════════
gonderilen_al_sinyalleri  = set()   # Aynı gün tekrar AL gönderme
gonderilen_cik_sinyalleri = set()   # Aynı gün tekrar ÇIK gönderme

def al_taramasi(hisseler, baslik):
    """5/7 ve üzeri skor → AL sinyali gönder"""
    global gonderilen_al_sinyalleri, tarama_aktif
    with tarama_kilidi:
        if tarama_aktif:
            print(f"{baslik} taraması zaten çalışıyor, atlanıyor.")
            return
        tarama_aktif = True

    try:
        simdi        = datetime.now().strftime("%d.%m.%Y %H:%M")
        bugun        = datetime.now().strftime("%Y-%m-%d")
        sinyaller    = []
        kullanicilar = aktif_kullanicilar()

        print(f"\n{'='*50}")
        print(f"AL TARAMASI — {baslik} — {len(hisseler)} hisse — {simdi}")
        print(f"{'='*50}")

        for ticker in hisseler:
            print(f"  {ticker}...", end=" ", flush=True)
            sonuc, hata = hisse_analiz(ticker)
            if sonuc and sonuc["al"]:
                anahtar = f"AL_{ticker}_{bugun}"
                if anahtar not in gonderilen_al_sinyalleri:
                    sinyaller.append(sonuc)
                    gonderilen_al_sinyalleri.add(anahtar)
                    print(f"★ AL! {sonuc['skor']}/7 RSI:{sonuc['rsi']}")
                else:
                    print("(zaten gönderildi)")
            else:
                eksik = hata or (", ".join(sonuc["eksik"][:2]) if sonuc else "?")
                print(f"- [{eksik}]")
            time.sleep(0.3)

        print(f"\n{baslik}: {len(sinyaller)} AL sinyali")

        if not sinyaller:
            return

        ozet = (
            f"🚀 <b>{baslik} — {len(sinyaller)} AL SİNYALİ</b>\n"
            f"⏰ {simdi}\n"
            f"🔥 BIST Master v4.0 | Eşik: 5/7"
        )
        for cid in kullanicilar:
            telegram_gonder(ozet, cid)
        time.sleep(1)

        for s in sinyaller:
            sid   = sinyal_kaydet(s["ticker"], s["piyasa"], s["giris"],
                                  s["sl"], s["tp1"], s["tp2"], s["tp3"],
                                  s["atr"], s["skor"])
            mesaj = al_mesaji(s, sid)
            for cid in kullanicilar:
                telegram_gonder_butonlu(
                    mesaj, cid, s["ticker"],
                    s["giris"], s["sl"], s["tp1"], sid
                )
                time.sleep(0.3)
            time.sleep(0.5)

    finally:
        with tarama_kilidi:
            tarama_aktif = False

def cik_taramasi():
    """
    Açık pozisyonları kontrol eder.
    Skor 3/7 veya altına düşerse ÇIK sinyali gönderir.
    """
    global gonderilen_cik_sinyalleri

    db      = db_oku()
    aciklar = db.get("acik_pozisyonlar", {})

    if not aciklar:
        print("ÇIK taraması: açık pozisyon yok.")
        return

    bugun        = datetime.now().strftime("%Y-%m-%d")
    simdi        = datetime.now().strftime("%d.%m.%Y %H:%M")
    kullanicilar = aktif_kullanicilar()
    cik_listesi  = []

    print(f"\n{'='*50}")
    print(f"ÇIK TARAMASI — {len(aciklar)} açık pozisyon — {simdi}")
    print(f"{'='*50}")

    for ticker, sid in list(aciklar.items()):
        print(f"  {ticker}...", end=" ", flush=True)
        anahtar = f"CIK_{ticker}_{bugun}"
        if anahtar in gonderilen_cik_sinyalleri:
            print("(bugün zaten gönderildi)")
            continue

        sonuc, hata = hisse_analiz(ticker)
        if sonuc and sonuc["cik"]:
            # Giriş fiyatını bul
            giris_fiyat = None
            for s in db["sinyaller"]:
                if s["id"] == sid:
                    giris_fiyat = s["giris"]
                    break
            cik_listesi.append((sonuc, giris_fiyat))
            gonderilen_cik_sinyalleri.add(anahtar)
            print(f"⛔ ÇIK! {sonuc['skor']}/7")
        else:
            puan = sonuc["skor"] if sonuc else "?"
            print(f"OK ({puan}/7)")
        time.sleep(0.4)

    if not cik_listesi:
        print("ÇIK taraması: ÇIK sinyali yok.")
        return

    ozet = (
        f"⛔ <b>ÇIK UYARISI — {len(cik_listesi)} pozisyon</b>\n"
        f"⏰ {simdi}\n"
        f"⚠️ Skor ≤3/7 eşiğine düştü!"
    )
    for cid in kullanicilar:
        telegram_gonder(ozet, cid)
    time.sleep(1)

    for sonuc, giris_fiyat in cik_listesi:
        mesaj = cik_mesaji(sonuc, giris_fiyat)
        for cid in kullanicilar:
            telegram_gonder(mesaj, cid)
            time.sleep(0.3)
        time.sleep(0.5)

# ══════════════════════════════════════════════
# ZAMANLANMIŞ GÖREVLER
# ══════════════════════════════════════════════
# BIST: Her 2 saatte 1 (10:30 - 18:30)
def bist_al():
    threading.Thread(
        target=al_taramasi,
        args=(BIST_HISSELER, "BIST"),
        daemon=True
    ).start()

# ABD: Açılış ortası (16:30 TR) ve kapanış (23:00 TR)
def abd_al():
    threading.Thread(
        target=al_taramasi,
        args=(ABD_HISSELER, "ABD"),
        daemon=True
    ).start()

# ÇIK taraması: Her 2 saatte 1
def cik_tara():
    threading.Thread(target=cik_taramasi, daemon=True).start()

# ══════════════════════════════════════════════
# TELEGRAM DİNLEYİCİ
# ══════════════════════════════════════════════
def yardim_mesaji():
    return (
        "🤖 <b>BIST Master Sinyal Botu v4.0</b>\n\n"
        "📊 <b>7 Gösterge Sistemi:</b>\n"
        "  EMA | RSI | MACD | Hacim/OBV\n"
        "  Bollinger | Supertrend | Ichimoku\n\n"
        "🟢 AL : 5/7+ onay\n"
        "🔴 ÇIK: 3/7 ve altı\n\n"
        "👤 Kayıt:\n"
        "/basla — Sisteme katıl\n\n"
        "📈 Analiz:\n"
        "THYAO veya AAPL — Tek hisse analiz\n\n"
        "🔍 Manuel Tarama:\n"
        "/bist — BIST AL taraması\n"
        "/abd  — ABD AL taraması\n"
        "/cik  — ÇIK taraması (açık poz.)\n\n"
        "📊 İstatistik:\n"
        "/stat_hafta  — Haftalık\n"
        "/stat_ay     — Aylık\n"
        "/stat_tum    — Tüm zamanlar\n\n"
        "💼 Portföy:\n"
        "/acik   — Açık pozisyonlar\n"
        "/gecmis — Son 10 işlem\n"
        "/kapat THYAO 395.50\n\n"
        "⏰ Otomatik:\n"
        "  BIST AL: 10:30/12:30/14:30/16:30/18:30\n"
        "  ABD  AL: 16:30/23:00\n"
        "  ÇIK kontrol: Her 2 saatte 1\n"
    )

def mesaji_isle(metin, chat_id, isim):
    cmd = metin.strip().upper()

    if cmd in ["/BASLA", "/START"]:
        yeni = kullanici_ekle(chat_id, isim)
        if yeni:
            telegram_gonder(
                f"✅ <b>Hoş geldin {isim}!</b>\n\n"
                f"BIST Master v4.0'a kayıt oldun.\n"
                f"🟢 AL sinyalleri: 5/7+ onay\n"
                f"🔴 ÇIK sinyalleri: ≤3/7\n\n"
                f"/yardim — Tüm komutlar", chat_id)
        else:
            telegram_gonder(f"✅ Zaten kayıtlısın {isim}!\n/yardim", chat_id)
        return

    if cmd in ["/YARDIM", "/HELP"]:
        telegram_gonder(yardim_mesaji(), chat_id)
        return

    # İstatistik
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

    # Portföy
    if cmd == "/ACIK":
        telegram_gonder(acik_pozisyonlar_mesaji(), chat_id)
        return
    if cmd == "/GECMIS":
        telegram_gonder(gecmis_mesaji(), chat_id)
        return

    # Manuel tarama
    if cmd == "/BIST":
        telegram_gonder("🇹🇷 BIST AL taraması başlatılıyor...", chat_id)
        threading.Thread(target=bist_al, daemon=True).start()
        return
    if cmd == "/ABD":
        telegram_gonder("🇺🇸 ABD AL taraması başlatılıyor...", chat_id)
        threading.Thread(target=abd_al, daemon=True).start()
        return
    if cmd == "/CIK":
        telegram_gonder("⛔ ÇIK taraması başlatılıyor...", chat_id)
        threading.Thread(target=cik_tara, daemon=True).start()
        return
    if cmd == "/TARA":
        telegram_gonder("🔍 Tüm piyasalar taranıyor...", chat_id)
        threading.Thread(target=bist_al, daemon=True).start()
        threading.Thread(target=abd_al, daemon=True).start()
        return

    if cmd == "/LISTE":
        telegram_gonder(
            f"📊 <b>Tarama Listesi</b>\n\n"
            f"🇹🇷 BIST: {len(BIST_HISSELER)} hisse\n"
            f"🇺🇸 ABD: {len(ABD_HISSELER)} hisse\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 AL eşiği: 5/7+\n"
            f"🔴 ÇIK eşiği: ≤3/7", chat_id)
        return

    # /kapat THYAO 395.50
    if cmd.startswith("/KAPAT"):
        parcalar = metin.strip().split()
        if len(parcalar) < 3:
            telegram_gonder("Kullanım: /kapat THYAO 395.50", chat_id)
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
        sid = dbx["acik_pozisyonlar"][tk]
        gf  = 0
        for sx in dbx["sinyaller"]:
            if sx["id"] == sid:
                gf = sx["giris"]
                break
        pozisyon_kapat(tk, "KAPANDI", fk)
        kz  = (fk - gf) / gf * 1000 if gf > 0 else 0
        pnl = ("+" if kz >= 0 else "") + "$" + str(round(kz, 2))
        telegram_gonder(
            ("✅ KAR" if kz >= 0 else "❌ ZARAR") +
            f" | {tk} | Giriş:{gf} → Çıkış:{fk} | PnL:{pnl}", chat_id)
        return

    # Tek hisse analizi
    ticker = cmd.replace("/", "").strip()
    if len(ticker) < 2:
        return

    telegram_gonder(f"🔍 <b>{ticker}</b> analiz ediliyor...", chat_id)
    sonuc, hata = hisse_analiz(ticker)
    if sonuc is None and not ticker.endswith(".IS"):
        sonuc, hata = hisse_analiz(ticker + ".IS")
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
    # Eski mesajları atla
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
            mesajlar = telegram_mesajlari_al()
            for m in mesajlar:
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
# ANA PROGRAM
# ══════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("BIST MASTER SİNYAL BOTU v4.0")
    print("=" * 60)
    print("7 Gösterge: EMA | RSI | MACD | OBV | BB | ST | Ichimoku")
    print(f"AL eşiği: 5/7+    ÇIK eşiği: ≤3/7")
    print(f"BIST: {len(BIST_HISSELER)} hisse")
    print(f"ABD:  {len(ABD_HISSELER)} hisse")
    print("Durdurmak: CTRL+C\n")

    kullanici_ekle(TELEGRAM_CHAT_ID, "Admin")

    telegram_gonder(
        "✅ <b>BIST Master Bot v4.0 Aktif!</b>\n\n"
        f"🇹🇷 BIST: {len(BIST_HISSELER)} hisse\n"
        f"🇺🇸 ABD: {len(ABD_HISSELER)} hisse\n\n"
        "📊 <b>7 Gösterge Sistemi:</b>\n"
        "  EMA 20/50/200\n"
        "  RSI (14) — 50-70\n"
        "  MACD (12/26/9)\n"
        "  Hacim / OBV\n"
        "  Bollinger Bantları\n"
        "  Supertrend (10, 3.0)\n"
        "  Ichimoku (Kumo+TK+Chikou)\n\n"
        "🟢 AL: 5/7+ onay\n"
        "🔴 ÇIK: ≤3/7\n\n"
        "⏰ <b>Otomatik Tarama:</b>\n"
        "  BIST AL: 10:30/12:30/14:30/16:30/18:30\n"
        "  ABD  AL: 16:30 / 23:00\n"
        "  ÇIK kontrol: Her 2 saatte 1\n\n"
        "/basla — Kayıt\n"
        "/yardim — Komutlar"
    )

    # ── BIST AL taraması: Her 2 saatte 1, piyasa saatlerinde ──
    schedule.every().day.at("10:30").do(bist_al)
    schedule.every().day.at("12:30").do(bist_al)
    schedule.every().day.at("14:30").do(bist_al)
    schedule.every().day.at("16:30").do(bist_al)
    schedule.every().day.at("18:30").do(bist_al)

    # ── ABD AL taraması: Açılış ortası + kapanış ──
    # ABD borsası 16:30 TR'de açılır, 23:00 TR'de kapanır
    schedule.every().day.at("16:30").do(abd_al)   # Açılış ortası (tam açılışta)
    schedule.every().day.at("19:45").do(abd_al)   # Seansın ortası
    schedule.every().day.at("23:00").do(abd_al)   # Kapanışa 30 dk

    # ── ÇIK taraması: Her 2 saatte 1 ──
    schedule.every().day.at("09:30").do(cik_tara)
    schedule.every().day.at("11:30").do(cik_tara)
    schedule.every().day.at("13:30").do(cik_tara)
    schedule.every().day.at("15:30").do(cik_tara)
    schedule.every().day.at("17:30").do(cik_tara)
    schedule.every().day.at("19:30").do(cik_tara)
    schedule.every().day.at("21:30").do(cik_tara)
    schedule.every().day.at("23:30").do(cik_tara)

    # Telegram dinleyici
    threading.Thread(target=telegram_dinle, daemon=True).start()

    print("Bot çalışıyor. CTRL+C ile dur.\n")
    while True:
        schedule.run_pending()
        time.sleep(30)
