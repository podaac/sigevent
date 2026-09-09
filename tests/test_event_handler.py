from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
from os import environ
from unittest import TestCase
from unittest.mock import MagicMock, patch

import boto3
from botocore.exceptions import ClientError
from moto import mock_dynamodb
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
@patch('podaac.sigevent.event_handler.claim_notification_slot')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_send(mock_claim, mock_send, mock_count, mock_cloudwatch, event_message):
    mock_claim.return_value = 1
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
    mock_claim.assert_called_once()


@patch('podaac.sigevent.event_handler.cloudwatchlogs')
@patch('podaac.sigevent.event_handler.lookup_notification_count')
@patch('podaac.sigevent.event_handler.send_notification')
@patch('podaac.sigevent.event_handler.claim_notification_slot')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_no_send(mock_claim, mock_send, mock_count, mock_cloudwatch, event_message):
    mock_claim.return_value = None
    # The result of model_copy was previously discarded, leaving this message at
    # the fixture's DEBUG level: the WARN cap was never exercised at all.
    event_message = event_message.model_copy(update={
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
    mock_claim.assert_called_once()


@patch('podaac.sigevent.event_handler.cloudwatchlogs')
@patch('podaac.sigevent.event_handler.evaluate_storm')
@patch('podaac.sigevent.event_handler.lookup_notification_count')
@patch('podaac.sigevent.event_handler.send_notification')
@patch('podaac.sigevent.event_handler.claim_notification_slot')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_error_sends_when_not_storming(
    mock_claim, mock_send, mock_count, mock_storm, mock_cloudwatch,
    event_message
):
    """An ERROR below both the storm threshold and the daily cap still goes out
    individually, as before. Replaces test_process_event_message_always_send,
    which asserted the uncapped behaviour this change removes."""
    mock_storm.return_value = {
        'suppress_individual': False, 'summary_count': None,
        'all_clear_count': None, 'window_seconds': None,
    }
    mock_claim.return_value = 1
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
    # ERROR is now counted, where previously it was not. That is the cap.
    mock_claim.assert_called_once()


@patch('podaac.sigevent.event_handler.cloudwatchlogs')
@patch('podaac.sigevent.event_handler.evaluate_storm')
@patch('podaac.sigevent.event_handler.claim_cap_notice')
@patch('podaac.sigevent.event_handler.lookup_notification_count')
@patch('podaac.sigevent.event_handler.send_notification')
@patch('podaac.sigevent.event_handler.send_storm_notification')
@patch('podaac.sigevent.event_handler.claim_notification_slot')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_error_at_cap_notifies_once(
    mock_slot, mock_storm_notify, mock_send, mock_count, mock_claim, mock_storm,
    mock_cloudwatch, event_message
):
    """At the cap, individual sends stop but the operator is told once. A cap
    that produces silence is what operators explicitly do not want."""
    mock_storm.return_value = {
        'suppress_individual': False, 'summary_count': None,
        'all_clear_count': None, 'window_seconds': None,
    }
    mock_slot.return_value = None          # the cap is spent
    mock_claim.return_value = True         # claim_cap_notice: first today
    event_message = event_message.model_copy(update={
        'event_level': EventLevel.ERROR
    })

    event_handler.process_event_message(event_message)

    mock_send.assert_not_called()
    mock_storm_notify.assert_called_once()
    assert mock_storm_notify.call_args.args[1] == 'cap'


@patch('podaac.sigevent.event_handler.cloudwatchlogs')
@patch('podaac.sigevent.event_handler.evaluate_storm')
@patch('podaac.sigevent.event_handler.lookup_notification_count')
@patch('podaac.sigevent.event_handler.send_notification')
@patch('podaac.sigevent.event_handler.send_storm_notification')
@patch('podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP', 'test-cw-group')
def test_process_event_message_error_suppressed_while_storming(
    mock_storm_notify, mock_send, mock_count, mock_storm, mock_cloudwatch,
    event_message
):
    """While storming: no individual mail, and the daily counter is not even
    consulted, so a storm cannot burn through the daily cap."""
    mock_storm.return_value = {
        'suppress_individual': True, 'summary_count': 11,
        'all_clear_count': None, 'window_seconds': None,
    }
    event_message = event_message.model_copy(update={
        'event_level': EventLevel.ERROR
    })

    event_handler.process_event_message(event_message)

    mock_send.assert_not_called()
    mock_count.assert_not_called()
    assert mock_storm_notify.call_args.args[1] == 'summary'

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

STORM_EVENT_COUNT = 3100

class _FakeCounterStore:
    """In-memory stand-in for the DynamoDB counter, so a volume run counts the
    way production would instead of returning a fixed mock value."""

    def __init__(self):
        self.counts = {}

    def lookup(self, message_hash):
        return self.counts.setdefault(message_hash, 0)

    def claim(self, message_hash, limit):
        """Mirrors claim_notification_slot: increment only while under limit."""
        count = self.counts.setdefault(message_hash, 0)

        if count >= limit:
            return None

        self.counts[message_hash] = count + 1
        return count + 1


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
        patch('podaac.sigevent.event_handler.claim_notification_slot', store.claim),
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

@fixture
def storm_table():
    with mock_dynamodb():
        with patch.dict(environ, {
            'AWS_ACCESS_KEY_ID': 'testing',
            'AWS_SECRET_ACCESS_KEY': 'testing',
            'AWS_DEFAULT_REGION': 'us-west-2',
        }):
            resource = boto3.resource('dynamodb', region_name='us-west-2')
            resource.create_table(
                TableName='sigevent-test-notification-count',
                KeySchema=[
                    {'AttributeName': 'message_hash', 'KeyType': 'HASH'}
                ],
                AttributeDefinitions=[
                    {'AttributeName': 'message_hash', 'AttributeType': 'S'}
                ],
                BillingMode='PAY_PER_REQUEST',
            )
            table = resource.Table('sigevent-test-notification-count')

            with patch(
                'podaac.sigevent.event_handler.notification_table', table
            ):
                yield table


def _drive_errors(count):
    """Runs `count` ERROR events for one collection through the real dispatch,
    returning (individual sends, [storm notification kinds])."""
    message = EventMessage(
        collection_name='storm-collection',
        category='category',
        subject='subject',
        description='description',
        source_name='source-name',
        executor='executor',
        event_level=EventLevel.ERROR,
        timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc),
    )

    with (
        patch('podaac.sigevent.event_handler.cloudwatchlogs'),
        patch('podaac.sigevent.event_handler.send_notification') as mock_send,
        patch(
            'podaac.sigevent.event_handler.send_storm_notification'
        ) as mock_storm,
        patch(
            'podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP',
            'test-cw-group'
        ),
    ):
        for _ in range(count):
            event_handler.process_event_message(message)

        kinds = [call.args[1] for call in mock_storm.call_args_list]
        return mock_send.call_count, kinds


def test_error_volume_is_bounded_under_storm_load(storm_table):
    """The headline guarantee. 3,100 ERROR events -- one collection's share of
    the 2026-08-28 storm -- must not produce 3,100 emails.

    Replaces test_error_volume_is_currently_unbounded, which asserted the defect
    this closes."""
    sends, kinds = _drive_errors(STORM_EVENT_COUNT)

    # Events 1-10 land under the threshold and go out individually. Event 11
    # crosses it and is announced once. Everything after is suppressed.
    assert sends == event_handler.STORM_THRESHOLD
    assert kinds == ['summary']
    assert sends + len(kinds) == 11


def test_storm_summary_is_sent_exactly_once_per_window(storm_table):
    """The failure mode this design exists to avoid: a summary per invocation
    once the threshold trips would simply recreate the flood."""
    _, kinds = _drive_errors(500)

    assert kinds.count('summary') == 1


def test_below_threshold_behaves_exactly_as_before(storm_table):
    """No storm, no change: every event still emails individually."""
    sends, kinds = _drive_errors(event_handler.STORM_THRESHOLD)

    assert sends == event_handler.STORM_THRESHOLD
    assert kinds == []


def test_every_event_reaches_cloudwatch_during_a_storm(storm_table):
    """Suppressing email must never suppress the log. That record is the
    operators' only complete history and the thing that made the 2026-08-28
    incident diagnosable."""
    message = EventMessage(
        collection_name='storm-collection',
        category='category',
        subject='subject',
        description='description',
        source_name='source-name',
        executor='executor',
        event_level=EventLevel.ERROR,
        timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc),
    )

    with (
        patch('podaac.sigevent.event_handler.cloudwatchlogs') as mock_cw,
        patch('podaac.sigevent.event_handler.send_notification'),
        patch('podaac.sigevent.event_handler.send_storm_notification'),
        patch(
            'podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP',
            'test-cw-group'
        ),
    ):
        for _ in range(STORM_EVENT_COUNT):
            event_handler.process_event_message(message)

    assert mock_cw.put_log_events.call_count == STORM_EVENT_COUNT


