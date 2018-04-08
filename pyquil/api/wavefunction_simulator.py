import warnings

from pyquil.wavefunction import Wavefunction

from pyquil.api import Job
from pyquil.api._base_connection import get_session, wait_for_job, TYPE_EXPECTATION, get_json, \
    post_json, get_job_id, Connection, validate_run_items, TYPE_WAVEFUNCTION
from six import integer_types
from pyquil import Program


class WavefunctionSimulator:
    def __init__(self, *, sync_endpoint='https://api.rigetti.com',
                 async_endpoint='https://job.rigetti.com/beta', api_key=None, user_id=None,
                 use_queue=False, ping_time=0.1, status_time=2, random_seed=None):
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
        self.connection = Connection(sync_endpoint=sync_endpoint, async_endpoint=async_endpoint,
                                     api_key=api_key, user_id=user_id, use_queue=use_queue,
                                     ping_time=ping_time, status_time=status_time)

        if random_seed is None:
            self.random_seed = None
        elif isinstance(random_seed, integer_types) and random_seed >= 0:
            self.random_seed = random_seed
        else:
            raise TypeError("random_seed should be None or a non-negative int")

    def wavefunction(self, quil_program, classical_addresses=None, needs_compilation=False,
                     isa=None):
        """
        Simulate a Quil program and get the wavefunction back.

        :note: If the execution of ``quil_program`` is **non-deterministic**, i.e., if it includes
            measurements and/or noisy quantum gates, then the final wavefunction from which the
            returned bitstrings are sampled itself only represents a stochastically generated sample
            and the wavefunctions returned by *different* ``wavefunction`` calls *will generally be
            different*.

        :param Program quil_program: A Quil program.
        :param list|range classical_addresses: An optional list of classical addresses.
        :param needs_compilation: If True, preprocesses the job with the compiler.
        :param isa: If set, compiles to this target ISA.
        :return: A tuple whose first element is a Wavefunction object,
                 and whose second element is the list of classical bits corresponding
                 to the classical addresses.
        :rtype: Wavefunction
        """
        if classical_addresses is None:
            classical_addresses = []

        if self.connection.use_queue or needs_compilation:
            if needs_compilation and not self.connection.use_queue:
                warnings.warn(
                    'Synchronous connection does not support compilation preprocessing. '
                    'Running this job over the asynchronous endpoint, as if use_queue were set to True.')

            payload = self._wavefunction_payload(quil_program, classical_addresses,
                                                 needs_compilation, isa)

            # TODO: Method on connection
            response = post_json(self.connection.session, self.connection.async_endpoint + "/job",
                                 {"machine": "QVM", "program": payload})
            job = self.wait_for_job(get_job_id(response))
            return job.result()
        else:
            payload = self._wavefunction_payload(quil_program, classical_addresses,
                                                 needs_compilation, isa)
            # TODO: Method on connection
            response = post_json(self.connection.session, self.connection.sync_endpoint + "/qvm", payload)
            return Wavefunction.from_bit_packed_string(response.content, classical_addresses)

    def wavefunction_async(self, quil_program, classical_addresses=None, needs_compilation=False,
                           isa=None):
        """
        Similar to wavefunction except that it returns a job id and doesn't wait for the program to be executed.
        See https://go.rigetti.com/connections for reasons to use this method.
        """
        if classical_addresses is None:
            classical_addresses = []

        payload = self._wavefunction_payload(quil_program, classical_addresses, needs_compilation,
                                             isa)

        # TODO: Method on connection
        response = post_json(self.connection.session, self.connection.async_endpoint + "/job",
                             {"machine": "QVM", "program": payload})
        return get_job_id(response)

    def _wavefunction_payload(self, quil_program, classical_addresses, needs_compilation, isa):
        if not isinstance(quil_program, Program):
            raise TypeError("quil_program must be a Quil program object")
        validate_run_items(classical_addresses)
        if needs_compilation and not isa:
            raise TypeError("ISA cannot be None if QVM program requires compilation preprocessing.")

        payload = {'type': TYPE_WAVEFUNCTION,
                   'addresses': list(classical_addresses)}

        if needs_compilation:
            payload['uncompiled-quil'] = quil_program.out()
            payload['target-device'] = {"isa": isa.to_dict()}
        else:
            payload['compiled-quil'] = quil_program.out()

        self._maybe_add_noise_to_payload(payload)
        self._add_rng_seed_to_payload(payload)

        return payload

    def expectation(self, prep_prog, operator_programs=None, needs_compilation=False, isa=None):
        """
        Calculate the expectation value of operators given a state prepared by
        prep_program.

        :note: If the execution of ``quil_program`` is **non-deterministic**, i.e., if it includes
            measurements and/or noisy quantum gates, then the final wavefunction from which the
            expectation values are computed itself only represents a stochastically generated
            sample. The expectations returned from *different* ``expectation`` calls *will then
            generally be different*.

        :param Program prep_prog: Quil program for state preparation.
        :param list operator_programs: A list of PauliTerms. Default is Identity operator.
        :param bool needs_compilation: If True, preprocesses the job with the compiler.
        :param ISA isa: If set, compiles to this target ISA.
        :returns: Expectation value of the operators.
        :rtype: float
        """
        if needs_compilation:
            raise TypeError(
                "Expectation QVM programs do not support compilation preprocessing.  Make a separate CompilerConnection job first.")
        if self.use_queue:
            payload = self._expectation_payload(prep_prog, operator_programs)
            response = post_json(self.session, self.async_endpoint + "/job",
                                 {"machine": "QVM", "program": payload})
            job = self.wait_for_job(get_job_id(response))
            return job.result()
        else:
            payload = self._expectation_payload(prep_prog, operator_programs)
            response = post_json(self.session, self.sync_endpoint + "/qvm", payload)
            return response.json()

    def expectation_async(self, prep_prog, operator_programs=None, needs_compilation=False,
                          isa=None):
        """
        Similar to expectation except that it returns a job id and doesn't wait for the program to be executed.
        See https://go.rigetti.com/connections for reasons to use this method.
        """
        if needs_compilation:
            raise TypeError(
                "Expectation QVM programs do not support compilation preprocessing.  Make a separate CompilerConnection job first.")

        payload = self._expectation_payload(prep_prog, operator_programs)
        response = post_json(self.session, self.async_endpoint + "/job",
                             {"machine": "QVM", "program": payload})
        return get_job_id(response)

    def _expectation_payload(self, prep_prog, operator_programs):
        if operator_programs is None:
            operator_programs = [Program()]

        if not isinstance(prep_prog, Program):
            raise TypeError("prep_prog variable must be a Quil program object")

        payload = {'type': TYPE_EXPECTATION,
                   'state-preparation': prep_prog.out(),
                   'operators': [x.out() for x in operator_programs]}

        self._add_rng_seed_to_payload(payload)

        return payload

    # TODO: Move to Connection
    def get_job(self, job_id):
        """
        Given a job id, return information about the status of the job

        :param str job_id: job id
        :return: Job object with the status and potentially results of the job
        :rtype: Job
        """
        response = get_json(self.connection.session, self.connection.async_endpoint + "/job/" + job_id)
        return Job(response.json(), 'QVM')

    # TODO: Move to Connection
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
                            ping_time if ping_time else self.connection.ping_time,
                            status_time if status_time else self.connection.status_time)

    def _maybe_add_noise_to_payload(self, payload):
        """
        Set the gate noise and measurement noise of a payload.
        """
        if self.measurement_noise is not None:
            payload["measurement-noise"] = self.measurement_noise
        if self.gate_noise is not None:
            payload["gate-noise"] = self.gate_noise

    def _add_rng_seed_to_payload(self, payload):
        """
        Add a random seed to the payload.
        """
        if self.random_seed is not None:
            payload['rng-seed'] = self.random_seed