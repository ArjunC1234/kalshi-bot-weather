# Quote Availability Clarification

Recorded before any timing-model fit or timing performance scoring. Source records
contain requested_at_utc as well as received_at_utc. Include both in the timing
export and require an entry quote's request to begin after the forecast receipt.
A response arriving later is not sufficient if its request was already in flight.
Record the wait to that first eligible quote and the forecast's publication age.

This strengthens the protocol's post-signal execution requirement. It does not
change forecast revision thresholds, training dates, model choice or scoring rules.
