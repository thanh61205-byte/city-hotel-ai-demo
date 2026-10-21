# Model report — stage F (notebook 04, f-v3.1-2026-10-19, run f-v3.1-2026-10-19_20261020-101915)

Two views are reported and never mixed: the **retrospective arrival-cohort evaluation** (evaluation only) and the **operational weekly-queue simulation** (decision at the end of the booking week, model fitted and selected by 2016-12-31; source of `risk_scores.csv`, tiers and actions). Data are a near-arrival snapshot; the 2017 cohort is a partially explored holdout.

## Headline — retrospective arrival cohort (test 2017)
- Individual bookings (model `xgboost`, 20,399 bookings, cancellation rate 34.4%): ROC-AUC **0.736**, average precision ("PR-AUC") 0.556, Brier 0.1918 (constant 0.2296).
- Cohort top 20% (k = 4,080): precision **58.3%**, lift **1.70**; it reaches **40.5%** of cancelled gross value ranked by p and **52.2%** ranked by expected loss (random: 20%).
- Versus the segment × lead-time rule, cohort top 20%: +308 cancellations reached (paired block bootstrap CI in `model_vs_rules.csv`; applies to this cohort ranking only).
- Whole City Hotel (individual model + group rule, 27,396 bookings): ROC-AUC **0.820** (a ranking metric), PR-AUC 0.807, lift@20% 2.09.

## Operational weekly queue 2017 (simulation)
- Eligible individual bookings in the queue: 12,102 in 35 weekly batches (cancellation rate 24.0%); 1,054 group bookings to the capacity track. Eligibility and the bookings not scored because they were created before the model existed: `queue_contract.csv`. Consent and open actions are unknown in the data.
- Top 20% per weekly batch (floor slots): 2,408 alerts, precision **41.1%**, recall 34.0% (ceiling 82.7%), lift 1.71. Rule ranked in the same batches: precision 35.3%; difference +139 cancellations reached (95% CI [48; 232], whole-batch bootstrap).

## Choices (all made before opening test)
- Folds: fold_1 fit 2016-03-31, fold_2 fit 2016-06-30, fold_3 fit 2016-09-30; final fit 2016-12-31.
- Model: xgboost {"half_life_days": 90, "n_estimators": 500, "max_depth": 3, "learning_rate": 0.022732512887240954, "subsample": 0.7243929286862649, "colsample_bytree": 0.6625916610133735, "min_child_weight": 11.959603830191838, "reg_lambda": 3.7958335792652886}; calibration: none (fold 3 was also part of the tuning objective).
- Policy: decision at the end of the booking week; high = top 5%, medium = next to 20%, slots = floor(share × batch); ties by booking_id hash.
- `pre_arrival` branches: own selection on labels known by the branch cutoff (`pre_arrival_selection.csv`).

## Model comparison (2016 out-of-fold)
| scorer | mean_fold_log_loss | brier | roc_auc | pr_auc | precision@20% | lift@20% |
|---|---|---|---|---|---|---|
| constant | 0.6239 | 0.2154 |  |  |  |  |
| rule_lead_time |  |  | 0.5975 | 0.3638 | 0.3722 | 1.2158 |
| rule_segment_x_leadband | 0.5824 | 0.1998 | 0.6487 | 0.3923 | 0.4254 | 1.3894 |
| baseline_logistic | 0.5935 | 0.2041 | 0.6361 | 0.3873 | 0.4 | 1.3064 |
| logistic_regression_default | 0.5739 | 0.1953 | 0.6855 | 0.4517 | 0.4579 | 1.4958 |
| logistic_regression | 0.5584 | 0.1887 | 0.7082 | 0.4877 | 0.5132 | 1.6762 |
| xgboost_default | 0.6146 | 0.2117 | 0.6657 | 0.428 | 0.4378 | 1.4302 |
| xgboost | 0.5514 | 0.1866 | 0.7153 | 0.4947 | 0.5051 | 1.6498 |

