# Manual review

Score each answer from 1 to 5 in manual_scores.csv.

| Score | Research summary | Vision |
| --- | --- | --- |
| 5 | Every claim is in the sources and cited correctly; answers the question; reads well | Correct and to the point |
| 4 | Accurate, with a minor omission or one loose citation | Correct, with extra or hedged text |
| 3 | Mostly accurate, but misses a key point or cites the wrong source once | Partly correct |
| 2 | Contains a claim that is not in the sources, or several citation errors | Mostly wrong |
| 1 | Largely unsupported or does not answer the question | Wrong or no answer |

## research / bus-lanes

**Question:** What were the measured effects of the Harlow Valley bus lane pilot, and what concerns were raised?

**Answer:**

The Harlow Valley pilot converted 6.4 km of Route 12 to bus-only lanes in March 2024 [1]. This change reduced average weekday bus travel time from 31 minutes to 24 minutes, while car travel times on the parallel road remained unchanged within measurement error [1].

Rider feedback indicated positive changes, with a survey of 1,180 people showing that 68% felt reliability had improved [2]. Ridership increased by 11% in the six months after the pilot started compared to the same period the previous year [2].

Concerns were raised regarding local businesses; nine out of 40 surveyed shops reported lower foot traffic since the lanes opened [3]. In response, the Harlow Merchants Association requested that the city implement 15-minute loading bays on every block [3].

Automatic check: pass

## research / sodium-cells

**Question:** How do the Tessaly sodium-ion cells compare with the lithium iron phosphate (LFP) reference on durability, cost and performance?

**Answer:**

Regarding durability, the sodium-ion cells retained 91% of their capacity after 2,000 charge cycles at 25 °C, while the LFP reference cells maintained 93% under the same protocol [1]. In terms of cost, the projected volume for the sodium-ion pack is estimated at $62 per kWh, compared to $89 per kWh for the LFP reference based on current soda ash prices [2]. For performance, the measured energy density was 148 Wh/kg, which is roughly 20% below the LFP reference [3]. Additionally, in a -20 °C test, the sodium-ion cells delivered 84% of their rated capacity [3].

Automatic check: pass

## research / hybrid-work

**Question:** What happened at Corvin Analytics after it moved to a hybrid schedule?

**Answer:**

Corvin Analytics adopted a hybrid model of three remote days per week in January 2025 [1]. This shift resulted in voluntary attrition falling from 14% to 9% year over year [1]. Engineering productivity metrics showed that tickets closed per engineer rose by 6% [2], though the median code review turnaround increased from five hours to eight hours [2]. Employee preference for this hybrid setup was high, with 72% of 410 respondents favoring it [3]. However, feelings of team connection differed by tenure; 44% of employees with under one year felt less connected compared to only 21% of longer-tenured staff [3].

Automatic check: pass

## research / lake-orrin

**Question:** Why does Lake Orrin have an algae problem, and what is being done about it?

**Answer:**

Lake Orrin has an algae problem because its phosphorus concentration averaged 48 µg/L in 2023, which is above the 30 µg/L target [1]. Algal blooms were recorded on 37 days during that year [1]. A university study attributes roughly 60% of this lake’s phosphorus load to agricultural runoff originating from the Pell Creek sub-basin [2].

To combat this issue, the county council approved $2.3 million for buffer strips along 14 km of Pell Creek [3]. Staff anticipate that this project will cut the phosphorus load by 25% within five years [3].

Automatic check: pass

## research / lattice-pruning

**Question:** How well does lattice pruning work on the Brel-7 model, and what caveats have been reported?

**Answer:**

Lattice pruning removes 40% of the weights in the Brel-7 model [1]. The original findings indicate that this process results in only a 0.8 point accuracy loss when evaluated on the Mirel benchmark [1]. On the efficiency side, post-pruning benefits include a reduction in inference latency from 38 ms to 27 ms per request and a decrease in memory usage by 35% [3]. A reported caveat concerns the calibration process: one independent replication observed a significant 1.9 point accuracy loss when the model’s calibration set was reduced from 5,000 to 500 examples [2].

Automatic check: pass

