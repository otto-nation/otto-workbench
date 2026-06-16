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


class TestScopeBoundaries:
    def test_function_boundary_resets_depth(self):
        code = '''\
func funcA() {
    for _, item := range items {
        if item.Valid {
            if item.Active {
                fmt.Println(item)
            }
        }
    }
}

func funcB() {
    if x > 0 {
        fmt.Println("depth 1 again")
    }
}
'''
        assert _check(code) == []

    def test_method_resets_depth(self):
        code = '''\
func (s *Server) handleA() {
    for _, r := range requests {
        if r.Valid {
            if r.Auth {
                process(r)
            }
        }
    }
}

func (s *Server) handleB() {
    if s.ready {
        fmt.Println("ok")
    }
}
'''
        assert _check(code) == []

    def test_closure_counts_as_nesting(self):
        code = '''\
func main() {
    for _, item := range items {
        if item.Valid {
            go func() {
                fmt.Println("inside closure")
            }()
        }
    }
}
'''
        violations = _check(code, max_depth=2)
        assert len(violations) > 0

    def test_switch_counts_as_nesting(self):
        code = '''\
func foo() {
    for _, item := range items {
        switch item.Type {
        case "a":
            if item.Active {
                fmt.Println("deep")
            }
        }
    }
}
'''
        violations = _check(code, max_depth=2)
        assert len(violations) > 0
        assert violations[0].depth == 3

    def test_select_counts_as_nesting(self):
        code = '''\
func foo() {
    for {
        select {
        case msg := <-ch:
            if msg.Important {
                for _, r := range msg.Recipients {
                    send(r, msg)
                }
            }
        }
    }
}
'''
        violations = _check(code)
        assert len(violations) > 0

    def test_else_does_not_increase_depth(self):
        code = '''\
func foo() {
    if x > 0 {
        fmt.Println("pos")
    } else {
        fmt.Println("neg")
    }
}
'''
        assert _check(code, max_depth=1) == []

    def test_else_if_does_not_increase_depth(self):
        code = '''\
func foo() {
    if x > 0 {
        fmt.Println("pos")
    } else if x == 0 {
        fmt.Println("zero")
    } else {
        fmt.Println("neg")
    }
}
'''
        assert _check(code, max_depth=1) == []

    def test_violation_reports_correct_function_name(self):
        code = '''\
func deepFunc() {
    for _, item := range items {
        if item.Valid {
            if item.Active {
                for _, sub := range item.Subs {
                    fmt.Println(sub)
                }
            }
        }
    }
}
'''
        violations = _check(code)
        assert violations[0].function_name == "deepFunc"
