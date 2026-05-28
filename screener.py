import os
import time
import logging
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf

# -------------------------------------------------------------------------
# LOGGING SETUP
# -------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# COIN TIERS
# -------------------------------------------------------------------------
COIN_TIERS: dict[str, int] = {
    "BTC-USD":   1, "ETH-USD":   1,
    "BNB-USD":   2, "SOL-USD":   2, "XRP-USD":  2,
    "NEAR-USD":  2, "OP-USD":    2, "ADA-USD":  2,
    "EIGEN-USD": 3, "FLOKI-USD": 3, "SHIB-USD": 3, "DOGE-USD": 3,
}

# TP/SL multiplier ตาม Tier (ATR × multiplier)
TIER_CONFIG: dict[int, dict] = {
    1: {"atr_tp_multiplier": 3.0, "atr_sl_multiplier": 1.5, "label": "🏆 Tier 1 (Blue Chip)"},
    2: {"atr_tp_multiplier": 2.0, "atr_sl_multiplier": 1.5, "label": "🥈 Tier 2 (Mid-Cap)"},
    3: {"atr_tp_multiplier": 1.2, "atr_sl_multiplier": 1.0, "label": "🎲 Tier 3 (Small/Meme)"},
}

# -------------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------------
CONFIG = {
    # --- RSI Thresholds ---
    "rsi_oversold":            35,
    "rsi_overbought":          65,
    "rsi_recovery_threshold":  45,
    "rsi_pullback_threshold":  55,
    "rsi_recovery_lookback":     5,

    # --- Divergence ---
    "rsi_bull_div_max":        45,
    "rsi_bear_div_min":        55,
    "lookback_bars":           15,
    "lookback_skip_bars":       3,

    # --- TP / SL (fallback ถ้าเหรียญไม่อยู่ใน COIN_TIERS) ---
    "atr_tp_multiplier":       2.0,
    "atr_sl_multiplier":       1.5,

    # --- Trend Continuity ---
    "trend_ema_slope_bars":     5,   # ดู slope ของ EMA200 ย้อนหลัง N bars
    "trend_candle_streak":      3,   # candle ติดกันกี่แท่งถึงนับว่า "ต่อเนื่อง"

    # --- RSI Recovery Quality ---
    "recovery_quality_high":   70,   # ≥ 70 = 🔥 Strong Recovery
    "recovery_quality_mid":    40,   # ≥ 40 = ✅ Moderate Recovery

    # --- Volume Filter ---
    "vol_filter_ratio":       0.5,

    # --- Indicators ---
    "ema_short":               50,
    "ema_long":               200,
    "rsi_length":              14,
    "atr_length":              14,

    # --- Data Fetching ---
    "interval":             "1h",
    "period":               "90d",
    "request_delay":         0.5,
    "max_retries":              3,
    "retry_delay":              2,
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

WATCHLIST = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "EIGEN-USD", "FLOKI-USD", "NEAR-USD", "OP-USD", "ADA-USD",
    "SHIB-USD", "DOGE-USD",
]

# -------------------------------------------------------------------------
# PRICE FORMATTING
# -------------------------------------------------------------------------
def fmt_price(price: float) -> str:
    if price >= 1_000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:,.4f}"
    elif price >= 0.001:
        return f"${price:,.6f}"
    else:
        return f"${price:,.8f}"

# -------------------------------------------------------------------------
# TELEGRAM UTILITIES
# -------------------------------------------------------------------------
def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("< ", "&lt; ").replace(" >", " &gt;").replace("<=", "&lt;=").replace(">=", "&gt;=")

