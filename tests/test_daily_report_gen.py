from datetime import datetime, timezone
import json
from os import environ
from unittest import TestCase
from unittest.mock import patch
import pytest

from podaac.sigevent.message import EventLevel, EventMessage

with (
    patch('boto3.client'),
    patch.dict(environ, {
        'SIGEVENT_ENV': 'test',
        'SIGEVENT_notification_emails': '[]'
    })
):
    from podaac.sigevent import daily_report_gen

class TestDailyReportGen(TestCase):
    def setUp(self):
        daily_report_gen.cloudwatchlogs.reset_mock(return_value=True, side_effect=True)
        daily_report_gen.ses.reset_mock(return_value=True, side_effect=True)

    @patch('podaac.sigevent.daily_report_gen.datetime')
    @patch('podaac.sigevent.daily_report_gen.cloudwatchlogs')
    @patch('podaac.sigevent.daily_report_gen.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
    def test_search_error_logs(self, mock_cloudwatch, mock_date):
        mock_date.now.return_value = datetime(1990, 1, 1, tzinfo=timezone.utc)
        mock_cloudwatch.filter_log_events.return_value = {
            'events': [{
                'message': json.dumps({
                    'collection_name': 'collection-name',
                    'category': 'category',
                    'subject': 'subject',
                    'description': 'description',
                    'event_level': EventLevel.DEBUG,
                    'source_name': 'source-name',
                    'executor': 'executor'
                })
            }]
        }
        
        results = daily_report_gen.search_error_logs()

        mock_cloudwatch.filter_log_events.assert_called_once_with(
            logGroupName='test-cw-group',
            startTime=631152000000,
            endTime=631238399999
        )

        self.assertEqual(
            repr(results[0]),
            'EventMessage(event_level=DEBUG, subject=subject, description=description, collection_name=collection-name, granule_name=None, category=category, source_name=source-name, executor=executor, timestamp=None)'
        )

    def test_analyze_messages(self):
        messages = []
        message_counts = {
            EventLevel.INFO: 6,
            EventLevel.DEBUG: 3,
            EventLevel.WARN: 9,
            EventLevel.ERROR: 4
        }
        
        for level, count in message_counts.items():
            for _ in range(count):
                messages.append(EventMessage(
                    collection_name='collection-name',
                    category='category',
                    subject='subject',
                    description='description',
                    source_name='source-name',
                    executor='executor',
                    event_level=level
                ))

        results = daily_report_gen.analyze_messages(messages)

        self.assertEqual(results, [{
            'name': 'collection-name',
            'level_counts': {
                EventLevel.ERROR: 4,
                EventLevel.WARN: 9,
                EventLevel.INFO: 6,
                EventLevel.DEBUG: 3
            },
            'category_counts': {
                'category': 22
            }
        }])

    @patch('podaac.sigevent.daily_report_gen.NOTIFICATION_EMAILS', [
        'joshua.a.garde@jpl.nasa.gov',
        'podaac-ia@jpl.nasa.gov'
    ])
    @patch('podaac.sigevent.daily_report_gen.analyze_messages')
    def test_invoke(self, mock_analyze):
        mock_analyze.return_value = [{
            'name': 'collection-name',
            'level_counts': {
                EventLevel.ERROR: 4,
                EventLevel.WARN: 9,
                EventLevel.INFO: 6,
                EventLevel.DEBUG: 3
            },
            'category_counts': {
                'category': 22
            }
        }]

        daily_report_gen.invoke(None, None)
        self.assertEqual(daily_report_gen.ses.send_email.call_count, 2)
