from chunks import chunks


def test_even_split():
    assert chunks([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
