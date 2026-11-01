# Deviations from the pre-registration

The pre-registration is [PREREG.md](PREREG.md) at tag `prereg-v1`. Any change to the protocol, arms, endpoints, statistics or reporting rules after that tag is recorded here, per PREREG §12. A deviation never silently replaces a pre-registered analysis: both are reported.

Each entry gives: the date; what changed; why; whether it was decided before or after seeing any test-pool (P1–P5) result; the expected effect on the endpoints; and the commit.

| # | Date (ET) | What changed | Why | Before or after test-pool results | Expected effect | Commit |
|---|---|---|---|---|---|---|
| 1 | Nov 1, 2026 | A6 (agent without `fit_surrogate`, seed 0 on P1–P3) was not recorded before submission. | Laptop time ran out before the deadline. | After: A5, A4, L and L2 test-pool results were known. | Secondary endpoint "A5 − A6" is not reported; the primary (A5 − A2) and other secondaries are unaffected. If A6 is recorded later it is added without changing any other number. | this commit |
| 2 | Oct 31, 2026 | The leak-probe sentence in the docs was reworded by hand: the automatic sentence called L = 0.65 "no sign of recall"; the published text says it is at the upper edge of chance and slight recall cannot be ruled out. | The automatic wording overstated the evidence. | After L and L2 were known. | None on any number; more conservative wording. | 0ecb5b2 |
