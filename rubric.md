# The rubric

This file exists so the scoring can be argued with. It is not buried in a prompt and it is not
implicit in the code. If you disagree with a weight, you can point at the line.

The weights here are mirrored in `config.py`. Change one, change both.

## The bar

A dimension is satisfied when at least one **verified** claim supports it. The score is the sum of
the weights of the satisfied dimensions. A claim that does not survive verification contributes
nothing, which is why a score can move down between stage 2 and stage 3.

| Dimension | Weight | What it is asking | Why it earns that weight |
|---|---|---|---|
| `b2b_saas` | 25 | Is this a B2B software company with a sales assisted motion | A demo request or a talk to sales path means there is a funnel to work on. Pure self serve or consumer is a different engagement. |
| `paid_motion` | 25 | Is money already moving through paid channels | An existing spend is a budget that can be redirected. A company with no paid motion is an education sale, which is slower and cheaper. |
| `gtm_gap` | 30 | Is there a visible go to market or revenue operations gap | The heaviest weight. A live req for growth, demand gen, lifecycle, or revenue operations is the clearest signal that the work is acknowledged, budgeted, and currently unowned. |
| `scale_band` | 20 | Is the company past the point of having nothing to operate | Customer stories, pricing tiers and an enterprise page mean there is enough surface area for the work to matter. |

## Bands

| Score | Band | Meaning |
|---|---|---|
| 60 and above | `pursue` | Enough verified signal to spend a human hour on |
| 35 to 59 | `watch` | Real but thin. Revisit rather than reach out. |
| Below 35 | `pass` | Not enough that survived verification |

## What this rubric deliberately does not do

It does not score intent, and it does not pretend that a tracker script on a homepage proves a media
budget. Every dimension is a proxy, and calling them proxies out loud is the honest version. The
verification stage exists because proxies degrade quietly.

It also does not weight hand-entered intel at all, because hand-entered intel carries no source and
never survives stage 3. That is not an accident. It is the point of the whole exercise: the notes
someone typed into a CRM are the least trustworthy thing in the pipeline, and a system that treats
them equally with a sourced observation is lying to whoever reads the output.
