import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import time
from datetime import datetime

# Page Configuration for Web App Look
st.set_page_config(
    page_title="Tesla-Grade AI Crypto Bot",
    page_icon="⚡",
    layout="wide"
)

# Custom Styling for Clean Dashboard
st.markdown("""
    <style>
    .main {background-color: #0e1117;}
    .metric-card {background-color: #161b22; padding: 15px; border-radius: 10px; border: 1px solid #30363d;}
    </style>
""", unsafe_allow_html=True)

st.title("⚡ AI Crypto Paper Trading Dashboard (BTC/USDT)")
st.markdown("---")

# Initialize Session State Variables
if 'usdt_balance' not in st.session_state:
    st.session_state.usdt_balance = 100.0
if 'in_position' not in st.session_state:
    st.session_state.in_position = False
if 'buy_price' not in st.session_state:
    st.session_state.buy_price = 0.0
if 'position_size' not in st.session_state:
    st.session_state.position_size = 0.0
if 'stop_loss' not in st.session_state:
    st.session_state.stop_loss = 0.0
if 'consecutive_losses' not in st.session_state:
    st.session_state.consecutive_losses = 0
if 'trade_history' not in st.session_state:
    st.session_state.trade_history = []
if 'logs' not in st.session_state:
    st.session_state.logs = []

# Optimized Data Fetching with Caching (Prevents API Rate Limits)
@st.cache_data(ttl=15)
def fetch_binance_data():
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1m', limit=120)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Calculate RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # Calculate EMAs
        df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
        return df
    except Exception as e:
        return None

df = fetch_binance_data()

if df is None or df.empty:
    st.warning("⚠️ Binance server se connection establish ho raha hai... Thoda wait karein.")
    time.sleep(5)
    st.rerun()

current_price = df['close'].iloc[-1]
current_rsi = df['RSI'].iloc[-1]
ema_9 = df['EMA_9'].iloc[-1]
ema_21 = df['EMA_21'].iloc[-1]

# Sidebar: Capital & Wallet Management
st.sidebar.header("💼 Wallet & Risk Controls")
st.sidebar.metric("Available Capital", f"${st.session_state.usdt_balance:,.2f} USDT")
st.sidebar.markdown("---")
st.sidebar.write("*Strategy Rules:*")
st.sidebar.info("• *Entry:* RSI < 40 & EMA 9 > 21\n• *Risk:* 1% Initial Stop-Loss\n• *Reward:* Open Trailing Stop-Loss\n• *Safety:* Max 2 Consecutive Losses Circuit Breaker")

# Live PnL Calculation
if st.session_state.in_position:
    pnl_pct = ((current_price - st.session_state.buy_price) / st.session_state.buy_price) * 100
    pnl_usd = (pnl_pct / 100) * st.session_state.position_size
else:
    pnl_pct = 0.0
    pnl_usd = 0.0

# Main Dashboard Top Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Live BTC Price", f"${current_price:,.2f}")
col2.metric("Position Status", "🟢 IN TRADE" if st.session_state.in_position else "⚪ SCANNING")
col3.metric("Running PnL", f"{pnl_pct:+.2f}%", f"${pnl_usd:+.2f}")
col4.metric("Consecutive Losses", f"{st.session_state.consecutive_losses} / 2")

st.markdown("---")