## Test metrics (retrospective cohort)
| scorer | test_cancel_rate | roc_auc | pr_auc | brier | precision@20% | lift@20% | recall@20% |
|---|---|---|---|---|---|---|---|
| final_model | 0.3437 | 0.7356 | 0.5556 | 0.1918 | 0.5831 | 1.6963 | 0.3393 |
| rule_segment_x_leadband | 0.3437 | 0.6775 | 0.4678 | 0.2061 | 0.5076 | 1.4767 | 0.2954 |
| rule_lead_time | 0.3437 | 0.6282 | 0.4498 |  | 0.4676 | 1.3605 | 0.2721 |
| baseline_logistic | 0.3437 | 0.6759 | 0.4934 | 0.2085 | 0.5088 | 1.4802 | 0.2961 |
| constant (train rate) | 0.3437 | 0.5 | 0.3437 | 0.2296 |  |  |  |

## Operating policy (operational queue; last row = retrospective v3 definition)
| policy | population | base_bookings | alerts | alert_share | cancellations_in_base | TP | precision | recall | recall_ceiling | lift |
|---|---|---|---|---|---|---|---|---|---|---|
| model: high + medium (top 20% per weekly batch) | operational weekly queue 2017 | 12102 | 2408 | 0.199 | 2910 | 989 | 0.4107 | 0.3399 | 0.8275 | 1.7081 |
| model: high (top 5% per weekly batch) | operational weekly queue 2017 | 12102 | 587 | 0.0485 | 2910 | 271 | 0.4617 | 0.0931 | 0.2017 | 1.92 |
| rule segment x lead band: top 20% per weekly batch | operational weekly queue 2017 | 12102 | 2408 | 0.199 | 2910 | 850 | 0.353 | 0.2921 | 0.8275 | 1.468 |
| rule segment x lead band: top 5% per weekly batch | operational weekly queue 2017 | 12102 | 587 | 0.0485 | 2910 | 229 | 0.3901 | 0.0787 | 0.2017 | 1.6224 |
| diagnostic fixed cut-off p >= 0.461 | operational weekly queue 2017 | 12102 | 2197 | 0.1815 | 2910 | 939 | 0.4274 | 0.3227 | 0.755 | 1.7775 |
| model: top 20% per booking week of the arrival cohort (v3 definition, not operational) | arrival cohort 2017 (retrospective) | 20399 | 4044 | 0.1982 | 7012 | 2192 | 0.542 | 0.3126 | 0.5767 | 1.5769 |

## Weekly policy, model minus rule (operational queue, paired whole-batch bootstrap, 95% CI)
| policy | metric | model_minus_rule | ci_low | ci_high | significant |
|---|---|---|---|---|---|
| top 20% per weekly batch (operational queue) | precision | 0.0577 | 0.0214 | 0.0875 | True |
| top 20% per weekly batch (operational queue) | tp | 139.0 | 48.475 | 232.0 | True |
| top 5% per weekly batch (operational queue) | precision | 0.0716 | 0.0074 | 0.1309 | True |
| top 5% per weekly batch (operational queue) | tp | 42.0 | 4.0 | 80.525 | True |

## Queue contract (eligibility at the decision time)
| status | group_block | individual | consent / open action |
|---|---|---|---|
| already cancelled | 1232 | 1619 | unknown (not in data) |
| arrived | 61 | 1797 | unknown (not in data) |
| complimentary (adr 0) | 0 | 56 | unknown (not in data) |
| eligible (known checks) | 1054 | 12102 | unknown (not in data) |
| not scored: 2017 arrival created in a week closing on/before the fit date | 4650 | 4857 | unknown (not in data) |

