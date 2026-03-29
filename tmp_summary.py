import pandas as pd
df = pd.read_csv('backtest_results.csv')
print(f'Total Trades: {len(df)}')
print(f'Total R: {df["r_multiple"].sum():.2f}')
print(f'Win Rate: {(df["r_multiple"] > 0).mean():.1%}')
print(f'EMA Coils Fired: {df["ema_coil_confirmed"].sum()}')
print(f'Litmus Passed: {df["litmus_passed"].sum()}')
print('\nPattern Breakdown:')
print(df['pattern'].value_counts())
