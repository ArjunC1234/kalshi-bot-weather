# Causal Bracket Logit v2

## Objective

Maximize doubled-fee selection PnL subject to a high hit rate and minimum activity,
then require every frozen success gate on the September 4-17 test. A profitable point
estimate is not sufficient. Failure of any gate is model failure.

## Model

Fit four regularized multinomial-by-normalization bracket models. Each starts with a
binary logistic probability for every bracket and normalizes the six probabilities
within a quote. Weather-only candidates use bracket position relative to NWS, HRRR,
NBM, ensemble and observed-temperature anchors. Market-weather candidates add the
pre-trade market logit. City and climate hour are included with regularization.

The fit interval starts August 14 because the contract rule source changed from NWS
to Weather Company then. Earlier labels describe a different target and are excluded.
Each event has total training weight one so its repeated hourly snapshots do not
masquerade as independent outcomes.

## Leakage boundary

For a weather row to be usable, every source payload referenced by that row must have
been received. A quote can use only the latest weather row whose maximum source
receipt precedes the quote request. Same-collector-cycle weather is therefore not
available to that cycle's earlier market request. Labels are used only for fitting
and scoring, never as features or policy inputs.

## Trading and selection

For each quote, calculate YES and NO net edge after one-cent adverse execution and a
full-cent taker fee. Require bounded prices, uncrossed spread, and at least one
displayed contract at the ask. Select the highest-edge eligible contract and lock the
first eligible intention per event. No later quote may replace it.

Fit ends August 26, and the recorded latest training-settlement receipt is the model's
availability time. Model and policy selection use August 28-September 3 only. The
qualifying pair with maximum doubled-fee PnL is chosen; ties prefer higher probability,
higher edge, lower price ceiling, tighter spread, later entry, NO-only, stronger
regularization, then lexical ID. September 4-17 is evaluated exactly once.

This historical test is causally ordered but September 4-10 has been examined during
earlier research, so it is not pristine independent confirmation. Passing would
justify prospective paper trading, not live capital.
