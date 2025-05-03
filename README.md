README.md
==========

## 1 . Purpose

This repo contains **`backtest.py`**, a self‑contained script that reproduces the static Cont–Kukanov smart‑order‑routing (SOR) experiment. It

* parses the 60 k‑message Level‑1 feed (`l1_day.csv`);
* exhaustively searches risk‑penalty triplets **(λ_over, λ_under, θ_queue)**;
* executes the exact allocator pseudocode on every snapshot;
* benchmarks the tuned router against three baselines (Best‑Ask, bucket TWAP, size‑weighted VWAP);
* prints one JSON block with totals, averages and basis‑point savings.

Everything runs in < 90 s on a laptop using only **pandas**, **numpy** and the standard library.

---

## 2 . Code structure

| Section          | Key objects / functions              | Responsibility                                                         |
|------------------|--------------------------------------|------------------------------------------------------------------------|
| Pre‑processing   | `df.sort_values`, `drop_duplicates`  | Normalises raw feed to one snapshot per venue per `ts_event`.          |
| Allocator core   | `compute_cost`, `allocate`           | Implements the cost model + exhaustive search (100‑share granularity). |
| Simulation loop  | `simulate_router`                    | Rolls the allocator forward, tracking remaining qty & cash.            |
| Baselines        | `simulate_naive`, `simulate_twap`, `simulate_vwap` | Straightforward reference executions.                                  |
| Parameter tuning | triple nested loop over `param_grid` | Tests multiple triplets; keeps the one that completes the order cheapest.    |
| Output           | `json.dumps(result)`                 | Formats the performance summary.                                       |

---

## 3 . Search choices

| Parameter | Grid                     | Rationale                                           |
|-----------|--------------------------|-----------------------------------------------------|
| λ_over    | 0.001, 0.005, 0.01       | 0.1  – 1 cent  per extra share (≈ 0.1 – 1 × spread) |
| λ_under   | 0.001, 0.005, 0.01       | Symmetric penalties for under‑fills.                |
| θ_queue   | 0, 0.0005, 0.001         | Small linear premium to avoid queue risk.           |

27 points keep the grid tiny; increasing the grid results in higher computational time.

---

## 4 . Suggested improvement (fill realism)

The back‑test assumes any displayed size executes immediately. A more realistic model would:

1. **Track queue position** – Record where your order sits in the visible queue and grant fills only when the running total of aggressive sells + order cancellations that print ahead of you exceeds that position. This prevents the overly-optimistic “displayed size = immediate fill” assumption.  
2. **Model hidden depth & slippage** – rWhen the displayed top-of-book (ask_sz_00) is insufficient, route the remainder down the book (or to dark pools) and apply an adverse-selection penalty that grows with depth and volatility. This captures hidden liquidity and the higher cost of sweeping multiple levels.
3. **Pacing / participation constraints** - Throttle the size of each  slice so that it does not exceed a configurable participation rate (e.g., ≤ 10 % of the last X-second traded volume). Pacing mitigates information leakage, limits market impact, and aligns fills with available liquidity—especially important for larger parent orders or thinly-traded venues.

Any of these would tighten cost estimates and further reduce cash slippage in live markets.
