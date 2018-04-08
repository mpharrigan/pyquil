from six import integer_types

from pyquil import Program
from pyquil.api._base_connection import Connection, validate_run_items, TYPE_MULTISHOT
from pyquil.noise import apply_noise_model


class QAM:
    """
    Represents a connection to the QPU (Quantum Processing Unit)
    """
    def __init__(self, connection=None):
        if connection is None:
            self.connection = Connection()

        self.is_qpu = False # TODO
        self.is_qvm = True # TODO

        self.compile_by_default = True if self.is_qpu else False


    def run(self, quil_program, classical_addresses, trials=1, needs_compilation=None, isa=None):
        """
        Run a Quil program multiple times, accumulating the values deposited in
        a list of classical addresses.

        :param Program quil_program: A Quil program.
        :param list|range classical_addresses: A list of addresses.
        :param int trials: Number of shots to collect.
        :param bool needs_compilation: If True, preprocesses the job with the compiler.
        :param ISA isa: If set, compiles to this target ISA.
        :return: A list of lists of bits. Each sublist corresponds to the values
                 in `classical_addresses`.
        :rtype: list
        """
        if needs_compilation is None:
            needs_compilation = self.compile_by_default

        payload = self._run_payload(quil_program, classical_addresses, trials, needs_compilation, isa)
        if self.connection.use_queue or needs_compilation:
            return self.connection.use_queue_or_needs_compilation(payload, needs_compilation=needs_compilation)
        else:
            return self.connection.run_helper(payload)

    def run_async(self, quil_program, classical_addresses, trials=1, needs_compilation=None, isa=None):
        """
        Similar to run except that it returns a job id and doesn't wait for the program to be executed.
        See https://go.rigetti.com/connections for reasons to use this method.
        """
        if needs_compilation is None:
            needs_compilation = self.compile_by_default

        payload = self._run_payload(quil_program, classical_addresses, trials, needs_compilation, isa)
        payload = self._wrap_payload(payload)
        return self.connection.run_async_helper(payload)

    def _run_payload(self, quil_program, classical_addresses, trials, needs_compilation, isa):
        raise NotImplementedError()

    def _wrap_payload(self, program):
        raise NotImplementedError()


    def _add_rng_seed_to_payload(self, payload):
        """
        Add a random seed to the payload.
        """
        if self.random_seed is not None:
            payload['rng-seed'] = self.random_seed

    def qpu_run(self, quil_program, classical_addresses, trials=1, needs_compilation=True, isa=None):
        """
        Run a pyQuil program on the QPU and return the values stored in the classical registers
        designated by the classical_addresses parameter. The program is repeated according to
        the number of trials provided to the run method. This functionality is in beta.

        It is important to note that our QPUs currently only allow a single set of simultaneous
        readout pulses on all qubits in the QPU at the end of the program. This means that
        missing or duplicate MEASURE instructions do not change the pulse program, but instead
        only contribute to making a less rich or richer mapping, respectively, between classical
        and qubit addresses.

        :param Program quil_program: Pyquil program to run on the QPU
        :param list|range classical_addresses: Classical register addresses to return
        :param int trials: Number of times to run the program (a.k.a. number of shots)
        :param bool needs_compilation: If True, preprocesses the job with the compiler.
        :param ISA isa: If set, specifies a custom ISA to compile to. If left unset,
                    Forest uses the default ISA associated to this QPU device.
        :return: A list of a list of classical registers (each register contains a bit)
        :rtype: list
        """
        job_id = self.run_async(quil_program, classical_addresses, trials, needs_compilation, isa)
        job = self.connection.wait_for_job(job_id)
        return job.result()



    def qpu_run_and_measure(self, quil_program, qubits, trials=1, needs_compilation=True, isa=None):
        """
        Similar to run, except for how MEASURE operations are dealt with. With run, users are
        expected to include MEASURE operations in the program if they want results back. With
        run_and_measure, users provide a pyquil program that does not have MEASURE instructions,
        and also provide a list of qubits to measure. All qubits in this list will be measured
        at the end of the program, and their results stored in corresponding classical registers.

        :param Program quil_program: Pyquil program to run on the QPU
        :param list|range qubits: The list of qubits to measure
        :param int trials: Number of times to run the program (a.k.a. number of shots)
        :param bool needs_compilation: If True, preprocesses the job with the compiler.
        :param ISA isa: If set, specifies a custom ISA to compile to. If left unset,
                    Forest uses the default ISA associated to this QPU device.
        :return: A list of a list of classical registers (each register contains a bit)
        :rtype: list
        """
        job = self.wait_for_job(self.run_and_measure_async(quil_program, qubits, trials, needs_compilation, isa))
        return job.result()

    def qpu_run_and_measure_async(self, quil_program, qubits, trials, needs_compilation=True, isa=None):
        """
        Similar to run_and_measure except that it returns a job id and doesn't wait for the program
        to be executed. See https://go.rigetti.com/connections for reasons to use this method.
        """
        full_program = append_measures_to_program(quil_program, qubits)
        payload = self._run_and_measure_payload(full_program, qubits, trials, needs_compilation=needs_compilation, isa=isa)
        response = post_json(self.session, self.async_endpoint + "/job", self._wrap_program(payload))
        return get_job_id(response)

    def qpu_run_and_measure_payload(self, quil_program, qubits, trials, needs_compilation, isa):
        if not isinstance(quil_program, Program):
            raise TypeError('quil_program must be a Quil program object')
        validate_run_items(qubits)
        if not isinstance(trials, integer_types):
            raise TypeError('trials must be an integer')

        payload = {'type': TYPE_MULTISHOT_MEASURE,
                   'qubits': list(qubits),
                   'trials': trials}

        if needs_compilation:
            payload['uncompiled-quil'] = quil_program.out()
            if isa:
                payload['target-device'] = {"isa": isa.to_dict()}
        else:
            payload['compiled-quil'] = quil_program.out()

        return payload


