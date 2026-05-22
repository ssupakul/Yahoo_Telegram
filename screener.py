import os
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf

# -------------------------------------------------------------------------
# SETUP & CONFIGURATION
# -------------------------------------------------------------------------
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

WATCHLIST = ["BTC-USD", "ETH-USD", "BNB-USD", "NEAR-USD", "SOL-USD", "XRP-USD", "ADA-USD", "EIGEN-USD", "OP-USD", "SHIB-USD", "DOGE-USD", "FLOKI-USD"]

def get_realtime_thb_rate():
    """ ดึงอัตราแลกเปลี่ยน USD/THB ปัจจุบันโดยตรงจาก Yahoo Finance """
    try:
        ticker = yf.Ticker("THB=X")
        df = ticker.history(period="1d", interval="1m")
        if not df.empty:
            return df["Close"].iloc[-1]
        return 35.5
    except Exception as e:
        print(f"Warning: Cannot fetch THB rate ({e}). Using default 35.5")
        return 35.5

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
        df = df.reset_index()
        df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)
        return df
    except Exception as e:
        print(f"Exception fetching {symbol}: {e}")
        return None

def check_bullish_divergence(df, rsi):
    if len(df) < 20:
        return False
    current_close = df["close"].iloc[-1]
    older_close = df["close"].iloc[-10:-3].min()
    current_rsi = rsi.iloc[-1]
    older_rsi = rsi.iloc[-10:-3].min()
    
    if current_close < older_close and current_rsi > older_rsi and current_rsi < 45:
        return True
    return False

def check_bearish_divergence(df, rsi):
    """ [เพิ่มฟังก์ชัน] ตรวจสอบสัญญาณ Bearish Divergence (ราคาสร้างจุดสูงสุดใหม่ แต่ RSI ต่ำลง) """
    if len(df) < 20:
        return False
    current_close = df["close"].iloc[-1]
    older_close = df["close"].iloc[-10:-3].max()
    current_rsi = rsi.iloc[-1]
    older_rsi = rsi.iloc[-10:-3].max()
    
    if current_close > older_close and current_rsi < older_rsi and current_rsi > 55:
        return True
    return False

def screen_crypto():
    print("🚀 Starting Binance Thailand Crypto Screener [Engine: Yahoo Finance Global-to-THB]...")
    thb_rate = get_realtime_thb_rate()
    print(f"Current FX Rate from Yahoo: 1 USD = {thb_rate:.2f} THB")
    
    signals = []
    
    for symbol in WATCHLIST:
        display_name = symbol.replace("-USD", "_THB")
        print(f"Scanning {display_name}...")
        
        df = get_historical_data_yf(symbol, interval="1h")
        if df is None or df.empty:
            continue
            
        df["EMA_50"] = ta.ema(df["close"], length=50)
        df["EMA_200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)
        
        last_close_usd = df["close"].iloc[-1]
        last_rsi = df["RSI"].iloc[-1]
        last_ema50_usd = df["EMA_50"].iloc[-1]
        last_ema200_usd = df["EMA_200"].iloc[-1]
        
        last_close_thb = last_close_usd * thb_rate
        last_ema50_thb = last_ema50_usd * thb_rate
        last_ema200_thb = last_ema200_usd * thb_rate
        
        # 🟢 เงื่อนไขเข้าซื้อ: RSI Oversold (<= 32)
        if last_rsi <= 32:
            is_bull_div = check_bullish_divergence(df, df["RSI"])
            buy_zone = f"{last_close_thb:,.2f} - {(last_close_thb * 0.98):,.2f}"
            take_profit = f"{(last_close_thb * 1.05):,.2f} (หรือแนวต้าน EMA50: {last_ema50_thb:,.2f})"
            stop_loss = f"{(last_close_thb * 0.95):,.2f}"
            
            status_context = "📉 RSI Oversold"
            if last_close_usd > last_ema200_usd:
                status_context += "\n+ ยืนเหนือเส้น EMA200 (ภาพใหญ่ยังเป็นแนวโน้มขาขึ้น)"
            else:
                status_context += "\n- อยู่ใต้เส้น EMA200 (ภาพใหญ่ขาลง ระวังเน้นเล่นรอบสั้น)"
                
            if is_bull_div:
                status_context += "\n🔥 พบบูลลิชไดเวอร์เจนท์ (Bullish Divergence) มีโอกาสกลับตัวสูง!"
                
            msg = (
                f"\n🟢 [SIGNAL BUY] {display_name}\n"
                f"ราคาปัจจุบัน: {last_close_thb:,.2f} THB\n"
                f"RSI (1h): {last_rsi:.2f}\n"
                f"สถานะกราฟ: {status_context}\n"
                f"📍 ช่วงราคาเข้าซื้อ: {buy_zone} THB\n"
                f"🎯 เป้าขายทำกำไร: {take_profit} THB\n"
                f"❌ จุดตัดขาดทุน: {stop_loss} THB\n"
                f"--------------------------------"
            )
            signals.append(msg)
            
        # 🔴 เงื่อนไขเตือนขาย: RSI Overbought (>= 70) [อัปเดตรายละเอียดใหม่]
        elif last_rsi >= 70:
            is_bear_div = check_bearish_divergence(df, df["RSI"])
            sell_zone = f"{last_close_thb:,.2f} - {(last_close_thb * 1.02):,.2f}"
            re_entry_zone = f"{(last_close_thb * 0.95):,.2f} (หรือแนวรับ EMA50: {last_ema50_thb:,.2f})"
            trailing_stop = f"{(last_close_thb * 0.97):,.2f}"
            
            status_context = "⚠️ RSI Overbought (ซื้อมากเกินไป)"
            if last_close_usd > last_ema200_usd:
                status_context += "\n+ ยืนเหนือเส้น EMA200 (โครงสร้างแข็งแกร่ง แต่อาจย่อตัวระยะสั้น)"
            else:
                status_context += "\n- อยู่ใต้เส้น EMA200 (เด้งเพื่อลงต่อในภาพใหญ่ ระวังแรงเทขาย)"
                
            if is_bear_div:
                status_context += "\n🚨 พบแบร์ริชไดเวอร์เจนท์ (Bearish Divergence) สัญญาณกลับตัวลงรุนแรง!"
                
            msg = (
                f"\n🔴 [SIGNAL SELL] {display_name}\n"
                f"ราคาปัจจุบัน: {last_close_thb:,.2f} THB\n"
                f"RSI (1h): {last_rsi:.2f}\n"
                f"สถานะกราฟ: {status_context}\n"
                f"📍 โซนแบ่งขายทำกำไร: {sell_zone} THB\n"
                f"🎯 รอรับกลับเมื่อย่อตัว: {re_entry_zone} THB\n"
                f"❌ หลุดจุดนี้ควรหนี (Trailing Stop): {trailing_stop} THB\n"
                f"--------------------------------"
            )
            signals.append(msg)

    if signals:
        alert_header = "📊 [Thai Crypto Screener Report]"
        full_message = alert_header + "".join(signals)
        send_line_messaging_api(full_message)
        print("Success! Notification sent to LINE.")
    else:
        print("Process complete: No assets matched the criteria at this hour.")

if __name__ == "__main__":
    screen_crypto()
