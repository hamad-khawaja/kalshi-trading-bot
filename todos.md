# Todos

Potential changes and improvements to revisit.

---

## Per-market stop loss cooldown

**Problem**: Bot gets stopped out then immediately re-enters the same market, doubling down on a wrong thesis. Example: BTC 2230 NO — stopped at -$6.20, re-entered, stopped again at -$12.19.

**Proposed fix**: After a stop loss on a market, block directional/settlement ride re-entry for that ticker. MM and certainty scalp still allowed (different thesis). Next 15-min window starts fresh since it's a different ticker.

**Priority**: Medium — happens occasionally but compounds losses when it does.

**Data**: Two consecutive stop losses on same market = -$18.39 when one loss would have been -$6.20.
