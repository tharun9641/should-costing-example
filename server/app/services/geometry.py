def nearly_equal(x: float, y: float, rel: float = 0.03) -> bool:
    return abs(x - y) <= rel * max(x, y)


def rotational_dims(bbox: list[float]) -> tuple[float, float]:
    """(diameter, length) of a turned part from its ascending-sorted envelope.

    The closest pair of dimensions is the cross-section: a shaft (Ø b, length c)
    or a disc (Ø c, length a). For hex bar the diameter is across corners.
    """
    a, b, c = bbox
    return (b, c) if b * b <= a * c else (c, a)  # b/a <= c/b without dividing
