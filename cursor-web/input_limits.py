"""Conservative no-truncation budgets matching the bundled site adapters.

These are adapter safety limits, not advertised model context windows. Count
UTF-16 units to match JavaScript String.length, including astral characters.
"""
# deepseek/arena: validated live in this pipeline. glm/kimi/qwen/gemini/meta:
# conservative STARTING budgets for the site adapters validated in the main
# ZeroScript extension (2026-06/07) but not yet live-validated HERE with large
# payloads - if the site clamps a write, the 0.4.11 mid-write clamp error says
# so; raise the cap once a large payload is confirmed to land intact.
LIMITS = {'deepseek': (160000, None), 'chatgpt': (120000, 600), 'arena': (118000, None),
          'glm': (100000, None), 'kimi': (100000, None), 'qwen': (100000, None),
          'gemini': (100000, None), 'meta': (100000, None)}
DEFAULT_LIMIT = (60000, None)


def utf16_units(text):
    return len(text.encode('utf-16-le')) // 2


def budget(session):
    return LIMITS.get(session.get('provider'), DEFAULT_LIMIT)


def size_error(text, session):
    cap, lines_cap = budget(session)
    units = utf16_units(text)
    lines = text.count('\n') + 1
    if units > cap or (lines_cap is not None and lines > lines_cap):
        return (f"Webpage input budget exceeded: provider={session.get('provider', 'unknown')}, "
                f"utf16_units={units}, limit={cap}, lines={lines}, line_limit={lines_cap}. "
                "Nothing sent or truncated. Reduce unrelated tools/history, or use a fresh conversation.")
    return None
