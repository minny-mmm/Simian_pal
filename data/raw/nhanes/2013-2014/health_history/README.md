# NHANES 2013–2014 (cycle H) — Health history questionnaires

Source: https://wwwn.cdc.gov/Nchs/Nhanes/ (Questionnaire data, 2013 DataFiles)
Companion to accelerometer file PAX80_H / PAXMIN_H. Join on `SEQN`.
Each `.xpt` (SAS transport) has its codebook `.htm` alongside.

| File | Content |
|---|---|
| MCQ_H | Medical conditions (arthritis, stroke, cancer, heart disease, ...) |
| RXQ_RX_H | Prescription medications, last 30 days (drug names → PD meds e.g. carbidopa-levodopa) |
| DIQ_H | Diabetes |
| BPQ_H | Blood pressure & cholesterol |
| CDQ_H | Cardiovascular health (chest pain, shortness of breath) |
| HSQ_H | Current health status |
| HUQ_H | Hospital utilization & access to care |
| KIQ_U_H | Kidney conditions / urology |
| DPQ_H | Depression screener (PHQ-9) |
| SLQ_H | Sleep disorders |
| PFQ_H | Physical functioning / limitations |

Load: `pd.read_sas("MCQ_H.xpt", format="xport")`
