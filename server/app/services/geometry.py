def nearly_equal(x: float, y: float, rel: float = 0.03) -> bool:
    return abs(x - y) <= rel * max(x, y)


def rotational_dims(bbox: list[float]) -> tuple[float, float]:
    """(diameter, length) of a turned part from its ascending-sorted envelope.

    a≈b is a shaft (Ø b, length c); otherwise a disc (Ø c, length a).
    """
    a, b, c = bbox
    return (b, c) if nearly_equal(a, b) else (c, a)
