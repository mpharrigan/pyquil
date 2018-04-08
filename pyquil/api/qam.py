import warnings

import networkx as nx
from six import integer_types

from pyquil import Program
from pyquil.api._base_connection import Connection, validate_run_items, TYPE_MULTISHOT
from pyquil.device import ISA
from pyquil.noise import apply_noise_model


class QAM:
    """
    Represents a connection to the QPU (Quantum Processing Unit)
    """
    def __init__(self, connection=None):
        if connection is None:
            self.connection = Connection()


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

    def run(self, quil_program, classical_addresses, trials=1, needs_compilation=None, isa=None):
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



class QVM(QAM):

    def __init__(self):
        pass

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


def get_qvm(*, imitate='acorn', restrict_lattice=True, restrict_gateset=True, noncontiguous_qubits=True, with_noise=True):
    if restrict_lattice:
        if imitate is not None:
            lattice = qpu_topology
        else:
            lattice = nx.grid_2d_graph(5,5)
    else:
        lattice = nx.complete_graph(25)

    if restrict_gateset:
        if imitate is not None:
            pass
        else:
            pass
    else:
        pass

    if noncontiguous_qubits:
        if imitate is not None:
            if imitate not in ['19Q-Acorn']:
                warnings.warn("won't actually have noncontiguous qubits")
            qubits = qpu_qubits
        else:
            qubits = [2*i for i in range(n_qubits)]
    else:
        qubits = None


    if with_noise:
        pass


    return QVM()


class QPU(QAM):

    def __init__(self, name, qubit_topology, connection=None):
        self.name = name
        self.qubit_topology = qubit_topology
        super().__init__(connection=connection)

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

def get_qpu(name, connection=None):
    if connection is None:
        connection = Connection()

    devices = connection.get_devices().json()['devices']
    try:
        device = devices[name]
    except KeyError:
        raise KeyError(f"The device named {name} does not exist or is not available.")

    isa = ISA.from_dict(device['isa'])
    topo = isa.topology()
    return QPU(name=name, qubit_topology=topo, connection=connection)

get_qpu('19Q-Acorn')