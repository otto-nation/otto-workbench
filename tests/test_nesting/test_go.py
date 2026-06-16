from nesting.go import GoChecker
from nesting.types import Violation


def _check(code: str, max_depth: int = 3) -> list[Violation]:
    return GoChecker().check_nesting(code.splitlines(keepends=True), max_depth)


class TestBasicNesting:
    def test_flat_function_no_violations(self):
        code = '''\
func main() {
    fmt.Println("hello")
    x := 1 + 2
}
'''
        assert _check(code) == []

    def test_single_if_depth_1_passes(self):
        code = '''\
func main() {
    if x > 0 {
        fmt.Println("positive")
    }
}
'''
        assert _check(code) == []

    def test_double_nesting_passes_depth_3(self):
        code = '''\
func process() {
    for _, item := range items {
        if item.Valid {
            fmt.Println(item)
        }
    }
}
'''
        assert _check(code) == []

    def test_triple_nesting_passes_depth_3(self):
        code = '''\
func process() {
    for _, item := range items {
        if err := validate(item); err != nil {
            if item.Required {
                return err
            }
        }
    }
}
'''
        assert _check(code) == []

    def test_quadruple_nesting_violates_depth_3(self):
        code = '''\
func process() {
    for _, item := range items {
        if err := validate(item); err != nil {
            if item.Required {
                for _, sub := range item.Subs {
                    fmt.Println(sub)
                }
            }
        }
    }
}
'''
        violations = _check(code)
        assert len(violations) == 1
        assert violations[0].depth == 4
        assert violations[0].function_name == "process"

    def test_custom_max_depth_2(self):
        code = '''\
func foo() {
    if x > 0 {
        for i := range items {
            if i > 5 {
                fmt.Println(i)
            }
        }
    }
}
'''
        violations = _check(code, max_depth=2)
        assert len(violations) > 0