## Precision, recall, F1 at each operating point
| operating_point | population | base_bookings | flag_share | flagged | TP | FP | FN | TN | precision | recall | recall_ceiling | f1 | lift |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| model top 5% (cohort) | arrival cohort 2017 (retrospective) | 20399 | 0.05 | 1020 | 675 | 345 | 6337 | 13042 | 0.6618 | 0.0963 | 0.1455 | 0.1681 | 1.9252 |
| model top 10% (cohort) | arrival cohort 2017 (retrospective) | 20399 | 0.1 | 2040 | 1238 | 802 | 5774 | 12585 | 0.6069 | 0.1766 | 0.2909 | 0.2735 | 1.7655 |
| model top 20% (cohort) | arrival cohort 2017 (retrospective) | 20399 | 0.2 | 4080 | 2379 | 1701 | 4633 | 11686 | 0.5831 | 0.3393 | 0.5819 | 0.429 | 1.6963 |
| model top 30% (cohort) | arrival cohort 2017 (retrospective) | 20399 | 0.3 | 6120 | 3441 | 2679 | 3571 | 10708 | 0.5623 | 0.4907 | 0.8728 | 0.5241 | 1.6357 |
| model top 40% (cohort) | arrival cohort 2017 (retrospective) | 20399 | 0.4 | 8160 | 4368 | 3792 | 2644 | 9595 | 0.5353 | 0.6229 | 1.0 | 0.5758 | 1.5573 |
| model top 50% (cohort) | arrival cohort 2017 (retrospective) | 20399 | 0.5 | 10200 | 5199 | 5001 | 1813 | 8386 | 0.5097 | 0.7414 | 1.0 | 0.6041 | 1.4828 |
| model fixed cut-off p >= 0.461 (from 2016) | arrival cohort 2017 (retrospective) | 20399 | 0.227 | 4631 | 2686 | 1945 | 4326 | 11442 | 0.58 | 0.3831 | 0.6604 | 0.4614 | 1.6873 |
| model p >= 0.5 | arrival cohort 2017 (retrospective) | 20399 | 0.1215 | 2479 | 1480 | 999 | 5532 | 12388 | 0.597 | 0.2111 | 0.3535 | 0.3119 | 1.7368 |
| rule segment x lead band top 20% (cohort) | arrival cohort 2017 (retrospective) | 20399 | 0.2 | 4080 | 2071 | 2009 | 4941 | 11378 | 0.5076 | 0.2954 | 0.5819 | 0.3734 | 1.4767 |
| model at v1 flag rate (59.6%) | arrival cohort 2017 (retrospective) | 20399 | 0.596 | 12158 | 5886 | 6272 | 1126 | 7115 | 0.4841 | 0.8394 | 1.0 | 0.6141 | 1.4084 |
| model at v2 flag rate (66.7%) | arrival cohort 2017 (retrospective) | 20399 | 0.667 | 13607 | 6190 | 7417 | 822 | 5970 | 0.4549 | 0.8828 | 1.0 | 0.6004 | 1.3234 |
| model tier high (top 5% per weekly batch) | operational weekly queue 2017 | 12102 | 0.0485 | 587 | 271 | 316 | 2639 | 8876 | 0.4617 | 0.0931 | 0.2017 | 0.155 | 1.92 |
| model tier high+medium (top 20% per weekly batch) | operational weekly queue 2017 | 12102 | 0.199 | 2408 | 989 | 1419 | 1921 | 7773 | 0.4107 | 0.3399 | 0.8275 | 0.3719 | 1.7081 |
| rule segment x lead band, top 20% per weekly batch | operational weekly queue 2017 | 12102 | 0.199 | 2408 | 850 | 1558 | 2060 | 7634 | 0.353 | 0.2921 | 0.8275 | 0.3197 | 1.468 |

## Staffing (planning estimate from 2016 booking dates)
| booking_month | new_individual_bookings | days | new_bookings_per_day | phone_calls_per_day_I3 (5%) | reminder_sequences_per_day_I2 (15%) |
|---|---|---|---|---|---|
| 2016-01 | 3843 | 31 | 124.0 | 6.2 | 18.6 |
| 2016-02 | 3706 | 29 | 127.8 | 6.4 | 19.2 |
| 2016-03 | 2810 | 31 | 90.6 | 4.5 | 13.6 |
| 2016-04 | 2421 | 30 | 80.7 | 4.0 | 12.1 |
| 2016-05 | 2129 | 31 | 68.7 | 3.4 | 10.3 |
| 2016-06 | 1704 | 30 | 56.8 | 2.8 | 8.5 |
| 2016-07 | 1956 | 31 | 63.1 | 3.2 | 9.5 |
| 2016-08 | 2211 | 31 | 71.3 | 3.6 | 10.7 |
| 2016-09 | 2491 | 30 | 83.0 | 4.2 | 12.5 |
| 2016-10 | 2304 | 31 | 74.3 | 3.7 | 11.1 |
| 2016-11 | 2245 | 30 | 74.8 | 3.7 | 11.2 |
| 2016-12 | 2249 | 31 | 72.5 | 3.6 | 10.9 |

