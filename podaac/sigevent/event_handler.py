"""Main handler for Sigevent messages"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
from importlib import resources

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError
from podaac.sigevent.message import EventMessage, EventLevel
from podaac.sigevent.utilities import utils


logger = utils.get_logger(__name__)

# Applied when the corresponding SSM parameter is absent. The legacy venues run
# deployments that predate these settings and will not carry them until they are
# redeployed -- which is deliberately avoided -- so every tunable must survive a
# missing parameter.
DEFAULT_MAX_DAILY_WARNS = 3
DEFAULT_MAX_DAILY_ERRORS = 20
DEFAULT_STORM_THRESHOLD = 10
DEFAULT_STORM_WINDOW_MINUTES = 5
DEFAULT_STORM_SUMMARY_MAX_INTERVAL_MINUTES = 60


def get_int_param(name, default):
    """
    Reads an integer parameter, falling back to a default when it is absent or
    unparseable.

    utils.get_param() returns None for a parameter that was never deployed, and
    int(None) raises. At module scope that kills the lambda at import, for every
    message, on every invocation. A venue running an older deployment must
    degrade to a sane default rather than stop processing entirely.
    """
    raw = utils.get_param(name)

    if raw is None:
        logger.warning(
            'Parameter %s is not deployed in this venue; using default %s',
            name, default
        )
        return default

    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.error(
            'Parameter %s is not an integer (got %r); using default %s',
            name, raw, default
        )
        return default


CLOUDWATCH_LOG_GROUP = utils.get_param('log_group')
NOTIFICATION_EMAILS = json.loads(utils.get_param('notification_emails'))
NOTIFICATION_TABLE_NAME = utils.get_param('notification_table_name')
NOTIFICATION_TEMPLATE = resources.files(__package__).joinpath(
    'resources', 'notification.html').read_text('utf-8')
STORM_TEMPLATE = resources.files(__package__).joinpath(
    'resources', 'storm_summary.html').read_text('utf-8')
STAGE = utils.get_param('stage')
MUTED_MODE = True if utils.get_param('muted_mode') == 'true' else False
MAX_DAILY_WARNS = get_int_param('max_daily_warns', DEFAULT_MAX_DAILY_WARNS)
MAX_DAILY_ERRORS = get_int_param('max_daily_errors', DEFAULT_MAX_DAILY_ERRORS)

STORM_THRESHOLD = get_int_param('storm_threshold', DEFAULT_STORM_THRESHOLD)
STORM_WINDOW_SECONDS = 60 * get_int_param(
    'storm_window_minutes', DEFAULT_STORM_WINDOW_MINUTES
)
STORM_MAX_WINDOW_SECONDS = 60 * get_int_param(
    'storm_summary_max_interval_minutes',
    DEFAULT_STORM_SUMMARY_MAX_INTERVAL_MINUTES
)

SES_REGION = utils.get_param('ses_region')
SES_SENDER_ARN = utils.get_param('ses_sender_arn')
SES_CONFIG_SET_NAME = utils.get_param('ses_config_set_name')

cloudwatchlogs = boto3.client('logs')
ses = boto3.client('sesv2', region_name=SES_REGION)

notification_table = boto3.resource('dynamodb').Table(NOTIFICATION_TABLE_NAME)
existing_log_streams = set()


def invoke(event: dict, _):
    """
    AWS Lambda entry point

    Parameters
    ----------
    event: dict
       AWS SQS event message
    _ : object
        Context object. Not used by this lambda
    """
    logger.debug('Event received: %s', event)

    for record in event['Records']:
        logger.debug('Attempting to parse: %s', str(record['body']))
        sns_record = json.loads(record['body'])
        raw_event_message = json.loads(sns_record['Message'])

        try:
            message = EventMessage.model_validate_json(sns_record['Message'])

            # Use SNS timestamp if message doesn't include timestamp
            if message.timestamp is None:
                logger.debug(
                    'Message does not include timestamp; using SNS timestamp'
                )
                message = message.model_copy(update={
                    'timestamp': datetime.fromisoformat(sns_record['Timestamp'])
                })

            process_event_message(message)
        except ValidationError as ex:
            logger.error(
                'Failed to validate message:\n%s\n%s', raw_event_message, ex
            )


def process_event_message(message: EventMessage):
    """
    Process a singular EventMessage performing the storage of the message in
    the CloudWatch log group and sending out a notification if the required
    conditions are met.
    
    On a WARN, the notification count is limited by MAX_DAILY_NOTIFICATIONS.
    This count limits the number of notifications sent out per collection,
    per day.
    
    On an ERROR, notifications are sent no matter what.
    
    For all else, notifications are just logged in CloudWatch without a
    notification.
    """

    # Create log stream if not exist or nop on already existing
    if message.collection_name not in existing_log_streams:
        try:
            cloudwatchlogs.create_log_stream(
                logGroupName=CLOUDWATCH_LOG_GROUP,
                logStreamName=message.collection_name
            )
        except ClientError as ex:
            if ex.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                logger.debug('Log stream already exists; no-op')
            else:
                raise ex

        existing_log_streams.add(message.collection_name)

    # Log to log group
    logger.info('Sending to log group')
    response = cloudwatchlogs.put_log_events(
        logGroupName=CLOUDWATCH_LOG_GROUP,
        logStreamName=message.collection_name,
        logEvents=[{
            'timestamp': int(message.timestamp.timestamp() * 1000),
            'message': message.model_dump_json()
        }]
    )
    logger.debug('put_log_events response: %s', response)

    # Bypass if we're in muted mode
    if MUTED_MODE:
        return

    # Filtered send logic
    if message.event_level is EventLevel.WARN:
        metadata_hash = _metadata_hash(message)

        notification_count = lookup_notification_count(metadata_hash)
        if notification_count < MAX_DAILY_WARNS:
            send_notification(message)
            increment_notification_count(metadata_hash)
    elif message.event_level is EventLevel.ERROR:
        process_error_message(message)
    else:
        logger.debug('Message not sent')


def _metadata_hash(message: EventMessage):
    """
    The counter key: one bucket per level, per collection.
    """
    return hashlib.sha1(
        bytes(message.event_level.value, 'utf-8') + \
        bytes(message.collection_name, 'utf-8'),
        usedforsecurity=False
    ).hexdigest()


def process_error_message(message: EventMessage):
    """
    Decides what an ERROR warrants.

    Storm detection is checked s.t. durng a burst a single summary carrying a
    running count is worth more than twenty individual emails
    followed by silence. Only when a collection is not storming does the daily
    cap apply.

    Storm summaries and the cap notice deliberately do NOT count against
    max_daily_errors. 

    Storm detection is not applied to WARN: max_daily_warns already bounds it at
    3 per collection per day, so a WARN storm cannot flood anyone.
    """
    metadata_hash = _metadata_hash(message)
    storm = evaluate_storm(metadata_hash)

    if storm['all_clear_count'] is not None:
        send_storm_notification(message, 'cleared', storm['all_clear_count'])

    if storm['summary_count'] is not None:
        send_storm_notification(message, 'summary', storm['summary_count'])

    if storm['suppress_individual']:
        logger.info(
            'Collection %s is storming; event logged but not emailed '
            'individually', message.collection_name
        )
        return

    notification_count = lookup_notification_count(metadata_hash)

    if notification_count < MAX_DAILY_ERRORS:
        send_notification(message)
        increment_notification_count(metadata_hash)
    elif claim_cap_notice(metadata_hash, date.today().isoformat()):
        logger.info(
            'Daily error notification cap reached for %s',
            message.collection_name
        )
        send_storm_notification(message, 'cap', notification_count)

def send_notification(message: EventMessage):
    """
    Sends notifications to interested parties via SES using a predefined
    email template.

    Each recipient is attempted independently. One failed address will not abort
    the invocation: SQS would redeliver the message and every address that had
    already succeeded would be sent to again, up to maxReceiveCount times.

    Returns the number of recipients successfully sent to. Raises if every
    attempt failed (revoked SES, permission, a bad identity ARN, etc)
    still dead-letters and stays visible
    """
    today = date.today()

    return send_email_to_recipients(
        f'[{message.category}] {today} {message.collection_name}',
        NOTIFICATION_TEMPLATE.format(
            raw_message=html.escape(message.model_dump_json()))
    )


def send_email_to_recipients(subject, html_body):
    """
    Issues one SES send per recipient, allowing individual failures. Shared by
    per-event notifications and by the storm and cap notices.
    """
    if not NOTIFICATION_EMAILS:
        logger.error('No notification recipients are configured; nothing sent')
        return 0

    sent = 0
    last_error = None

    for address in NOTIFICATION_EMAILS:
        logger.debug('Sending email to: %s', address)

        try:
            ses.send_email(
                ConfigurationSetName=SES_CONFIG_SET_NAME,
                FromEmailAddressIdentityArn=SES_SENDER_ARN,
                FromEmailAddress=f'{STAGE} Sigevent <noreply@nasa.gov>',
                Destination={'ToAddresses': [address]},
                Content={
                    'Simple': {
                        'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                        'Body': {
                            'Html': {
                                'Data': html_body,
                                'Charset': 'UTF-8'
                            }
                        }
                    }
                }
            )
            sent += 1
        except Exception as ex:
            last_error = ex
            logger.error('Failed to send notification to %s: %s', address, ex)

    if sent == 0 and last_error is not None:
        logger.error(
            'All %d notification sends failed; failing the invocation so the '
            'message is retried and dead-letters rather than being lost',
            len(NOTIFICATION_EMAILS)
        )
        raise last_error

    logger.debug(
        'Sending finished: %d of %d succeeded', sent, len(NOTIFICATION_EMAILS)
    )
    return sent


STORM_MESSAGES = {
    'summary': (
        'Error storm',
        '{count} errors from {collection} in the last {window}.',
        'Individual notifications are paused for this collection while the '
        'burst continues. Every event is still recorded in CloudWatch.'
    ),
    'cleared': (
        'Error storm cleared',
        '{collection} has stopped producing errors at storm rate.',
        'The last window closed with {count} events. Individual notifications '
        'have resumed.'
    ),
    'cap': (
        'Daily error limit reached',
        '{collection} has reached its daily limit of {count} error '
        'notifications.',
        'Further errors today are recorded in CloudWatch but not emailed. '
        'The limit resets at midnight UTC.'
    )
}


def send_storm_notification(message: EventMessage, kind, count):
    """
    Sends either a storm summary, an all-clear, or a daily-cap notice.
    """
    title, headline, detail = STORM_MESSAGES[kind]
    window = _describe_window(STORM_WINDOW_SECONDS)

    fields = {
        'count': count,
        'collection': message.collection_name,
        'window': window
    }

    subject = f'[{title.upper()}] {date.today()} {message.collection_name}'
    body = STORM_TEMPLATE.format(
        title=html.escape(title),
        headline=html.escape(headline.format(**fields)),
        detail=html.escape(detail.format(**fields)),
        collection_name=html.escape(message.collection_name)
    )

    return send_email_to_recipients(subject, body)


def _describe_window(seconds):
    """Renders a window length the way an operator would say it."""
    minutes = seconds // 60

    if minutes < 60:
        return f'{minutes} minutes'

    hours = minutes // 60
    return '1 hour' if hours == 1 else f'{hours} hours'



def lookup_notification_count(message_hash: str):
    """
    Looks up the number of notifications already sent based on the hashed
    metadata attributes generated from an EventMessage. Will create a
    DynamoDB table entry if one does not exist with a count of 0.
    """
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_date = midnight.date()
    tomorrow = midnight + timedelta(days=1)

    try:
        response = notification_table.update_item(
            Key={'message_hash': message_hash},
            UpdateExpression=(
                'SET #date = :today, #count = :zero, #expiration = :expiration'
            ),
            # Rolls the day over only when the item is absent, or present and
            # carrying a date other than today.
            ConditionExpression=(
                'attribute_not_exists(#date) OR #date <> :today'
            ),
            # date, count and expiration are all DynamoDB reserved words.
            ExpressionAttributeNames={
                '#date': 'date',
                '#count': 'count',
                '#expiration': 'expiration'
            },
            ExpressionAttributeValues={
                ':today': today_date.isoformat(),
                ':zero': 0,
                ':expiration': int(tomorrow.timestamp())
            },
            ReturnValues='ALL_NEW'
        )

        logger.debug('Notification count rolled over: %s', response)
        return response['Attributes']['count']
    except ClientError as ex:
        if ex.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise

    response = notification_table.get_item(Key={'message_hash': message_hash})
    logger.debug('Notification lookup response: %s', response)

    # Defensive: the TTL could in principle delete the item between the failed
    # condition and this read.
    return response.get('Item', {}).get('count', 0)

def increment_notification_count(message_hash: str):
    """
    Increments notification count of the message_hash provided.
    """
    notification_table.update_item(
        Key={'message_hash': message_hash},
        AttributeUpdates={
            'count': {
                'Value': 1,
                'Action': 'ADD'
            }
        }
    )
