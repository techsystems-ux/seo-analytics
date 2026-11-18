"""Validated inclusive date windows for the Organic Search dashboard."""
from datetime import date, timedelta


def select_range(args, latest):
    fallback = (latest - timedelta(days=27), latest)
    try:
        if args.get('start') or args.get('end'):
            start, end = date.fromisoformat(args.get('start', '')), date.fromisoformat(args.get('end', ''))
            if start > end:
                raise ValueError('Start date must be on or before the end date.')
            if end > latest:
                raise ValueError('End date is after the latest available Search Console data.')
            if (end - start).days >= 366:
                raise ValueError('Choose no more than 366 days.')
            return (start, end), None
        days = int(args.get('days', '28'))
        if days not in (7, 28, 90):
            raise ValueError('Choose a 7, 28 or 90 day preset, or enter custom dates.')
        return (latest - timedelta(days=days - 1), latest), None
    except (ValueError, TypeError) as exc:
        message = str(exc) if str(exc).startswith(('Start date', 'End date', 'Choose')) else 'Enter valid start and end dates.'
        return fallback, message + ' Showing the latest 28 days.'
