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

The Harlow Valley Transit Authority converted 6.4 km of Route 12 to bus-only lanes in March 2024 [1]. Average weekday bus travel time on the corridor fell from 31 minutes to 24 minutes, while car travel times remained unchanged within measurement error [1]. A survey of 1,180 riders found that 68% rated reliability as improved [2], and ridership rose 11% in the six months after launch compared with the same months a year earlier [2]. However, nine of 40 surveyed shops on the corridor reported lower foot traffic since the lanes opened [3]. The association asked the city to add 15-minute loading bays on each block [3].

Automatic check: pass

## research / sodium-cells

**Question:** How do the Tessaly sodium-ion cells compare with the lithium iron phosphate (LFP) reference on durability, cost and performance?

**Answer:**

Tessaly sodium-ion cells retained 91% of their capacity after 2,000 charge cycles at 25 °C [1]. The LFP reference cells retained 93% under the same protocol [1]. Regarding performance, measured energy density for sodium-ion was 148 Wh/kg, which is about 20% below the LFP reference [3]. In a -20 °C test, the sodium-ion cells delivered 84% of rated capacity [3]. On cost, an independent analysis estimates the sodium-ion pack at $62 per kWh against $89 per kWh for the LFP reference [2]. This estimate assumes current soda ash prices [2].

Automatic check: pass

## research / hybrid-work

**Question:** What happened at Corvin Analytics after it moved to a hybrid schedule?

**Answer:**

In January 2025, Corvin Analytics transitioned to a hybrid schedule with three remote days per week [1]. Following this change, voluntary attrition decreased from 14% to 9% year over year [1]. Engineering productivity saw mixed results, with tickets closed per engineer rising by 6% [2]. However, the median code review turnaround time lengthened from 5 hours to 8 hours [2]. An employee survey indicated that 72% of respondents preferred the hybrid schedule [3]. Conversely, 44% of employees with under one year of tenure reported feeling less connected to their team compared to 21% of longer-tenured staff [3].

Automatic check: pass

## research / lake-orrin

**Question:** Why does Lake Orrin have an algae problem, and what is being done about it?

**Answer:**

Lake Orrin experiences algal blooms on approximately 37 days annually due to high phosphorus concentrations averaging 48 µg/L in 2023 [1]. A University of Pell watershed study attributes roughly 60% of this phosphorus load to agricultural runoff from the Pell Creek sub-basin [2]. To address this issue, the county council approved $2.3 million for buffer strips along 14 km of Pell Creek [3]. Staff expect this project to cut the phosphorus load by 25% within five years [3].

Automatic check: pass

## research / lattice-pruning

**Question:** How well does lattice pruning work on the Brel-7 model, and what caveats have been reported?

**Answer:**

(no answer)

Automatic check: fail

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

Based on the screenshot, here are the details of the error:

*   **Type of Error:** It is a **`ValueError`**. Specifically, Python tried to convert the string `'n/a'` into a floating-point number (`float`), which failed because that string isn't a valid number.
*   **Location:** The error occurred in the file **`summary.py`** on **line 17**.

This is indicated by the line in the traceback: `File "summary.py", line 17, in summarize`.

Automatic check: pass