## Workload of the operational queue by decision month
| decision_month | weekly_batches | queued | high | medium | phone_calls_I3 | buffer_I5 | reminder_sequences_I2 | median_days_to_arrival | cancellations_among_flagged | phone_calls_per_day | alert_share | precision_among_flagged |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2017-01 | 5 | 3671 | 182 | 550 | 178 | 4 | 548 | 82.0 | 342 | 5.7 | 0.199 | 0.467 |
| 2017-02 | 4 | 2633 | 130 | 396 | 129 | 1 | 395 | 80.0 | 210 | 4.6 | 0.2 | 0.399 |
| 2017-03 | 4 | 1475 | 71 | 222 | 68 | 3 | 222 | 77.0 | 121 | 2.2 | 0.199 | 0.413 |
| 2017-04 | 5 | 1665 | 81 | 251 | 78 | 3 | 250 | 41.0 | 135 | 2.6 | 0.199 | 0.407 |
| 2017-05 | 4 | 1270 | 62 | 191 | 62 | 0 | 191 | 31.0 | 89 | 2.0 | 0.199 | 0.352 |
| 2017-06 | 4 | 608 | 28 | 92 | 28 | 0 | 92 | 23.5 | 45 | 0.9 | 0.197 | 0.375 |
| 2017-07 | 5 | 603 | 27 | 91 | 27 | 0 | 85 | 15.0 | 41 | 0.9 | 0.196 | 0.347 |
| 2017-08 | 4 | 177 | 6 | 28 | 6 | 0 | 20 | 7.0 | 6 | 0.2 | 0.192 | 0.176 |

## Model minus rules, cohort top-k (paired block bootstrap, 95% CI)
| model_minus | metric | diff | ci_low | ci_high | significant |
|---|---|---|---|---|---|
| rule_segment_x_leadband | roc_auc | 0.0581 | 0.0508 | 0.0667 | True |
| rule_segment_x_leadband | pr_auc | 0.0878 | 0.0777 | 0.1005 | True |
| rule_segment_x_leadband | precision@5% | 0.1206 | 0.0813 | 0.1554 | True |
| rule_segment_x_leadband | precision@10% | 0.0544 | 0.0261 | 0.0838 | True |
| rule_segment_x_leadband | precision@20% | 0.0755 | 0.0537 | 0.0995 | True |
| rule_segment_x_leadband | lift@20% | 0.2196 | 0.1514 | 0.2901 | True |
| rule_lead_time | roc_auc | 0.1075 | 0.0951 | 0.1203 | True |
| rule_lead_time | pr_auc | 0.1058 | 0.0864 | 0.1261 | True |
| rule_lead_time | precision@5% | 0.0755 | 0.0105 | 0.1384 | True |
| rule_lead_time | precision@10% | 0.074 | 0.0279 | 0.1157 | True |
| rule_lead_time | precision@20% | 0.1154 | 0.086 | 0.1409 | True |
| rule_lead_time | lift@20% | 0.3358 | 0.2466 | 0.415 | True |
| baseline_logistic | roc_auc | 0.0597 | 0.0504 | 0.07 | True |
| baseline_logistic | pr_auc | 0.0622 | 0.0448 | 0.0804 | True |
| baseline_logistic | precision@5% | 0.0471 | -0.0233 | 0.1009 | False |
| baseline_logistic | precision@10% | 0.0485 | 0.0076 | 0.0837 | True |
| baseline_logistic | precision@20% | 0.0743 | 0.0466 | 0.098 | True |
| baseline_logistic | lift@20% | 0.216 | 0.1327 | 0.2876 | True |

