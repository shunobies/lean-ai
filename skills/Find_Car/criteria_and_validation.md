# Criteria and Validation Rules

## Required criteria
A listing qualifies only if all required checks pass:
1. Hybrid vehicle
2. Private seller / sell-by-owner (or likely private seller)
3. Price under $19,000
4. Mileage under 80,000
5. Reachable listing URL
6. Prefer clean title when available

Search should start within 250 miles of Kansas City and expand nationwide only if fewer than 25 qualifying listings are found.

## Explicit exclusions
Reject clear:
- dealer/dealership/broker/auction/commercial/business-account listings
- rebuilt/salvage/branded/lemon/flood/total-loss/insurance-loss titles
- non-hybrids
- duplicates
- over-price / over-mileage listings

## Required fields for accepted listings
- Year
- Make
- Model/trim
- Price
- Miles
- Location
- Seller Type
- Contact Info
- Listing URL
- VIN (or `Not listed`)
- Title Status
- Owner Count
- Hybrid Verification
- Notes

If missing: `Not listed`
If uncertain: `Unclear`
Never guess.

## Seller type values
- `Private seller`
- `Likely private seller`
- `Unclear`

## Contact information
Use only public contact details visible on listing pages.
If unavailable, use `Contact through listing`.

## Hybrid verification
Must verify listed year/make/model/trim was sold as hybrid.
Preferred sources:
- manufacturer pages
- Edmunds
- Kelley Blue Book
- Cars.com specs
- Car and Driver
- Consumer Reports
- Wikipedia (fallback only)

Format: `Verified hybrid — [source name]`
If not verifiable: reject with `Hybrid status could not be verified`.

## Title status values
- `Confirmed clean`
- `Likely clean`
- `Unclear`
- `Not clean / branded title`

Rules:
- explicit "clean/clear title" -> `Confirmed clean`
- "clean title in hand" -> `Confirmed clean`
- "title in hand" only -> `Likely clean`
- no title mention -> `Unclear`

## Owner count values
- `1 owner`
- `2 owners`
- `3+ owners`
- `Original owner`
- `Family owned since new`
- `Unclear`
- `Not listed`

Only record owner count if explicitly stated.
