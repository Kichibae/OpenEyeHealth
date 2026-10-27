from datetime import time

from nightlight import from_12h, is_night, to_12h


def test_is_night():
    late = dict(start=time(21), end=time(7))   # crosses midnight
    assert is_night(time(23), **late) and is_night(time(3), **late)
    assert not is_night(time(12), **late) and not is_night(time(7), **late)
    early = dict(start=time(1), end=time(6))   # same day
    assert is_night(time(2), **early) and not is_night(time(23), **early)
    assert not is_night(time(9), start=time(9), end=time(9))   # same time = never


def test_12h():
    # 12 AM is midnight and 12 PM is noon, the classic mix-up.
    for hhmm, twelve in (("21:30", (9, 30, "PM")), ("00:05", (12, 5, "AM")),
                         ("12:00", (12, 0, "PM")), ("07:45", (7, 45, "AM"))):
        assert to_12h(hhmm) == twelve and from_12h(*twelve) == hhmm
    for bad in ((13, 0, "AM"), (0, 0, "AM"), (9, 60, "PM")):
        try:
            from_12h(*bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} should be rejected")


if __name__ == "__main__":
    test_is_night()
    test_12h()
    print("ok")