def send_telegram_message(text_msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    MAX_LEN = 4000
    lines = text_msg.split("\n")
    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > MAX_LEN:
            chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    if current_chunk:
        chunks.append(current_chunk.strip())

    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram message sent successfully.")
            else:
                logger.warning(f"Telegram error {response.status_code}: {response.text}")
                if response.status_code == 400:
                    logger.info("Retrying to send as plain text...")
                    plain_text = chunk.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
                    payload["text"] = plain_text
                    payload.pop("parse_mode", None)
                    requests.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Exception while sending Telegram message: {e}")

# -------------------------------------------------------------------------
# DATA FETCHING
# -------------------------------------------------------------------------
def get_historical_data_yf(symbol: str) -> pd.DataFrame | None:
    interval = CONFIG["interval"]
    period   = CONFIG["period"]

    for attempt in range(1, CONFIG["max_retries"] + 1):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)

            if df is None or df.empty:
                logger.warning(f"[{symbol}] No data returned (attempt {attempt}).")
            else:
                df = df.reset_index().copy()
                df.rename(columns={
                    "Open": "open", "High": "high",
                    "Low": "low",  "Close": "close",
                    "Volume": "volume"
                }, inplace=True)
                return df
        except Exception as e:
            logger.error(f"[{symbol}] Fetch error (attempt {attempt}): {e}")

        if attempt < CONFIG["max_retries"]:
            time.sleep(CONFIG["retry_delay"])

    return None

# -------------------------------------------------------------------------
# INDICATOR CALCULATION
# -------------------------------------------------------------------------
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    df[f"EMA_{CONFIG['ema_short']}"]  = ta.ema(df["close"], length=CONFIG["ema_short"])
    df[f"EMA_{CONFIG['ema_long']}"]   = ta.ema(df["close"], length=CONFIG["ema_long"])
    df["RSI"]    = ta.rsi(df["close"], length=CONFIG["rsi_length"])
    df["ATR"]    = ta.atr(df["high"], df["low"], df["close"], length=CONFIG["atr_length"])
    df["VOL_MA"] = df["volume"].rolling(20).mean()
    return df

def has_valid_indicators(row: pd.Series, cols: list[str]) -> bool:
    return all(not pd.isna(row[col]) for col in cols)

# -------------------------------------------------------------------------
# TREND CONTINUITY ANALYSIS
# -------------------------------------------------------------------------
def analyze_trend_continuity(df: pd.DataFrame) -> dict:
    ema_long_col  = f"EMA_{CONFIG['ema_long']}"
    ema_short_col = f"EMA_{CONFIG['ema_short']}"
    slope_bars    = CONFIG["trend_ema_slope_bars"]
    streak_target = CONFIG["trend_candle_streak"]

    result = {
        "ema_slope":       "neutral",   
        "candle_streak":   0,           
        "ema_cross_zone":  "neutral",   
        "is_trending_up":  False,
        "is_trending_down": False,
        "trend_strength":  "",          
    }

    if len(df) < slope_bars + 2:
        return result

    ema_now  = df[ema_long_col].iloc[-1]
    ema_prev = df[ema_long_col].iloc[-slope_bars]
    if pd.isna(ema_now) or pd.isna(ema_prev):
        return result

    slope_pct = (ema_now - ema_prev) / ema_prev * 100
    if slope_pct > 0.1:
        result["ema_slope"] = "rising"
    elif slope_pct < -0.1:
        result["ema_slope"] = "falling"

    streak = 0
    for i in range(1, streak_target + 3):
        idx = -i
        if abs(idx) > len(df):
            break
        candle = df.iloc[idx]
        if candle["close"] > candle["open"]:
            if streak >= 0:
                streak += 1
            else:
                break
        elif candle["close"] < candle["open"]:
            if streak <= 0:
                streak -= 1
            else:
                break
        else:
            break
    result["candle_streak"] = streak

    ema50_now = df[ema_short_col].iloc[-1]
    if not pd.isna(ema50_now):
        if ema50_now > ema_now:
            result["ema_cross_zone"] = "golden"
        elif ema50_now < ema_now:
            result["ema_cross_zone"] = "death"

    up_score   = 0
    down_score = 0

    if result["ema_slope"] == "rising":   up_score   += 2
    if result["ema_slope"] == "falling":  down_score += 2
    if result["candle_streak"] >= streak_target:   up_score   += 1
    if result["candle_streak"] <= -streak_target:  down_score += 1
    if result["ema_cross_zone"] == "golden":  up_score   += 1
    if result["ema_cross_zone"] == "death":   down_score += 1

    result["is_trending_up"]   = up_score   >= 3
    result["is_trending_down"] = down_score >= 3

    parts = []
    if result["ema_slope"] == "rising":
        parts.append(f"📈 EMA200 เอียงขึ้น (+{slope_pct:.2f}%)")
    elif result["ema_slope"] == "falling":
        parts.append(f"📉 EMA200 เอียงลง ({slope_pct:.2f}%)")
    else:
        parts.append("➡️ EMA200 แบนราบ")

    if result["candle_streak"] >= streak_target:
        parts.append(f"🕯️ Green candle ต่อเนื่อง {streak} แท่ง")
    elif result["candle_streak"] <= -streak_target:
        parts.append(f"🕯️ Red candle ต่อเนื่อง {abs(streak)} แท่ง")

    if result["ema_cross_zone"] == "golden":
        parts.append("✨ EMA50 อยู่เหนือ EMA200 (Golden zone)")
    elif result["ema_cross_zone"] == "death":
        parts.append("☠️ EMA50 อยู่ใต้ EMA200 (Death zone)")

    if result["is_trending_up"]:
        parts.append("⚡ <b>แนวโน้มขึ้นต่อเนื่องแข็งแกร่ง</b>")
    elif result["is_trending_down"]:
        parts.append("⚡ <b>แนวโน้มลงต่อเนื่องแข็งแกร่ง</b>")

    result["trend_strength"] = " | ".join(parts)
    return result

