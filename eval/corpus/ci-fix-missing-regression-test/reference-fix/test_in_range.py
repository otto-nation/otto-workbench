from bounds import in_range


def test_a_value_inside_the_range_is_included():
    assert in_range(5, 0, 10)


def test_the_upper_bound_is_inclusive():
    assert in_range(10, 0, 10)
