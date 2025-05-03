import pandas as pd
import numpy as np
import json
from itertools import product

# Load and preprocess the market data
df = pd.read_csv('l1_day.csv', parse_dates=['ts_event'])
df = df.sort_values(['ts_event', 'sequence'])
df = df.drop_duplicates(subset=['ts_event', 'publisher_id'], keep='first')
timestamps = df['ts_event'].unique()
venues_ids = df['publisher_id'].unique()

# Define constants
ORDER_SIZE = 5000
FEE = 0.00  #Assumed zero
REBATE = 0.00  #Assumed zero

# Allocator functions
def compute_cost(split, venues, order_size, lam_over, lam_under, theta):
    executed = 0
    cash_spent = 0
    for i, venue in enumerate(venues):
        exe = min(split[i], venue['ask_size'])
        executed += exe
        cash_spent += exe * (venue['ask'] + venue['fee'])
        maker_rebate = max(split[i] - exe, 0) * venue['rebate']
        cash_spent -= maker_rebate
    underfill = max(order_size - executed, 0)
    overfill = max(executed - order_size, 0)
    risk_pen = theta * (underfill + overfill)
    cost_pen = lam_under * underfill + lam_over * overfill
    return cash_spent + risk_pen + cost_pen

def allocate(order_size, venues, lam_over, lam_under, theta, step=100):
    splits = [[]]
    for v in range(len(venues)):
        new_splits = []
        for alloc in splits:
            used = sum(alloc)
            max_v = min(order_size - used, venues[v]['ask_size'])
            for q in range(0, max_v + step, step):
                if used + q > order_size:
                    break
                new_splits.append(alloc + [q])
        splits = new_splits

    best_cost = float('inf')
    best_split = None
    for alloc in splits:
        if sum(alloc) != order_size:
            continue
        cost = compute_cost(alloc, venues, order_size, lam_over, lam_under, theta)
        if cost < best_cost:
            best_cost = cost
            best_split = alloc
    return best_split, best_cost

# Simulation function for the tuned router
def simulate_router(order_size, df, timestamps, venues_ids, lam_over, lam_under, theta, fee, rebate):
    remaining = order_size
    total_cash_spent = 0
    total_executed = 0
    for ts in timestamps:
        if remaining <= 0:
            break
        snapshot = df[df['ts_event'] == ts]
        venues = [
            {
                'ask': venue_data['ask_px_00'].iloc[0],
                'ask_size': venue_data['ask_sz_00'].iloc[0],
                'fee': fee,
                'rebate': rebate
            }
            for vid in venues_ids
            if not (venue_data := snapshot[snapshot['publisher_id'] == vid]).empty
        ]
        if not venues:
            continue
        best_split, _ = allocate(remaining, venues, lam_over, lam_under, theta)
        if best_split is None:
            continue
        executed = sum(min(split_i, venues[i]['ask_size']) for i, split_i in enumerate(best_split))
        cash_spent = sum(
            min(split_i, venues[i]['ask_size']) * (venues[i]['ask'] + venues[i]['fee'])
            for i, split_i in enumerate(best_split)
        )
        total_cash_spent += cash_spent
        total_executed += executed
        remaining -= executed
    avg_fill_price = total_cash_spent / total_executed if total_executed > 0 else 0
    return total_cash_spent, avg_fill_price, total_executed

# Baseline: Naïve "take the best ask"
def simulate_naive(order_size, df, timestamps, venues_ids, fee):
    remaining = order_size
    total_cash_spent = 0
    total_executed = 0
    for ts in timestamps:
        if remaining <= 0:
            break
        snapshot = df[df['ts_event'] == ts]
        min_ask = float('inf')
        best_venue = None
        for vid in venues_ids:
            venue_data = snapshot[snapshot['publisher_id'] == vid]
            if not venue_data.empty and venue_data['ask_px_00'].iloc[0] < min_ask:
                min_ask = venue_data['ask_px_00'].iloc[0]
                best_venue = venue_data
        if best_venue is not None:
            exe = min(remaining, best_venue['ask_sz_00'].iloc[0])
            total_executed += exe
            total_cash_spent += exe * (min_ask + fee)
            remaining -= exe
    avg_fill_price = total_cash_spent / total_executed if total_executed > 0 else 0
    return total_cash_spent, avg_fill_price, total_executed

