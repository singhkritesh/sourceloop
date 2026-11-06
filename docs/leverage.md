# Does stating competing offers move the price?

Replay-mode negotiations on synthetic data, seeds [42, 43, 44], 36 runs, each run twice (without and with the statement). Rule-based moves; a live model may react differently. Prices are synthetic.

| Runs | Count | A statement was made | Deal without | Deal with | Price change per unit | Mean saving (currency) |
|---|---|---|---|---|---|---|
| All runs | 36 | 86% | 92% | 92% | -0.152 | +125.797 |
| Runs where a rival was cheaper | 28 | 100% | 100% | 100% | -0.179 | +148.261 |

Every supplier price stayed at or above that supplier's own floor: yes. Runs where a different supplier won once the statement was made: 0.

## How to read this

- The statement is weak on purpose: closed signals and a rounded band, never a name or an exact price. A supplier that hears a clear gap against a rival (5% or more) moves its planned counter one step towards the buyer, still above its own floor.
- The effect is small in this rule-based setting: the price per unit moves by about a tenth of a percent and deals are not lost. That is a measurement of THESE rules on synthetic data. A live model may react more, less or differently, and real suppliers will.
- Regenerate with `python -m sourceloop.eval.leverage`.
