import pandas as pd

from tools.audit_model_readiness import candidate_labels


def test_candidate_hours_exclude_duplicates_missing_scores_sleep_and_bad_days():
    d = pd.DataFrame(dict(Day=["Day1", "Day1", "Day1", "Day1", "Day2", "Day1"],
                          Time=["1 hr", "1 hr", "2 hr", "3 hr", "4 hr", "5 hr"],
                          TremorScore=["0", "2", "1", "No Data", "2", "2"],
                          KinesiaScore=["0"] * 6, Visit=["outpatient"] * 6,
                          Kinesia=["Normal", "Normal", "Normal", "Normal", "Normal", "Sleep"],
                          WearableDataAvailability=["Wearable Worn"] * 6))
    qc = dict(included=True, status="ok", days=[dict(day=1, valid=True), dict(day=2, valid=False)])
    result = candidate_labels(d, qc)
    assert result.candidate_tremor.tolist() == [False, False, True, False, False, False]
    assert result.hour.tolist() == [0, 0, 1, 2, 3, 4]
    assert result.tremor_score.iloc[1] == 2  # keep suspect/duplicate labels for inspection


def test_excluded_participant_has_no_candidate_labels():
    d = pd.DataFrame(dict(Day=["Day1"], Time=["2 hr"], TremorScore=["2"], KinesiaScore=["0"],
                          Visit=["outpatient"], Kinesia=["Normal"],
                          WearableDataAvailability=["Wearable Worn"]))
    result = candidate_labels(d, dict(included=False, status="ok", days=[dict(day=1, valid=True)]))
    assert not result.candidate.any()
    assert not result.candidate_tremor.any()
