"""
FOMC announcement dates 2008-2027.

These are the dates when the Fed publishes its rate decision (end of the
2-day meeting, or the single-day emergency meeting date).  The strategy
skips new entries when the *next* trading day is on this list — entry on
the eve of a Fed announcement carries uncompensated gap risk.

Source: Federal Reserve (federalreserve.gov/monetarypolicy/fomccalendars.htm).
2026-2027 are estimated from the pattern (8 meetings/year).

Fixed safety constant — do NOT add to the tunable parameter list.
"""

from datetime import date

# fmt: off
FOMC_DATES: frozenset[date] = frozenset([
    # 2008 (includes emergency inter-meeting cuts)
    date(2008, 1, 30), date(2008, 3, 18), date(2008, 4, 30), date(2008, 6, 25),
    date(2008, 8, 5),  date(2008, 9, 16), date(2008, 10, 8), date(2008, 10, 29),
    date(2008, 12, 16),
    # 2009
    date(2009, 1, 28), date(2009, 3, 18), date(2009, 4, 29), date(2009, 6, 24),
    date(2009, 8, 12), date(2009, 9, 23), date(2009, 11, 4), date(2009, 12, 16),
    # 2010
    date(2010, 1, 27), date(2010, 3, 16), date(2010, 4, 28), date(2010, 6, 23),
    date(2010, 8, 10), date(2010, 9, 21), date(2010, 11, 3), date(2010, 12, 14),
    # 2011
    date(2011, 1, 26), date(2011, 3, 15), date(2011, 4, 27), date(2011, 6, 22),
    date(2011, 8, 9),  date(2011, 9, 21), date(2011, 11, 2), date(2011, 12, 13),
    # 2012
    date(2012, 1, 25), date(2012, 3, 13), date(2012, 4, 25), date(2012, 6, 20),
    date(2012, 8, 1),  date(2012, 9, 13), date(2012, 10, 24), date(2012, 12, 12),
    # 2013
    date(2013, 1, 30), date(2013, 3, 20), date(2013, 5, 1),  date(2013, 6, 19),
    date(2013, 7, 31), date(2013, 9, 18), date(2013, 10, 30), date(2013, 12, 18),
    # 2014
    date(2014, 1, 29), date(2014, 3, 19), date(2014, 4, 30), date(2014, 6, 18),
    date(2014, 7, 30), date(2014, 9, 17), date(2014, 10, 29), date(2014, 12, 17),
    # 2015
    date(2015, 1, 28), date(2015, 3, 18), date(2015, 4, 29), date(2015, 6, 17),
    date(2015, 7, 29), date(2015, 9, 17), date(2015, 10, 28), date(2015, 12, 16),
    # 2016
    date(2016, 1, 27), date(2016, 3, 16), date(2016, 4, 27), date(2016, 6, 15),
    date(2016, 7, 27), date(2016, 9, 21), date(2016, 11, 2), date(2016, 12, 14),
    # 2017
    date(2017, 2, 1),  date(2017, 3, 15), date(2017, 5, 3),  date(2017, 6, 14),
    date(2017, 7, 26), date(2017, 9, 20), date(2017, 11, 1), date(2017, 12, 13),
    # 2018
    date(2018, 1, 31), date(2018, 3, 21), date(2018, 5, 2),  date(2018, 6, 13),
    date(2018, 8, 1),  date(2018, 9, 26), date(2018, 11, 8), date(2018, 12, 19),
    # 2019
    date(2019, 1, 30), date(2019, 3, 20), date(2019, 5, 1),  date(2019, 6, 19),
    date(2019, 7, 31), date(2019, 9, 18), date(2019, 10, 30), date(2019, 12, 11),
    # 2020 (includes emergency meetings Mar 3 and Mar 15)
    date(2020, 1, 29), date(2020, 3, 3),  date(2020, 3, 15), date(2020, 4, 29),
    date(2020, 6, 10), date(2020, 7, 29), date(2020, 9, 16), date(2020, 11, 5),
    date(2020, 12, 16),
    # 2021
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16),
    date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    # 2022
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4),  date(2022, 6, 15),
    date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    # 2023
    date(2023, 2, 1),  date(2023, 3, 22), date(2023, 5, 3),  date(2023, 6, 14),
    date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    # 2024
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),  date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),  date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    # 2026 — estimated from pattern
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 11, 4), date(2026, 12, 16),
    # 2027 — estimated from pattern
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 16),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 11, 3), date(2027, 12, 15),
])
# fmt: on


def is_fomc_day(d: date) -> bool:
    """Return True if `d` is a scheduled FOMC announcement date."""
    return d in FOMC_DATES
