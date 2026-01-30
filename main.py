#!/usr/bin/env python3

import os
import sys
import time
import signal
from datetime import datetime
import requests
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import pytz

load_dotenv()

# ======================
# CONFIG / ENV
# ======================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

POLL_SECONDS = int(os.getenv("POLL_SECONDS") or 60)
KLINES_LIMIT = int(os.getenv("KLINES_LIMIT") or 200)

BOLLINGER_PERIOD = 8
BOLLINGER_STD = 2
ADX_PERIOD = 8

BOLLINGER_WIDTH_MIN_PCT = float(os.getenv("BOLLINGER_WIDTH_MIN_PCT") or 0.015)
ADX_MIN = float(os.getenv("ADX_MIN") or 15)
ADX_ACCEL_THRESHOLD = float(os.getenv("ADX_ACCEL_THRESHOLD") or 0.05)

BINANCE_FAPI = "https://fapi.binance.com"

# ======================
# LISTA FIXA DE ATIVOS
# ======================
FIXED_SYMBOLS = [
    "BCHUSDT", "BNBUSDT", "CHZUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT",
    "JASMYUSDT", "SOLUSDT", "UNIUSDT", "XMRUSDT", "XRPUSDT"
]

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print("TELEGRAM_TOKEN e TELEGRAM_CHAT_ID não definidos no .env")
    sys.exit(1)

# ======================
# UTILITÁRIOS
# ======================
def now_sp_str():
    tz = pytz.timezone("America/Sao_Paulo")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    requests.post(url, data=payload, timeout=10)

def fetch_klines(symbol, interval="15m", limit=KLINES_LIMIT):
    url = BINANCE_FAPI + "/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()

    cols = [
        "open_time","open","high","low","close","volume","close_time",
        "quote_asset_volume","number_of_trades","taker_buy_base",
        "taker_buy_quote","ignore"
    ]
    df = pd.DataFrame(r.json(), columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.set_index("open_time", inplace=True)
    return df

# ======================
# INDICADORES
# ======================
def sma(series, period):
    return series.rolling(period).mean()

def true_range(df):
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"] - df["close"].shift()).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

def atr(df, period=14):
    return true_range(df).rolling(period).mean()

def bollinger_bands(series):
    ma = series.rolling(BOLLINGER_PERIOD).mean()
    std = series.rolling(BOLLINGER_PERIOD).std()
    upper = ma + BOLLINGER_STD * std
    lower = ma - BOLLINGER_STD * std
    width = (upper - lower) / ma
    return upper, lower, width

def adx(df, period=ADX_PERIOD):
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr = true_range(df)
    atr_val = tr.rolling(period).mean()

    plus_di = 100 * plus_dm.rolling(period).sum() / atr_val
    minus_di = 100 * minus_dm.rolling(period).sum() / atr_val

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.rolling(period).mean().fillna(0)

# ======================
# LÓGICA DE SINAL
# ======================
def triple_sma_cross(df):
    s3 = sma(df["close"], 3)
    s8 = sma(df["close"], 8)
    s20 = sma(df["close"], 20)

    if len(df) < 3:
        return None

    if s3.iloc[-1] > s8.iloc[-1] > s20.iloc[-1] and not (s3.iloc[-2] > s8.iloc[-2] > s20.iloc[-2]):
        return "LONG"

    if s3.iloc[-1] < s8.iloc[-1] < s20.iloc[-1] and not (s3.iloc[-2] < s8.iloc[-2] < s20.iloc[-2]):
        return "SHORT"

    return None

def analyze_symbol(symbol):
    df = fetch_klines(symbol)
    df = df.iloc[:-1]

    upper, lower, width = bollinger_bands(df["close"])
    if width.iloc[-1] < BOLLINGER_WIDTH_MIN_PCT:
        return None

    adx_val = adx(df).iloc[-1]
    if adx_val < ADX_MIN:
        return None

    side = triple_sma_cross(df)
    if not side:
        return None

    price = float(df["close"].iloc[-1])
    return {
        "symbol": symbol,
        "side": side,
        "price": price,
        "adx": adx_val
    }

# ======================
# LOOP PRINCIPAL
# ======================
SHUTDOWN = False

def handle_sigint(sig, frame):
    global SHUTDOWN
    SHUTDOWN = True
    send_telegram(f"⛔ Scanner interrompido em {now_sp_str()}")

signal.signal(signal.SIGINT, handle_sigint)
signal.signal(signal.SIGTERM, handle_sigint)

def main_loop():
    send_telegram("🤖 Scanner iniciado com sucesso")

    while not SHUTDOWN:
        for symbol in FIXED_SYMBOLS:
            res = analyze_symbol(symbol)
            if res:
                msg = (
                    f"🚨 <b>ALERTA 15m</b>\n"
                    f"Par: <b>{res['symbol']}</b>\n"
                    f"Sinal: <b>{res['side']}</b>\n"
                    f"Preço: {res['price']:.8f}\n"
                    f"ADX: {res['adx']:.2f}\n"
                    f"Horário: {now_sp_str()}"
                )
                send_telegram(msg)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main_loop()