def test_daily_cap_bounds_a_slow_burn_that_never_storms(storm_table):
    """The trickle case: too slow to trip storm detection, but enough to flood a
    mailbox over a day. The daily cap is the backstop."""
    message = EventMessage(
        collection_name='trickle-collection',
        category='category',
        subject='subject',
        description='description',
        source_name='source-name',
        executor='executor',
        event_level=EventLevel.ERROR,
        timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc),
    )

    with (
        patch('podaac.sigevent.event_handler.cloudwatchlogs'),
        patch('podaac.sigevent.event_handler.evaluate_storm') as mock_storm,
        patch('podaac.sigevent.event_handler.send_notification') as mock_send,
        patch(
            'podaac.sigevent.event_handler.send_storm_notification'
        ) as mock_notify,
        patch(
            'podaac.sigevent.event_handler.CLOUDWATCH_LOG_GROUP',
            'test-cw-group'
        ),
    ):
        mock_storm.return_value = {
            'suppress_individual': False, 'summary_count': None,
            'all_clear_count': None, 'window_seconds': None,
        }
        for _ in range(60):
            event_handler.process_event_message(message)

    assert mock_send.call_count == event_handler.MAX_DAILY_ERRORS
    # Exactly one "limit reached" notice, however many events follow.
    assert [c.args[1] for c in mock_notify.call_args_list] == ['cap']


