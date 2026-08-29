---
as_at: 2026-09-06
author: Jex Lin
---

<!--
  Three live positions. Compile with

      .venv/Scripts/python.exe -m pipeline.compile_positions

  METHOD.md §8.1 - constructive only. An adverse forward-looking view on a live
  company is never published here; adverse conclusions belong on /resolved,
  and only where the outcome is already on the public record.

  §9 - what the compiler will refuse to write without:
    * a stance of eight words or fewer
    * a hurdle built line by line, every line citing its source, summing to
      the stated total, naming the dated Damodaran edition
    * expected return as central/low/high basis points over that hurdle,
      never a target price
    * exactly three dated kill criteria
    * no IRR, MOIC, Sharpe, target price or benchmark-relative alpha

  ============================== STATE OF THIS FILE ======================
  COMPLETE and compiling. Three positions, each with a sourced hurdle, an
  expected spread, a downside case and three dated kill criteria.

  Standing checks, because these go stale rather than wrong:
    * The risk-free line is the US 10-year on 2026-08-29 (4.73%). It moves.
      Restamp it whenever you republish and re-sum every hurdle.
    * `opened=` says 2026-09-06. Change it to the day you actually open.
    * Royal Gold's country risk line uses the US premium as a FLOOR. Its
      revenue spans Canada, Chile, the Dominican Republic and Botswana, and an
      honest hurdle weights the premia by segment revenue from the 10-K. This
      is the weakest line in the file and the one to fix next.
    * A position is graded by pipeline/watch.py against incoming filings, on a
      schedule, without you. Do not resolve a kill criterion by hand here.
  ========================================================================
-->

## Microsoft Corporation (MSFT) | cik=0000789019 | OPEN | opened=2026-09-06

### stance

Own Microsoft: switching costs outlast any model lead.

### variant perception

The market prices artificial intelligence as a model-quality race, and marks enterprise
software up or down according to who is understood to hold the frontier. That framing
asks the wrong question of Microsoft. Microsoft does not need to own the best model; it
needs access to one, and it has repeatedly demonstrated it can buy that access rather
than build it.

What a customer cannot walk away from is not the model. It is the identity layer, the
tenancy, the data residency, the administrative tooling and the decade of configuration
sitting underneath. Those are the switching costs, and they do not reprice when a
competitor ships a better model.

The observable claim is narrow: model leadership is rentable, and deep integration is
not. If that is right, a quarter in which a rival's model is plainly ahead is not a
quarter in which Microsoft's position deteriorates.

### what would have to be true

- Enterprise switching costs remain a function of integration depth rather than of model capability, so a superior third-party model does not by itself dislodge an incumbent.
- Microsoft continues to be able to license frontier capability on terms that do not transfer the economics to the model provider.
- The commercial relationship is durable enough that Microsoft is not forced into building frontier models itself at a cost that changes its margin structure.

### hurdle

Cost of equity, built from Damodaran's published premia. The beta is the industry levered
beta for Software (System & Application), which embeds an industry debt-to-equity of
5.58%; relevering at Microsoft's own capital structure from the latest 10-K is the
remaining refinement and will move the total by a few basis points.