# -------------------------------------------------------------------------
# RSI RECOVERY QUALITY SCORE
# -------------------------------------------------------------------------
def score_rsi_recovery(df: pd.DataFrame) -> int:
    lookback = CONFIG["rsi_recovery_lookback"]
    if len(df) < lookback + 2:
        return 0

    rsi_series   = df["RSI"]
    vol_series   = df["volume"]
    last_rsi     = rsi_series.iloc[-1]
    recent_slice = rsi_series.iloc[-lookback:]
    recent_min   = recent_slice.min()
    oversold_lvl = CONFIG["rsi_oversold"]

    score = 0

    depth = max(0, oversold_lvl - recent_min)
    score += min(35, int(depth * 2.5))

    rise = last_rsi - recent_min
    score += min(30, int(rise * 2))

    dist = last_rsi - oversold_lvl
    if dist > 0:
        score += min(20, int(dist * 2))

    vol_now  = vol_series.iloc[-1]
    vol_prev = vol_series.iloc[-lookback:].mean()
    if not pd.isna(vol_prev) and vol_prev > 0:
        vol_ratio = vol_now / vol_prev
        if vol_ratio >= 1.5:
            score += 15
        elif vol_ratio >= 1.2:
            score += 8
        elif vol_ratio >= 1.0:
            score += 3

    return min(100, score)

def recovery_quality_label(score: int) -> str:
    if score >= CONFIG["recovery_quality_high"]:
        return f"🔥 Strong Recovery (คะแนน {score}/100)"
    elif score >= CONFIG["recovery_quality_mid"]:
        return f"✅ Moderate Recovery (คะแนน {score}/100)"
    else:
        return f"⚠️ Weak Recovery (คะแนน {score}/100) — ระวังดีดไม่ต่อ"

# -------------------------------------------------------------------------
# DIVERGENCE DETECTION
# -------------------------------------------------------------------------
def _find_swing_low(lookback: pd.DataFrame) -> pd.Series | None:
    closes = lookback["close"].values
    for i in range(1, len(closes) - 1):
        if closes[i] < closes[i - 1] and closes[i] < closes[i + 1]:
            return lookback.iloc[i]
    return lookback.iloc[lookback["close"].idxmin()]