def test_claim_cap_notice_is_granted_once_per_day(storm_table):
    """The claim is a conditional write, so concurrent invocations cannot each
    announce the cap."""
    assert event_handler.claim_cap_notice('h', '1970-01-01') is True
    assert event_handler.claim_cap_notice('h', '1970-01-01') is False
    # A new day re-arms it.
    assert event_handler.claim_cap_notice('h', '1970-01-02') is True


def test_window_rolls_and_backs_off_then_clears(storm_table):
    """Walks the window state machine directly: trip, roll while storming (with
    backoff), then roll below threshold to produce the all-clear."""
    key = {'message_hash': 'h'}

    # Trip the storm.
    for _ in range(event_handler.STORM_THRESHOLD + 1):
        decision = event_handler.evaluate_storm('h')
    assert decision['suppress_individual'] is True
    assert decision['summary_count'] == event_handler.STORM_THRESHOLD + 1

    item = storm_table.get_item(Key=key)['Item']
    assert item['storm_active'] is True
    first_window = int(item['window_seconds'])

    # Expire the window with the storm still raging.
    storm_table.update_item(
        Key=key,
        UpdateExpression='SET window_ends_at = :past',
        ExpressionAttributeValues={':past': 0},
    )
    decision = event_handler.evaluate_storm('h')

    assert decision['summary_count'] == event_handler.STORM_THRESHOLD + 1
    assert decision['all_clear_count'] is None
    item = storm_table.get_item(Key=key)['Item']
    # Backoff: the next window is longer, so a long storm does not produce a
    # summary every five minutes for hours.
    assert int(item['window_seconds']) == first_window * 2

    # Expire again, this time with the window below threshold.
    storm_table.update_item(
        Key=key,
        UpdateExpression='SET window_ends_at = :past',
        ExpressionAttributeValues={':past': 0},
    )
    decision = event_handler.evaluate_storm('h')

    assert decision['all_clear_count'] == 1
    assert decision['suppress_individual'] is False
    item = storm_table.get_item(Key=key)['Item']
    assert item['storm_active'] is False
    # And the window length resets for the next storm.
    assert int(item['window_seconds']) == event_handler.STORM_WINDOW_SECONDS


def test_storm_summary_reports_the_window_it_actually_covers(storm_table):
    """An escalated summary must describe the lengthened window, not the
    configured base window. Reporting "5 minutes" for a count covering ten
    understates the rate to the operator during an escalating storm."""
    key = {'message_hash': 'h'}

    for _ in range(event_handler.STORM_THRESHOLD + 1):
        decision = event_handler.evaluate_storm('h')
    assert decision['window_seconds'] == event_handler.STORM_WINDOW_SECONDS

    # Expire the window with the storm still raging, forcing backoff.
    storm_table.update_item(
        Key=key,
        UpdateExpression='SET window_ends_at = :past',
        ExpressionAttributeValues={':past': 0},
    )
    decision = event_handler.evaluate_storm('h')

    # The summary covers the window that just closed, which was the base
    # length; the item's window is now double that.
    assert decision['summary_count'] == event_handler.STORM_THRESHOLD + 1
    assert decision['window_seconds'] == event_handler.STORM_WINDOW_SECONDS
    item = storm_table.get_item(Key=key)['Item']
    assert int(item['window_seconds']) == event_handler.STORM_WINDOW_SECONDS * 2

    # Expire the doubled window. The all-clear must report the doubled length.
    storm_table.update_item(
        Key=key,
        UpdateExpression='SET window_ends_at = :past',
        ExpressionAttributeValues={':past': 0},
    )
    decision = event_handler.evaluate_storm('h')

    assert decision['all_clear_count'] == 1
    assert decision['window_seconds'] == event_handler.STORM_WINDOW_SECONDS * 2


