# Startup vs. Current Job — Equity Calculator

## Files
- Build script (current): `/Users/isaacgarza/git/build_v18.py`
- Output (current): `/Users/isaacgarza/Desktop/Startup_vs_Job_v18.xlsx`
- Previous: `build_v17.py`, `build_v16.py`, `build_v15.py`
- Original claude.ai conversation: `5a7ff867-94ee-4add-a397-1eb074ab1cf2`
- Export location: `/Users/isaacgarza/Downloads/data-2026-03-03-17-39-11-batch-0000/`

## User's Situation

### Current Job
- Base salary: $208,000
- Bonus: 20% target ($41,600); actual received 165% of target this year
- Benefits: ~$15,000
- 401k match: 4% of salary ($8,320)
- Annual RSU refresh grants: ~$100,000/year
- RSU stock price: $4/share (public, liquid)
- RSU forfeit if leaving now: 92,201.5 units × $4 = **$368,806 gross**
  - Q1 kept: 11,977 units (vests before departure)
  - Q2/Q3/Q4 forfeited: 14,700 × 3 = 44,100 units
  - Year 2 forfeited: 24,050.8 units
  - Year 3 forfeited: 24,050.7 units
- Tax: 0% state, 23.8% LTCG, 37% ordinary income (RSUs)

