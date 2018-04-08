##############################################################################
# Copyright 2016-2017 Rigetti Computing
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
##############################################################################

from __future__ import print_function

import re
import time
import warnings

import requests
from requests.adapters import HTTPAdapter
from six import integer_types
from urllib3 import Retry

from pyquil.api import Job
from pyquil.api.errors import error_mapping, UnknownApiError, TooManyQubitsError
from ._config import PyquilConfig

# Deal with JSONDecodeError across Python 2 and 3
# Ref: https://www.peterbe.com/plog/jsondecodeerror-in-requests.get.json-python-2-and-3
try:
    from json.decoder import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError    # type: ignore  # python2to3 hack)

TYPE_EXPECTATION = "expectation"
TYPE_MULTISHOT = "multishot"
TYPE_MULTISHOT_MEASURE = "multishot-measure"
TYPE_WAVEFUNCTION = "wavefunction"


def wait_for_job(get_job_fn, ping_time=None, status_time=None):
    """
    Wait for job logic
    """
    count = 0
    while True:
        job = get_job_fn()
        if job.is_done():
            break

        if status_time and count % int(status_time / ping_time) == 0:
            if job.is_queued_for_compilation():
                print("job {} is currently queued for compilation".format(job.job_id))
            elif job.is_queued():
                print("job {} is currently queued at position {}. "
                      "Estimated time until execution: {} seconds."
                      .format(job.job_id, job.position_in_queue(),
                              job.estimated_time_left_in_queue()))
            elif job.is_running():
                print("job {} is currently running".format(job.job_id))
            elif job.is_compiling():
                print("job {} is currently compiling".format(job.job_id))

        time.sleep(ping_time)
        count += 1

    return job


def get_json(session, url):
    """
    Get JSON from a Forest endpoint.
    """
    res = session.get(url)
    if res.status_code >= 400:
        raise parse_error(res)
    return res


def post_json(session, url, json):
    """
    Post JSON to the Forest endpoint.
    """
    res = session.post(url, json=json)
    if res.status_code >= 400:
        raise parse_error(res)
    return res


def parse_error(res):
    """
    Every server error should contain a "status" field with a human readable explanation of what went wrong as well as
    a "error_type" field indicating the kind of error that can be mapped to a Python type.

    There's a fallback error UnknownError for other types of exceptions (network issues, api gateway problems, etc.)
    """
    try:
        body = res.json()
    except JSONDecodeError:
        raise UnknownApiError(res.text)

    if 'error_type' not in body:
        raise UnknownApiError(str(body))

    error_type = body['error_type']
    status = body['status']

    if re.search(r"[0-9]+ qubits were requested, but the QVM is limited to [0-9]+ qubits.", status):
        return TooManyQubitsError(status)

    error_cls = error_mapping.get(error_type, UnknownApiError)
    return error_cls(status)


def get_session(api_key, user_id):
    """
    Create a requests session to access the cloud API with the proper authentication

    :param str api_key: custom api key, if None will fallback to reading from the config
    :param str user_id: custom user id, if None will fallback to reading from the config
    :return: requests session
    :rtype: Session
    """
    session = requests.Session()
    retry_adapter = HTTPAdapter(max_retries=Retry(total=3,
                                                  method_whitelist=['POST'],
                                                  status_forcelist=[502, 503, 504, 521, 523],
                                                  backoff_factor=0.2,
                                                  raise_on_status=False))

    session.mount("http://", retry_adapter)
    session.mount("https://", retry_adapter)

    # We need this to get binary payload for the wavefunction call.
    session.headers.update({"Accept": "application/octet-stream"})

    config = PyquilConfig()
    session.headers.update({
        'X-Api-Key': api_key if api_key else config.api_key,
        'X-User-Id': user_id if user_id else config.user_id,
        'Content-Type': 'application/json; charset=utf-8'
    })

    return session


def validate_noise_probabilities(noise_parameter):
    """
    Is noise_parameter a valid specification of noise probabilities for depolarizing noise?

    :param list noise_parameter: List of noise parameter values to be validated.
    """
    if not noise_parameter:
        return
    if not isinstance(noise_parameter, list):
        raise TypeError("noise_parameter must be a list")
    if any([not isinstance(value, float) for value in noise_parameter]):
        raise TypeError("noise_parameter values should all be floats")
    if len(noise_parameter) != 3:
        raise ValueError("noise_parameter lists must be of length 3")
    if sum(noise_parameter) > 1 or sum(noise_parameter) < 0:
        raise ValueError("sum of entries in noise_parameter must be between 0 and 1 (inclusive)")
    if any([value < 0 for value in noise_parameter]):
        raise ValueError("noise_parameter values should all be non-negative")