## Risk deciles (retrospective cohort)
| risk_decile | bookings | mean_p | cancel_rate | cancellations | cancelled_gross_eur | gap_observed_minus_p | cum_share_cancellations | cum_share_cancelled_gross |
|---|---|---|---|---|---|---|---|---|
| 1 | 2040 | 0.5687 | 0.6069 | 1238 | 787797.66 | 0.0382 | 0.1766 | 0.2419 |
| 2 | 2040 | 0.4881 | 0.5593 | 1141 | 530464.13 | 0.0712 | 0.3393 | 0.4047 |
| 3 | 2040 | 0.4544 | 0.5206 | 1062 | 497978.52 | 0.0662 | 0.4907 | 0.5576 |
| 4 | 2040 | 0.4277 | 0.4544 | 927 | 416285.85 | 0.0267 | 0.6229 | 0.6854 |
| 5 | 2040 | 0.3973 | 0.4074 | 831 | 351274.29 | 0.01 | 0.7414 | 0.7932 |
| 6 | 2040 | 0.3268 | 0.349 | 712 | 304331.64 | 0.0222 | 0.843 | 0.8866 |
| 7 | 2040 | 0.2175 | 0.1828 | 373 | 165905.29 | -0.0346 | 0.8962 | 0.9376 |
| 8 | 2040 | 0.174 | 0.151 | 308 | 97235.79 | -0.023 | 0.9401 | 0.9674 |
| 9 | 2040 | 0.138 | 0.1343 | 274 | 78880.27 | -0.0036 | 0.9792 | 0.9916 |
| 10 | 2039 | 0.0819 | 0.0716 | 146 | 27223.45 | -0.0103 | 1.0 | 1.0 |

## Tiers (operational queue)
| risk_tier | bookings | mean_p | cancel_rate | cancellations | cancelled_gross_eur | share_bookings | share_cancelled_gross | decision_rule |
|---|---|---|---|---|---|---|---|---|
| high | 587 | 0.5647 | 0.4617 | 271 | 243584.5 | 0.0485 | 0.1686 | I3 phone call (I5 if <= 7 days) |
| medium | 1821 | 0.4789 | 0.3943 | 718 | 356119.25 | 0.1505 | 0.2466 | I2 personalised reminders |
| low | 9694 | 0.2758 | 0.1982 | 1921 | 844690.33 | 0.801 | 0.5848 | I1 standard confirmation |

## Action schedule summary
| index | count |
|---|---|
| queued individual bookings | 12102 |
| I3 phone call | 576 |
| I5 buffer (<= 7 days) | 11 |
| I2 reminders | 1803 |
| I1 standard | 9712 |
| G2 group capacity track | 1054 |
| I2 entered after the 30-day mark (immediate reminder) | 276 |
| I2 with 8-14 days left (one message only) | 54 |
| I1 without the 7-day reminder (date not after the decision time) | 1388 |
| dated actions in the schedule | 22164 |

