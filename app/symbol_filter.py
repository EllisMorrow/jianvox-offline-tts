"""Literal character exclusion; never interprets regex or modifies editor text."""


def filter_symbols(text, enabled=False, symbols=""):
    if not enabled or not symbols:
        return text
    excluded = {ord(char): None for char in symbols if not char.isspace()}
    return text.translate(excluded)