def validate_run_items(run_items):
    """
    Check the validity of classical addresses / qubits for the payload.

    :param list|range run_items: List of classical addresses or qubits to be validated.
    """
    if not isinstance(run_items, (list, range)):
        raise TypeError("run_items must be a list")
    if any([not isinstance(i, integer_types) for i in run_items]):
        raise TypeError("run_items list must contain integer values")


def get_job_id(response):
    return response.json()['jobId']

class Connection:
    def __init__(self, sync_endpoint='https://api.rigetti.com',
                 async_endpoint='https://job.rigetti.com/beta', api_key=None, user_id=None,
                 use_queue=False, ping_time=0.1, status_time=2):
        """
        Constructor for QVMConnection. Sets up any necessary security, and establishes the noise
        model to use.

        :param Device device: The optional device, from which noise will be added by default to all
                              programs run on this instance.
        :param sync_endpoint: The endpoint of the server for running small jobs
        :param async_endpoint: The endpoint of the server for running large jobs
        :param api_key: The key to the Forest API Gateway (default behavior is to read from config file)
        :param user_id: Your userid for Forest (default behavior is to read from config file)
        :param bool use_queue: Disabling this parameter may improve performance for small, quick programs.
                               To support larger programs, set it to True. (default: False)
                               *_async methods will always use the queue
                               See https://go.rigetti.com/connections for more information.
        :param int ping_time: Time in seconds for how long to wait between polling the server for updated status
                              information on a job. Note that this parameter doesn't matter if use_queue is False.
        :param int status_time: Time in seconds for how long to wait between printing status information.
                                To disable printing of status entirely then set status_time to False.
                                Note that this parameter doesn't matter if use_queue is False.
        :param gate_noise: A list of three numbers [Px, Py, Pz] indicating the probability of an X,
                           Y, or Z gate getting applied to each qubit after a gate application or
                           reset. (default None)
        :param measurement_noise: A list of three numbers [Px, Py, Pz] indicating the probability of
                                  an X, Y, or Z gate getting applied before a a measurement.
                                  (default None)
        :param random_seed: A seed for the QVM's random number generators. Either None (for an
                            automatically generated seed) or a non-negative integer.
        """
        self.async_endpoint = async_endpoint
        self.sync_endpoint = sync_endpoint
        self.session = get_session(api_key, user_id)

        self.use_queue = use_queue
        self.ping_time = ping_time
        self.status_time = status_time

    def use_queue_or_needs_compilation(self, payload, *, needs_compilation):
        if needs_compilation and not self.use_queue:
            warnings.warn('Synchronous QVM connection does not support compilation preprocessing. Running this job over the asynchronous endpoint, as if use_queue were set to True.')

        response = post_json(self.session, self.async_endpoint + "/job", {"machine": "QVM", "program": payload})
        job = self.wait_for_job(get_job_id(response))
        return job.result()

    def run_helper(self, payload):
        response = post_json(self.session, self.sync_endpoint + "/qvm", payload)
        return response.json()

    def run_async_helper(self, payload):
        response = post_json(self.session, self.async_endpoint + "/job", payload)
        return get_job_id(response)

    def wait_for_job(self, job_id, ping_time=None, status_time=None):
        """
        Wait for the results of a job and periodically print status

        :param job_id: Job id
        :param ping_time: How often to poll the server.
                          Defaults to the value specified in the constructor. (0.1 seconds)
        :param status_time: How often to print status, set to False to never print status.
                            Defaults to the value specified in the constructor (2 seconds)
        :return: Completed Job
        """
        def get_job_fn():
            return self.get_job(job_id)
        return wait_for_job(get_job_fn,
                            ping_time if ping_time else self.ping_time,
                            status_time if status_time else self.status_time)


    def get_job(self, job_id):
        """
        Given a job id, return information about the status of the job

        :param str job_id: job id
        :return: Job object with the status and potentially results of the job
        :rtype: Job
        """
        response = get_json(self.session, self.async_endpoint + "/job/" + job_id)
        return Job(response.json(), 'QVM')