def _find_swing_high(lookback: pd.DataFrame) -> pd.Series | None:
    closes = lookback["close"].values
    for i in range(1, len(closes) - 1):
        if closes[i] > closes[i - 1] and closes[i] > closes[i + 1]:
            return lookback.iloc[i]
    return lookback.iloc[lookback["close"].idxmax()]

def check_bullish_divergence(df: pd.DataFrame) -> bool:
    min_bars = CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"] + 2
    if len(df) < min_bars:
        return False
    current = df.iloc[-1]
    if current["RSI"] >= CONFIG["rsi_bull_div_max"]:
        return False
    lookback = df.iloc[-(CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"]):-CONFIG["lookback_skip_bars"]].reset_index(drop=True)
    swing = _find_swing_low(lookback)
    if swing is None:
        return False
    return (current["close"] < swing["close"]) and (current["RSI"] > swing["RSI"])

def check_bearish_divergence(df: pd.DataFrame) -> bool:
    min_bars = CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"] + 2
    if len(df) < min_bars:
        return False
    current = df.iloc[-1]
    if current["RSI"] <= CONFIG["rsi_bear_div_min"]:
        return False
    lookback = df.iloc[-(CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"]):-CONFIG["lookback_skip_bars"]].reset_index(drop=True)
    swing = _find_swing_high(lookback)
    if swing is None:
        return False
    return (current["close"] > swing["close"]) and (current["RSI"] < swing["RSI"])

# -------------------------------------------------------------------------
# RSI SIGNAL MODE DETECTION
# -------------------------------------------------------------------------
def detect_buy_mode(rsi_series: pd.Series) -> str | None:
    if len(rsi_series) < CONFIG["rsi_recovery_lookback"] + 1:
        return None

    last_rsi = rsi_series.iloc[-1]
    prev_rsi = rsi_series.iloc[-2]
    ov       = CONFIG["rsi_oversold"]
    rec      = CONFIG["rsi_recovery_threshold"]
    lookback = CONFIG["rsi_recovery_lookback"]

    recent_min = rsi_series.iloc[-lookback:].min()
    if last_rsi <= rec and last_rsi > prev_rsi and recent_min <= ov:
        return "recovery"
    if last_rsi <= ov and prev_rsi > ov:
        return "crossunder"
    if last_rsi <= ov and prev_rsi <= ov:  # 🟢 แก้ไข: ลบคำว่า coach ที่หลุดพิมพ์ออกแล้ว
        return "in_zone"
    return None

def detect_sell_mode(rsi_series: pd.Series) -> str | None:
    if len(rsi_series) < CONFIG["rsi_recovery_lookback"] + 1:
        return None

    last_rsi = rsi_series.iloc[-1]
    prev_rsi = rsi_series.iloc[-2]
    ob       = CONFIG["rsi_overbought"]
    pb       = CONFIG["rsi_pullback_threshold"]
    lookback = CONFIG["rsi_recovery_lookback"]

    recent_max = rsi_series.iloc[-lookback:].max()
    if last_rsi >= pb and last_rsi < prev_rsi and recent_max >= ob:
        return "pullback"
    if last_rsi >= ob and prev_rsi < ob:
        return "crossover"
    if last_rsi >= ob and prev_rsi >= ob:
        return "in_zone"
    return None

# -------------------------------------------------------------------------
# SIGNAL BUILDER
# -------------------------------------------------------------------------
_MODE_LABEL_BUY = {
    "crossunder": "🔔 RSI เพิ่งลงใต้ Oversold",
    "in_zone":    "📉 RSI อยู่ใน Oversold ต่อเนื่อง",
    "recovery":   "🚀 RSI กำลังดีดกลับจาก Oversold ← จังหวะเข้าซื้อที่ดีที่สุด",
}
_MODE_LABEL_SELL = {
    "crossover": "🔔 RSI เพิ่งขึ้นเหนือ Overbought",
    "in_zone":    "⚠️ RSI อยู่ใน Overbought ต่อเนื่อง",
    "pullback":   "🎯 RSI กำลังย่อจาก Overbought ← จังหวะขายที่ดีที่สุด",
}

def _get_tier_cfg(symbol: str) -> tuple[int, dict]:
    tier = COIN_TIERS.get(symbol, 2)
    return tier, TIER_CONFIG[tier]

def build_buy_signal(
    symbol: str,
    display_name: str,
    last: pd.Series,
    coin_trend: str,
    mode: str,
    has_div: bool,
    trend: dict,
    recovery_score: int,
) -> str:
    tier_num, tier_cfg = _get_tier_cfg(symbol)
    tp_mult = tier_cfg["atr_tp_multiplier"]
    sl_mult = tier_cfg["atr_sl_multiplier"]
    atr     = last["ATR"]

    tp_price  = last["close"] + (atr * tp_mult)
    sl_price  = last["close"] - (atr * sl_mult)
    ema_short = last[f"EMA_{CONFIG['ema_short']}"]
    ema_long  = last[f"EMA_{CONFIG['ema_long']}"]

    context_lines = [_MODE_LABEL_BUY[mode]]

    if mode == "recovery":
        context_lines.append(recovery_quality_label(recovery_score))

    if last["close"] > ema_long:
        context_lines.append("+ ยืนเหนือ EMA200 (ภาพใหญ่ยังเป็นขาขึ้น)")
    else:
        context_lines.append("- อยู่ใต้ EMA200 (ภาพใหญ่ขาลง — เล่นรอบสั้นเท่านั้น)")

    if trend["is_trending_up"]:
        context_lines.append("⚡ แนวโน้มขึ้นต่อเนื่อง — เพิ่มความมั่นใจสัญญาณซื้อ!")
    elif trend["is_trending_down"]:
        context_lines.append("⚠️ แนวโน้มลงต่อเนื่อง — อาจเป็นแค่ bounce ชั่วคราว")
    context_lines.append(trend["trend_strength"])

    if has_div:
        context_lines.append("🔥 พบ Bullish Divergence — โอกาสกลับตัวสูง!")
    context = "\n".join(context_lines)

    entry_low = fmt_price(last["close"] * 0.99)
    entry_hi  = fmt_price(last["close"])
    price_now = fmt_price(last["close"])
    tp_fmt    = fmt_price(tp_price)
    sl_fmt    = fmt_price(sl_price)

    return escape_html(
        f"\n🟢 <b>[BUY] {display_name}</b> {tier_cfg['label']}\n"
        f"ราคา: <b>{price_now}</b> ({coin_trend})\n"
        f"RSI: {last['RSI']:.2f} | ATR: {atr:,.6f}\n"
        f"EMA50: {fmt_price(ema_short)} | EMA200: {fmt_price(ema_long)}\n"
        f"สถานะ: {context}\n"
        f"📍 ช่วงเข้าซื้อ: {entry_low} – {entry_hi}\n"
        f"🎯 Take Profit (ATR×{tp_mult}): {tp_fmt}\n"
        f"❌ Stop Loss (ATR×{sl_mult}): {sl_fmt}\n"
        f"{'─'*32}"
    )

def build_sell_signal(
    symbol: str,
    display_name: str,
    last: pd.Series,
    coin_trend: str,
    mode: str,
    has_div: bool,
    trend: dict,
) -> str:
    tier_num, tier_cfg = _get_tier_cfg(symbol)
    tp_mult = tier_cfg["atr_tp_multiplier"]
    sl_mult = tier_cfg["atr_sl_multiplier"]
    atr     = last["ATR"]

    tp_price  = last["close"] - (atr * tp_mult)
    sl_price  = last["close"] + (atr * sl_mult)
    ema_short = last[f"EMA_{CONFIG['ema_short']}"]
    ema_long  = last[f"EMA_{CONFIG['ema_long']}"]

    context_lines = [_MODE_LABEL_SELL[mode]]

    if last["close"] > ema_long:
        context_lines.append("+ ยืนเหนือ EMA200 (แข็งแกร่ง แต่อาจย่อระยะสั้น)")
    else:
        context_lines.append("- อยู่ใต้ EMA200 (เด้งขึ้นมาเพื่อลงต่อ — ระวังแรงเทขาย)")

    if trend["is_trending_down"]:
        context_lines.append("⚡ แนวโน้มลงต่อเนื่อง — เสริมความน่าเชื่อถือสัญญาณขาย!")
    elif trend["is_trending_up"]:
        context_lines.append("⚠️ แนวโน้มขึ้นต่อเนื่อง — อาจย่อแล้วขึ้นต่อ ระวังสัญญาณหลอก")
    context_lines.append(trend["trend_strength"])

    if has_div:
        context_lines.append("🚨 พบ Bearish Divergence — สัญญาณกลับตัวลงรุนแรง!")
    context = "\n".join(context_lines)

    entry_low = fmt_price(last["close"])
    entry_hi  = fmt_price(last["close"] * 1.01)
    price_now = fmt_price(last["close"])
    tp_fmt    = fmt_price(tp_price)
    sl_fmt    = fmt_price(sl_price)

    return escape_html(
        f"\n🔴 <b>[SELL] {display_name}</b> {tier_cfg['label']}\n"
        f"ราคา: <b>{price_now}</b> ({coin_trend})\n"
        f"RSI: {last['RSI']:.2f} | ATR: {atr:,.6f}\n"
        f"EMA50: {fmt_price(ema_short)} | EMA200: {fmt_price(ema_long)}\n"
        f"สถานะ: {context}\n"
        f"📍 โซนแบ่งขาย: {entry_low} – {entry_hi}\n"
        f"🎯 รอรับกลับ (ATR×{tp_mult}): {tp_fmt}\n"
        f"❌ Trailing Stop (ATR×{sl_mult}): {sl_fmt}\n"
        f"{'─'*32}"
    )

# -------------------------------------------------------------------------
# MAIN SCREENER
# -------------------------------------------------------------------------
def screen_crypto() -> None:
    logger.info(
        "🚀 Starting Crypto Screener [Engine: Yahoo Finance | Interval: %s]",
        CONFIG["interval"],
    )

    buy_signals:    list[str] = []
    sell_signals:   list[str] = []
    coin_summaries: list[str] = []
    bullish_count = 0
    total_coins   = 0

    required_cols = [
        "RSI", "ATR", "VOL_MA",
        f"EMA_{CONFIG['ema_short']}",
        f"EMA_{CONFIG['ema_long']}",
    ]

    for symbol in WATCHLIST:
        display_name = symbol.replace("-USD", "_USD")
        logger.info(f"Scanning {display_name}...")
        time.sleep(CONFIG["request_delay"])

        df = get_historical_data_yf(symbol)
        if df is None or df.empty:
            logger.warning(f"[{display_name}] Skipped — no data.")
            continue

        df = calculate_indicators(df)

        if len(df) < CONFIG["rsi_recovery_lookback"] + 2:
            continue

        last = df.iloc[-1]

        if not has_valid_indicators(last, required_cols):
            logger.warning(
                f"[{display_name}] Skipped — NaN detected "
                f"(ข้อมูลไม่พอสำหรับ EMA{CONFIG['ema_long']})."
            )
            continue

        low_volume = last["volume"] < last["VOL_MA"] * CONFIG["vol_filter_ratio"]
        if low_volume:
            logger.info(
                f"[{display_name}] Low volume — signal suppressed "
                f"(volume < VOL_MA × {CONFIG['vol_filter_ratio']})."
            )

        total_coins  += 1
        ema_long_val  = last[f"EMA_{CONFIG['ema_long']}"]
        tier_num, tier_cfg = _get_tier_cfg(symbol)

        trend = analyze_trend_continuity(df)

        if last["close"] > ema_long_val:
            coin_trend = "🟢 ขาขึ้น"
            bullish_count += 1
        else:
            coin_trend = "🔴 ขาลง"

        tier_badge = {1: "🏆", 2: "🥈", 3: "🎲"}.get(tier_num, "")
        trend_cont_label = ""
        if trend["is_trending_up"]:
            trend_cont_label = " ⚡ต่อเนื่อง"
        elif trend["is_trending_down"]:
            trend_cont_label = " ⚡ต่อเนื่อง"

        coin_summaries.append(
            f"• {tier_badge} <b>{display_name}</b>: {fmt_price(last['close'])} "
            f"({coin_trend}{trend_cont_label} | RSI: {last['RSI']:.1f} | ATR: {last['ATR']:,.6f})"
        )

        if low_volume:
            continue

        rsi_series = df["RSI"]

        # ตรวจสัญญาณซื้อ
        buy_mode = detect_buy_mode(rsi_series)
        if buy_mode:
            is_div = check_bullish_divergence(df)

            rec_score = 0
            if buy_mode == "recovery":
                rec_score = score_rsi_recovery(df)

            buy_signals.append(
                build_buy_signal(
                    symbol, display_name, last, coin_trend,
                    buy_mode, is_div, trend, rec_score,
                )
            )
            logger.info(
                f"[{display_name}] BUY [{buy_mode}] | RSI={last['RSI']:.1f} "
                f"| Tier={tier_num} | TrendUp={trend['is_trending_up']} "
                f"| RecScore={rec_score}"
            )
            continue

        # ตรวจสัญญาณขาย
        sell_mode = detect_sell_mode(rsi_series)
        if sell_mode:
            is_div = check_bearish_divergence(df)
            sell_signals.append(
                build_sell_signal(
                    symbol, display_name, last, coin_trend,
                    sell_mode, is_div, trend,
                )
            )
            logger.info(
                f"[{display_name}] SELL [{sell_mode}] | RSI={last['RSI']:.1f} "
                f"| Tier={tier_num} | TrendDown={trend['is_trending_down']}"
            )

    if total_coins == 0:
        logger.warning("No coins analyzed. Check WATCHLIST or network connection.")
        return

    bullish_ratio = bullish_count / total_coins
    if bullish_ratio >= 0.6:
        market_overview = "📈 ขาขึ้นชัดเจน (Bullish)"
    elif bullish_ratio <= 0.4:
        market_overview = "📉 ขาลงรุนแรง (Bearish)"
    else:
        market_overview = "↔️ ไซด์เวย์เลือกทาง (Sideways)"

    tier_legend = (
        "\n<i>🏆 Tier1 TP×3.0  🥈 Tier2 TP×2.0  🎲 Tier3 TP×1.2  (ATR-based)</i>"
    )

    report = (
        f"📊 <b>[Crypto Screener] ภาพรวมตลาด: {market_overview}</b>\n"
        f"เหรียญขาขึ้น: {bullish_count}/{total_coins} "
        f"({bullish_ratio * 100:.0f}%)\n"
        f"{tier_legend}\n"
        f"{'='*33}\n\n"
        f"<b>🧐 สรุปรายเหรียญ:</b>\n"
        + "\n".join(coin_summaries)
        + f"\n\n{'='*33}\n"
    )

    total_signals = len(buy_signals) + len(sell_signals)
    if total_signals > 0:
        report += f"⚡ <b>สัญญาณเทรดชั่วโมงนี้ ({total_signals} สัญญาณ):</b>\n"
        if buy_signals:
            report += "".join(buy_signals)
        if sell_signals:
            report += "".join(sell_signals)
    else:
        report += "\nℹ️ <i>ไม่มีเหรียญใดเข้าเงื่อนไขสัญญาณซื้อ/ขายในชั่วโมงนี้</i>"

    send_telegram_message(report)
    logger.info(
        "✅ Report sent | BUY: %d | SELL: %d",
        len(buy_signals), len(sell_signals),
    )

# -------------------------------------------------------------------------
# ENTRY POINT
# -------------------------------------------------------------------------
if __name__ == "__main__":
    screen_crypto()
