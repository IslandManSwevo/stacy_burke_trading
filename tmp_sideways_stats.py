import pandas as pd
import numpy as np

def check_sideways_stats(file_path):
    df = pd.read_csv(file_path)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    
    # ATR 14
    df['tr'] = np.maximum(df['high'] - df['low'], 
                          np.maximum(abs(df['high'] - df['close'].shift(1)), 
                                     abs(df['low'] - df['close'].shift(1))))
    df['atr14'] = df['tr'].rolling(window=14).mean()
    
    # 3-day range
    df['high3'] = df['high'].rolling(window=3).max()
    df['low3'] = df['low'].rolling(window=3).min()
    df['range3'] = df['high3'] - df['low3']
    df['range3_atr'] = df['range3'] / df['atr14']
    
    # Stats
    if len(df) == 0:
        print("no data")
        return

    for threshold in [0.75, 1.0, 1.25, 1.5, 2.0]:
        count = (df['range3_atr'] <= threshold).sum()
        pct = count / len(df) * 100
        print(f"3-Day Range {threshold:4.2f}x ATR: {count:4d} days ({pct:4.1f}%)")

if __name__ == '__main__':
    print("EURUSD Daily Sideways Stats (2022-2024):")
    check_sideways_stats('backtest_data/EURUSD_D1.csv')
