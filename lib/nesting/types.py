from dataclasses import dataclass


@dataclass
class Violation:
    line_number: int
    depth: int
    function_name: str
