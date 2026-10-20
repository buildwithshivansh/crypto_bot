import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
from datetime import datetime

# ==========================================
# 1. STREAMLIT PAGE CONFIG & AUTO REFRESH
# ==========================================
st.set_page_config(
    page_title="Pro Alpha Crypto Bot",
    page_icon="⚡",
    layout="centered"
)

HISTORY_FILE = "trade_history.json"
SYMBOL = "BTC/USDT"
INITIAL_CAPITAL = 100.0

# Auto Refresh Every 15 Seconds
st.empty()

# ==========================================
# 2. PERMANENT TRADE HISTORY DATA STORAGE
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

# Load existing history or init session
if "trade_history" not in st.session_state:
    st.session_state.trade_history = load_history()

if "active_trade" not in st.session_state:
    st.session_state.active_trade = None

# Calculate Total Capital based on initial + realized PnL from history
realized_pnl = sum([t.get("pnl_usd", 0.0) for t in st.session_state.trade_history])
current_total_capital = INITIAL_CAPITAL + realized_pnl

# ==========================================
# 3. KRAKEN DATA & TECHNICAL INDICATORS
# ==========================================
@st.cache_data(ttl=10)
def fetch_market_data():
    try:
        exchange = ccxt.kraken({'enableRateLimit': True})
        ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe='15m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Indicators: 9 EMA & 15 EMA
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema15'] = df['close'].ewm(span=15, adjust=False).mean()
        
        # ATR for Volatility
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        df['atr'] = df['tr'].rolling(window=14).mean()
        
        # Dynamic Support & Resistance (Last 20 candles)
        df['support'] = df['low'].rolling(window=20).min()
        df['resistance'] = df['high'].rolling(window=20).max()
        
        return df
    except Exception as e:
        st.error(f"Error fetching market data: {e}")
        return None

df = fetch_market_data()

# ==========================================
# 4. BOT HEADER & LIVE STATUS
# ==========================================
st.title("⚡ Pro Trading Bot (Super Smart)")
st.caption("9/15 EMA Cross | Dynamic Support/Resistance SL (Max 1%) | Un-capped Trailing")

# Status and Refresh Time
col_sys1, col_sys2 = st.columns(2)
with col_sys1:
    st.markdown("*System Status:* RUNNING 🟢")
with col_sys2:
    st.markdown(f"*Last Refreshed:* {datetime.now().strftime('%H:%M:%S IST')}")

st.divider()

