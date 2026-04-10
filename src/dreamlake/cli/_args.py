"""
Utility to parse a list of CLI args into a dict for params-proto _update().

Converts ['--category', 'audio', '--target', 'alice@robotics'] into
{'category': 'audio', 'target': 'alice@robotics'}.
"""


def args_to_dict(args: list) -> dict:
    """Parse --key value pairs from an args list into a dict."""
    result = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                result[key] = args[i + 1]
                i += 2
            else:
                result[key] = True
                i += 1
        else:
            i += 1
    return result
