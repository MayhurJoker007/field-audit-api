"""The audit questionnaire and how it's scored.

Scoring happens on the server. The app shows a live score while the rep fills it in, but the number that goes
to the client report is always recalculated here from the raw answers.
"""
import math

# question id -> (type, weight, extra)
QUESTIONS = {
    'fridge_present':        ('yes_no', 15, None),
    'fridge_clean':          ('yes_no', 10, None),
    'fridge_brand_share':    ('percent', 20, None),        # % of fridge facings that are our brands
    'price_board_visible':   ('yes_no', 10, None),
    'core_range_in_stock':   ('count', 25, 8),             # how many of the 8 core SKUs are on shelf
    'promo_material':        ('choice', 10, {'none': 0, 'old': 0.5, 'current': 1}),
    'out_of_date_stock':     ('yes_no_inverse', 10, None),  # finding expired stock costs points
}

GEOFENCE_M = 200


class AnswerError(ValueError):
    pass


def score(answers):
    """Returns a 0-100 score. Unknown or missing questions are errors; a half-filled audit isn't scored."""
    unknown = set(answers) - set(QUESTIONS)
    if unknown:
        raise AnswerError('unknown questions: %s' % ', '.join(sorted(unknown)))
    missing = set(QUESTIONS) - set(answers)
    # Fridge cleanliness and brand share only make sense if there is a fridge
    if answers.get('fridge_present') is False:
        missing -= {'fridge_clean', 'fridge_brand_share'}
    if missing:
        raise AnswerError('missing answers: %s' % ', '.join(sorted(missing)))

    earned = possible = 0.0
    for qid, (kind, weight, extra) in QUESTIONS.items():
        if qid not in answers:
            possible += weight          # no fridge: those points are simply lost
            continue
        value = answers[qid]
        if kind == 'yes_no':
            _expect(qid, isinstance(value, bool), 'true or false')
            fraction = 1.0 if value else 0.0
        elif kind == 'yes_no_inverse':
            _expect(qid, isinstance(value, bool), 'true or false')
            fraction = 0.0 if value else 1.0
        elif kind == 'percent':
            _expect(qid, isinstance(value, (int, float)) and 0 <= value <= 100, 'a number from 0 to 100')
            fraction = value / 100
        elif kind == 'count':
            _expect(qid, isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= extra,
                    'a whole number from 0 to %d' % extra)
            fraction = value / extra
        else:
            _expect(qid, value in extra, 'one of %s' % ', '.join(extra))
            fraction = extra[value]
        earned += weight * fraction
        possible += weight

    return round(100 * earned / possible, 1)


def _expect(qid, ok, what):
    if not ok:
        raise AnswerError('%s must be %s' % (qid, what))


def distance_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))
