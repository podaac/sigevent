from datetime import datetime, timezone
import logging
from os import environ
from unittest import TestCase
from unittest.mock import patch

from botocore.exceptions import ClientError
from pytest import fixture, raises

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


CONDITION_FAILED = ClientError(
    {'Error': {'Code': 'ConditionalCheckFailedException', 'Message': 'stub'}},
    'UpdateItem'
)


def _reset_table():
    """
    notification_table is a module-level mock shared by every test, so any
    side_effect or recorded call has to be cleared between them.
    """
    event_handler.notification_table.reset_mock(side_effect=True)
    event_handler.notification_table.update_item.reset_mock(side_effect=True)
    event_handler.notification_table.get_item.reset_mock(side_effect=True)


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_existing(mock_date):
    """An item already carrying today's date: the rollover is refused and the
    stored count is read back unchanged."""
    _reset_table()
    mock_date.now.return_value = datetime(1970, 1, 1)
    event_handler.notification_table.update_item.side_effect = CONDITION_FAILED
    event_handler.notification_table.get_item.return_value = {
        'Item': {
            'date': '1970-01-01',
            'message_hash': 'test-hash',
            'count': 42,
            'expiration': None,
        }
    }

    count = event_handler.lookup_notification_count('test-hash')
    assert count == 42


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_expired(mock_date):
    """A stale item: the conditional update rolls it over to today at zero."""
    _reset_table()
    mock_date.now.return_value = datetime(1970, 1, 2)
    event_handler.notification_table.update_item.return_value = {
        'Attributes': {
            'message_hash': 'test-hash',
            'date': '1970-01-02',
            'count': 0,
            'expiration': 201600,
        }
    }

    count = event_handler.lookup_notification_count('test-hash')

    assert count == 0

    kwargs = event_handler.notification_table.update_item.call_args.kwargs
    assert kwargs['Key'] == {'message_hash': 'test-hash'}
    assert kwargs['ExpressionAttributeValues'] == {
        ':today': '1970-01-02',
        ':zero': 0,
        ':expiration': 201600,
    }
    assert 'attribute_not_exists' in kwargs['ConditionExpression']
    assert '#date <> :today' in kwargs['ConditionExpression']
    event_handler.notification_table.put_item.assert_not_called()


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_nonexistent(mock_date):
    """No item at all: the same conditional update creates it at zero."""
    _reset_table()
    mock_date.now.return_value = datetime(1970, 1, 1)
    event_handler.notification_table.update_item.return_value = {
        'Attributes': {
            'message_hash': 'test-hash',
            'date': '1970-01-01',
            'count': 0,
            'expiration': 115200,
        }
    }

    count = event_handler.lookup_notification_count('test-hash')

    assert count == 0
    kwargs = event_handler.notification_table.update_item.call_args.kwargs
    assert kwargs['ExpressionAttributeValues'][':expiration'] == 115200


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_ttl_race(mock_date):
    """The time to live may delete the item between the refused condition and the read;
    that must return zero rather than raise KeyError."""
    _reset_table()
    mock_date.now.return_value = datetime(1970, 1, 1)
    event_handler.notification_table.update_item.side_effect = CONDITION_FAILED
    event_handler.notification_table.get_item.return_value = {}

    assert event_handler.lookup_notification_count('test-hash') == 0


@patch('podaac.sigevent.event_handler.datetime')
def test_lookup_notification_count_propagates_other_errors(mock_date):
    _reset_table()
    mock_date.now.return_value = datetime(1970, 1, 1)
    event_handler.notification_table.update_item.side_effect = ClientError(
        {'Error': {'Code': 'ProvisionedThroughputExceededException'}},
        'UpdateItem'
    )

    with raises(ClientError):
        event_handler.lookup_notification_count('test-hash')


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

def test_get_int_param_missing_uses_default():
    """A parameter that was never deployed must not kill the lambda at import.
    The legacy venues run older stacks that lack newer parameters entirely."""
    with patch.object(event_handler.utils, 'get_param', return_value=None):
        assert event_handler.get_int_param('nope', 7) == 7

def test_get_int_param_present():
    with patch.object(event_handler.utils, 'get_param', return_value='11'):
        assert event_handler.get_int_param('max_daily_warns', 3) == 11

def test_get_int_param_unparseable_uses_default():
    """A typo in an operator-edited value must degrade, not crash."""
    with patch.object(event_handler.utils, 'get_param', return_value='three'):
        assert event_handler.get_int_param('max_daily_warns', 3) == 3

def _error_message():
    return EventMessage(
        collection_name='collection-name',
        category='category',
        subject='subject',
        description='description',
        source_name='source-name',
        executor='executor',
        event_level=EventLevel.ERROR,
    )


