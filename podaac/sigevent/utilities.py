"""Shared utilities for lambdas"""
import logging
from os import getenv
import boto3


class Utilities:
    '''
    A utility class containing miscellaneous shared helper functions.
    Shamelessly ported from SWODLR - Josh
    '''

    def __init__(self):
        if hasattr(Utilities, '_instance'):
            raise RuntimeError('Utilities were already initialized')

        Utilities._instance = self
        self._env = getenv('SIGEVENT_ENV', 'prod')
        self._service_name = 'sigevent'
        self._ssm_path = f'/service/{self._service_name}/'

        if self._env == 'prod':
            self._load_params_from_ssm()
        else:
            from dotenv import load_dotenv  # noqa: E501 # pylint: disable=import-outside-toplevel
            load_dotenv()

    @classmethod
    def get_instance(cls):
        '''
        Returns the already initiated instance of a subclass which extends
        BaseUtilities
        '''
        if not hasattr(cls, '_instance'):
            raise RuntimeError('Utilities were not initialized yet')

        return cls._instance

    def _load_params_from_ssm(self):
        ssm = boto3.client('ssm')

        parameters = []
        next_token = None
        while True:
            kwargs = {'NextToken': next_token} \
                if next_token is not None else {}
            res = ssm.get_parameters_by_path(
                Path=self._ssm_path,
                WithDecryption=True,
                **kwargs
            )

            parameters.extend(res['Parameters'])
            if 'NextToken' in res:
                next_token = res['NextToken']
            else:
                break

        self._ssm_parameters = {}

        for param in parameters:
            name = param['Name'].removeprefix(self._ssm_path)
            self._ssm_parameters[name] = param['Value']

    def get_param(self, name):
        '''
        Retrieves a parameter from SSM or the environment depending on the
        environment
        '''
        if self._env == 'prod':
            return self._ssm_parameters.get(name)

        return getenv(f'{self._service_name.upper()}_{name}')

    def get_logger(self, name):
        '''
        Creates a logger for a requestor with a global log level defined from
        parameters
        '''
        logger = logging.getLogger(name)

        log_level = getattr(logging, self.get_param('log_level')) \
            if self.get_param('log_level') is not None else logging.INFO
        logger.setLevel(log_level)
        return logger


utils = Utilities()
