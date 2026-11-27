from unittest.mock import MagicMock

from flask import Response

from api.logging import RequestResponseLogging, log_request_body_and_headers, log_response_body_and_headers
from api.logging.logger import DefaultLogger
import logging


def test_logger_level_prod():
  """
  Given a DefaultLogger
    When not running local
      It should set log level to INFO
  """

  logger = DefaultLogger(is_running_local=False)
  assert logger.logger.getEffectiveLevel() == logging.INFO


def test_logger_level_dev():
  """
  Given a DefaultLogger
    When is running local
      It should set log level to DEBUG
  """

  logger = DefaultLogger(is_running_local=True)
  assert logger.logger.getEffectiveLevel() == logging.DEBUG


def test_request_response_logging_extension(app):
    """
    Given a Flask app
      when using the RequestResponseLogging extension
        it should present a log_request before func and a log_response after_func
    """
    RequestResponseLogging(app)

    before_func_names = __get_function_name_list(app.before_request_funcs)
    after_func_names = __get_function_name_list(app.after_request_funcs)

    assert 'log_request' in before_func_names
    assert 'log_response' in after_func_names

def test_request_response_logging_execution_urls_deny_list(app, mocker):
    """
        Given a Flask app
          when using the RequestResponseLogging extension
          when invoking a path from the specified no_logs_path
            it should not call the log_request_body_and_headers and log_response_body_and_headers functions
        """

    mock_log_request = mocker.patch('api.logging.http_info.log_request_body_and_headers')
    mock_log_response = mocker.patch('api.logging.http_info.log_response_body_and_headers')

    RequestResponseLogging(app)

    with app.test_request_context('/logs'):
        app.preprocess_request()
        app.process_response(Response('fake-response'))

    mock_log_request.assert_not_called()
    mock_log_response.assert_not_called()

def test_request_response_logging_execution(app, mocker):
    """
        Given a Flask app
          when using the RequestResponseLogging extension
          when invoking a path
            it should call the log_request_body_and_headers and log_response_body_and_headers functions
        """
    mock_log_request = mocker.patch('api.logging.log_request_body_and_headers')
    mock_log_response = mocker.patch('api.logging.log_response_body_and_headers')

    RequestResponseLogging(app)

    with app.test_request_context('/'):
        app.preprocess_request()
        app.process_response(Response('fake-response'))

    mock_log_request.assert_called_once()
    mock_log_response.assert_called_once()

def __get_function_name_list(functions):
    return list(fun.__name__ for app_name, funcs in functions.items() for fun in funcs)


class MockRequest:
    headers = {'int_value': 100}
    args = {'region': 'eu-west-1'}
    json = {'username': 'user@email.com'}
    path = '/fake-path'
    environ = {
        'serverless.event': {
            'requestContext': {
                'requestId': 'apigw-request-id'
            }
        }
    }


def test_log_request_body_and_headers():
    mock_logger = MagicMock(wraps=DefaultLogger(True))
    log_request_body_and_headers(mock_logger, MockRequest())

    expected_details = {
        'headers': {'int_value': 100},
        'body': {'username': 'user@email.com'},
        'path': '/fake-path',
        'params': {'region': 'eu-west-1'},
        'apigw-request-id': 'apigw-request-id'
    }

    mock_logger.info.assert_called_once_with(expected_details)


def test_log_response_body_and_headers():
    mock_logger = MagicMock(wraps=DefaultLogger(True))
    log_response_body_and_headers(mock_logger, MockRequest())

    expected_details = {
        'headers': {'int_value': 100},
        'body': {'username': 'user@email.com'}
    }

    mock_logger.info.assert_called_once_with(expected_details)


class MockRequestWithSensitiveHeaders:
    headers = {
        'Content-Type': 'application/json',
        'Cookie': 'accessToken=eyJsecrettoken',
        'Set-Cookie': 'accessToken=eyJsecrettoken; Secure; HttpOnly',
        'X-CSRF-Token': 'csrf-secret-value',
        'Authorization': 'Bearer eyJsecrettoken',
    }
    args = {}
    json = None
    path = '/fake-path'
    environ = {}


def test_log_filters_sensitive_headers():
    """
    Given headers containing sensitive values (Cookie, Set-Cookie, X-CSRF-Token, Authorization)
      When logging request body and headers
        Then sensitive headers should be stripped from the log output
    """
    mock_logger = MagicMock(wraps=DefaultLogger(True))
    log_request_body_and_headers(mock_logger, MockRequestWithSensitiveHeaders())

    logged_details = mock_logger.info.call_args[0][0]
    logged_headers = logged_details['headers']

    assert 'Content-Type' in logged_headers
    assert 'Cookie' not in logged_headers
    assert 'Set-Cookie' not in logged_headers
    assert 'X-CSRF-Token' not in logged_headers
    assert 'Authorization' not in logged_headers


class MockMemberSubmitRequest:
    headers = {'Content-Type': 'application/json'}
    args = {}
    json = {
        'team': 'teamc',
        'script_content': '#!/bin/bash\necho MARKER\n',
        'nested': {'NewPassword': 'p', 'items': [{'content_b64': 'TUFSS0VS', 'path': '/fsx/teamc/x'}]},
        'client_secret': 's',
        'refresh_token': 't',
    }
    path = '/team-quota/member/jobs'
    environ = {}


def test_log_redacts_user_content_and_credentials_in_body():
    """
    Given a body with a pasted script, file contents and credential-like keys
      When logging request body and headers
        Then those values are replaced and the other fields are kept
    """
    mock_logger = MagicMock(wraps=DefaultLogger(True))
    log_request_body_and_headers(mock_logger, MockMemberSubmitRequest())

    body = mock_logger.info.call_args[0][0]['body']

    assert body == {
        'team': 'teamc',
        'script_content': '<redacted>',
        'nested': {'NewPassword': '<redacted>', 'items': [{'content_b64': '<redacted>', 'path': '/fsx/teamc/x'}]},
        'client_secret': '<redacted>',
        'refresh_token': '<redacted>',
    }
    assert 'MARKER' not in str(mock_logger.info.call_args)


def test_log_response_redacts_file_contents():
    mock_logger = MagicMock(wraps=DefaultLogger(True))
    response = MagicMock(headers={}, json={'path': '/fsx/teamc/slurm-1.out', 'content_b64': 'TUFSS0VS', 'size': 6})
    log_response_body_and_headers(mock_logger, response)

    assert mock_logger.info.call_args[0][0]['body'] == {
        'path': '/fsx/teamc/slurm-1.out', 'content_b64': '<redacted>', 'size': 6,
    }


class MockRequestWithMixedCaseHeaders:
    headers = {
        'Content-Type': 'application/json',
        'cookie': 'accessToken=eyJsecrettoken',
        'SET-COOKIE': 'accessToken=eyJsecrettoken; Secure; HttpOnly',
        'x-csrf-token': 'csrf-secret-value',
        'AUTHORIZATION': 'Bearer eyJsecrettoken',
    }
    args = {}
    json = None
    path = '/fake-path'
    environ = {}


def test_log_filters_sensitive_headers_case_insensitive():
    mock_logger = MagicMock(wraps=DefaultLogger(True))
    log_request_body_and_headers(mock_logger, MockRequestWithMixedCaseHeaders())

    logged_details = mock_logger.info.call_args[0][0]
    logged_headers = logged_details['headers']

    assert 'Content-Type' in logged_headers
    assert 'cookie' not in logged_headers
    assert 'SET-COOKIE' not in logged_headers
    assert 'x-csrf-token' not in logged_headers
    assert 'AUTHORIZATION' not in logged_headers
