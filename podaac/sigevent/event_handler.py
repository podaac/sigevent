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


CLOUDWATCH_LOG_GROUP = utils.get_param('log_group')
NOTIFICATION_EMAILS = json.loads(utils.get_param('notification_emails'))
NOTIFICATION_TABLE_NAME = utils.get_param('notification_table_name')
NOTIFICATION_TEMPLATE = resources.files(__package__).joinpath(
    'resources', 'notification.html').read_text('utf-8')
STAGE = utils.get_param('stage')
MUTED_MODE = utils.get_param('muted_mode') == 'true'
MAX_DAILY_WARNS = int(utils.get_param('max_daily_warns'))

SES_REGION = utils.get_param('ses_region')
SES_SENDER_ARN = utils.get_param('ses_sender_arn')
SES_CONFIG_SET_NAME = utils.get_param('ses_config_set_name')

cloudwatchlogs = boto3.client('logs')
ses = boto3.client('sesv2', region_name=SES_REGION)

notification_table = boto3.resource('dynamodb').Table(NOTIFICATION_TABLE_NAME)
logger = utils.get_logger(__name__)
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
                    'timestamp': datetime.fromisoformat(
                        sns_record['Timestamp']
                    )
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
            if ex.response['Error']['Code'] == 'ResourceAlreadyExistsException':  # noqa: E501
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
        metadata_hash = hashlib.sha1(
            bytes(message.event_level.value, 'utf-8') +
            bytes(message.collection_name, 'utf-8'),
            usedforsecurity=False
        ).hexdigest()

        notification_count = lookup_notification_count(metadata_hash)
        if notification_count < MAX_DAILY_WARNS:
            send_notification(message)
            increment_notification_count(metadata_hash)
    elif message.event_level is EventLevel.ERROR:
        # Always send out errors
        send_notification(message)
    else:
        logger.debug('Message not sent')


def send_notification(message: EventMessage):
    """
    Sends notifications to interested parties via SES using a predefined
    email template
    """
    today = date.today()

    for address in NOTIFICATION_EMAILS:
        logger.debug('Sending email to: %s', address)

        ses.send_email(
            ConfigurationSetName=SES_CONFIG_SET_NAME,
            FromEmailAddressIdentityArn=SES_SENDER_ARN,
            FromEmailAddress=f'{STAGE} Sigevent <noreply@nasa.gov>',
            Destination={'ToAddresses': [address]},
            Content={
                'Simple': {
                    'Subject': {
                        'Data': f'[{message.category}] {today} {message.collection_name}',  # noqa: E501 # pylint: disable=line-too-long
                        'Charset': 'UTF-8'
                    },
                    'Body': {
                        'Html': {
                            'Data': NOTIFICATION_TEMPLATE.format(
                                raw_message=html.escape(
                                    message.model_dump_json())),
                            'Charset': 'UTF-8'
                        }
                    }
                }
            }
        )

    logger.debug('Sending finished')


def lookup_notification_count(message_hash: str):
    """
    Looks up the number of notifications already sent based on the hashed
    metadata attributes generated from an EventMessage. Will create a
    DynamoDB table entry if one does not exist with an event_count of 0
    """
    response = notification_table.get_item(Key={
        'message_hash': message_hash
    })

    now = datetime.now(timezone.utc)
    today_date = now.replace(hour=0, minute=0, second=0, microsecond=0).date()
    tomorrow = (
        now.replace(hour=0, minute=0, second=0, microsecond=0) +
        timedelta(days=1)
    )

    logger.debug("Notification lookup response: %s", response)
    if 'Item' not in response:
        notification_table.put_item(Item={
            'message_hash': message_hash,
            'date': today_date.isoformat(),
            'count': 0,
            'expiration': int(tomorrow.timestamp())
        })
        return 0

    item = response['Item']

    if date.fromisoformat(item['date']) != today_date:
        notification_table.put_item(Item={
            'message_hash': message_hash,
            'date': today_date.isoformat(),
            'count': 0,
            'expiration': int(tomorrow.timestamp())
        })
        return 0

    return item['count']


def increment_notification_count(message_hash: str):
    """
    Atomically increments notification count of the message_hash provided.
    The table element should already exist otherwise this will fail.
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