@patch('podaac.sigevent.event_handler.send_email_to_recipients')
def test_storm_notification_renders_the_window_it_is_given(mock_send,
                                                           event_message):
    """The window reaches the rendered body, rather than being dropped on the
    way through send_storm_notification."""
    event_handler.send_storm_notification(event_message, 'summary', 42, 600)
    body = mock_send.call_args.args[1]
    assert '10 minutes' in body

    # Omitted, as the cap notice does, falls back to the configured window.
    mock_send.reset_mock()
    event_handler.send_storm_notification(event_message, 'summary', 42)
    body = mock_send.call_args.args[1]
    assert _describe(event_handler.STORM_WINDOW_SECONDS) in body


def _describe(seconds):
    return event_handler._describe_window(seconds)


def test_claim_notification_slot_stops_exactly_at_the_limit(storm_table):
    """Slots are handed out 1..limit and then refused, with no off-by-one at
    either end."""
    storm_table.put_item(Item={'message_hash': 'h', 'count': 0})

    claimed = [
        event_handler.claim_notification_slot('h', 3) for _ in range(6)
    ]

    assert claimed == [1, 2, 3, None, None, None]
    assert int(storm_table.get_item(Key={'message_hash': 'h'})['Item']['count']) == 3


def test_claim_notification_slot_needs_the_day_rolled_first(storm_table):
    """Without #count the condition is false, which reads as "at cap" and would
    silently suppress the first notification of the day. lookup_notification_count
    is what creates it, so the ordering in process_error_message is load-bearing."""
    assert event_handler.claim_notification_slot('never-seen', 3) is None

    event_handler.lookup_notification_count('never-seen')
    assert event_handler.claim_notification_slot('never-seen', 3) == 1


def test_claim_notification_slot_is_a_single_conditional_write():
    """The defect this replaces was a read, a comparison in Python, then a blind
    write. 
    """
    table = MagicMock()
    table.update_item.return_value = {'Attributes': {'count': 1}}

    with patch('podaac.sigevent.event_handler.notification_table', table):
        assert event_handler.claim_notification_slot('h', 3) == 1

    table.get_item.assert_not_called()
    table.update_item.assert_called_once()

    kwargs = table.update_item.call_args.kwargs
    assert kwargs['ConditionExpression'] == '#count < :limit'
    assert kwargs['ExpressionAttributeValues'][':limit'] == 3


def test_claim_notification_slot_grants_exactly_the_limit(storm_table):
    """Exactly `limit` claims succeed however many callers ask. NOTE: this does
    not demonstrate atomicity -- see the test above for why."""
    storm_table.put_item(Item={'message_hash': 'h', 'count': 0})
    limit = 5

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(
            lambda _: event_handler.claim_notification_slot('h', limit),
            range(64)
        ))

    granted = [r for r in results if r is not None]
    assert len(granted) == limit
    # Every winner got a distinct slot number; none was handed out twice.
    assert sorted(granted) == list(range(1, limit + 1))


def test_expression_attribute_names_are_all_referenced():
    """Every declared ExpressionAttributeName must appear in the expression that
    declares it.
    """
    table = MagicMock()
    table.get_item.return_value = {}

    with patch('podaac.sigevent.event_handler.notification_table', table):
        # Live-window path.
        table.update_item.return_value = {
            'Attributes': {'window_count': 1, 'storm_active': False}
        }
        event_handler.evaluate_storm('h')

        # Window-roll path: the first update must fail its condition.
        table.update_item.side_effect = [CONDITION_FAILED, {}, {}]
        event_handler.evaluate_storm('h')

        # Daily counter and cap claim.
        table.update_item.side_effect = None
        table.update_item.return_value = {'Attributes': {'count': 0}}
        event_handler.lookup_notification_count('h')
        event_handler.claim_cap_notice('h', '1970-01-01')

    checked = 0
    for call in table.update_item.call_args_list:
        kwargs = call.kwargs
        names = kwargs.get('ExpressionAttributeNames', {})
        if not names:
            continue

        expressions = ' '.join([
            kwargs.get('UpdateExpression', ''),
            kwargs.get('ConditionExpression', ''),
        ])
        unused = sorted(n for n in names if n not in expressions)

        assert not unused, (
            f'ExpressionAttributeNames declared but never referenced: {unused} '
            f'in {expressions!r}'
        )
        checked += 1

    # Guard against the test silently inspecting nothing.
    assert checked >= 4
