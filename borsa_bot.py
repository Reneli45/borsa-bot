"""
ULS + Fibonacci EMA Borsa Tarama Botu v4
- BIST: ~100 hisse (BIST100), 10B TL+, 18:30
- ABD S&P500+: 314 hisse, 10B $+, 23:30
- TradingView uyumlu: Wilder ATR/ADX/RSI
- Telegram manuel sorgulama
"""
 
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import time
import schedule
import threading
 
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
 
# ══════════════════════════════════════════════
# TRADINGVIEW UYUMLU HESAPLAMALAR
# ══════════════════════════════════════════════
def tv_ema(seri, periyot):
    return seri.ewm(span=periyot, adjust=False).mean()
 
def tv_rma(seri, periyot):
    alpha  = 1.0 / periyot
    result = seri.copy().astype(float) * np.nan
    result.iloc[periyot - 1] = seri.iloc[:periyot].mean()
    for i in range(periyot, len(seri)):
        result.iloc[i] = alpha * seri.iloc[i] + (1 - alpha) * result.iloc[i - 1]
    return result
 
def tv_atr(df, periyot=14):
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()
    prev  = close.shift(1)
    tr    = pd.concat([high-low,(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1)
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
    tr     = pd.concat([high-low,(high-prev_c).abs(),(low-prev_c).abs()],axis=1).max(axis=1)
    up     = high - high.shift(1)
    down   = low.shift(1) - low
    pdm    = up.where((up > down) & (up > 0), 0.0)
    mdm    = down.where((down > up) & (down > 0), 0.0)
    atr_w  = tv_rma(tr, periyot)
    pdi    = 100 * tv_rma(pdm, periyot) / atr_w
    mdi    = 100 * tv_rma(mdm, periyot) / atr_w
    dx     = 100 * (pdi-mdi).abs() / (pdi+mdi).replace(0, np.nan)
    return pdi, mdi, tv_rma(dx, periyot)
 
def tv_macd(close, fast=12, slow=26, signal=9):
    ml = tv_ema(close, fast) - tv_ema(close, slow)
    ms = tv_ema(ml, signal)
    return ml, ms, ml - ms
 
# ══════════════════════════════════════════════
# HISSE ANALİZ
# ══════════════════════════════════════════════
 
def turtle_analiz(ticker):
    """Saf Richard Dennis Turtle - sadece 40G kirilim + ATR stop"""
    try:
        piyasa  = "BIST" if ticker.endswith(".IS") else "ABD"
        min_cap = 10_000_000_000
        try:
            bilgi  = yf.Ticker(ticker).info
            mktcap = bilgi.get("marketCap", 0) or 0
        except:
            mktcap = 0
        if mktcap > 0 and mktcap < min_cap:
            return None, f"Kucuk sirket"
 
        df = None
        for deneme in range(3):
            try:
                df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
                if df is not None and len(df) >= 60:
                    break
                time.sleep(1)
            except:
                time.sleep(2)
 
        if df is None or len(df) < 60:
            return None, "Yeterli veri yok"
 
        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
 
        # 40 gunluk Donchian - onceki bar [1]
        don_hi40 = high.rolling(40).max().shift(1)
        don_lo40 = low.rolling(40).min().shift(1)
        # Cikis: 20 gunluk dusuk
        don_ex20 = low.rolling(20).min().shift(1)
 
        # Kirilim kontrolu
        brkout = bool(close.iloc[-1] > don_hi40.iloc[-1])
        if not brkout:
            return None, "Kirilim yok"
 
        # ATR - Wilder RMA
        N_s  = tv_atr(df, 20)
        N    = float(N_s.iloc[-1])
        risk = N * 2.0
        fyt  = float(close.iloc[-1])
 
        # Dennis'in orijinal seviyeleri
        sl   = fyt - risk
        tp1  = fyt + risk * 1.5
        tp2  = fyt + risk * 3.0
        tp3  = fyt + risk * 5.0
 
        # 40G yuksek ve onceki kapanisla mesafe
        mesafe = (fyt - float(don_hi40.iloc[-1])) / fyt * 100
 
        return {
            "ticker"  : ticker,
            "piyasa"  : piyasa,
            "giris"   : round(fyt, 2),
            "sl"      : round(sl, 2),
            "tp1"     : round(tp1, 2),
            "tp2"     : round(tp2, 2),
            "tp3"     : round(tp3, 2),
            "atr"     : round(N, 2),
            "don_hi"  : round(float(don_hi40.iloc[-1]), 2),
            "don_ex"  : round(float(don_ex20.iloc[-1]), 2),
            "mesafe"  : round(mesafe, 2),
            "mktcap"  : mktcap,
        }, None
 
    except Exception as e:
        return None, str(e)
 
def hisse_analiz(ticker):
    try:
        piyasa  = "BIST" if ticker.endswith(".IS") else "ABD"
        min_cap = 10_000_000_000
        try:
            bilgi  = yf.Ticker(ticker).info
            mktcap = bilgi.get("marketCap", 0) or 0
        except:
            mktcap = 0
        if mktcap > 0 and mktcap < min_cap:
            return None, f"Kucuk sirket ({mktcap/1e9:.1f}B)"
 
        df = None
        for deneme in range(3):
            try:
                df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
                if df is not None and len(df) >= 250:
                    break
                time.sleep(1)
            except:
                time.sleep(2)
        if df is None or len(df) < 250:
            return None, "Yeterli veri yok"
 
        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
 
        ema200  = tv_ema(close, 200)
        trendOK = bool(close.iloc[-1] > ema200.iloc[-1])
 
        _, _, adx_s = tv_dmi(df, 14)
        adx_val = float(adx_s.iloc[-1])
        adxOK   = adx_val >= 15
 
        rsi_s   = tv_rsi(close, 14)
        rsi_val = float(rsi_s.iloc[-1])
        rsiOK   = 45 <= rsi_val <= 80
 
        ml, ms, mh = tv_macd(close, 12, 26, 9)
        macdOK = bool(ml.iloc[-1] > ms.iloc[-1]) and bool(mh.iloc[-1] > 0)
 
        don_hi = high.rolling(40).max().shift(1)
        brkout = bool(close.iloc[-1] > don_hi.iloc[-1])
 
        N_s  = tv_atr(df, 20)
        N    = float(N_s.iloc[-1])
        risk = N * 2.0
        fyt  = float(close.iloc[-1])
        sl   = fyt - risk
        tp1  = fyt + risk * 1.5
        tp2  = fyt + risk * 3.0
        tp3  = fyt + risk * 5.0
 
        emalar = [float(tv_ema(close, p).iloc[-1]) for p in [5,8,13,34,55,89,144,233]]
        fib    = sum(emalar[i] > emalar[i+1] for i in range(7))
        fibOK  = fib >= 5
 
        master    = trendOK and adxOK and rsiOK and macdOK and brkout and fibOK
        uls_score = sum([trendOK, adxOK, rsiOK, macdOK, brkout])
 
        eksik = []
        if not trendOK: eksik.append("EMA200")
        if not adxOK:   eksik.append(f"ADX({adx_val:.0f})<15")
        if not rsiOK:   eksik.append(f"RSI({rsi_val:.0f}) 45-80 olmali")
        if not macdOK:  eksik.append("MACD")
        if not brkout:  eksik.append("Turtle")
        if not fibOK:   eksik.append(f"Fib({fib}/7)")
 
        return {
            "ticker":ticker,"piyasa":piyasa,"master":master,
            "giris":round(fyt,2),"sl":round(sl,2),
            "tp1":round(tp1,2),"tp2":round(tp2,2),"tp3":round(tp3,2),
            "rsi":round(rsi_val,1),"adx":round(adx_val,1),
            "fib":fib,"uls":uls_score,"atr":round(N,2),
            "mktcap":mktcap,
            "trendOK":trendOK,"adxOK":adxOK,"rsiOK":rsiOK,
            "macdOK":macdOK,"brkout":brkout,"fibOK":fibOK,
            "eksik":eksik
        }, None
    except Exception as e:
        return None, str(e)
 
# ══════════════════════════════════════════════
# HİSSE LİSTELERİ
# ══════════════════════════════════════════════
BIST_HISSELER = [
    # BIST 30
    "AKBNK.IS","ARCLK.IS","ASELS.IS","BIMAS.IS","DOHOL.IS",
    "EKGYO.IS","ENKAI.IS","EREGL.IS","FROTO.IS","GARAN.IS",
    "HALKB.IS","ISCTR.IS","KCHOL.IS","KOZAL.IS","KRDMD.IS",
    "MGROS.IS","PETKM.IS","PGSUS.IS","SAHOL.IS","SASA.IS",
    "SISE.IS","TAVHL.IS","TCELL.IS","THYAO.IS","TKFEN.IS",
    "TOASO.IS","TTKOM.IS","TUPRS.IS","VAKBN.IS","YKBNK.IS",
    # BIST 50
    "AEFES.IS","AGESA.IS","AKSEN.IS","ALARK.IS","AYGAZ.IS",
    "BRYAT.IS","CCOLA.IS","CIMSA.IS","CLEBI.IS","GUBRF.IS",
    "ISGYO.IS","KARSN.IS","KOZAA.IS","LOGO.IS","MAVI.IS",
    "ODAS.IS","OTKAR.IS","SARKY.IS","SOKM.IS","TSKB.IS",
    # BIST 100
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
    "ULUUN.IS","UMPAS.IS","USAK.IS","YAPRK.IS","ALFAS.IS"
]
 
ABD_HISSELER = [
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
    "YUM","CMG","DPZ","DARDEN","TXRH","NKE","LULU","SKX","CROX","DECK",
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
]
 
# ══════════════════════════════════════════════
# MESAJ FORMATLARI
# ══════════════════════════════════════════════
def sinyal_mesaji(s):
    bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
    para   = "₺" if s["piyasa"] == "BIST" else "$"
    yildiz = "⭐" * s["fib"] + "☆" * (7 - s["fib"])
    cap    = f"{s['mktcap']/1e9:.1f}B {para}" if s["mktcap"] > 0 else "?"
    return (
        f"{bayrak} <b>{s['ticker']}</b> [{s['piyasa']}]\n"
        f"💹 Piyasa Degeri: {cap}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Giris: {para}{s['giris']}\n"
        f"🛑 S/L:   {para}{s['sl']}\n"
        f"🎯 TP1:  {para}{s['tp1']}\n"
        f"🎯 TP2:  {para}{s['tp2']}\n"
        f"🎯 TP3:  {para}{s['tp3']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 RSI: {s['rsi']}  💪 ADX: {s['adx']}\n"
        f"🔢 ATR: {para}{s['atr']}\n"
        f"⭐ Fib: {yildiz} {s['fib']}/7\n"
        f"✅ ULS: {s['uls']}/5\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Yatirim tavsiyesi degildir."
    )
 
def detay_mesaji(s):
    isk    = lambda ok: "✅" if ok else "❌"
    bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
    para   = "₺" if s["piyasa"] == "BIST" else "$"
    sonuc  = "🚀 SİNYAL VAR!" if s["master"] else "❌ Sinyal yok"
    ekstra = f"\nGecmeyen: {', '.join(s['eksik'])}" if not s["master"] and s["eksik"] else ""
    cap    = f"{s['mktcap']/1e9:.1f}B {para}" if s["mktcap"] > 0 else "?"
    return (
        f"{bayrak} <b>{s['ticker']}</b> [{s['piyasa']}]\n"
        f"💹 Piyasa Degeri: {cap}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{isk(s['trendOK'])} EMA200 Trend\n"
        f"{isk(s['adxOK'])} ADX: {s['adx']}\n"
        f"{isk(s['rsiOK'])} RSI: {s['rsi']}\n"
        f"{isk(s['macdOK'])} MACD\n"
        f"{isk(s['brkout'])} Turtle 40G Kirilim\n"
        f"{isk(s['fibOK'])} Fibonacci: {s['fib']}/7\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Fiyat: {para}{s['giris']}\n"
        f"🛑 S/L:   {para}{s['sl']}\n"
        f"🎯 TP1:  {para}{s['tp1']}\n"
        f"🎯 TP2:  {para}{s['tp2']}\n"
        f"🎯 TP3:  {para}{s['tp3']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{sonuc}{ekstra}"
    )
 
# ══════════════════════════════════════════════
# TARAMA
# ══════════════════════════════════════════════
def tarama_yap(hisseler, baslik):
    simdi     = datetime.now().strftime("%d.%m.%Y %H:%M")
    sinyaller = []
    telegram_gonder(
        f"🔍 <b>{baslik} TARANIYOR</b>\n"
        f"⏰ {simdi}\n"
        f"📊 {len(hisseler)} hisse kontrol ediliyor..."
    )
    for ticker in hisseler:
        print(f"  {ticker}...", end=" ", flush=True)
        sonuc, _ = hisse_analiz(ticker)
        if sonuc and sonuc["master"]:
            sinyaller.append(sonuc)
            print("SINYAL!")
        else:
            print("-")
        time.sleep(0.3)
 
    if not sinyaller:
        telegram_gonder(f"📊 <b>{baslik}</b> tamamlandi — sinyal bulunamadi.")
        return
 
    telegram_gonder(
        f"🚀 <b>{baslik} SINYALLERI</b>\n"
        f"⏰ {simdi}\n"
        f"📊 {len(sinyaller)} sinyal bulundu!"
    )
    time.sleep(1)
    for s in sinyaller:
        telegram_gonder(sinyal_mesaji(s))
        time.sleep(0.8)
 
def bist_tarama():
    global tarama_aktif
    with tarama_kilidi:
        if tarama_aktif:
            print("Tarama zaten devam ediyor, atlandi.")
            return
        tarama_aktif = True
    try:
        print(f"\nBIST TARAMA: {datetime.now().strftime('%H:%M')}")
        tarama_yap(BIST_HISSELER, "BIST")
    finally:
        tarama_aktif = False
 
def abd_tarama():
    global tarama_aktif
    with tarama_kilidi:
        if tarama_aktif:
            print("Tarama zaten devam ediyor, atlandi.")
            return
        tarama_aktif = True
    try:
        print(f"\nABD TARAMA: {datetime.now().strftime('%H:%M')}")
        tarama_yap(ABD_HISSELER, f"ABD S&P500+ ({len(ABD_HISSELER)} hisse)")
    finally:
        tarama_aktif = False
 
 
def turtle_tarama_yap(hisseler, baslik):
    simdi     = datetime.now().strftime("%d.%m.%Y %H:%M")
    sinyaller = []
    telegram_gonder(
        f"🐢 <b>{baslik} TURTLE TARAMASI</b>\n"
        f"⏰ {simdi}\n"
        f"📊 {len(hisseler)} hisse - 40G Dennis Sistemi"
    )
    for ticker in hisseler:
        print(f"  TURTLE {ticker}...", end=" ", flush=True)
        sonuc, _ = turtle_analiz(ticker)
        if sonuc:
            sinyaller.append(sonuc)
            print("KIRILIM!")
        else:
            print("-")
        time.sleep(0.3)
 
    if not sinyaller:
        telegram_gonder(f"🐢 <b>{baslik} TURTLE</b> — Kirilim bulunamadi.")
        return
 
    telegram_gonder(
        f"🐢 <b>{baslik} TURTLE SINYALLERI</b>\n"
        f"⏰ {simdi}\n"
        f"📊 {len(sinyaller)} hisse 40G kirildi!"
    )
    time.sleep(1)
    for s in sinyaller:
        bayrak = "🇹🇷" if s["piyasa"] == "BIST" else "🇺🇸"
        para   = "₺" if s["piyasa"] == "BIST" else "$"
        cap    = f"{s['mktcap']/1e9:.1f}B {para}" if s["mktcap"] > 0 else "?"
        mesaj  = (
            f"🐢 {bayrak} <b>{s['ticker']}</b> — 40G KIRILIM\n"
            f"💹 Piyasa Degeri: {cap}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Giris:    {para}{s['giris']}\n"
            f"🛑 S/L:      {para}{s['sl']}\n"
            f"🎯 TP1:     {para}{s['tp1']}\n"
            f"🎯 TP2:     {para}{s['tp2']}\n"
            f"🎯 TP3:     {para}{s['tp3']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📏 40G Yuksek: {para}{s['don_hi']}\n"
            f"🚪 Cikis 20G:  {para}{s['don_ex']}\n"
            f"🔢 ATR (N):    {para}{s['atr']}\n"
            f"📐 Mesafe:     %{s['mesafe']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Yatirim tavsiyesi degildir."
        )
        telegram_gonder(mesaj)
        time.sleep(0.8)
 
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
# TELEGRAM DINLEYICI
# ══════════════════════════════════════════════
def yardim_mesaji():
    return (
        "🤖 <b>ULS+FIB Borsa Botu v4</b>\n\n"
        f"🇹🇷 BIST: {len(BIST_HISSELER)} hisse → 18:30\n"
        f"🇺🇸 ABD S&P500+: {len(ABD_HISSELER)} hisse → 23:30\n\n"
        "Hisse analizi:\n"
        "THYAO / THYAO.IS → BIST\n"
        "AAPL / NVDA / JPM → ABD\n\n"
        "Komutlar:\n"
        "/bist   BIST tara\n"
        "/abd    ABD tara\n"
        "/tara   Hepsini tara\n"
        "/liste  Kac hisse var\n"
        "/yardim Bu menu\n\n"
        "🐢 Turtle Komutlar:\n"
        "/turtle_bist  BIST Turtle tara\n"
        "/turtle_abd   ABD Turtle tara\n"
        "/turtle_tara  Her ikisi Turtle"
    )
 
def mesaji_isle(metin, chat_id):
    cmd = metin.strip().upper()
 
    if cmd in ["/START", "/YARDIM", "/HELP"]:
        telegram_gonder(yardim_mesaji(), chat_id)
        return
    if cmd == "/LISTE":
        telegram_gonder(
            f"📊 <b>Tarama Listesi</b>\n\n"
            f"🇹🇷 BIST: {len(BIST_HISSELER)} hisse\n"
            f"🇺🇸 ABD S&P500+: {len(ABD_HISSELER)} hisse\n"
            f"📊 Toplam: {len(BIST_HISSELER)+len(ABD_HISSELER)} hisse\n\n"
            f"Filtreler:\n"
            f"💹 Piyasa degeri 10B+\n"
            f"✅ EMA200 + ADX + RSI\n"
            f"✅ MACD + Turtle 40G\n"
            f"✅ Fibonacci 5/7+", chat_id)
        return
    if cmd == "/BIST":
        telegram_gonder("🇹🇷 BIST taramasi baslatiliyor...", chat_id)
        threading.Thread(target=bist_tarama, daemon=True).start()
        return
    if cmd == "/ABD":
        telegram_gonder(f"🇺🇸 ABD taramasi baslatiliyor ({len(ABD_HISSELER)} hisse)...", chat_id)
        threading.Thread(target=abd_tarama, daemon=True).start()
        return
    if cmd == "/TARA":
        telegram_gonder("🔍 Tum piyasalar taranıyor...", chat_id)
        threading.Thread(target=bist_tarama, daemon=True).start()
        threading.Thread(target=abd_tarama,  daemon=True).start()
        return
    if cmd == "/TURTLE_BIST":
        telegram_gonder("🐢 BIST Turtle taramasi baslatiliyor...", chat_id)
        threading.Thread(target=bist_turtle, daemon=True).start()
        return
    if cmd == "/TURTLE_ABD":
        telegram_gonder("🐢 ABD Turtle taramasi baslatiliyor...", chat_id)
        threading.Thread(target=abd_turtle, daemon=True).start()
        return
    if cmd == "/TURTLE_TARA":
        telegram_gonder("🐢 Turtle taramasi baslatiliyor...", chat_id)
        threading.Thread(target=bist_turtle, daemon=True).start()
        threading.Thread(target=abd_turtle,  daemon=True).start()
        return
 
    ticker = cmd.replace("/", "")
    telegram_gonder(f"🔍 <b>{ticker}</b> analiz ediliyor...", chat_id)
    sonuc, hata = hisse_analiz(ticker)
    if sonuc is None and not ticker.endswith(".IS"):
        sonuc, hata = hisse_analiz(ticker + ".IS")
    if sonuc is None:
        telegram_gonder(
            f"❌ <b>{ticker}</b> bulunamadi.\n"
            f"BIST: THYAO veya THYAO.IS\n"
            f"ABD: AAPL, MSFT, JPM\n"
            f"Hata: {hata}", chat_id)
        return
    telegram_gonder(detay_mesaji(sonuc), chat_id)
 
def telegram_dinle():
    global son_update_id
    print("Telegram dinleniyor...")
    # Eski mesajlari atla - bot yeniden basladiginda gecmis mesajlari isleme
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        r = requests.get(url, params={"offset": -1}, timeout=10)
        if r.status_code == 200:
            sonuclar = r.json().get("result", [])
            if sonuclar:
                son_update_id = sonuclar[-1]["update_id"]
                print(f"Eski mesajlar atlandi. Son ID: {son_update_id}")
    except:
        pass
 
    while True:
        try:
            for m in telegram_mesajlari_al():
                son_update_id = m["update_id"]
                if "message" in m and "text" in m["message"]:
                    metin   = m["message"]["text"]
                    chat_id = m["message"]["chat"]["id"]
                    print(f"Mesaj: '{metin}'")
                    threading.Thread(target=mesaji_isle, args=(metin, chat_id), daemon=True).start()
            time.sleep(2)
        except Exception as e:
            print(f"Dinleme hatasi: {e}")
            time.sleep(5)
 
# ══════════════════════════════════════════════
# ANA PROGRAM
# ══════════════════════════════════════════════
if __name__ == "__main__":
    print(f"ULS+FIB BORSA BOTU v4")
    print(f"BIST: {len(BIST_HISSELER)} hisse - 18:30")
    print(f"ABD:  {len(ABD_HISSELER)} hisse - 23:30")
    print(f"Durdurmak: CTRL+C\n")
 
    telegram_gonder(
        f"✅ <b>ULS+FIB Botu v4 Aktif!</b>\n\n"
        f"🇹🇷 BIST: {len(BIST_HISSELER)} hisse → 18:30\n"
        f"🇺🇸 ABD S&P500+: {len(ABD_HISSELER)} hisse → 23:30\n\n"
        f"📐 TradingView tam uyumlu\n"
        f"📊 Filtreler: RSI 45-80 | ADX 15+\n"
        f"💹 Piyasa degeri filtresi: 10B+\n\n"
        f"Test: THYAO veya AAPL yaz\n"
        f"/yardim → komutlar"
    )
    print("Bot aktif!\n")
 
    schedule.every().day.at("18:30").do(bist_tarama)
    schedule.every().day.at("19:00").do(bist_turtle)
    schedule.every().day.at("23:30").do(abd_tarama)
    schedule.every().day.at("00:00").do(abd_turtle)
 
    threading.Thread(target=telegram_dinle, daemon=True).start()
 
    while True:
        schedule.run_pending()
        time.sleep(30)
