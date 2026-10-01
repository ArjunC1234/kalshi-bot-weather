# Validation Superseded

An independent audit found five selected validation trades before the latest
training settlement used by their rolling model was collected. Frozen August
and September test trades had zero such violations.

The validation sweep and resulting selection in this directory are superseded
by experiment_v3, which gates probabilities on training-label availability.
All original results and the lock remain here for audit. September performance
has now been inspected and subsequent runs must be labeled retrospective.

The reproduced model artifact matched all 11,874 original frozen probability
rows exactly. Reuse that identical frozen artifact for the corrected experiment.
