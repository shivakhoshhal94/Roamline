import pytest
from app.domain.destination_features import FEATURES, normalize_count


def test_reviewed_mapping_and_explicit_unsupported_interests():
    assert FEATURES["museums"].categories == ("entertainment.museum",)
    assert "tourism.attraction.viewpoint" in FEATURES["photography"].categories
    assert "scenic-subject proxies" in FEATURES["photography"].description
    assert all(name not in FEATURES for name in ["football", "hiking", "music", "local culture", "birdwatching"])
    assert len(FEATURES) == 10


def test_normalization_is_bounded_monotonic_and_population_adjusted():
    values = [normalize_count(count, 500000) for count in [0, 1, 10, 50, 100]]
    assert values == sorted(values)
    assert values[0] == 0
    assert values[-1] <= 1
    assert normalize_count(30, 100000) > normalize_count(30, 1000000)
    assert normalize_count(10, 1) == normalize_count(10, 100000)
    # A large raw-count advantage need not beat much denser mapped evidence.
    assert normalize_count(30, 100000) > normalize_count(100, 10000000)


@pytest.mark.parametrize("count", [-1, 101, 1.5, True])
def test_invalid_counts_are_not_silently_normalized(count):
    with pytest.raises(ValueError):
        normalize_count(count, 100000)
