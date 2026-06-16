import re


def strip_strings_and_comments(line: str) -> str:
    line = re.sub(r"'[^']*'", '', line)
    line = re.sub(r'"[^"]*"', '', line)
    line = re.sub(r'#.*', '', line)
    return line
