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

In March 2024, the Harlow Valley Transit Authority converted 6.4 km of Route 12 into bus-only lanes [1]. Average weekday bus travel time on this corridor fell from 31 minutes to 24 minutes, while car travel times on the parallel road remained unchanged within measurement error [1]. A rider survey found that 68% rated reliability as improved and ridership rose 11% in six months after the launch compared with the previous year [2]. Conversely, nine of 40 surveyed shops reported lower foot traffic since the lanes opened [3]. The Harlow Merchants Association asked the city to add 15-minute loading bays on each block [3].

Automatic check: pass

## research / sodium-cells

**Question:** How do the Tessaly sodium-ion cells compare with the lithium iron phosphate (LFP) reference on durability, cost and performance?

**Answer:**

According to the Tessaly Labs cycling study, sodium-ion cells retained 91% of capacity after 2,000 charge cycles at 25 °C [1]. The LFP reference cells retained 93% under the same protocol [1]. At projected volume, the sodium-ion pack is estimated at $62 per kWh against $89 per kWh for the LFP reference [2]. Measured energy density was 148 Wh/kg, about 20% below the LFP reference [3]. In a -20 °C test, the sodium-ion cells delivered 84% of rated capacity [3].

Automatic check: pass

## research / hybrid-work

**Question:** What happened at Corvin Analytics after it moved to a hybrid schedule?

**Answer:**

Corvin Analytics implemented a three-day-remote work policy in January 2025 [1]. This change coincided with voluntary attrition decreasing from 14% to 9% year over year [1]. Engineering productivity saw tickets closed per engineer increase by 6% [2]. However, median code review turnaround lengthened from 5 hours to 8 hours [2]. Employee sentiment was largely positive, with 72% of respondents preferring the hybrid schedule [3]. Conversely, staff with under one year tenure reported feeling less connected to their team compared to longer-tenured employees [3].

Automatic check: pass

## research / lake-orrin

**Question:** Why does Lake Orrin have an algae problem, and what is being done about it?

**Answer:**

In 2023, phosphorus concentration averaged 48 µg/L in Lake Orrin, which was above the 30 µg/L target [1]. Algal blooms were recorded on 37 days during this period [1]. Research attributes roughly 60% of the lake's phosphorus load to agricultural runoff from the Pell Creek sub-basin [2]. The county council approved $2.3 million for buffer strips along 14 km of Pell Creek [3]. Staff expect the project to cut the phosphorus load by 25% within five years [3].

Automatic check: pass

## research / lattice-pruning

**Question:** How well does lattice pruning work on the Brel-7 model, and what caveats have been reported?

**Answer:**

Lattice pruning removes 40% of the weights of the Brel-7 model with a 0.8 point accuracy loss on the Mirel benchmark [1]. Inference latency on a mid-range GPU dropped from 38 ms to 27 ms per request after pruning, and memory use fell 35% [3]. However, an independent replication found a higher accuracy loss of 1.9 points when the calibration set was reduced from 5,000 to 500 examples [2].

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

Apr

Automatic check: pass

## vision / table-cell

**Question:** According to the table, what is the speed for Bravo? Answer with the number only.

**Answer:**

33

Automatic check: pass

## vision / terminal-error

**Question:** This is a terminal screenshot. What type of error was raised, and on which line of summary.py?

**Answer:**

Based on the terminal screenshot provided:

*   **Error Type:** A `ValueError` was raised. specifically stating `could not convert string to float: 'n/a'`. This happens because the code tried to turn the text "n/a" into a number, which Python cannot do.
*   **Line Number:** The error occurred on line **17** of the file **summary.py**.

Automatic check: pass

