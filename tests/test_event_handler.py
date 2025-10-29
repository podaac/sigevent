from datetime import datetime, timezone
from os import environ
from unittest import TestCase
from unittest.mock import patch

from pytest import fixture

from podaac.sigevent.message import EventLevel, EventMessage

with (
    patch('boto3.client'),
    patch('boto3.resource'),
    patch.dict(
        environ,
        {
            'SIGEVENT_ENV': 'test',
            'SIGEVENT_notification_emails': '[]',
            'SIGEVENT_max_daily_warns': '3',
        },
    ),
):
    from podaac.sigevent import event_handler


@fixture
def event_message():
    return EventMessage(
        collection_name='collection-name',
        category='category',
        subject='subject',
        description='description',
        source_name='source-name',
        executor='executor',
        event_level=EventLevel.DEBUG,
        timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc),
        message='Test message'
    )


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_existing(mock_date):
    mock_date.now.return_value = datetime(1970, 1, 1, tzinfo=timezone.utc)
    event_handler.notification_table.get_item.return_value = {
        'Item': {
            'date': '1970-01-01',
            'message_hash': None,
            'count': 42,
            'expiration': None,
        }
    }

    count = event_handler.lookup_notification_count(None)
    assert count == 42


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_expired(mock_date):
    mock_date.now.return_value = datetime(1970, 1, 2, tzinfo=timezone.utc)
    event_handler.notification_table.get_item.return_value = {
        'Item': {
            'date': '1970-01-01',
            'message_hash': 'test-hash',
            'count': 42,
            'expiration': None,
        }
    }

    count = event_handler.lookup_notification_count('test-hash')

    assert count == 0
    event_handler.notification_table.put_item.assert_called_with(
        Item={
            'message_hash': 'test-hash',
            'date': '1970-01-02',
            'count': 0,
            'expiration': 172800,
        }
    )


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_nonexistent(mock_date):
    mock_date.now.return_value = datetime(1970, 1, 1, tzinfo=timezone.utc)
    event_handler.notification_table.get_item.return_value = {}

    count = event_handler.lookup_notification_count('test-hash')

    assert count == 0
    event_handler.notification_table.put_item.assert_called_with(
        Item={
            'message_hash': 'test-hash',
            'date': '1970-01-01',
            'count': 0,
            'expiration': 86400,
        }
    )


@patch(
    'podaac.sigevent.event_handler.NOTIFICATION_EMAILS',
    ['joshua.a.garde@jpl.nasa.gov', 'podaac-ia@jpl.nasa.gov'],
)
def test_send_notification():
    event_handler.send_notification(
        EventMessage(
            collection_name='collection-name',
            category='category',
            subject='subject',
            description='description',
            source_name='source-name',
            executor='executor',
            event_level=EventLevel.ERROR,
        )
    )

    emails = ('joshua.a.garde@jpl.nasa.gov', 'podaac-ia@jpl.nasa.gov')

    assert event_handler.ses.send_email.call_count == 2
    for call in event_handler.ses.send_email.call_args_list:
        kwargs = call.kwargs
        assert kwargs['Destination']['ToAddresses'][0] in emails


@patch('podaac.sigevent.event_handler.cloudwatchlogs')
@patch('podaac.sigevent.event_handler.lookup_notification_count')
@patch('podaac.sigevent.event_handler.send_notification')
@patch('podaac.sigevent.event_handler.increment_notification_count')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_send(mock_increment, mock_send, mock_count, mock_cloudwatch, event_message):
    mock_count.return_value = 0
    event_message = event_message.model_copy(
        update={
            'collection_name': 'unique-collection-name',
            'event_level': EventLevel.WARN
        }
    )

    event_handler.process_event_message(event_message)

    mock_cloudwatch.create_log_stream.assert_called_with(
        logGroupName='test-cw-group', logStreamName='unique-collection-name'
    )
    mock_cloudwatch.put_log_events.assert_called_with(
        logGroupName='test-cw-group',
        logStreamName='unique-collection-name',
        logEvents=[{
            'timestamp': 0,
            'message': event_message.model_dump_json()
        }]
    )
    mock_send.assert_called_with(event_message)
    mock_increment.assert_called_once()


@patch('podaac.sigevent.event_handler.cloudwatchlogs')
@patch('podaac.sigevent.event_handler.lookup_notification_count')
@patch('podaac.sigevent.event_handler.send_notification')
@patch('podaac.sigevent.event_handler.increment_notification_count')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_no_send(mock_increment, mock_send, mock_count, mock_cloudwatch, event_message):
    mock_count.return_value = 999
    event_message.model_copy(update={
        'event_level': EventLevel.WARN
    })

    event_handler.process_event_message(event_message)

    mock_cloudwatch.put_log_events.assert_called_with(
        logGroupName='test-cw-group',
        logStreamName='collection-name',
        logEvents=[{
            'timestamp': 0,
            'message': event_message.model_dump_json()
        }]
    )
    mock_send.assert_not_called()
    mock_increment.assert_not_called()


@patch('podaac.sigevent.event_handler.cloudwatchlogs')
@patch('podaac.sigevent.event_handler.lookup_notification_count')
@patch('podaac.sigevent.event_handler.send_notification')
@patch('podaac.sigevent.event_handler.increment_notification_count')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_always_send(mock_increment, mock_send, mock_count, mock_cloudwatch, event_message):
    mock_count.return_value = 999
    event_message = event_message.model_copy(update={
        'event_level': EventLevel.ERROR
    })
    
    event_handler.process_event_message(event_message)

    mock_cloudwatch.put_log_events.assert_called_with(
        logGroupName='test-cw-group',
        logStreamName='collection-name',
        logEvents=[{
            'timestamp': 0,
            'message': event_message.model_dump_json()
        }]
    )
    mock_send.assert_called_with(event_message)
    mock_increment.assert_not_called()
