"""count_distinct_speakers: the single speaker-counting helper every
segment-rewriting path (voice match, rename, retag, undo, PATCH) recounts
from after it writes new segments (services/relabel.py, issue #111)."""
from services.relabel import count_distinct_speakers


def test_empty_list_is_zero():
    assert count_distinct_speakers([]) == 0


def test_none_is_zero():
    assert count_distinct_speakers(None) == 0


def test_three_distinct_labels():
    segments = [
        {"speaker": "SPEAKER_00"},
        {"speaker": "SPEAKER_01"},
        {"speaker": "SPEAKER_02"},
    ]
    assert count_distinct_speakers(segments) == 3


def test_shared_name_across_segments_merges_to_one():
    segments = [
        {"speaker": "Alice"},
        {"speaker": "Alice"},
        {"speaker": "Alice"},
    ]
    assert count_distinct_speakers(segments) == 1


def test_none_speaker_value_not_counted():
    segments = [{"speaker": "Alice"}, {"speaker": None}]
    assert count_distinct_speakers(segments) == 1


def test_empty_string_speaker_not_counted():
    segments = [{"speaker": "Alice"}, {"speaker": ""}]
    assert count_distinct_speakers(segments) == 1


def test_segment_with_no_speaker_key_not_counted():
    segments = [{"speaker": "Alice"}, {"text": "no speaker key here"}]
    assert count_distinct_speakers(segments) == 1


def test_unknown_sentinel_excluded():
    segments = [{"speaker": "Alice"}, {"speaker": "Unknown"}]
    assert count_distinct_speakers(segments) == 1


def test_unknown_sentinel_excluded_case_variants():
    segments = [{"speaker": "Alice"}, {"speaker": "unknown"}, {"speaker": "UNKNOWN"}]
    assert count_distinct_speakers(segments) == 1


def test_only_unknown_segments_is_zero():
    segments = [{"speaker": "Unknown"}, {"speaker": "unknown"}, {"speaker": "UNKNOWN"}]
    assert count_distinct_speakers(segments) == 0


def test_surrounding_whitespace_does_not_create_second_speaker():
    segments = [{"speaker": "  Alice  "}, {"speaker": "Alice"}]
    assert count_distinct_speakers(segments) == 1


# The segments PATCH stores client JSON with no validation, so these shapes
# are reachable from a request body. They must count as "no speaker", not
# raise: an AttributeError here would surface as a 500 on PATCH.


def test_non_string_speaker_value_not_counted():
    segments = [{"speaker": "Alice"}, {"speaker": 123}]
    assert count_distinct_speakers(segments) == 1


def test_bool_speaker_value_not_counted():
    segments = [{"speaker": "Alice"}, {"speaker": True}]
    assert count_distinct_speakers(segments) == 1


def test_container_speaker_value_not_counted():
    segments = [{"speaker": "Alice"}, {"speaker": ["Bob"]}, {"speaker": {"n": "Bob"}}]
    assert count_distinct_speakers(segments) == 1


def test_non_dict_segment_entry_not_counted():
    segments = [{"speaker": "Alice"}, "not a dict", None, 7]
    assert count_distinct_speakers(segments) == 1


def test_only_malformed_input_is_zero():
    segments = ["not a dict", {"speaker": 123}, {"speaker": None}, None]
    assert count_distinct_speakers(segments) == 0