# Layout: Chart on Left/Top, Details on Right/Bottom
st.subheader("📊 Live BTC/USDT Market Chart & Indicators")
fig = go.Figure(data=[go.Candlestick(
    x=df['timestamp'], open=df['open'], high=df['high'], low=df['low'], close=df['close'],
    name="BTC Candles"
)])
fig.add_trace(go.Scatter(x=df['timestamp'], y=df['EMA_9'], mode='lines', name='EMA 9', line=dict(color='orange', width=1.5)))
fig.add_trace(go.Scatter(x=df['timestamp'], y=df['EMA_21'], mode='lines', name='EMA 21', line=dict(color='cyan', width=1.5)))
fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# Smart Trading Logic & Execution
if st.session_state.consecutive_losses < 2:
    if not st.session_state.in_position:
        # Buy Condition
        if current_rsi < 40 and ema_9 > ema_21:
            st.session_state.in_position = True
            st.session_state.buy_price = current_price
            st.session_state.position_size = st.session_state.usdt_balance  # Compounding full wallet
            st.session_state.stop_loss = current_price * 0.99  # 1% Strict SL
            
            # Lot / Unit calculation example
            btc_lots = st.session_state.position_size / current_price
            log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 BUY: ${current_price:,.2f} | Size: ${st.session_state.position_size:.2f} ({btc_lots:.4f} BTC) | SL: ${st.session_state.stop_loss:,.2f}"
            st.session_state.logs.append(log_msg)
            st.rerun()
    else:
        # Trailing Stop Loss Management
        new_sl = current_price * 0.995 # 0.5% trailing lock
        if new_sl > st.session_state.stop_loss:
            st.session_state.stop_loss = new_sl
            st.session_state.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] 📈 TRAILING SL UPDATED -> ${st.session_state.stop_loss:,.2f}")

        # Exit / Stop Loss Hit Condition
        if current_price <= st.session_state.stop_loss:
            sell_price = st.session_state.stop_loss
            trade_pnl_usd = ((sell_price - st.session_state.buy_price) / st.session_state.buy_price) * st.session_state.position_size
            st.session_state.usdt_balance += trade_pnl_usd
            
            # Record Trade in History
            trade_record = {
                "Time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "Type": "BUY -> SELL",
                "Buy Price": f"${st.session_state.buy_price:,.2f}",
                "Exit Price": f"${sell_price:,.2f}",
                "PnL ($)": f"${trade_pnl_usd:+,.2f}",
                "New Balance": f"${st.session_state.usdt_balance:,.2f}"
            }
            st.session_state.trade_history.insert(0, trade_record)

            if trade_pnl_usd < 0:
                st.session_state.consecutive_losses += 1
            else:
                st.session_state.consecutive_losses = 0

            st.session_state.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🔴 SELL: ${sell_price:,.2f} | PnL: ${trade_pnl_usd:+,.2f}")
            st.session_state.in_position = False
            st.rerun()
else:
    st.error("🚨 Circuit Breaker Active: 2 consecutive losses reached. Bot is paused for safety.")

# Detailed Section: Active Trade Info & Trade History Table
col_info1, col_info2 = st.columns(2)

with col_info1:
    st.subheader("📋 Active Trade Details")
    if st.session_state.in_position:
        st.write(f"*Buy Price:* ${st.session_state.buy_price:,.2f}")
        st.write(f"*Current Stop-Loss:* ${st.session_state.stop_loss:,.2f}")
        st.write(f"*Invested Amount / Lots:* ${st.session_state.position_size:,.2f} (~{(st.session_state.position_size/current_price):.4f} BTC)")
        st.write(f"*Running PnL:* ${pnl_usd:+,.2f} ({pnl_pct:+.2f}%)")
    else:
        st.info("No active trade right now. Scanning market conditions...")

with col_info2:
    st.subheader("📜 Execution Logs")
    log_text = "\n".join(reversed(st.session_state.logs[-6:]))
    st.text_area("Live Logs", value=log_text, height=130, label_visibility="collapsed")

# Complete Trade History Table Section
st.markdown("---")
st.subheader("🏆 Completed Trade History & Performance")
if len(st.session_state.trade_history) > 0:
    history_df = pd.DataFrame(st.session_state.trade_history)
    st.dataframe(history_df, use_container_width=True)
else:
    st.write("Abhi tak koi trade complete nahi hua hai. Jaise hi bot trade execute karke close karega, yahan history dikhegi.")

# Auto-refresh app every 5 seconds for smooth real-time monitoring without lag
time.sleep(5)
st.rerun()