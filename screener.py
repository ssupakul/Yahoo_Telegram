import os
import json
import requests
import subprocess
import pandas as pd
import pandas_ta as ta
import yfinance as yf

# -------------------------------------------------------------------------
# SETUP & CONFIGURATION
# -------------------------------------------------------------------------
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

WATCHLIST = ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD", "EIGEN-USD", "FLOKI-USD", "ADA-USD", "OP-USD", "NEAR-USD", "SHIB-USD", "DOGE-USD"]
STATE_FILE = "screener_state.json"

def load_state():
    """ อ่านสถานะการแจ้งเตือนล่าสุดจากไฟล์ JSON """
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Cannot read state file ({e}). Starting with empty state.")
    return {}

def save_state(state):
    """ บันทึกสถานะการแจ้งเตือนล่าสุดลงไฟล์ JSON """
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving state file: {e}")

def commit_and_push_state():
    """ สั่งให้บอททำการ Commit และ Push ไฟล์สถานะกลับขึ้น GitHub ด้วยสิทธิ์ Write """
    try:
        print("🔄 Detected state change. Preparing to sync with GitHub repository...")
        
        # ตั้งค่า Identity ชั่วคราวให้บอทในระบบ Actions
        subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        
        # สั่งเพิ่มไฟล์ ดำเนินการคอมมิต และดันสเตทกลับขึ้นไป
        subprocess.run(["git", "add", STATE_FILE], check=True)
        subprocess.run(["git", "commit", "-m", "Chore: update screener state [skip ci]"], check=True) # [skip ci] เพื่อไม่ให้สคริปต์รันวนลูปซ้ำ
        subprocess.run(["git", "push"], check=True)
        
        print("✅ Successfully pushed updated state file back to GitHub repository.")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Git Sync Warning: Could not push state to GitHub ({e}). This is expected if running locally without Git config.")
    except Exception as e:
        print(f"Error during Git execution: {e}")

def send_line_messaging_api(text_msg):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("Error: Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_USER_ID.")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text_msg}]
    }
    try:
        response = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            print("Successfully sent message via LINE Messaging API.")
        else:
            print(f"Failed to send LINE message: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception while sending LINE message: {e}")

def get_historical_data_yf(symbol, interval="1h"):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="60d", interval=interval)
        if df.empty:
            return None
        df = df.reset_index().copy()
        df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)
        return df
    except Exception as e:
        print(f"Exception fetching {symbol}: {e}")
        return None

def check_bullish_divergence(df):
    if len(df) < 20:
        return False
    current_close = df["close"].iloc[-1]
    current_rsi = df["RSI"].iloc[-1]
    lookback_df = df.iloc[-15:-3]
    lowest_price_idx = lookback_df["close"].idxmin()
    older_close = df["close"].loc[lowest_price_idx]
    older_rsi = df["RSI"].loc[lowest_price_idx]
    if current_close < older_close and current_rsi > older_rsi and current_rsi < 45:
        return True
    return False

def check_bearish_divergence(df):
    if len(df) < 20:
        return False
    current_close = df["close"].iloc[-1]
    current_rsi = df["RSI"].iloc[-1]
    lookback_df = df.iloc[-15:-3]
    highest_price_idx = lookback_df["close"].idxmax()
    older_close = df["close"].loc[highest_price_idx]
    older_rsi = df["RSI"].loc[highest_price_idx]
    if current_close > older_close and current_rsi < older_rsi and current_rsi > 55:
        return True
    return False