### Startup Offer
- Base salary: $223,000
- Benefits: $8,000
- 401k: 0%
- Equity Offer A: **0.1%** (initial, post-raise, fully diluted)
- Equity Offer B: **0.175%** (user's counter)
- Equity Offer C: blank (waiting for response)
- Company valuation: **$150M** post-money
- Total raised: **$30M** (1x liquidation preference)
- Assumed dilution at exit: 25%
- Strike price: unknown (ask company — 409A price)
- FDS (fully diluted shares): unknown (ask company)

## Model Key Numbers (v2, compounded)

| Metric | Value |
|---|---|
| Total hurdle (gross) | **$945,093** |
| — Cash sacrifice (4yr compounded) | $176,287 |
| — RSU forfeit gross | $368,806 |
| — Future RSU grants forgone | $400,000 |
| Break-even Offer A (0.1%) | **8.60x = $1,290M exit** |
| Break-even Offer B (0.175%) | **5.00x = $750M exit** |
| Home Run scenario ($1B) | Below Offer A break-even |

## Exit Scenarios (Inputs)
| Scenario | Prob | Exit Val | Years |
|---|---|---|---|
| Home Run (IPO) | 5% | $1,000M | 7 |
| Good Exit | 20% | $200M | 5 |
| Struggling (< current val) | 30% | $50M | 5 |
| Acqui-hire | 30% | $5M | 4 |
| Failure | 15% | $0 | 3 |

## Spreadsheet Structure (v2)
7 sheets, all formulas pull from **Inputs** (single source of truth):
1. **Inputs** — all tunable values; Inputs row map defined in build_v2.py `I` dict
2. **Comparison** — annual cash comp, compounded sacrifice, total hurdle
3. **RSU Tracker** — forfeit breakdown by tranche, future grants
4. **Equity Scenarios** — Offer A / B / C side-by-side, 5 exit scenarios each, exercise cost, break-even, AMT flag
5. **Break-Even Chart** — line chart (0–25x exit multiple vs. gross payout, hurdle line)
6. **Verdict** — key numbers summary
7. **Glossary** — color guide, assumptions, 8 questions to ask company

## Key Modeling Assumptions (baked in)
- Non-participating preferred (investors take LP OR convert, not both)
- Both current job and startup salaries grow at 4%/yr
- Current job bonus (20%) and 401k (4%) also compound with salary raises
- No illiquidity discount on startup equity
- LTCG rate (23.8%) applied to equity — assumes ISO + long hold (not NSO)
- Exercise cost activates when Strike Price + FDS filled in Inputs
- Hurdle computed on gross basis throughout (apples-to-apples)

## v3 Enhancements (all implemented)
- **Phase 1**: `vest_prob` input (default 0.75) + Risk-Adjusted EV row per offer block (EV × vest_prob, amber)
- **Phase 2**: `qsbs` toggle (default 1=Yes); tax formula: if QSBS and gross≤$10M → 0% federal, only state tax
- **Phase 3**: `refresh_pct` + `refresh_years` inputs; new `write_refresh_block()` section in Equity Scenarios
- **Phase 4**: `participating` toggle (default 0); `gross_equity_formula` + `be_formula` + chart loop all updated with IF(part=0, nonpart, partici) where partici = (1 - lp_stack/valuation) factor applied to common proceeds
- New Inputs section at rows 51-57 (after exit scenarios)
- Glossary expanded: QSBS/ISO/NSO assumption updated, vesting/refresh assumptions added, Negotiation Checklist (83b, post-term window, double-trigger, QSBS rep)
- Run command: `source /Users/isaacgarza/.claude/projects/startup-tools-venv/bin/activate && python3 /Users/isaacgarza/git/build_v3.py`

## v4 Enhancements (all implemented)
- **Bug 1**: Break-even number format fixed (`'0.00"x"'` — previous format mangled display)
- **Bug 2**: Struggling Exit note no longer hardcodes stale `$15K` amount
- **Bug 3**: RSU Tracker row 14 label → "Tax Saved (forgone income tax)" (was misleading "Tax on RSU Income")
- **Bug 4**: CAGR column header → "Startup Salary CAGR (ref only)" — never used in any formula
- **Bug 5**: Break-even note → "current valuation" (was hardcoded "$150M")
- **Verdict gap**: Discounted Risk-Adj EV rows 11-12 added for Offer A and B (`write_offer_block` now returns tuple `(next_row, vest_adj_ev_row)`)
- **Flag #7 (tax-adjusted hurdle)**: Comparison rows 21-23 added (section header, after-tax hurdle, gross equity needed); `be_formula` takes optional `hurdle_cell` param; Verdict rows 13-15 added
- **Flag #8 (time-value discounting)**: `disc_rate` input added (row 58, default 10%); vest-adj EV is now `Σ(prob_i × gross_i / (1+r)^years_i) × vest_prob`; refresh block EV updated consistently; label → "Discounted Risk-Adj EV (NPV × vest prob)"
- Run command: `source /Users/isaacgarza/.claude/projects/startup-tools-venv/bin/activate && python3 /Users/isaacgarza/git/build_v4.py`
- Output: `/Users/isaacgarza/Desktop/Startup_vs_Job_v4.xlsx`

## v5 Enhancements (all implemented)
- **B1**: Cash sacrifice `ABS(...)` → `MAX(0,...)` — prevents false positive hurdle when startup pays more
- **B2**: RSU Tracker column header "Value @ $4/share" → "Value @ market price" (was hardcoded)
- **B3**: After-Tax Hurdle all three components now multiplied by (1-rsu_tax): `=B17*(1-rsu_tax) + RST!B13*(1-rsu_tax) + RST!B20*(1-rsu_tax)` (v4 left cash sacrifice gross)
- **M1**: Tax-Adj Break-Even Offer A & B added to Verdict (rows 16-17) using `be_formula(eq, "B23")`
- **M2**: Future RSU grants now discounted using PV annuity formula `PMT × (1-(1+r)^-n)/r` with IF r=0 guard (was undiscounted nominal)
- **M3**: Expected After-Tax EV column G row added (weighted sum of all scenario net-after-tax payouts)
- **I1**: Conditional formatting on prob sum cell — highlights amber if sum ≠ 100%
- **Verdict**: YES/NO/BORDERLINE section (rows 19-25) with FormulaRule green/amber/red highlighting; side-by-side Offer A vs B; EV Coverage %; threshold: YES ≥ ATH, BORDERLINE ≥ 85% ATH
- Run command: `source /Users/isaacgarza/.claude/projects/startup-tools-venv/bin/activate && python3 /Users/isaacgarza/git/build_v5.py`
- Output: `/Users/isaacgarza/Desktop/Startup_vs_Job_v5.xlsx`

## Remaining Limitations (not modeled)
- Dilution modeled as flat multiplier, not sequential rounds
- No 83(b)/early exercise dollar impact (Glossary explains qualitatively)
- No cliff risk modeling (vest_prob is blunt — doesn't distinguish cliff vs. pro-rata risk)
- Participating preferred investor ownership approximated as lp_stack/valuation (not actual share counts)
- No startup refresh grant vesting schedule (half-vested approximation only)

## Questions to Ask the Company (priority order)
1. What is the 409A strike price per share?
2. What are fully diluted shares outstanding (FDS)?
3. Is the preferred non-participating or participating?
4. Does the company qualify for QSBS? Will you provide a QSBS representation?
5. Is early exercise offered? (enables 83(b) election)
6. What is the post-termination exercise window?
7. Monthly burn, ARR, runway?
8. ISO or NSO grants?
9. Double-trigger acceleration on unvested shares?
10. When was the last 409A valuation conducted?

## Audit History
### Bugs fixed (v1 → v2)
- Cash sacrifice was flat (year-1 × n); now compounded with raises
- Startup salary CAGR input was collected but never used; now both sides compound at 4%
- Vesting period was proxied by "years of grants modeled"; now its own Inputs field
- RSU cross-sheet reference was initially wrong (B17→B13); now clean via row-map dict
- Break-even formula had units mismatch ($ vs $M); fixed with /1,000,000
- Strike price was collected but never used; now subtracts exercise cost from gross equity
- "Modest Exit" ($50M < $150M valuation) relabeled "Struggling Exit"
- Offer C showed $0 silently when blank; now shows "—"
- Post-dilution equity % was repeated 5× across scenario columns; now merged single cell
- LP haircut row hardcoded $0 was misleading; now shows note "baked into gross formula"

### Known remaining limitations
- Dilution modeled as flat multiplier, not sequential rounds
- No QSBS toggle
- No 83(b)/early exercise modeling
- No vesting probability weighting
- Participating preferred not modeled (non-participating assumed)
- State taxes: currently 0%; update Inputs B12 if applicable