## Sample decisions (outcome known only afterwards)
| booking_id | score_time | booking_date | arrival_date | days_to_arrival | market_segment | gross_value | p_cancel | risk_tier | rule | scheduled_actions | outcome (known later) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 75304 | 2017-01-08 | 2017-01-02 | 2017-06-28 | 171 | Online TA | 653.38 | 0.57 | high | I3 | phone_call 2017-01-08 | stayed |
| 26524 | 2017-02-19 | 2017-02-15 | 2017-04-21 | 61 | Online TA | 1569.6 | 0.63 | high | I3 | phone_call 2017-02-19 | cancelled |
| 33092 | 2017-04-02 | 2017-04-02 | 2017-08-15 | 135 | Online TA | 1560.0 | 0.51 | high | I3 | phone_call 2017-04-02 | cancelled |
| 74532 | 2017-04-30 | 2017-04-28 | 2017-06-23 | 54 | Online TA | 260.0 | 0.5 | high | I3 | phone_call 2017-04-30 | stayed |
| 65466 | 2017-01-22 | 2017-01-20 | 2017-01-25 | 3 | Online TA | 3791.0 | 0.72 | high | I5 | none: count in date x room-type cancellation buffer | stayed |
| 68088 | 2017-03-12 | 2017-03-09 | 2017-03-15 | 3 | Online TA | 1664.0 | 0.52 | high | I5 | none: count in date x room-type cancellation buffer | stayed |
| 27389 | 2017-03-05 | 2017-03-05 | 2017-05-03 | 59 | Online TA | 1228.0 | 0.51 | medium | I2 | reminder 2017-04-03; reminder 2017-04-19 | cancelled |
| 72137 | 2017-04-23 | 2017-04-17 | 2017-05-19 | 26 | Online TA | 320.0 | 0.49 | medium | I2 | reminder 2017-04-23; reminder 2017-05-05 | stayed |
| 33596 | 2017-07-23 | 2017-07-17 | 2017-08-29 | 37 | Online TA | 549.0 | 0.43 | medium | I2 | reminder 2017-07-30; reminder 2017-08-15 | cancelled |
| 66687 | 2017-01-15 | 2017-01-15 | 2017-02-26 | 42 | Online TA | 408.0 | 0.34 | low | I1 | confirm_email 2017-01-15; reminder 2017-02-19 | stayed |
| 76889 | 2017-02-26 | 2017-02-20 | 2017-07-22 | 146 | Online TA | 635.5 | 0.15 | low | I1 | confirm_email 2017-02-26; reminder 2017-07-15 | stayed |
| 71666 | 2017-05-07 | 2017-05-03 | 2017-05-12 | 5 | Online TA | 480.0 | 0.36 | low | I1 | confirm_email 2017-05-07 | stayed |

## Agent-mapping leakage test
| fold | fit_date | top_agents_in_fold | agents_in_nb03_list_not_in_fold_list | train_rows_whose_nb03_category_differs | test_passed |
|---|---|---|---|---|---|
| fold_1 | 2016-03-31 | 15 | 3 | 794 | True |
| fold_2 | 2016-06-30 | 15 | 3 | 1151 | True |
| fold_3 | 2016-09-30 | 15 | 4 | 1623 | True |

## Robustness (population and time contract per row)
| view | population | time_contract | bookings | cancel_rate | roc_auc | pr_auc | lift@20% | brier | note |
|---|---|---|---|---|---|---|---|---|---|
| A. headline: all individual test 2017 | individual, rule A (block size from the snapshot) | retrospective arrival cohort (exception to the strict rule) | 20399 | 0.3437 | 0.7356 | 0.5556 | 1.6963 | 0.1918 |  |
| 1. booking-date-filtered retrospective view, population A | individual, rule A (block size from the snapshot) | booking date after the fit date (date filter only) | 15124 | 0.2993 | 0.7302 | 0.4971 | 1.7409 | 0.1808 | 5,275 test bookings created on/before the fit date excluded; not a strict booking-time test |
| 2. no duplicates (retrained) | individual, rule A (block size from the snapshot), is_duplicate == 0 | retrospective arrival cohort (exception to the strict rule) | 18752 | 0.337 | 0.7353 | 0.5504 | 1.7017 | 0.1904 |  |
| 3. population rule B (retrained) | individual, rule B (Groups / Non Refund only) | retrospective arrival cohort (exception to the strict rule) | 21747 | 0.3443 | 0.7401 | 0.5659 | 1.7359 | 0.1909 | 7,054 extra bookings move to individual |
| 3b. rule B + booked after the fit date (combined) | individual, rule B (Groups / Non Refund only) | booking date after the fit date (date filter only) and no block-size proxy in the population; feature values still snapshot | 15509 | 0.3014 | 0.7319 | 0.5033 | 1.7275 | 0.1813 | 385 of these bookings are group_block under rule A |
| 4. pre_arrival H=14, booking_time inputs | individual, rule A (block size from the snapshot), eligible at arrival-14 | strict: score date arrival-14 > fit date 2016-12-17; configuration and calibration selected on labels known by 2016-12-17 | 10958 | 0.0829 | 0.6723 | 0.1713 | 2.0206 | 0.075 | xgboost; eligible 10,958 of 20,399 |
| 4. pre_arrival H=14, + pre_arrival_extra | individual, rule A (block size from the snapshot), eligible at arrival-14 | strict: score date arrival-14 > fit date 2016-12-17; configuration and calibration selected on labels known by 2016-12-17 | 10958 | 0.0829 | 0.686 | 0.1825 | 2.0811 | 0.0749 | adds special requests AND waiting-list fields (not isolated) |
| 4. pre_arrival H=30, booking_time inputs | individual, rule A (block size from the snapshot), eligible at arrival-30 | strict: score date arrival-30 > fit date 2016-12-01; configuration and calibration selected on labels known by 2016-12-01 | 9735 | 0.1344 | 0.686 | 0.2651 | 1.9916 | 0.1099 | xgboost; eligible 9,735 of 20,399 |
| 4. pre_arrival H=30, + pre_arrival_extra | individual, rule A (block size from the snapshot), eligible at arrival-30 | strict: score date arrival-30 > fit date 2016-12-01; configuration and calibration selected on labels known by 2016-12-01 | 9735 | 0.1344 | 0.7028 | 0.2937 | 2.1407 | 0.1082 | adds special requests AND waiting-list fields (not isolated) |
| 5. leakage illustration: + pre_arrival_extra WITHOUT eligibility | individual, rule A (block size from the snapshot) | not valid | 20399 | 0.3437 | 0.7566 | 0.6134 | 1.9687 | 0.1883 | cancelled bookings stop adding requests |