def screen_crypto():
    print("🚀 Starting Crypto Screener (USD)...")
    
    alert_state = load_state()
    state_updated = False
    signals = []
    
    for symbol in WATCHLIST:
        display_name = symbol.replace("-USD", "_USD")
        df = get_historical_data_yf(symbol, interval="1h")
        if df is None or df.empty or len(df) < 30:
            continue
            
        df["EMA_50"] = ta.ema(df["close"], length=50)
        df["EMA_200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)
        df["VOL_MA20"] = ta.sma(df["volume"], length=20)
        
        macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd_df is None or macd_df.empty:
            continue
        df["MACD"] = macd_df["MACD_12_26_9"]
        df["MACD_Signal"] = macd_df["MACDs_12_26_9"]
        
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        time_col = "Datetime" if "Datetime" in df.columns else "Date"
        last_candle_time = str(last_row[time_col])
        
        last_close_usd = last_row["close"]
        last_rsi = last_row["RSI"]
        last_macd = last_row["MACD"]
        last_signal = last_row["MACD_Signal"]
        last_vol = last_row["volume"]
        vol_ma = last_row["VOL_MA20"]
        
        prev_macd = prev_row["MACD"]
        prev_signal = prev_row["MACD_Signal"]
        
        last_ema50_usd = last_row["EMA_50"]
        last_ema200_usd = last_row["EMA_200"]
        
        is_macd_bullish_cross = (prev_macd <= prev_signal) and (last_macd > last_signal)
        is_macd_bearish_cross = (prev_macd >= prev_signal) and (last_macd < last_signal)
        is_volume_confirmed = last_vol > (vol_ma * 1.2)
        
        buy_state_key = f"{symbol}_BUY"
        sell_state_key = f"{symbol}_SELL"
        
        # 🟢 สัญญาณซื้อ (USD)
        if last_rsi <= 35 and is_macd_bullish_cross:
            if alert_state.get(buy_state_key) != last_candle_time:
                is_bull_div = check_bullish_divergence(df)
                buy_zone = f"{last_close_usd:,.4f} - {(last_close_usd * 0.98):,.4f}"
                take_profit = f"{(last_close_usd * 1.05):,.4f} (หรือแนวต้าน EMA50: {last_ema50_usd:,.4f})"
                stop_loss = f"{(last_close_usd * 0.95):,.4f}"
                
                status_context = "📉 RSI ต่ำ + 🔥 MACD Golden Cross (เพิ่งตัดขึ้น!)"
                if is_volume_confirmed:
                    status_context += "\n📊 Volume เพิ่มขึ้นแรงกว่าค่าเฉลี่ย 20% (ยืนยันสัญญาณซื้อ)"
                if last_close_usd > last_ema200_usd:
                    status_context += "\n+ อยู่เหนือ EMA200 (ภาพใหญ่ยังเป็นขาขึ้น)"
                else:
                    status_context += "\n- อยู่ใต้ EMA200 (ภาพใหญ่ขาลง เน้นรอบสั้น)"
                if is_bull_div:
                    status_context += "\n🔥 พบ Bullish Divergence มีโอกาสกลับตัวสูง!"
                    
                msg = (
                    f"\n🟢 [SIGNAL BUY] {display_name}\n"
                    f"ราคาปัจจุบัน: {last_close_usd:,.4f} USD\n"
                    f"RSI (1h): {last_rsi:.2f}\n"
                    f"สถานะกราฟ: {status_context}\n"
                    f"📍 ช่วงราคาเข้าซื้อ: {buy_zone} USD\n"
                    f"🎯 เป้าขายทำกำไร: {take_profit} USD\n"
                    f"❌ จุดตัดขาดทุน: {stop_loss} USD\n"
                    f"--------------------------------"
                )
                signals.append(msg)
                alert_state[buy_state_key] = last_candle_time
                state_updated = True
                
        # 🔴 สัญญาณขาย (USD)
        elif last_rsi >= 65 and is_macd_bearish_cross:
            if alert_state.get(sell_state_key) != last_candle_time:
                is_bear_div = check_bearish_divergence(df)
                sell_zone = f"{last_close_usd:,.4f} - {(last_close_usd * 1.02):,.4f}"
                re_entry_zone = f"{(last_close_usd * 0.95):,.4f} (หรือแนวรับ EMA50: {last_ema50_usd:,.4f})"
                trailing_stop = f"{(last_close_usd * 0.97):,.4f}"
                
                status_context = "⚠️ RSI สูง + 🚨 MACD Dead Cross (เพิ่งตัดลง!)"
                if is_volume_confirmed:
                    status_context += "\n📊 Volume เทขายหนาแน่นกว่าปกติ 20%"
                if last_close_usd > last_ema200_usd:
                    status_context += "\n+ ยืนเหนือเส้น EMA200 (แนวโน้มแข็งแกร่ง อาจย่อตัวระยะสั้น)"
                else:
                    status_context += "\n- อยู่ใต้เส้น EMA200 (แนวโน้มขาลงหลัก ระวังแรงเทขายซ้ำ)"
                if is_bear_div:
                    status_context += "\n🚨 พบ Bearish Divergence สัญญาณกลับตัวลงรุนแรง!"
                    
                msg = (
                    f"\n🔴 [SIGNAL SELL] {display_name}\n"
                    f"ราคาปัจจุบัน: {last_close_usd:,.4f} USD\n"
                    f"RSI (1h): {last_rsi:.2f}\n"
                    f"สถานะกราฟ: {status_context}\n"
                    f"📍 โซนแบ่งขายทำกำไร: {sell_zone} USD\n"
                    f"🎯 รอรับกลับเมื่อย่อตัว: {re_entry_zone} USD\n"
                    f"❌ หลุดจุดนี้ควรหนี (Trailing Stop): {trailing_stop} USD\n"
                    f"--------------------------------"
                )
                signals.append(msg)
                alert_state[sell_state_key] = last_candle_time
                state_updated = True

    # บันทึก และสั่งดันโค้ดขึ้น GitHub เมื่อมีการอัปเดตสถานะใหม่จริง
    if state_updated:
        save_state(alert_state)
        commit_and_push_state()

    if signals:
        alert_header = "📊 [Crypto Screener Report - USD]"
        full_message = alert_header + "".join(signals)
        send_line_messaging_api(full_message)
        print("Success! Notification sent to LINE.")
    else:
        print("Process complete: No new crossover signals found at this hour.")

if __name__ == "__main__":
    screen_crypto()
