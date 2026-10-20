import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
from datetime import datetime

# ==========================================
# 1. STREAMLIT PAGE CONFIG & SETUP
# ==========================================
st.set_page_config(
    page_title="AI Alpha Pro Bot (Elon Musk Multi-TF Edition)",
    page_icon="⚡",
    layout="centered"
)

HISTORY_FILE = "trade_history.json"
SYMBOL = "BTC/USDT"
INITIAL_CAPITAL = 100.0

# ==========================================
# 2. PERMANENT DATA STORAGE (JSON)
# ==========================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_trade_to_history(trade):
    history = load_history()
    history.append(trade)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

if "trade_history" not in st.session_state:
    st.session_state.trade_history = load_history()

if "active_trade" not in st.session_state:
    st.session_state.active_trade = None

realized_pnl = sum([t.get("pnl_usd", 0.0) for t in st.session_state.trade_history])
current_total_capital = INITIAL_CAPITAL + realized_pnl

def get_consecutive_losses(history):
    count = 0
    for trade in reversed(history):
        if trade.get("pnl_usd", 0.0) < 0:
            count += 1
        else:
            break
    return count

consecutive_losses = get_consecutive_losses(st.session_state.trade_history)

# ==========================================
# 3. 15-TIMEFRAME RESAMPLING & AI ENGINE
# ==========================================
@st.cache_data(ttl=12)
def fetch_and_resample_data():
    try:
        exchange = ccxt.kraken({'enableRateLimit': True})
        
        ohlcv_1m = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=200)
        df_1m = pd.DataFrame(ohlcv_1m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'], unit='ms')
        df_1m.set_index('timestamp', inplace=True)
        
        ohlcv_1h = exchange.fetch_ohlcv(SYMBOL, timeframe='1h', limit=200)
        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
        df_1h.set_index('timestamp', inplace=True)
        
        timeframes_rules = {
            '1min': '1min', '3min': '3min', '5min': '5min', '10min': '10min',
            '15min': '15min', '30min': '30min', '45min': '45min',
            '1hr': '1h', '2hr': '2h', '3hr': '3h', '4hr': '4h',
            '6hr': '6h', '12hr': '12h', '1day': '1d', '1week': '1W'
        }
        
        resampled_data = {}
        for tf_name, rule in timeframes_rules.items():
            base_df = df_1m if tf_name in ['1min', '3min', '5min', '10min', '15min', '30min', '45min'] else df_1h
            
            resampled = base_df.resample(rule).agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna()
            
            resampled['ema9'] = resampled['close'].ewm(span=9, adjust=False).mean()
            resampled['ema15'] = resampled['close'].ewm(span=15, adjust=False).mean()
            
            resampled['tr'] = np.maximum(
                resampled['high'] - resampled['low'],
                np.maximum(abs(resampled['high'] - resampled['close'].shift(1)), abs(resampled['low'] - resampled['close'].shift(1)))
            )
            # Safe rolling with min_periods=1 to prevent NaN
            resampled['atr'] = resampled['tr'].rolling(window=14, min_periods=1).mean().fillna(value=10.0)
            resampled['support'] = resampled['low'].rolling(window=15, min_periods=1).min().fillna(value=resampled['low'] * 0.99)
            resampled['resistance'] = resampled['high'].rolling(window=15, min_periods=1).max().fillna(value=resampled['high'] * 1.01)
            
            resampled_data[tf_name] = resampled
            
        return resampled_data
    except Exception as e:
        return None

tf_data = fetch_and_resample_data()

# ==========================================
# 4. DASHBOARD HEADER & METRICS
# ==========================================
st.title("⚡ AI Alpha Pro Bot (Elon Musk Multi-TF Edition)")
st.caption("15 Timeframes Confluence (1m to 1week) | Auto-Sideways Detector | Max 1% Strict SL")

col_sys1, col_sys2 = st.columns(2)
with col_sys1:
    st.markdown("*AI Multi-TF Status:* ONLINE & SCANNING 🟢")
with col_sys2:
    st.markdown(f"*Last Refreshed:* {datetime.now().strftime('%H:%M:%S IST')}")

st.divider()

if tf_data is not None:
    live_price = tf_data['1min']['close'].iloc[-1]
    
    active_pnl_usd = 0.0
    active_pnl_pct = 0.0
    
    if st.session_state.active_trade is not None:
        trade = st.session_state.active_trade
        entry_price = trade['entry_price']
        if trade['side'] == "BUY":
            active_pnl_pct = ((live_price - entry_price) / entry_price) * 100
        else:
            active_pnl_pct = ((entry_price - live_price) / entry_price) * 100
        active_pnl_usd = (trade['position_size'] * active_pnl_pct) / 100

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Total Capital", f"${current_total_capital + active_pnl_usd:.2f}", f"${realized_pnl + active_pnl_usd:+.2f}")
    col_m2.metric("Live BTC", f"${live_price:,.2f}")
    col_m3.metric("Running PnL", f"{active_pnl_pct:+.2f}%", f"${active_pnl_usd:+.2f}")
    col_m4.metric("Loss Streak", f"{consecutive_losses} / 2", "Cool-down Limit")

    st.divider()

    tf_trends = {}
    bullish_count = 0
    bearish_count = 0
    
    for tf, df in tf_data.items():
        if len(df) > 0:
            e9 = df['ema9'].iloc[-1]
            e15 = df['ema15'].iloc[-1]
            trend = "BULLISH" if e9 > e15 else "BEARISH"
            tf_trends[tf] = trend
            if trend == "BULLISH":
                bullish_count += 1
            else:
                bearish_count += 1

    df_15m = tf_data.get('15min')
    range_high = df_15m['resistance'].iloc[-1] if df_15m is not None else live_price * 1.01
    range_low = df_15m['support'].iloc[-1] if df_15m is not None else live_price * 0.99
    range_pct = ((range_high - range_low) / live_price) * 100
    is_sideways = range_pct < 0.6

    if st.session_state.active_trade is None:
        st.subheader("🌐 15-Timeframe AI Confluence Matrix")
        
        tf_df_display = pd.DataFrame(list(tf_trends.items()), columns=['Timeframe', 'Detected Trend'])
        st.dataframe(tf_df_display.set_index('Timeframe').T, use_container_width=True)
        
        st.write(f"*Market State:* {'SIDEWAYS ZONE ⚠️ (No Trade)' if is_sideways else 'TRENDING 🚀'} | *Range:* {range_pct:.2f}%")

        if consecutive_losses >= 2 and is_sideways:
            st.warning("🛑 *Cool-down Active:* 2 consecutive losses + Sideways market detected. Bot is holding fire.")
        elif not is_sideways:
            if bullish_count >= 12:
                support_val = df_15m['support'].iloc[-1] if df_15m is not None else live_price * 0.99
                dist_support = max(((live_price - support_val) / live_price) * 100, 0.2)
                final_sl_pct = min(dist_support, 1.0)
                sl_price = live_price * (1 - (final_sl_pct / 100.0))
                
                st.session_state.active_trade = {
                    "side": "BUY",
                    "entry_price": live_price,
                    "position_size": current_total_capital,
                    "sl_price": sl_price,
                    "peak_price": live_price,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                st.rerun()
            elif bearish_count >= 12:
                resistance_val = df_15m['resistance'].iloc[-1] if df_15m is not None else live_price * 1.01
                dist_res = max(((resistance_val - live_price) / live_price) * 100, 0.2)
                final_sl_pct = min(dist_res, 1.0)
                sl_price = live_price * (1 + (final_sl_pct / 100.0))
                
                st.session_state.active_trade = {
                    "side": "SELL",
                    "entry_price": live_price,
                    "position_size": current_total_capital,
                    "sl_price": sl_price,
                    "peak_price": live_price,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                st.rerun()
            else:
                st.info("⏳ Waiting for high multi-timeframe consensus (at least 12/15 TFs must align)...")
        else:
            st.info("⚠️ Market is consolidating. AI bot waiting for clean breakout.")
            
    else:
        trade = st.session_state.active_trade
        st.subheader(f"⚡ Active AI Position: {trade['side']} MODE")
        
        col_t1, col_t2, col_t3 = st.columns(3)
        col_t1.write(f"*Entry Price:* ${trade['entry_price']:,.2f}")
        col_t2.write(f"*Trailing SL:* ${trade['sl_price']:,.2f}")
        col_t3.write(f"*Position Size:* ${trade['position_size']:.2f}")

        atr_val = df_15m['atr'].iloc[-1] if df_15m is not None else 50.0

        if trade['side'] == "BUY":
            if live_price > trade['peak_price']:
                trade['peak_price'] = live_price
                trade['sl_price'] = max(trade['sl_price'], live_price - (1.5 * atr_val))

            if live_price <= trade['sl_price']:
                pnl_pct = ((live_price - trade['entry_price']) / trade['entry_price']) * 100
                pnl_usd = (trade['position_size'] * pnl_pct) / 100
                completed = {
                    "side": "BUY", "entry_price": trade['entry_price'], "exit_price": live_price,
                    "entry_time": trade['entry_time'], "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2)
                }
                save_trade_to_history(completed)
                st.session_state.trade_history = load_history()
                st.session_state.active_trade = None
                st.rerun()

        elif trade['side'] == "SELL":
            if live_price < trade['peak_price']:
                trade['peak_price'] = live_price
                trade['sl_price'] = min(trade['sl_price'], live_price + (1.5 * atr_val))

            if live_price >= trade['sl_price']:
                pnl_pct = ((trade['entry_price'] - live_price) / trade['entry_price']) * 100
                pnl_usd = (trade['position_size'] * pnl_pct) / 100
                completed = {
                    "side": "SELL", "entry_price": trade['entry_price'], "exit_price": live_price,
                    "entry_time": trade['entry_time'], "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2)
                }
                save_trade_to_history(completed)
                st.session_state.trade_history = load_history()
                st.session_state.active_trade = None
                st.rerun()

st.divider()
st.subheader("📜 Permanent Trade History & Logs")
history_data = load_history()
if len(history_data) > 0:
    hist_df = pd.DataFrame(history_data)
    hist_df = hist_df[['entry_time', 'exit_time', 'side', 'entry_price', 'exit_price', 'pnl_pct', 'pnl_usd']]
    hist_df.columns = ['Entry Time', 'Exit Time', 'Type', 'Entry ($)', 'Exit ($)', 'PnL (%)', 'PnL ($)']
    st.dataframe(hist_df, use_container_width=True)
else:
    st.info("No completed trades recorded yet. History saves permanently in trade_history.json.")

time.sleep(15)
st.rerun()
