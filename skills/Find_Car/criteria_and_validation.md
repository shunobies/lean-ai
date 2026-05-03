# Criteria and Validation Rules

## Required Criteria (Default)

A listing qualifies only if all required checks pass:

1. Hybrid vehicle
2. Private seller / sell-by-owner (or likely private seller)
3. Price under $19,000 (unless user changed budget)
4. Mileage under 80,000 (unless user changed mileage cap)
5. Reachable listing URL
6. Title not branded/salvage/rebuilt/flood/total-loss

Search starts local (default 250-mile radius around user location) and expands nationwide only when local yield is insufficient.

## Explicit Exclusions

Reject listings that are clearly:

- dealer / dealership / broker / auction / commercial / business-account
- rebuilt / salvage / branded / lemon / flood / total-loss / insurance-loss title
- non-hybrid
- duplicate of an already recorded listing
- above budget or mileage cap

If user explicitly allows dealers, log that change in `Requirements Change Log` and apply updated rule.

## Required Fields for Accepted Listings

- Year
- Make
- Model/Trim
- Price
- Miles
- Location
- Seller Type
- Contact Info
- Listing URL
- VIN
- Title Status
- Owner Count
- Hybrid Verification
- Notes

Field rules:

- Missing: `Not listed`
- Uncertain: `Unclear`
- Never guess

## Seller Type Values

- `Private seller`
- `Likely private seller`
- `Unclear`

## Contact Information

Use only public contact details visible on listing pages.
If unavailable: `Contact through listing`.

## Hybrid Verification

Must verify listed year/make/model/trim was sold as hybrid.

Preferred sources:

- manufacturer pages
- Edmunds
- Kelley Blue Book
- Cars.com specs
- Car and Driver
- Consumer Reports
- Wikipedia (fallback only)

Format:

- `Verified hybrid — [source name]`

If not verifiable: reject with `Hybrid status could not be verified`.

## Title Status Values

- `Confirmed clean`
- `Likely clean`
- `Unclear`
- `Not clean / branded title`

Rules:

- explicit "clean/clear title" -> `Confirmed clean`
- "clean title in hand" -> `Confirmed clean`
- "title in hand" only -> `Likely clean`
- no title mention -> `Unclear`

## Owner Count Values

- `1 owner`
- `2 owners`
- `3+ owners`
- `Original owner`
- `Family owned since new`
- `Unclear`
- `Not listed`

Only record owner count if explicitly stated.
