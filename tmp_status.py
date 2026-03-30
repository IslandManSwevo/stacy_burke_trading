import csv
import sys

print("=== ALL ACCEPTED TRADES ===")
try:
    with open("backtest_results.csv") as f:
        rows = list(csv.DictReader(f))
except FileNotFoundError:
    print("backtest_results.csv not found, skipping accepted trades.")
    rows = []

for r in rows:
    print(f"  {r['pair']:8s} {r['pattern']:20s} {r['direction']:6s} {r['terminal_state']:20s} r={r['r_multiple']:>6s}  score={r['score']}")

wins = sum(1 for r in rows if float(r["r_multiple"]) > 0)
total = len(rows)
total_r = sum(float(r["r_multiple"]) for r in rows)
gross_win = sum(float(r["r_multiple"]) for r in rows if float(r["r_multiple"]) > 0)
gross_loss = abs(sum(float(r["r_multiple"]) for r in rows if float(r["r_multiple"]) < 0))
pf = gross_win / gross_loss if gross_loss else float("inf")
print()
wr = 100 * wins // total if total else 0
print(f"  Total: {total}  Wins: {wins}  WR: {wr}%  TotalR: {total_r:.2f}  PF: {pf:.2f}")
print()
print("=== WOULD_HAVE_HIT (below_min_score leaks) ===")
try:
    with open("backtest_discards_would_have_hit.csv") as f:
        rows2 = list(csv.DictReader(f))
    print("Cols:", list(rows2[0].keys()) if rows2 else "empty")
    for r in rows2[:15]:
        print(dict(r))
except Exception as e:
    print("Error:", e)

print()
print("=== DISCARD SUMMARY (top 20) ===")
try:
    with open("backtest_discards_summary.csv") as f:
        rows3 = list(csv.DictReader(f))
    # Show all BELOW_MIN_SCORE
    bms = [r for r in rows3 if r["reason"] == "BELOW_MIN_SCORE"]
    print("BELOW_MIN_SCORE entries:")
    for r in bms:
        print(f"  {r['pattern']:30s} count={r['count']}")
except FileNotFoundError:
    print("backtest_discards_summary.csv not found. Skipping summary.")
except Exception as e:
    print("Error reading backtest_discards_summary.csv:", e)
