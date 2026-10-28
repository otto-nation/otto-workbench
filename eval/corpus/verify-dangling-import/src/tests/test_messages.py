from messages import not_found


def test_not_found_names_the_thing():
    assert "widget" in not_found("widget")
