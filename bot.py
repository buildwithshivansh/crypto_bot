import ccxt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import time

# ==========================================
# 1. PARAMETERS & CONFIGURATION
# ==========================================
SYMBOL = "BTC/USDT"
TIMEFRAME_MICRO = "5m"
TIMEFRAME_MACRO = "1h"
RISK_REWARD_RATIO = 1.5           # 1 : 1.5 RR
ML_CONFIDENCE_THRESHOLD = 0.62    # Minimum 62% ML probability
FEE_DISCOUNT_MAKER_RATE = 0.00012 # ~0.012% Discounted Maker Fee Rate

# Initialize Exchange (Using public CCXT fetch for Binance Testnet / Live data)
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'} # Futures market setup
})

# ==========================================
# 2. DATA FETCHER & INDICATORS (CCXT)
# ==========================================
def fetch_live_data():
    """Fetches real-time OHLCV candles from the exchange via CCXT."""
    try:
        print(f"🔄 Fetching live data for {SYMBOL}...")
        
        # Fetch 5-minute candles (Micro timeframe)
        ohlcv_5m = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME_MICRO, limit=200)
        df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_5m['timestamp'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
        df_5m.set_index('timestamp', inplace=True)
        
        # Fetch 1-hour candles (Macro timeframe)
        ohlcv_1h = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME_MACRO, limit=100)
        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
        df_1h.set_index('timestamp', inplace=True)
        
        return df_5m, df_1h
    except Exception as e:
        print(f"❌ Error fetching data from exchange: {e}")
        return None, None

def calculate_atr(df, period=14):
    """Calculate Average True Range for dynamic volatility stop-loss/take-profit."""
    df['high_low'] = df['high'] - df['low']
    df['high_close'] = np.abs(df['high'] - df['close'].shift())
    df['low_close'] = np.abs(df['low'] - df['close'].shift())
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=period).mean()
    return df

def generate_multi_timeframe_features(df_5m, df_1h):
    """Build multi-timeframe feature matrix: 1H trend + 5M momentum & ATR."""
    df = df_5m.copy()
    
    # Micro features (5-minute)
    df['returns_5m'] = df['close'].pct_change()
    df['volatility_5m'] = df['returns_5m'].rolling(window=12).std()
    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema_diff_5m'] = df['ema_fast'] - df['ema_slow']
    
    # ATR Calculation
    df = calculate_atr(df)
    
    # Macro feature (1-Hour Trend Alignment)
    df_1h['ema_1h'] = df_1h['close'].ewm(span=50, adjust=False).mean()
    df_1h['macro_trend'] = np.where(df_1h['close'] > df_1h['ema_1h'], 1, -1)
    
    # Merge Macro trend into 5M dataframe
    df = df.join(df_1h[['macro_trend']], how='left').ffill()
    
    # Target variable for ML training
    df['target'] = np.where(df['close'].shift(-3) > df['close'], 1, 0)
    
    return df.dropna()

# ==========================================
# 3. MACHINE LEARNING MODEL TRAINER
# ==========================================
def train_quant_model(df):
    features = ['returns_5m', 'volatility_5m', 'ema_diff_5m', 'atr', 'macro_trend']
    X = df[features]
    y = df['target']
    
    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    model.fit(X, y)
    return model, features

# ==========================================
# 4. EXECUTION ENGINE (1:1.5 RR + CIRCUIT BREAKER)
# ==========================================
class QuantExecutionEngine:
    def _init_(self, capital=100.0, position_ratio=0.8):
        self.capital = capital
        self.position_ratio = position_ratio
        self.active_position = None
        self.consecutive_losses = 0
        self.shutdown_until = None

    def check_circuit_breaker(self):
        """Checks if the bot is locked out due to 3 consecutive losses."""
        if self.shutdown_until and time.time() < self.shutdown_until:
            remaining_minutes = int((self.shutdown_until - time.time()) / 60)
            print(f"🛑 [CIRCUIT BREAKER ACTIVE] System locked for {remaining_minutes} more minutes.")
            return True
        elif self.shutdown_until and time.time() >= self.shutdown_until:
            print("🟢 [CIRCUIT BREAKER] Cooldown expired. Resuming trading operations.")
            self.shutdown_until = None
            self.consecutive_losses = 0
        return False

    def record_trade_outcome(self, is_win):
        """Updates consecutive loss counter and locks system if threshold met."""
        if is_win:
            print("✅ [TRADE WON] Resetting consecutive loss counter.")
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            print(f"⚠️ [TRADE LOST] Consecutive losses count: {self.consecutive_losses}/3")
            if self.consecutive_losses >= 3:
                self.shutdown_until = time.time() + (12 * 3600)
                print("🚨 [CRITICAL ALERT] 3 consecutive losses hit! System shutting down for 12 hours.")

    def execute_trade_with_sl_tp(self, current_price, atr_value, signal_type):
        """Executes a Limit Maker order with rigid Stop-Loss and Take-Profit bounds."""
        if self.check_circuit_breaker():
            return
            
        stop_loss_distance = atr_value * 1.5
        take_profit_distance = stop_loss_distance * RISK_REWARD_RATIO
        
        if signal_type == "BUY":
            entry_price = current_price
            stop_loss = entry_price - stop_loss_distance
            take_profit = entry_price + take_profit_distance
        else:
            entry_price = current_price
            stop_loss = entry_price + stop_loss_distance
            take_profit = entry_price - take_profit_distance
            
        position_size = self.capital * self.position_ratio
        
        self.active_position = {
            'type': signal_type,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_size': position_size,
            'status': 'OPEN'
        }
        
        print("=" * 60)
        print(f"🚀 [ORDER EXECUTED - LIMIT MAKER]")
        print(f"Type        : {signal_type}")
        print(f"Entry Price : ${entry_price:.2f}")
        print(f"Stop Loss   : ${stop_loss:.2f} (Risk: ${stop_loss_distance:.2f})")
        print(f"Take Profit : ${take_profit:.2f} (Reward: ${take_profit_distance:.2f})")
        print(f"RR Ratio    : 1 : {RISK_REWARD_RATIO}")
        print("=" * 60)

# ==========================================
# 5. MAIN EXECUTION LOOP
# ==========================================
if _name_ == "_main_":
    # Fetch live data from exchange
    df_5m, df_1h = fetch_live_data()
    
    if df_5m is not None and df_1h is not None:
        # Process features & train model
        data = generate_multi_timeframe_features(df_5m, df_1h)
        model, feature_cols = train_quant_model(data)
        
        # Initialize Engine with $100 virtual capital
        engine = QuantExecutionEngine(capital=100.0)
        
        # Predict on latest market bar
        latest_bar = data[feature_cols].iloc[[-1]]
        latest_price = data['close'].iloc[-1]
        latest_atr = data['atr'].iloc[-1]
        
        probs = model.predict_proba(latest_bar)[0] # [Prob_Loss, Prob_Win]
        win_prob = probs[1]
        
        print(f"\n📊 Latest Market Signal Confidence: {win_prob * 100:.2f}%")
        
        # Execute only if High Conviction
        if win_prob >= ML_CONFIDENCE_THRESHOLD:
            engine.execute_trade_with_sl_tp(latest_price, latest_atr, signal_type="BUY")
        else:
            print("⚠️ Signal below threshold (Low Conviction). No trade executed.")
