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

The Harlow Valley bus lane pilot, which converted six point four kilometers of Route 12 into bus-only lanes in March 2024, showed positive travel benefits [1]. Average weekday bus travel time on the corridor decreased from 31 minutes to 24 minutes [1]. Ridership rose by 11% during the six months following launch compared with the same period a year earlier, and sixty-eight percent of surveyed riders rated reliability as improved [2].

Conversely, concerns were raised regarding local business. Nine out of forty surveyed shops reported lower foot traffic since the lanes opened [3]. The Harlow Merchants Association formally requested that the city add 15-minute loading bays on each block to address this decrease in commerce [3].

Automatic check: pass

## research / sodium-cells

**Question:** How do the Tessaly sodium-ion cells compare with the lithium iron phosphate (LFP) reference on durability, cost and performance?

**Answer:**

Regarding durability, sodium-ion cells retained 91% of their capacity after 2,000 charge cycles at 25 °C, whereas the LFP reference retained 93% under the same protocol [1]. In a field trial, sodium-ion cells delivered 84% of rated capacity when tested in -20 °C [3]. On cost, the sodium-ion pack is significantly cheaper, estimated at $62 per kWh compared to $89 per kWh for LFP, assuming current soda ash prices [2]. Performance comparisons show that the measured energy density of the sodium-ion cells was 148 Wh/kg, approximately 20% below the LFP reference in a Northern fleet trial [3].

Automatic check: pass

## research / hybrid-work

**Question:** What happened at Corvin Analytics after it moved to a hybrid schedule?

**Answer:**

Corvin Analytics adopted a hybrid schedule, moving to three remote days per week in January 2025 [1]. Following this shift, voluntary attrition decreased from 14% to 9% year over year [1].

Regarding productivity, tickets closed per engineer increased by 6%; however, the median time required for code reviews lengthened from five hours to eight hours [2]. A survey of 410 respondents indicated that 72% preferred the new hybrid schedule [3]. Despite this overall preference, employees with under one year of tenure felt significantly less connected to their teams (44%) compared to those with longer tenures (21%) [3].

Automatic check: pass

## research / lake-orrin

**Question:** Why does Lake Orrin have an algae problem, and what is being done about it?

**Answer:**

Lake Orrin faces an algal bloom problem due to excessive phosphorus levels, which averaged 48 µg/L in 2023 and exceeded the 30 µg/L target [1]. The lake recorded algal blooms on 37 days during this period [1]. A watershed study attributes approximately 60% of this phosphorus load to agricultural runoff originating from the Pell Creek sub-basin [2].

To address this issue, the County council approved $2.3 million for a project involving buffer strips along 14 km of Pell Creek [3]. Staff anticipate that these efforts will decrease the overall phosphorus load by 25% within five years [3].

Automatic check: pass

## research / lattice-pruning

**Question:** How well does lattice pruning work on the Brel-7 model, and what caveats have been reported?

**Answer:**

Lattice pruning can remove 40% of the weights in the Brel-7 model while incurring only a 0.8 point accuracy loss on the Mirel benchmark [1]. Beyond accuracy, post-pruning results include increased efficiency, such as inference latency decreasing from 38 ms to 27 ms per request and memory usage falling by 35% [3].

However, there are reported caveats regarding implementation consistency. One independent replication found a 1.9 point accuracy loss when the calibration set was reduced from 5,000 to only 500 examples [2].

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

Based on the terminal traceback provided:

1.  **Type of Error:** A **`ValueError`**.
2.  **Location:** It was raised on **line 17** of the file `summary.py`.

Automatic check: pass