class QVM(QAM):
    def _wrap_payload(self, program):
        return {
            "machine": "QVM",
            "program": program,
        }

    def _run_payload(self, quil_program, classical_addresses, trials, needs_compilation, isa):
        if not isinstance(quil_program, Program):
            raise TypeError("quil_program must be a Quil program object")
        validate_run_items(classical_addresses)
        if not isinstance(trials, integer_types):
            raise TypeError("trials must be an integer")
        if needs_compilation and not isa:
            raise TypeError("ISA cannot be None if program needs compilation preprocessing.")

        if self.noise_model is not None:
            compiled_program = self.compiler.compile(quil_program)
            quil_program = apply_noise_model(compiled_program, self.noise_model)

        payload = {"type": TYPE_MULTISHOT,
                   "addresses": list(classical_addresses),
                   "trials": trials}
        if needs_compilation:
            payload["uncompiled-quil"] = quil_program.out()
            payload["target-device"] = {"isa": isa.to_dict()}
        else:
            payload["compiled-quil"] = quil_program.out()

        self._maybe_add_noise_to_payload(payload)
        self._add_rng_seed_to_payload(payload)

        return payload

    def _maybe_add_noise_to_payload(self, payload):
        """
        Set the gate noise and measurement noise of a payload.
        """
        if self.measurement_noise is not None:
            payload["measurement-noise"] = self.measurement_noise
        if self.gate_noise is not None:
            payload["gate-noise"] = self.gate_noise


class QPU(QAM):
    def _wrap_payload(self, program):
        return {
            "machine": "QPU",
            "program": program,
            "device": self.device_name
        }

    def _run_payload(self, quil_program, classical_addresses, trials, needs_compilation, isa):
        if not isinstance(quil_program, Program):
            raise TypeError("quil_program must be a Quil program object")
        validate_run_items(classical_addresses)
        if not isinstance(trials, integer_types):
            raise TypeError("trials must be an integer")

        payload = {"type": TYPE_MULTISHOT,
                   "addresses": list(classical_addresses),
                   "trials": trials}

        if needs_compilation:
            payload["uncompiled-quil"] = quil_program.out()
            if isa:
                payload["target-device"] = {"isa": isa.to_dict()}
        else:
            payload["compiled-quil"] = quil_program.out()

        return payload