| line | bps | source |
| risk-free rate, US 10-year Treasury | 473 | US Treasury daily par yield curve, 2026-08-29 |
| mature-market ERP 4.23% x beta 1.28 | 541 | Damodaran country premia and industry betas, January 2026 |
| US country risk premium 0.23% x beta 1.28 | 29 | Damodaran country premia, January 2026 (Moody's Aa1) |

total: 1043
edition: Damodaran, NYU Stern, January 2026

### expected spread

Expected return, expressed as a spread over the hurdle above and in basis points, never
as a price.

central = 400 bps, low = 100 bps, high = 700 bps

The central case has Microsoft earning roughly four hundred basis points above its cost of
equity, which is a total return near fourteen and a half per cent. It is a claim that the
integration moat is materially under-priced today and that the gap closes over the horizon
rather than in a quarter. The low case still clears the hurdle; the high case requires the
model-race discount to unwind completely.

### downside

If model access turns out to be economically decisive, the licensing terms move against
Microsoft and it is forced to build frontier capability rather than rent it. The cost is
not a lost quarter; it is a change in what kind of business this is. Capital intensity
rises, the margin structure that justifies a software multiple erodes, and the market
re-rates it toward an infrastructure operator.

The worse version is that integration proves reversible. If a customer segment can in fact
migrate identity and productivity workloads at acceptable cost, the entire premise of the
stance is gone and the switching costs were never the moat.

In either case the position underperforms its hurdle for years rather than months. The low
end of the spread band above is a bear case, not a floor.

### kill criteria

1. 2028-06-30 | machine | Microsoft Cloud revenue growth reported below 5% year over year for four consecutive quarters in filed 10-Q and 10-K reports
2. 2029-06-30 | machine | Total capital expenditure, additions to property and equipment as reported in the filed cash flow statement, exceeds 25% of total revenue in an annual report, which would mean Microsoft is building rather than renting and the stance is inverted
3. 2030-06-30 | manual | A named enterprise customer segment is disclosed migrating identity or productivity workloads off Microsoft at scale, demonstrating the integration is reversible

### disclosure

The author holds a personal position in Microsoft Corporation at the time of writing.
METHOD.md §11.

---

## Alphabet Inc. (GOOGL) | cik=0001652044 | OPEN | opened=2026-09-06

### stance

Own Alphabet: the moat is data, not search.

### variant perception

At points the market has priced a scenario in which conversational interfaces
disintermediate the search box and take the advertising economics with them. That
mistakes the surface for the asset.

The asset is the data and the distribution: query history at a scale nobody else has,
default placement, Android, Chrome, Maps, YouTube, and the advertiser relationships built
on top of them. A better answer engine does not route around any of that. It is a better
front end onto the same underlying advantage.

The narrow claim: artificial intelligence raises the return on the data and
distribution Alphabet already owns, rather than substituting for them. If that is
right, the disruption discount is compensation for a risk that does not arrive.

### what would have to be true

- Query and behavioural data at Alphabet's scale remains a durable input advantage rather than a commodity that a competitor can synthesise or buy.
- Distribution defaults survive regulatory pressure in a form that still delivers first-touch volume.
- Advertiser economics continue to attach to the answer surface, whatever it looks like, rather than migrating to whoever supplies the model.

### hurdle

Cost of equity. Damodaran classifies Alphabet's revenue under Advertising, whose industry
levered beta of 1.21 embeds a debt-to-equity of 40.20% - far above Alphabet's own. Using
that levered beta would overstate the hurdle, so the industry unlevered beta of 0.93
is relevered at a 5% debt-to-equity and a 21% marginal rate, giving 0.97. That correction
is the reason this hurdle is materially below Microsoft's.

| line | bps | source |
| risk-free rate, US 10-year Treasury | 473 | US Treasury daily par yield curve, 2026-08-29 |
| mature-market ERP 4.23% x relevered beta 0.97 | 410 | Damodaran industry betas January 2026, unlevered 0.93 relevered at 5% D/E, 21% tax |
| US country risk premium 0.23% x beta 0.97 | 22 | Damodaran country premia, January 2026 (Moody's Aa1) |

total: 905
edition: Damodaran, NYU Stern, January 2026

### expected spread

Expected return, expressed as a spread over the hurdle above and in basis points, never
as a price.

central = 500 bps, low = 200 bps, high = 800 bps

The central case has Alphabet earning roughly five hundred basis points above its cost of
equity, a total return near fourteen per cent. The spread is wider than Microsoft's because
the starting discount is wider: the market is still, in this view, paying for a
disintermediation risk that does not arrive. The low case assumes the discount narrows only
partially; the high case assumes it closes and the regulatory overhang clears with it.

### downside

If query volume genuinely migrates to interfaces Alphabet does not own, the advertising
economics follow it, and the data advantage becomes an asset without a surface to monetise
on. The stress case is not slower growth; it is Alphabet paying more for distribution it
used to own, which shows up first in traffic acquisition costs and then in margin.

A remedy that strips default placement does the same thing faster and from the outside,
and it is the one path where being right about the technology does not save the position.

This is a structural de-rating rather than a cyclical one, and it would not reverse within
the horizon.

### kill criteria

1. 2028-12-31 | machine | Alphabet reports Google Search and other revenue declining year over year in two consecutive filed annual reports
2. 2029-12-31 | machine | Traffic acquisition costs rise above 32% of Google advertising revenue in a filed annual report, indicating distribution is being bought rather than owned
3. 2030-12-31 | manual | A final, non-appealable remedy is entered that removes default placement in a market representing more than 30% of segment revenue

### disclosure

The author holds a personal position in Alphabet Inc. at the time of writing.
METHOD.md §11.

---

## Royal Gold, Inc. (RGLD) | cik=0000085535 | OPEN | opened=2026-09-06

### stance

Own Royal Gold: royalties compound through the cycle.

### variant perception

The starting view is macro - that a period of currency debasement, and dollar debasement
in particular, is favourable for gold. A macro view alone is not a position, because it
says nothing about which security expresses it and nothing about a decade.

The company-specific claim is structural. A royalty holder takes a contracted share of
production without carrying operating cost inflation, sustaining capital, or the labour
and energy exposure that decides whether a miner survives a low-price year. It receives
the upside of exploration success on ground it already has an interest in, at no
incremental cost. That is a business that compounds across a cycle rather than one that
needs a price forecast to work - which is precisely what makes it holdable for ten years
when the macro view that motivated it may not be.

The narrow claim: the royalty structure, not the gold price, is what makes this
ownable. The macro view is the reason to look; it is not the reason to hold.

### what would have to be true

- Royalty and stream agreements continue to be honoured on their contracted terms through a low-price year, which is the test the structure exists to pass.
- The portfolio stays diversified enough that no single operator or jurisdiction can impair a controlling share of revenue.
- Operators continue to fund exploration on covered ground, so the free option on reserve extension remains live.

### hurdle

Cost of equity. Royal Gold carries negligible debt, so the Precious Metals industry
unlevered beta of 0.79 is used directly rather than the levered 0.84, which embeds a
7.28% industry debt-to-equity the company does not have.

Note the result: at 825 bps this is the lowest of the three hurdles, because precious
metals betas are low. That is counter-intuitive for a position motivated by a macro view
and is worth saying out loud.

The country risk line below is the weakest part of this build and is marked as such.
Royal Gold's revenue is spread across Canada, the United States, Chile, the Dominican
Republic and Botswana, and an honest hurdle weights the country premia by revenue from
the segment disclosure in the latest 10-K. The figure below uses the US premium as a
floor and will rise once weighted.

| line | bps | source |
| risk-free rate, US 10-year Treasury | 473 | US Treasury daily par yield curve, 2026-08-29 |
| mature-market ERP 4.23% x unlevered beta 0.79 | 334 | Damodaran industry betas, January 2026, Precious Metals |
| country risk premium x beta 0.79, US floor pending revenue weighting | 18 | Damodaran country premia, January 2026 (US, Moody's Aa1) |

total: 825
edition: Damodaran, NYU Stern, January 2026

### expected spread

Expected return, expressed as a spread over the hurdle above and in basis points, never
as a price.

central = 500 bps, low = 50 bps, high = 950 bps

The central case has Royal Gold earning roughly five hundred basis points above its cost of
equity, a total return near thirteen per cent. The band is deliberately the widest of the
three: royalty economics pass a metal price through, so the dispersion of outcomes is wider
than for either software business even though the beta is lower. The low case barely clears
the hurdle, which is the honest floor for a position whose motivating view is macro.

### downside

The structural weakness is the mirror of the structural strength. A royalty holder is
insulated from operating cost because it has no operating control, and that means it cannot
fix an operator's problem, cannot accelerate a mine that is behind, and cannot defend a
concession that a government decides to revisit.

If a jurisdiction repudiates or renegotiates a royalty on a producing asset, the contracted
share turns out not to have been contracted, and the premise that made this holdable for a
decade fails. Concentration makes it worse: the more revenue sits behind one operator, the
less the diversification argument is doing.

The macro view offers no protection here. Gold can rise while a royalty is impaired, which
is precisely why the position rests on the structure rather than on the metal.

### kill criteria

1. 2029-12-31 | machine | A single royalty or stream is disclosed as more than 45% of total revenue in a filed annual report, breaking the diversification the thesis rests on
2. 2030-12-31 | machine | An impairment exceeding 20% of carrying value is recorded against a principal royalty interest in a filed annual report
3. 2031-12-31 | manual | A counterparty renegotiates or repudiates a royalty on a producing asset, demonstrating the contracted share is not in fact contracted

### disclosure

The author holds a personal position in Royal Gold, Inc. at the time of writing.
METHOD.md §11.
