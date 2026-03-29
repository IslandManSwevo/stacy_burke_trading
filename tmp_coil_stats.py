import pandas as pd
import numpy as np

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def check_coil_stats(file_path):
    df = pd.read_csv(file_path)
    # Correct date parsing
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    
    # ATR 14
    df['tr'] = np.maximum(df['high'] - df['low'], 
                          np.maximum(abs(df['high'] - df['close'].shift(1)), 
                                     abs(df['low'] - df['close'].shift(1))))
    df['atr14'] = df['tr'].rolling(window=14).mean()
    
    # EMAs
    ema9 = compute_ema(df['close'], 9)
    ema20 = compute_ema(df['close'], 20)
    ema50 = compute_ema(df['close'], 50)
    
    # Spread
    df['max_ema'] = np.maximum(ema9, np.maximum(ema20, ema50))
    df['min_ema'] = np.minimum(ema9, np.minimum(ema20, ema50))
    df['spread'] = df['max_ema'] - df['min_ema']
    df['spread_atr'] = df['spread'] / df['atr14']
    
    # Stats
    # Stats
    if len(df) == 0:
        print("No data to analyze")
        return
    for threshold in [0.5, 0.75, 1.0, 1.25]:
        count = (df['spread_atr'] <= threshold).sum()
        pct = count / len(df) * 100
        print(f"Threshold {threshold:4.2f}x ATR: {count:4d} days ({pct:4.1f}%)")

if __name__ == '__main__':
    print("EURUSD Daily EMA Coil Stats (2022-2024):")
    check_coil_stats('backtest_data/EURUSD_D1.csv')