## pre_arrival: own selection per branch
| H_days | branch_cutoff | fold_fit_dates | selected_model | params | calibration | lr_mean_fold_log_loss | xgb_mean_fold_log_loss | selection_rows | fold3_rows_dropped_label_after_cutoff | max_label_known_date_in_selection | seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 14 | 2016-12-17 | 2016-03-17, 2016-06-16, 2016-09-16 | xgboost | {"half_life_days": 90, "n_estimators": 550, "max_depth": 2, "learning_rate": 0.020187766237511395, "subsample": 0.7143417233780396, "colsample_bytree": 0.945270014934525, "min_child_weight": 12.282411920017974, "reg_lambda": 13.48020011979224} | none | 0.3547 | 0.3514 | 11998 | 461 | 2016-12-17 | 79 |
| 30 | 2016-12-01 | 2016-03-01, 2016-05-31, 2016-08-31 | xgboost | {"half_life_days": 90, "n_estimators": 550, "max_depth": 2, "learning_rate": 0.020187766237511395, "subsample": 0.7143417233780396, "colsample_bytree": 0.945270014934525, "min_child_weight": 12.282411920017974, "reg_lambda": 13.48020011979224} | none | 0.464 | 0.4583 | 10353 | 705 | 2016-12-01 | 65 |

## Timing of 2017 outcomes after the H mark
| H_days | eligible_share_of_test | cancel_rate_among_eligible | outcomes_after_mark_eligible_at_mark | outcomes_after_mark_created_after_mark | outcomes_after_mark_all | test_outcomes | share_after_mark_eligible_only | share_after_mark_all |
|---|---|---|---|---|---|---|---|---|
| 14 | 0.5372 | 0.0829 | 908 | 553 | 1461 | 7012 | 0.1295 | 0.2084 |
| 30 | 0.4772 | 0.1344 | 1308 | 1340 | 2648 | 7012 | 0.1865 | 0.3776 |

## Two-track system (retrospective cohort)
| scope | bookings | cancel_rate | roc_auc | pr_auc | precision@20% | lift@20% |
|---|---|---|---|---|---|---|
| individual (model) | 20399 | 0.3437 | 0.7356 | 0.5556 | 0.5831 | 1.6963 |
| group_block (rule) | 6997 | 0.6667 | 0.9346 | 0.9636 | 1.0 | 1.4999 |
| whole City Hotel 2017 (two-track system) | 27396 | 0.4262 | 0.8196 | 0.8069 | 0.8891 | 2.0858 |