@patch(
    'podaac.sigevent.event_handler.NOTIFICATION_EMAILS',
    ['good-one@jpl.nasa.gov', 'bad@jpl.nasa.gov', 'good-two@jpl.nasa.gov'],
)
def test_send_notification_continues_past_a_failing_recipient(caplog):
    """One bad address must not abort the invocation. Aborting would dead-letter
    the message and, on SQS redelivery, re-send to everyone who already got it."""
    event_handler.ses.send_email.reset_mock(side_effect=True)
    event_handler.ses.send_email.side_effect = [
        None,
        RuntimeError('rejected'),
        None,
    ]

    with caplog.at_level(logging.ERROR):
        sent = event_handler.send_notification(_error_message())

    # All three attempted, two delivered.
    assert event_handler.ses.send_email.call_count == 3
    assert sent == 2

    # The failure must be visible, a silently swallowed AccessDenied is how
    # this service failed unnoticed for two years.
    assert 'bad@jpl.nasa.gov' in caplog.text
    assert 'rejected' in caplog.text


@patch(
    'podaac.sigevent.event_handler.NOTIFICATION_EMAILS',
    ['a@jpl.nasa.gov', 'b@jpl.nasa.gov'],
)
def test_send_notification_raises_when_every_recipient_fails():
    """Total failure is a broken configuration, not a bad address. It must still
    dead-letter so the DLQ keeps surfacing it."""
    event_handler.ses.send_email.reset_mock(side_effect=True)
    event_handler.ses.send_email.side_effect = RuntimeError('AccessDenied')

    with raises(RuntimeError):
        event_handler.send_notification(_error_message())

    assert event_handler.ses.send_email.call_count == 2


@patch('podaac.sigevent.event_handler.NOTIFICATION_EMAILS', [])
def test_send_notification_no_recipients_does_not_raise():
    """An empty list means nothing was attempted, so nothing failed. Raising
    here would dead-letter every message in a venue with no recipients set."""
    event_handler.ses.send_email.reset_mock(side_effect=True)

    assert event_handler.send_notification(_error_message()) == 0
    event_handler.ses.send_email.assert_not_called()


# ---------------------------------------------------------------------------
# Bounded-volume Test
# These tests measure the send decision in process_event_message directly, 
# without AWS involved, so a volume regression is caught before anything is deployed.
# ---------------------------------------------------------------------------

STORM_EVENT_COUNT = 3100


class _FakeCounterStore:
    """In-memory stand-in for the DynamoDB counter, so a volume run counts the
    way production would instead of returning a fixed mock value."""

    def __init__(self):
        self.counts = {}

    def lookup(self, message_hash):
        return self.counts.setdefault(message_hash, 0)

    def increment(self, message_hash):
        self.counts[message_hash] = self.counts.get(message_hash, 0) + 1


def _run_volume(event_level, event_count):
    """Drives event_count events of one level through process_event_message and
    returns how many notifications were sent."""
    message = EventMessage(
        collection_name='storm-collection',
        category='category',
        subject='subject',
        description='description',
        source_name='source-name',
        executor='executor',
        event_level=event_level,
        timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc),
    )
    store = _FakeCounterStore()

    with (
        patch('podaac.sigevent.event_handler.cloudwatchlogs') as mock_cloudwatch,
        patch('podaac.sigevent.event_handler.send_notification') as mock_send,
        patch('podaac.sigevent.event_handler.lookup_notification_count', store.lookup),
        patch('podaac.sigevent.event_handler.increment_notification_count', store.increment),
        patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group'),
    ):
        for _ in range(event_count):
            event_handler.process_event_message(message)

        return mock_send.call_count, mock_cloudwatch.put_log_events.call_count


def test_warn_volume_is_bounded_under_storm_load():
    """The existing WARN cap bounds outbound email under storm load. This also
    validates the harness against a known-good path before it is pointed at
    ERROR in Step 4."""
    sends, _ = _run_volume(EventLevel.WARN, STORM_EVENT_COUNT)

    assert sends == event_handler.MAX_DAILY_WARNS


def test_every_event_reaches_cloudwatch_regardless_of_send_decision():
    """Rate limiting must never suppress the CloudWatch write. That log is the
    operators' complete record and the only thing that made the 2026-08-28
    incident diagnosable."""
    _, logged = _run_volume(EventLevel.WARN, STORM_EVENT_COUNT)

    assert logged == STORM_EVENT_COUNT


def test_error_volume_is_currently_unbounded():
    """Documents the open defect: ERROR has no cap, so N events produce N sends.

    This is the 2026-08-28 failure mode, asserted precisely so the fix in Step 4
    has a baseline to move. REPLACE THIS with a bounded assertion when
    max_daily_errors and storm detection land."""
    sends, _ = _run_volume(EventLevel.ERROR, STORM_EVENT_COUNT)

    assert sends == STORM_EVENT_COUNT