# ==========================================
# 5. CAPITAL & LIVE METRICS DISPLAY
# ==========================================
if df is not None:
    live_price = df['close'].iloc[-1]
    ema9_val = df['ema9'].iloc[-1]
    ema15_val = df['ema15'].iloc[-1]
    atr_val = df['atr'].iloc[-1]
    support_val = df['support'].iloc[-2]  # Recent swing low
    resistance_val = df['resistance'].iloc[-2]  # Recent swing high

    # Calculate active PnL if trade exists
    active_pnl_usd = 0.0
    active_pnl_pct = 0.0
    trade_side = "NONE"
    
    if st.session_state.active_trade is not None:
        trade = st.session_state.active_trade
        entry_price = trade['entry_price']
        trade_side = trade['side']
        
        if trade_side == "BUY":
            active_pnl_pct = ((live_price - entry_price) / entry_price) * 100
        else: # SELL / SHORT
            active_pnl_pct = ((entry_price - live_price) / entry_price) * 100
            
        active_pnl_usd = (trade['position_size'] * active_pnl_pct) / 100

    # Capital Display Metric
    col_cap1, col_cap2, col_cap3 = st.columns(3)
    col_cap1.metric("Total Capital", f"${current_total_capital + active_pnl_usd:.2f}", f"${realized_pnl + active_pnl_usd:+.2f}")
    col_cap2.metric("Live BTC Price", f"${live_price:,.2f}")
    col_cap3.metric("Running PnL", f"{active_pnl_pct:+.2f}%", f"${active_pnl_usd:+.2f}")

    st.divider()

    # ==========================================
    # 6. SUPER SMART DECISION & TRAILING ENGINE
    # ==========================================
    
    # A. NO ACTIVE TRADE -> SCANNING FOR HIGH-ACCURACY SIGNAL
    if st.session_state.active_trade is None:
        st.subheader("🔍 Scanning Market Signals...")
        st.info(f"Checking 9 EMA (${ema9_val:.1f}) vs 15 EMA (${ema15_val:.1f})...")
        
        # BUY SIGNAL (BUY MODE)
        # 1. 9 EMA crosses above 15 EMA
        # 2. Price above 9 EMA
        if ema9_val > ema15_val and df['ema9'].iloc[-2] <= df['ema15'].iloc[-2] and live_price > ema9_val:
            
            # Calculate Smart Stop Loss (Based on Support)
            dist_to_support_pct = ((live_price - support_val) / live_price) * 100
            
            # Max SL strictly <= 1%
            final_sl_pct = min(dist_to_support_pct, 1.0)
            if final_sl_pct <= 0.1: # Fallback if support is too tight
                final_sl_pct = 0.8
                
            sl_price = live_price * (1 - (final_sl_pct / 100.0))
            
            st.session_state.active_trade = {
                "side": "BUY",
                "entry_price": live_price,
                "position_size": current_total_capital,
                "sl_price": sl_price,
                "sl_pct": final_sl_pct,
                "peak_price": live_price,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            st.rerun()

        # SELL SIGNAL (SELLING MODE)
        # 1. 9 EMA crosses below 15 EMA
        # 2. Price below 9 EMA
        elif ema9_val < ema15_val and df['ema9'].iloc[-2] >= df['ema15'].iloc[-2] and live_price < ema9_val:
            
            # Calculate Smart Stop Loss (Based on Resistance)
            dist_to_resistance_pct = ((resistance_val - live_price) / live_price) * 100
            
            final_sl_pct = min(dist_to_resistance_pct, 1.0)
            if final_sl_pct <= 0.1:
                final_sl_pct = 0.8
                
            sl_price = live_price * (1 + (final_sl_pct / 100.0))
            
            st.session_state.active_trade = {
                "side": "SELL",
                "entry_price": live_price,
                "position_size": current_total_capital,
                "sl_price": sl_price,
                "sl_pct": final_sl_pct,
                "peak_price": live_price,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            st.rerun()

    # B. ACTIVE TRADE MANAGEMENT & TRAILING STOP
    else:
        trade = st.session_state.active_trade
        st.subheader(f"⚡ Active Position: {trade['side']} MODE")
        
        col_t1, col_t2, col_t3 = st.columns(3)
        col_t1.write(f"*Entry Price:* ${trade['entry_price']:,.2f}")
        col_t2.write(f"*Trailing SL:* ${trade['sl_price']:,.2f}")
        col_t3.write(f"*Position Size:* ${trade['position_size']:.2f}")

        # TRAILING LOGIC FOR BUY POSITION
        if trade['side'] == "BUY":
            # Update Peak Price
            if live_price > trade['peak_price']:
                trade['peak_price'] = live_price
                # Trail Stop-loss Upward using 1.5x ATR
                new_sl = max(trade['sl_price'], live_price - (1.5 * atr_val))
                
                # SL can never exceed max profit gap or fall below initial SL
                trade['sl_price'] = new_sl

            # CHECK EXIT (Hit Trailing SL / Initial SL)
            if live_price <= trade['sl_price']:
                close_pnl_pct = ((live_price - trade['entry_price']) / trade['entry_price']) * 100
                close_pnl_usd = (trade['position_size'] * close_pnl_pct) / 100
                
                completed_trade = {
                    "side": "BUY",
                    "entry_price": trade['entry_price'],
                    "exit_price": live_price,
                    "entry_time": trade['entry_time'],
                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl_pct": round(close_pnl_pct, 2),
                    "pnl_usd": round(close_pnl_usd, 2)
                }
                
                save_trade_to_history(completed_trade)
                st.session_state.trade_history = load_history()
                st.session_state.active_trade = None
                st.rerun()

        # TRAILING LOGIC FOR SELL POSITION
        elif trade['side'] == "SELL":
            # Update Peak (Lowest Price in Short)
            if live_price < trade['peak_price']:
                trade['peak_price'] = live_price
                # Trail Stop-loss Downward
                new_sl = min(trade['sl_price'], live_price + (1.5 * atr_val))
                trade['sl_price'] = new_sl

            # CHECK EXIT (Hit Trailing SL / Initial SL)
            if live_price >= trade['sl_price']:
                close_pnl_pct = ((trade['entry_price'] - live_price) / trade['entry_price']) * 100
                close_pnl_usd = (trade['position_size'] * close_pnl_pct) / 100
                
                completed_trade = {
                    "side": "SELL",
                    "entry_price": trade['entry_price'],
                    "exit_price": live_price,
                    "entry_time": trade['entry_time'],
                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pnl_pct": round(close_pnl_pct, 2),
                    "pnl_usd": round(close_pnl_usd, 2)
                }
                
                save_trade_to_history(completed_trade)
                st.session_state.trade_history = load_history()
                st.session_state.active_trade = None
                st.rerun()

st.divider()

# ==========================================
# 7. PERMANENT COMPLETED TRADE HISTORY
# ==========================================
st.subheader("📜 Permanent Trade History & Logs")

history_data = load_history()
if len(history_data) > 0:
    hist_df = pd.DataFrame(history_data)
    # Rearrange columns for clean display
    hist_df = hist_df[['entry_time', 'exit_time', 'side', 'entry_price', 'exit_price', 'pnl_pct', 'pnl_usd']]
    hist_df.columns = ['Entry Time', 'Exit Time', 'Type', 'Entry ($)', 'Exit ($)', 'PnL (%)', 'PnL ($)']
    
    st.dataframe(hist_df, use_container_width=True)
else:
    st.info("No completed trades recorded yet. History will save permanently to trade_history.json once a trade closes.")

# 15 second auto re-runner loop
time.sleep(15)
st.rerun()
