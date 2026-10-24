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

The pilot converted 6.4 km of Route 12 to bus-only lanes in March 2024 [1]. Average weekday bus travel times on the corridor fell from 31 minutes to 24 minutes [1]. Car travel times on the parallel road remained unchanged [1]. A survey of 1,180 riders found that 68% rated reliability as improved [2]. Ridership on Route 12 rose 11% in the six months after launch compared with the same months a year earlier [2]. Nine of 40 surveyed shops on the corridor reported lower foot traffic since the lanes opened [3]. The Harlow Merchants Association requested that the city add 15-minute loading bays on each block [3].

Automatic check: pass

## research / sodium-cells

**Question:** How do the Tessaly sodium-ion cells compare with the lithium iron phosphate (LFP) reference on durability, cost and performance?

**Answer:**

Sodium-ion cells retain 91% of their capacity after 2,000 charge cycles at 25 °C, while the LFP reference cells retain 93% under the same protocol [1]. In terms of cost, the sodium-ion pack is estimated at $62 per kWh at projected volume, compared to $89 per kWh for the LFP reference [2]. Regarding performance, the sodium-ion cells have a measured energy density of 148 Wh/kg, which is approximately 20% below the LFP reference [3]. Additionally, the sodium-ion cells delivered 84% of their rated capacity during a test at -20 °C [3].

Automatic check: pass

## research / hybrid-work

**Question:** What happened at Corvin Analytics after it moved to a hybrid schedule?

**Answer:**

Corvin Analytics moved to a hybrid schedule of three remote days per week in January 2025 [1]. Following this change, voluntary attrition fell from 14% to 9% year over year [1]. Engineering productivity metrics showed that tickets closed per engineer rose by 6% [2], while the median code review turnaround time lengthened from 5 hours to 8 hours [2]. In a survey of 410 respondents, 72% expressed a preference for the hybrid schedule [3]. However, 44% of employees with under one year of tenure reported feeling less connected to their teams, compared to 21% of longer-tenured staff [3].

Automatic check: pass

## research / lake-orrin

**Question:** Why does Lake Orrin have an algae problem, and what is being done about it?

**Answer:**

Lake Orrin has an algae problem because its phosphorus concentration averaged 48 µg/L in 2023, which is above the 30 µg/L target [1]. Algal blooms were recorded on 37 days during that year [1]. Roughly 60% of the lake's phosphorus load is attributed to agricultural runoff from the Pell Creek sub-basin [2]. To address this, the county council approved $2.3 million for buffer strips along 14 km of Pell Creek [3]. This project is expected to cut the phosphorus load by 25% within five years [3].

Automatic check: pass

## research / lattice-pruning

**Question:** How well does lattice pruning work on the Brel-7 model, and what caveats have been reported?

**Answer:**

Lattice pruning removes 40% of the weights of the Brel-7 model [1]. This process results in a 0.8 point accuracy loss on the Mirel benchmark [1]. In terms of efficiency, inference latency on a mid-range GPU dropped from 38 ms to 27 ms per request after pruning [3]. Memory usage also fell by 35% [3]. However, a replication study reported a caveat regarding the calibration set, noting that reducing it from 5,000 to 500 examples resulted in a 1.9 point accuracy loss [2].

Automatic check: pass

## vision / bar-highest

**Question:** Which region has the highest value? Answer with the region name only.

**Answer:**

South

Automatic check: pass

## vision / bar-read-value

**Question:** What is the value for Mar? Answer with the number only.

**Answer:**

180

Automatic check: pass

## vision / line-peak

**Question:** In which month does the line reach its highest point? Answer with the month only.

**Answer:**

April

Automatic check: pass

## vision / table-cell

**Question:** According to the table, what is the speed for Bravo? Answer with the number only.

**Answer:**

33

Automatic check: pass

## vision / terminal-error

**Question:** This is a terminal screenshot. What type of error was raised, and on which line of summary.py?

**Answer:**

Based on the terminal screenshot, the error details are as follows:

*   **Error Type:** `ValueError`
*   **Line in summary.py:** Line 17

Automatic check: pass

