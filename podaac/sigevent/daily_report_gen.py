"""
lambda_daily_handler.py is a thin wrapper around the daily report
sigevent handler logic and is the entrypoint for AWS Lambda.
"""

import csv
from datetime import date, datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from tempfile import NamedTemporaryFile, TemporaryFile
import logging
import json

import boto3
import jinja2

from podaac.sigevent.message import EventMessage, EventLevel
from podaac.sigevent.utilities import utils

MAX_TABLE_SIZE = 10
CLOUDWATCH_LOG_GROUP = utils.get_param('log_group')
NOTIFICATION_EMAILS = json.loads(utils.get_param('notification_emails'))
STAGE = utils.get_param('stage')

SES_REGION = utils.get_param('ses_region')
SES_SENDER_ARN = utils.get_param('ses_sender_arn')
SES_CONFIG_SET_NAME = utils.get_param('ses_config_set_name')

ses = boto3.client('sesv2', region_name=SES_REGION)
cloudwatchlogs = boto3.client('logs')
jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader(__package__, 'resources'))
logger = utils.get_logger(__name__)


def invoke(event, _):
    """
    AWS Lambda entry point. This Lambda is invoked on a schedule, and
    the input payload is not used by the software.
    """
    logging.debug('Received event: %s; this should be blank', event)

    today = str(date.today())
    logger.info('Searching logs for errors')
    error_logs = search_error_logs()

    analyses = analyze_messages(error_logs)

    logger.info('Generating csv')
    csv_file = generate_csv_report(analyses)

    logger.info('Generating html')
    html_report = generate_html_report(analyses)

    message = MIMEMultipart()
    message['subject'] = f'{today} Daily Report'
    message['from'] = f'{STAGE} Sigevent <noreply@nasa.gov>'
    message.attach(MIMEText(html_report, 'html'))

    csv_attachment = MIMEApplication(csv_file.read())
    csv_attachment.add_header(
        'Content-Disposition',
        'attachment',
        filename=f'{today}-sigevent-daily.csv'
    )
    message.attach(csv_attachment)

    for address in NOTIFICATION_EMAILS:
        logger.info('Sending emails to: %s', address)
        result = ses.send_email(
            ConfigurationSetName=SES_CONFIG_SET_NAME,
            FromEmailAddressIdentityArn=SES_SENDER_ARN,
            FromEmailAddress=message['from'],
            Destination={'ToAddresses': [address]},
            Content={
                'Raw': {
                    'Data': message.as_string().encode()
                }
            }
        )

        logger.debug('Send email result: %s', result)

    logger.debug('Finished sending emails')


def search_error_logs():
    '''
    Generates a list of dictionaries containing an analyzed version of log
    messages including the first timestamp within the logs, the last timestamp,
    the message, and the number of times the message occurred
    '''
    logs: list = []
    now = datetime.now(timezone.utc)
    start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    logger.debug('Start time: %s', start_time)
    logger.debug('End time: %s', end_time)

    next_token = None
    while True:
        response = cloudwatchlogs.filter_log_events(
            logGroupName=CLOUDWATCH_LOG_GROUP,
            startTime=int(start_time.timestamp() * 1000),
            endTime=int(end_time.timestamp() * 1000),
            **({'nextToken': next_token} if next_token is not None else {})
        )

        logger.debug('CloudWatch logs response: %s', response)

        for event in response['events']:
            logs.append(EventMessage.model_validate_json(event['message']))

        if 'nextToken' in response:
            next_token = response['nextToken']
        else:
            return logs


def analyze_messages(messages: list[EventMessage]) -> dict:
    '''
    Analyze messages and generate stats about the messages; ordering the
    messages from most errors to least
    '''
    analyses = {}
    for message in messages:
        if message.collection_name in analyses:
            analysis = analyses[message.collection_name]
        else:
            analysis = analyses[message.collection_name] = {
                'name': message.collection_name,
                'level_counts': {
                    level: 0 for level in EventLevel
                },
                'category_counts': {}
            }

        level_counts = analysis['level_counts']
        category_counts = analysis['category_counts']

        level_counts[message.event_level] += 1

        if message.category not in category_counts:
            category_counts[message.category] = 1
        else:
            category_counts[message.category] += 1

    # Sort collections by levels; starting at ERROR as the primary sort key
    # and going down to DEBUG as the lowest sort key
    analyses = sorted(
        list(analyses.values()),
        key=lambda x: (
            x['level_counts']['ERROR'],
            x['level_counts']['WARN'],
            x['level_counts']['INFO'],
            x['level_counts']['DEBUG']
        ),
        reverse=True
    )

    # Sort collection's categories by counts
    for collection in analyses:
        collection['category_counts'] = dict(sorted(
            collection['category_counts'].items(),
            key=lambda item: item[1],
            reverse=True
        ))

    return analyses


def generate_csv_report(analyses: list[dict]) -> TemporaryFile:
    """
    Generate an CSV report from an analysis dict
    """
    # pylint: disable=consider-using-with
    csv_file = NamedTemporaryFile(mode='r+', encoding='utf-8', delete=False)

    writer = csv.DictWriter(csv_file, fieldnames=[
        'Collection Name',
        'Errors',
        'Warnings',
        'Info',
        'Debug',
        'Categories'
    ])
    writer.writeheader()

    for analysis in analyses:
        name = analysis['name']
        level_counts = analysis['level_counts']
        category_counts = analysis['category_counts']

        writer.writerow({
            'Collection Name': name,
            'Errors': level_counts[EventLevel.ERROR],
            'Warnings': level_counts[EventLevel.WARN],
            'Info': level_counts[EventLevel.INFO],
            'Debug': level_counts[EventLevel.DEBUG],
            'Categories': '\n'.join([
                f'{category}: {count}'
                for category, count in category_counts.items()
            ])
        })

    csv_file.flush()
    csv_file.seek(0)
    return csv_file


def generate_html_report(analyses: list[dict]) -> str:
    """
    Generates an HTML report using a predefined template and the analysis
    data generated earlier
    """

    template = jinja_env.get_template('summary.html')
    num_items = MAX_TABLE_SIZE if len(analyses) > MAX_TABLE_SIZE \
        else len(analyses)

    return template.render(
        analyses=analyses,
        today=str(date.today()),
        num_collections=num_items,
        total_num_collections=len(analyses)
    )
