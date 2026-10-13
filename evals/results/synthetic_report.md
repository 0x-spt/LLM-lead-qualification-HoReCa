# Synthetic eval — rules vs. LLM text stage vs. cascade

330 generated profiles in 22 slices (105 accept, 225 reject). All names, venues and handles are invented (`evals/synthetic.py`, seed 20261013).

## Overall (positive class = accept, 95% Wilson CI)

| Stage | n | Precision | Recall | F1 | False accepts | Lost leads |
|---|---|---|---|---|---|---|
| rules | 330 | 67% (58–74) | 86% (78–91) | 75% | 45 | 15 |
| llm_text | 330 | 54% (47–61) | 100% (96–100) | 70% | 90 | 0 |
| cascade | 330 | 100% (96–100) | 86% (78–91) | 92% | 0 | 15 |

## Accuracy by slice

| Slice | Label | n | Rules | LLM text | Cascade |
|---|---|---|---|---|---|
| chef | accept | 15 | 100% | 100% | 100% |
| chef_latin_bio | accept | 15 | 100% | 100% | 100% |
| chef_trained_abroad | accept | 15 | 100% | 100% | 100% |
| pastry_chef | accept | 15 | 100% | 100% | 100% |
| chef_mixed_feed | accept | 15 | 100% | 100% | 100% |
| chef_born_abroad | accept | 15 | 100% | 100% | 100% |
| chef_bio_booking_cta | accept | 15 | 0% | 100% | 0% |
| abroad_ukraine | reject | 15 | 100% | 0% | 100% |
| abroad_ukraine_flag | reject | 15 | 100% | 0% | 100% |
| abroad_kazakhstan | reject | 15 | 100% | 0% | 100% |
| abroad_uzbekistan | reject | 15 | 100% | 0% | 100% |
| abroad_georgia | reject | 15 | 100% | 0% | 100% |
| venue_names_its_chef | reject | 15 | 100% | 100% | 100% |
| venue_account | reject | 15 | 100% | 100% | 100% |
| no_role | reject | 15 | 100% | 0% | 100% |
| courses_teacher | reject | 15 | 100% | 100% | 100% |
| catering_home_baking | reject | 15 | 100% | 100% | 100% |
| private_chef | reject | 15 | 100% | 100% | 100% |
| former_chef | reject | 15 | 100% | 100% | 100% |
| consultant_no_venue | reject | 15 | 0% | 100% | 100% |
| food_blogger | reject | 15 | 0% | 100% | 100% |
| travel_chef | reject | 15 | 0% | 100% | 100% |