# Baseline: Sixty-second-bucket TWAP (simplified as fixed amount per snapshot)
def simulate_twap(order_size, df, timestamps, venues_ids, fee):
    total_cash_spent = 0
    total_executed = 0
    remaining = order_size
    amount_per_snapshot = order_size / len(timestamps) if timestamps.size > 0 else 0
    for ts in timestamps:
        if remaining <= 0:
            break
        snapshot = df[df['ts_event'] == ts]
        min_ask = float('inf')
        best_venue = None
        for vid in venues_ids:
            venue_data = snapshot[snapshot['publisher_id'] == vid]
            if not venue_data.empty and venue_data['ask_px_00'].iloc[0] < min_ask:
                min_ask = venue_data['ask_px_00'].iloc[0]
                best_venue = venue_data
        if best_venue is not None:
            exe = min(amount_per_snapshot, best_venue['ask_sz_00'].iloc[0], remaining)
            total_executed += exe
            total_cash_spent += exe * (min_ask + fee)
            remaining -= exe
    if remaining > 0 and timestamps.size > 0:
        last_snapshot = df[df['ts_event'] == timestamps[-1]]
        min_ask = min(
            (venue_data['ask_px_00'].iloc[0] for vid in venues_ids
             if not (venue_data := last_snapshot[last_snapshot['publisher_id'] == vid]).empty),
            default=float('inf')
        )
        if min_ask != float('inf'):
            total_cash_spent += remaining * (min_ask + fee)
            total_executed += remaining
    avg_fill_price = total_cash_spent / total_executed if total_executed > 0 else 0
    return total_cash_spent, avg_fill_price, total_executed

# Baseline: VWAP weighted by ask size
def simulate_vwap(order_size, df, timestamps, venues_ids, fee):
    remaining = order_size
    total_cash_spent = 0
    total_executed = 0
    for ts in timestamps:
        if remaining <= 0:
            break
        snapshot = df[df['ts_event'] == ts]
        total_ask_size = sum(
            venue_data['ask_sz_00'].iloc[0]
            for vid in venues_ids
            if not (venue_data := snapshot[snapshot['publisher_id'] == vid]).empty
        )
        if total_ask_size == 0:
            continue
        for vid in venues_ids:
            venue_data = snapshot[snapshot['publisher_id'] == vid]
            if not venue_data.empty:
                split_i = remaining * (venue_data['ask_sz_00'].iloc[0] / total_ask_size)
                exe = min(split_i, venue_data['ask_sz_00'].iloc[0])
                total_executed += exe
                total_cash_spent += exe * (venue_data['ask_px_00'].iloc[0] + fee)
                remaining -= exe
    avg_fill_price = total_cash_spent / total_executed if total_executed > 0 else 0
    return total_cash_spent, avg_fill_price, total_executed

# Parameter tuning
param_grid = {
    'lam_over': [0.001, 0.005, 0.01],
    'lam_under': [0.001, 0.005, 0.01],
    'theta': [0, 0.0005, 0.001]
}
best_cost = float('inf')
best_params = None
for lam_over, lam_under, theta in product(param_grid['lam_over'], param_grid['lam_under'], param_grid['theta']):
    total_cash, _, executed = simulate_router(ORDER_SIZE, df, timestamps, venues_ids, lam_over, lam_under, theta, FEE, REBATE)
    if executed == ORDER_SIZE and total_cash < best_cost:
        best_cost = total_cash
        best_params = {'lambda_over': lam_over, 'lambda_under': lam_under, 'theta_queue': theta}

# Final simulations with best parameters and baselines
total_cash_tuned, avg_fill_tuned, _ = simulate_router(
    ORDER_SIZE, df, timestamps, venues_ids,
    best_params['lambda_over'], best_params['lambda_under'], best_params['theta_queue'], FEE, REBATE
)
total_cash_naive, avg_fill_naive, _ = simulate_naive(ORDER_SIZE, df, timestamps, venues_ids, FEE)
total_cash_twap, avg_fill_twap, _ = simulate_twap(ORDER_SIZE, df, timestamps, venues_ids, FEE)
total_cash_vwap, avg_fill_vwap, _ = simulate_vwap(ORDER_SIZE, df, timestamps, venues_ids, FEE)

# Compute savings in basis points
savings_naive = (total_cash_naive - total_cash_tuned) / total_cash_tuned * 10000
savings_twap = (total_cash_twap - total_cash_tuned) / total_cash_tuned * 10000
savings_vwap = (total_cash_vwap - total_cash_tuned) / total_cash_tuned * 10000

# Prepare and output JSON result
result = {
    "best_parameters": best_params,
    "tuned_router": {
        "total_cash_spent": total_cash_tuned,
        "average_fill_price": avg_fill_tuned
    },
    "naive_strategy": {
        "total_cash_spent": total_cash_naive,
        "average_fill_price": avg_fill_naive
    },
    "twap_strategy": {
        "total_cash_spent": total_cash_twap,
        "average_fill_price": avg_fill_twap
    },
    "vwap_strategy": {
        "total_cash_spent": total_cash_vwap,
        "average_fill_price": avg_fill_vwap
    },
    "savings_vs_naive_bp": savings_naive,
    "savings_vs_twap_bp": savings_twap,
    "savings_vs_vwap_bp": savings_vwap
}
print(json.dumps(result, indent=4))