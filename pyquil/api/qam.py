import warnings

import networkx as nx
from six import integer_types

from pyquil import Program
from pyquil.api._base_connection import Connection, validate_run_items, TYPE_MULTISHOT
from pyquil.api.errors import QVMError
from pyquil.device import ISA, isa_from_graph
from pyquil.noise import apply_noise_model
from pyquil.quilbase import Gate


class QuantumComputer:
    """
    Represents an abstract quantum computer
    """

    def __init__(self, connection=None):
        if connection is None:
            connection = Connection()

        self.connection = connection
        self.compile_by_default = NotImplemented

    def run_async(self, quil_program, classical_addresses, trials=1, needs_compilation=None,
                  isa=None):
        """
        Similar to run except that it returns a job id and doesn't wait for the program to be executed.
        See https://go.rigetti.com/connections for reasons to use this method.
        """
        if needs_compilation is None:
            needs_compilation = self.compile_by_default

        if needs_compilation:
            # TODO: actually compile this.
            quil_program = compile(quil_program)

        payload = self._run_payload(quil_program, classical_addresses, trials, isa)
        payload = self._wrap_payload(payload)
        return self.connection.run_async_helper(payload)

    def _run_payload(self, quil_program, classical_addresses, trials, isa):
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


class QVM(QuantumComputer):

    def __init__(self, name, qubit_topology: nx.Graph, supported_gates, connection=None):
        self.name = name
        self.qubit_topology = qubit_topology
        self.supported_gates = supported_gates
        self.compile_by_default = False
        super().__init__(connection=connection)

    def _wrap_payload(self, program):
        return {
            "machine": "QVM",
            "program": program,
        }

    def _check_respects_topology(self, program: Program):
        for inst in program:
            if hasattr(inst, 'qubits'):
                qubits = [q.index for q in inst.qubits]
                if len(qubits) == 0:
                    pass
                elif len(qubits) == 1:
                    q = qubits[0]
                    if not q in self.qubit_topology.nodes:
                        raise QVMError(
                            f"The qubit {q} is not in the emulated qubit topology for {self.name}")
                elif len(qubits) == 2:
                    if not tuple(qubits) in self.qubit_topology.edges:
                        raise QVMError(
                            f"The qubit pair {qubits} is not in the emulated qubit topology for {self.name}")

    def _run_payload(self, quil_program, classical_addresses, trials, isa):
        if not isinstance(quil_program, Program):
            raise TypeError("quil_program must be a Quil program object")
        validate_run_items(classical_addresses)
        if not isinstance(trials, integer_types):
            raise TypeError("trials must be an integer")

        if self.qubit_topology is not None:
            self._check_respects_topology(quil_program)

        # TODO Noise model
        # if self.noise_model is not None:
        #     compiled_program = self.compiler.compile(quil_program)
        #     quil_program = apply_noise_model(compiled_program, self.noise_model)

        payload = {"type": TYPE_MULTISHOT,
                   "addresses": list(classical_addresses),
                   "trials": trials}

        payload["compiled-quil"] = quil_program.out()

        # self._maybe_add_noise_to_payload(payload)
        # self._add_rng_seed_to_payload(payload)

        return payload

    def _maybe_add_noise_to_payload(self, payload):
        """
        Set the gate noise and measurement noise of a payload.
        """
        if self.measurement_noise is not None:
            payload["measurement-noise"] = self.measurement_noise
        if self.gate_noise is not None:
            payload["gate-noise"] = self.gate_noise


def get_qvm(*, imitate=None, restrict_topology=False, restrict_gateset=False,
            noncontiguous_qubits=False, with_noise=False, connection=None):
    modifier_str = ''
    if restrict_topology:
        if imitate is not None:
            raise NotImplementedError
        else:
            topo = nx.convert_node_labels_to_integers(nx.grid_2d_graph(5, 5))

        modifier_str += 't'
    else:
        topo = nx.complete_graph(25)

    if restrict_gateset:
        if imitate is not None:
            raise NotImplementedError
        else:
            oneq_gates = ['X(pi/2)', 'X(-pi/2)', 'RZ(theta)', 'I']
            twoq_gates = ['CZ']

        modifier_str += 'g'
    else:
        oneq_gates = None
        twoq_gates = None

    if noncontiguous_qubits:
        if imitate is not None:
            if imitate not in ['19Q-Acorn']:
                warnings.warn("won't actually have noncontiguous qubits")
            raise NotImplementedError
        else:
            mapping = {i: 2 * i for i in topo.nodes}
            topo = nx.relabel_nodes(topo, mapping)

        modifier_str += 'q'
    else:
        pass

    isa = isa_from_graph(graph=topo, oneq_gates=oneq_gates, twoq_gates=twoq_gates)

    if with_noise:
        raise NotImplementedError

    name_parts = []
    if imitate is not None:
        name_parts += [str(imitate)]

    if len(modifier_str) > 0:
        name_parts += [modifier_str]

    name_parts += ['qvm']
    name = '-'.join(name_parts)

    return QVM(name=name, isa=isa, connection=connection)


class QPU(QuantumComputer):

    def __init__(self, name, isa, connection=None):
        self.name = name
        self.isa = isa
        self.compile_by_default = True
        super().__init__(connection=connection)

    def _wrap_payload(self, program):
        return {
            "machine": "QPU",
            "program": program,
            "device": self.name,
        }

    def _run_payload(self, quil_program, classical_addresses, trials, isa):
        if not isinstance(quil_program, Program):
            raise TypeError("quil_program must be a Quil program object")
        validate_run_items(classical_addresses)
        if not isinstance(trials, integer_types):
            raise TypeError("trials must be an integer")

        return {"type": TYPE_MULTISHOT,
                "addresses": list(classical_addresses),
                "trials": trials,
                "compiled-quil": quil_program.out()}


def list_qpus(connection=None):
    if connection is None:
        connection = Connection()

    return connection.list_devices()


def get_qpu(name, connection=None):
    if connection is None:
        connection = Connection()

    device_data = connection.get_device_data(name=name)
    isa = ISA.from_api(device_data['isa'])
    return QPU(name=name, isa=isa, connection=connection)
