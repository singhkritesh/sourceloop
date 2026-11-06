# Does misreporting a limit pay?

Replay-mode negotiations on synthetic data, seeds [42, 43, 44]. Rule-based moves; a live model may differ. Prices are synthetic.

## A supplier that overstates its floor

Only runs where the truthful supplier won are compared. Gain is the supplier's surplus over its TRUE floor (0 when it loses the award), versus telling the truth.

| Reported margin | Runs | Still wins | Price change per unit | Mean gain (currency) |
|---|---|---|---|---|
| +0 points | 33 | 100% | +0.000 | +0 |
| +5 points | 33 | 88% | +1.081 | +6792 |
| +10 points | 33 | 67% | +2.032 | -3075 |
| +20 points | 33 | 45% | +2.458 | -5733 |
| +40 points | 33 | 24% | +3.078 | -4810 |

## A buyer that understates its highest price

Saving is the price paid versus the truthful run, times volume (0 when no deal is reached).

| Reported highest price | Runs | Deal reached | Price change per unit | Mean saving (currency) |
|---|---|---|---|---|
| -0 % | 33 | 100% | +0.000 | +0 |
| -5 % | 33 | 100% | -0.506 | +253 |
| -10 % | 33 | 94% | -5.108 | +16735 |
| -20 % | 33 | 88% | -5.492 | +16864 |
## How to read this

- It is a measurement on synthetic data with rule-based moves, 33 negotiations. It is not a proof, and a live model may behave differently.
- **The mechanism is not strategy-proof.** A small overstatement by the supplier (+5 margin points) raised its surplus on average, even though it lost the award in 12% of runs. Larger overstatements lost more deals than they gained, on average. A buyer that understates its highest price by 10 to 20% saved money on average and lost 6 to 12% of deals.
- So the honest claim for the deck is: the platform keeps each side's real limits private and inside code, and it does not remove the incentive to shade a limit. What it adds is that shading carries a measurable risk of losing the deal, and every shaded limit is a signed mandate in the audit trail.
- Reproduce with `python -m sourceloop.eval.fairness` (the `ScenarioSpec.supplier_margin` override exists for this measurement only).