## Retrospective aggregate: sum of p per arrival day / week (bookings; not a room forecast)
| level | scope | unit | periods | mean_actual_cancelled_bookings | MAE_sum_of_p | constant_rate_same_population | MAE_constant_rate | WAPE_sum_of_p | corr_sum_of_p |
|---|---|---|---|---|---|---|---|---|---|
| day | individual | bookings (not rooms / room-nights) | 243 | 28.856 | 4.1372 | 0.2804 | 6.7279 | 0.1434 | 0.9123 |
| day | whole hotel | bookings (not rooms / room-nights) | 243 | 48.0535 | 5.2394 | 0.4153 | 12.223 | 0.109 | 0.9766 |
| week | individual | bookings (not rooms / room-nights) | 36 | 194.7778 | 14.1031 | 0.2804 | 36.8478 | 0.0724 | 0.9765 |
| week | whole hotel | bookings (not rooms / room-nights) | 36 | 324.3611 | 16.5777 | 0.4153 | 41.6737 | 0.0511 | 0.9888 |

## Odds ratios (categorical vs a stated reference; numeric per +1 SD)
| variable | kind | level | reference | log_odds | odds_ratio |
|---|---|---|---|---|---|
| agent_top | categorical (vs reference) | 9 | none | 0.9643 | 2.6229 |
| customer_type | categorical (vs reference) | Transient-Party | Transient | -0.8651 | 0.421 |
| agent_top | categorical (vs reference) | 28 | none | -0.6771 | 0.5081 |
| customer_type | categorical (vs reference) | Contract | Transient | -0.6685 | 0.5125 |
| customer_type | categorical (vs reference) | Group | Transient | -0.6155 | 0.5404 |
| meal | categorical (vs reference) | SC | BB | 0.4365 | 1.5472 |
| agent_top | categorical (vs reference) | 7 | none | -0.4266 | 0.6527 |
| prev_cancel_ratio | numeric (per +1 SD) | +1 SD | 1 SD lower (SD = 0.135) | 0.4225 | 1.5258 |
| agent_top | categorical (vs reference) | 22 | none | -0.3511 | 0.7039 |
| agent_top | categorical (vs reference) | 83 | none | 0.3387 | 1.4031 |
| lead_time_log | numeric (per +1 SD) | +1 SD | 1 SD lower (SD = 1.53) | 0.325 | 1.3841 |
| agent_top | categorical (vs reference) | 16 | none | -0.2702 | 0.7632 |
| agent_top | categorical (vs reference) | 27 | none | -0.2669 | 0.7657 |
| is_last_minute | numeric (per +1 SD) | +1 SD | 1 SD lower (SD = 0.414) | -0.2638 | 0.7681 |
| agent_top | categorical (vs reference) | 11 | none | -0.2616 | 0.7698 |

## Limitations
- Snapshot features (lead time, party, room type) are last recorded values; even the operational simulation scores them, so it is a retrospective prototype, not a live booking-time system.
- The operational queue covers bookings created in weeks closing after 2016-12-31; earlier-created 2017 arrivals would need earlier models. Summer 2017 batches miss bookings for arrivals after 31/08/2017 (end of the extract).
- Fold 3 is in the tuning objective and in the calibration choice; the 2017 cohort is partially explored. Neither makes the test AUC false, but the selection evidence is cross-validated, not independent.
- `lead_time` 0–7 days means booked close to arrival; the subgroup table says nothing about how late a cancellation notice comes.
- Probabilities were kept raw because they had the best pre-test Brier; the decile table shows gaps of a few points, so p × value is an estimate, not an exact loss.
- The sum of p is a retrospective aggregate of bookings without a forecast origin; it is not a forecast of resellable rooms (G1 needs origin, horizon, known cancellations, not-yet-created bookings and stay date × room type).
- Adding `pre_arrival_extra` (special requests and waiting-list fields together) raises AUC also after the eligibility filter; the effect of special requests alone is not isolated and nothing here is causal.
- Configurations not tried, or tried without improvement, do not prove that the data or the method has reached its limit.
- Detected ≠ avoided: the uplift of interventions is a G1 assumption to be measured by a randomised pilot.
