# Results from production

Aggregates from the production deployment, **2026-09-29 → 2026-10-13 (15 days, 24/7)**.
Only counts and rates are published. The gold set and every profile behind these numbers
stay on the operator's machine (`evals/data/private/`, git-ignored).

## 1. Funnel: where each profile is decided

| Stage | Profiles in | Decided here | Cost per profile |
|---|---|---|---|
| Profiles read from Instagram | 887,136 | — | 1 HTTP request |
| Free rules (role in bio, venue account, format stop-list, abroad, …) | 887,136 | 873,024 rejected (98.4%) | 0 LLM calls |
| LLM text stage (Haiku, up to 25 profiles per call) | 5,516 | 3,883 rejected, 1,393 pass, 509 unsure¹ | ≈0.09 calls |
| LLM vision stage (Sonnet, 12-post grid, up to 5 per call) | 1,839 | 1,066 rejected, 659 accept, 177 unsure¹ | ≈0.27 calls |
| Human in the loop (Telegram) | 637 decisions | 500 accept, 137 reject¹ | 1 tap |
| **Delivered to sales (Google Sheets)** | **1,100 leads** | ≈73 per day | |

¹ Verdict breakdown covers the whole `verdicts` table, including re-grades, so the sums slightly exceed the unique profiles and delivered leads in the 29.09–13.10 window.

- **0.62%** of the profiles that were read reach the LLM at all. The vision model sees **0.21%**.
- 1,016 LLM calls in total, which is **≈1 delivered lead per LLM call**.
- Structured-output reliability: 99.2% (text) and 99.8% (vision) of calls returned schema-valid JSON.
  Failed calls are logged, and the batch is retried on the next run. A failed call never becomes a verdict.
- 9% of vision verdicts (177 of 1,902) are "unsure" and are routed to a human
  instead of being auto-decided.

The largest rejection reasons: "no target role in the bio" (821,094), "venue account found while walking a
venue's followings" (20,055), "role disabled for this department" (13,879), "role not in the bio after
normalisation" (4,685), "no sign of Russia: bio not in Russian, no geotags in Russia" (2,456).

## 2. Decision quality against human labels

Gold set: **595 profiles** with the client's final decision in the bot (472 accept, 123 reject).
The grader's verdicts are frozen *before* the human decision, so the human never sees and confirms the model.
The set is biased toward hard cases, because the bot receives disputed profiles, rejects queued for review, and a
sample of auto-accepts. Treat these numbers as a lower bound for the full stream.

| Component | Coverage | Precision (accept) | Recall (accept) | F1 | False accepts | Lost leads |
|---|---|---|---|---|---|---|
| Free rules | 100% | 81% (95% CI 77–84) | 90% (87–92) | 85% | 102 | 49 |
| LLM text stage | 80% | 77% (73–81) | 95% (92–97) | 85% | 103 | 19 |
| LLM vision stage | 66% | 82% (78–86) | 88% (84–91) | 85% | 59 | 37 |
| **Whole system** | 100% | **83% (79–86)** | 78% (74–81) | 81% | 74 | 104 |

Reading the table:
- The text stage is tuned for **recall** (95%). It is a cheap filter whose job is not to lose leads before the
  expensive vision model.
- The system trades recall for precision on purpose. A false accept costs a salesperson a wasted call, while a
  lost lead is replaced from the stream within hours.
- 119 system rejects were overturned by the client, and the current rules now accept 90 of them. That is the
  feedback loop working.

Where it is still wrong (system vs. human):

| Human | System | Decided by | Cases |
|---|---|---|---|
| reject | accept | accepted by all stages | 74 |
| accept | reject | rules | 49 |
| accept | reject | vision stage | 36 |
| accept | reject | text stage | 19 |

### Regression set

The client reviewed one export and flagged nine contacts that should never have passed. Each one became a
permanent regression case, and all nine are rejected today:

- a venue in Tashkent whose bio contains another handle with "moscow" in it
- Kazakhstan by domain suffix (`.kz`)
- Aktobe in a handle
- Tallinn, caught by a role stop-marker
- a restaurant account that names its brand chef (venue, not a person)
- Ukraine, four ways: Ukrainian-language bio, `_ua` suffix, Ukrainian city spelling (Київ, Одеса), and the 🇺🇦 flag

## 3. Cost per lead by channel (14 days)

The reason the system has many sources: their cost per delivered lead differs by **190×**.

| Channel | Profiles read | Leads | Profiles per lead |
|---|---|---|---|
| Venue bio → the chef it names | 347 | 29 | **12** |
| Tagged in venue posts | 6,757 | 27 | 250 |
| Web search → Instagram bridge | 6,430 | 10 | 643 |
| Venue followings walk | 682,969 | 299 | 2,284 |
| "Similar accounts" | 39,526 | 19 | 2,080 |
| Geotags | 22,613 | 7 | 3,230 |

The scheduler therefore drains cheap channels first and runs the 2,000-profiles-per-lead graph walk only as a
background filler.

## 4. A hypothesis the data rejected

Accounts occasionally hit Instagram checkpoints. The intuitive fix was to cap request volume, so a cap of
300 requests per hour per account went live on 2026-10-08. The eval compared the 7 days before and after:

| Period | Own-account requests | Checkpoints | Per 1,000 requests |
|---|---|---|---|
| Before the cap | 418,433 | 12 | 0.029 |
| After the cap | 20,595 | 4 | 0.194 |

The median account made **3 requests** in the hour before its checkpoint, and only 3 of 16 made more than
200. The session is flagged earlier (by past load, an IP change or a re-login), and the checkpoint only
surfaces on the next request. The cap does no harm, but the data does not show that it helps. This changed
where engineering time went: exit stability and login hygiene instead of throttling.

## 5. Synthetic eval (public, reproducible)

See [`evals/results/synthetic_report.md`](../evals/results/synthetic_report.md) and the README section
*Prompt iteration*. It contains 330 invented profiles in 22 behavioural slices, and anyone can rerun it with
`python -m evals.offline`.
