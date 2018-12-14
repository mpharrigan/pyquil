import itertools
import json
import logging
import sys
from json import JSONEncoder
from math import pi
from typing import List, Union, Iterable, Dict

import networkx as nx
import numpy as np
from networkx.algorithms.approximation.clique import clique_removal

from pyquil import Program
from pyquil.api import QuantumComputer
from pyquil.gates import *
from pyquil.paulis import PauliTerm, is_identity

if sys.version_info < (3, 7):
    from pyquil.external.dataclasses import dataclass
else:
    from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Experiment:
    """
    One tomography-like experiment.

    Many near-term quantum algorithms take the following form:
        - Start in a pauli state
        - Prepare some ansatz
        - Measure it w.r.t. pauli operators

    Where we typically use a large number of (start, measure) pairs but keep the ansatz preparation
    program consistent. This class represents the (start, measure) pairs. Typically a large
    number of these :py:class:`Experiment` objects will be created and grouped into
    an :py:class:`ExperimentSuite`.
    """
    in_operator: PauliTerm
    out_operator: PauliTerm

    def __str__(self):
        return f'{self.in_operator.compact_str()}→{self.out_operator.compact_str()}'

    def __repr__(self):
        return f'Experiment[{self}]'

    def serializable(self):
        return str(self)

    @classmethod
    def from_str(cls, s: str):
        """The opposite of str(expt)"""
        instr, outstr = s.split('→')
        return cls(in_operator=PauliTerm.from_compact_str(instr),
                   out_operator=PauliTerm.from_compact_str(outstr))


def _abbrev_program(program: Program, max_len=10):
    """Create an abbreviated string representation of a Program.

    This will join all instructions onto a single line joined by '; '. If the number of
    instructions exceeds ``max_len``, some will be excluded from the string representation.
    """
    program_lines = program.out().splitlines()
    if max_len is not None and len(program_lines) > max_len:
        first_n = max_len // 2
        last_n = max_len - first_n
        excluded = len(program_lines) - max_len
        program_lines = (program_lines[:first_n] + [f'... {excluded} instrs not shown ...']
                         + program_lines[-last_n:])

    return '; '.join(program_lines)


class ExperimentSuite:
    """
    A whole set of tomography-like experiments

    Many near-term quantum algorithms involve:

     - some limited state preperation
     - enacting a quantum process (like in tomography) or preparing a variational ansatz state
       (like in VQE)
     - measuring observables of the state.

    Where we typically use a large number of (state_prep, measure) pairs but keep the ansatz
    program consistent. This class stores the ansatz program as a :py:class:`~pyquil.Program`
    and maintains a list of :py:class:`Experiment` objects which each represent a
    (state_prep, measure) pair.

    Experiments belonging to a shared tensor product basis (TPB) can (optionally) be estimated
    simultaneously. Therefore, this class is backed by a list of list of Experiments.
    Experiments sharing an inner list will be estimated simultaneously. If you don't want this,
    provide a list of length-1-lists. As a convenience, if you pass a 1D list to the constructor
    it will expand it to a list of length-1-lists.

    This class will not group experiments for you. Please see :py:func:`group_experiments` for
    a function that will automatically process an ExperimentSuite to group Experiments sharing
    a TPB.
    """

    def __init__(self,
                 experiments: Union[List[Experiment], List[List[Experiment]]],
                 program: Program,
                 qubits: List[int]):
        if len(experiments) == 0:
            experiments = []
        else:
            if isinstance(experiments[0], Experiment):
                # convenience wrapping in lists of length 1
                experiments = [[expt] for expt in experiments]

        self._experiments = experiments  # type: List[List[Experiment]]
        self.program = program
        self.qubits = qubits

    def __len__(self):
        return len(self._experiments)

    def __getitem__(self, item):
        return self._experiments[item]

    def __setitem__(self, key, value):
        self._experiments[key] = value

    def __delitem__(self, key):
        self._experiments.__delitem__(key)

    def __iter__(self):
        yield from self._experiments

    def __reversed__(self):
        yield from reversed(self._experiments)

    def __contains__(self, item):
        return item in self._experiments

    def append(self, expts):
        if not isinstance(expts, list):
            expts = [expts]
        return self._experiments.append(expts)

    def count(self, expt):
        return self._experiments.count(expt)

    def index(self, expt, start=None, stop=None):
        return self._experiments.index(expt, start, stop)

    def extend(self, expts):
        return self._experiments.extend(expts)

    def insert(self, index, expt):
        return self._experiments.insert(index, expt)

    def pop(self, index=None):
        return self._experiments.pop(index)

    def remove(self, expt):
        return self._experiments.remove(expt)

    def reverse(self):
        return self._experiments.reverse()

    def sort(self, key=None, reverse=False):
        return self._experiments.sort(key, reverse)

    def experiment_strings(self):
        yield from ('{i}: {exptstr}'.format(i=i, exptstr=', '.join(str(expt) for expt in expts))
                    for i, expts in enumerate(self._experiments))

    def experiments_string(self, abbrev_after=None):
        exptstrs = list(self.experiment_strings())
        if abbrev_after is not None and len(exptstrs) > abbrev_after:
            first_n = abbrev_after // 2
            last_n = abbrev_after - first_n
            excluded = len(exptstrs) - abbrev_after
            exptstrs = (exptstrs[:first_n] + [f'... {excluded} not shown ...',
                                              '... use e.experiments_string() for all ...']
                        + exptstrs[-last_n:])
        return '\n'.join(exptstrs)

    def __str__(self):
        return _abbrev_program(self.program) + '\n' + self.experiments_string(abbrev_after=20)

    def serializable(self):
        return {
            'type': 'ExperimentSuite',
            'experiments': self._experiments,
            'program': self.program.out(),
            'qubits': self.qubits,
        }

    def __eq__(self, other):
        if not isinstance(other, ExperimentSuite):
            return False
        return self.serializable() == other.serializable()


class OperatorEncoder(JSONEncoder):
    def default(self, o):
        if isinstance(o, Experiment):
            return str(o)
        if isinstance(o, ExperimentSuite):
            return {
                'type': 'ExperimentSuite',
                'experiments': o._experiments,
                'program': o.program.out(),
                'qubits': o.qubits,
            }
        if isinstance(o, ExperimentResult):
            return {
                'type': 'ExperimentResult',
                'experiment': o.experiment,
                'expectation': o.expectation,
                'stddev': o.stddev,
            }

        return o


def to_json(fn, obj):
    with open(fn, 'w') as f:
        json.dump(obj, f, cls=OperatorEncoder, indent=2)
    return fn


def _operator_object_hook(obj):
    if 'type' in obj and obj['type'] == 'ExperimentSuite':
        return ExperimentSuite([[Experiment.from_str(e) for e in expts]
                                for expts in obj['experiments']],
                               program=Program(obj['program']),
                               qubits=obj['qubits'])
    return obj


def read_json(fn):
    with open(fn) as f:
        return json.load(f, object_hook=_operator_object_hook)


def _local_pauli_eig_prep(op: str, idx: int):
    """
    Generate gate sequence to prepare a the +1 eigenstate of a Pauli operator, assuming
    we are starting from the |00..00> ground state.

    :param op: A string representation of the Pauli operator whose +1 eigenstate we'd like to
        prepare.
    :param idx: The index of the qubit that the preparation is acting on
    :return: A program which will prepare the requested state.
    """
    if op == 'X':
        return Program(RY(pi / 2, idx))
    elif op == 'Y':
        return Program(RX(-pi / 2, idx))
    elif op == 'Z':
        return Program()

    raise ValueError(f'Unknown operation {op}')


def _local_pauli_eig_meas(op, idx):
    """
    Generate gate sequence to measure in the eigenbasis of a Pauli operator, assuming
    we are only able to measure in the Z eigenbasis.

    """
    if op == 'X':
        return Program(RY(-pi / 2, idx))
    elif op == 'Y':
        return Program(RX(pi / 2, idx))
    elif op == 'Z':
        return Program()
    raise ValueError(f'Unknown operation {op}')


def _ops_diagonal_in_tpb(op_code1: str, op_code2: str):
    """
    Given two op strings (I, X, Y, or Z) return whether they are diagonal in a tensor product basis.

    I.e. are they the same or is one of them 'I'.
    """
    if op_code1 not in ['X', 'Y', 'Z', 'I']:
        raise ValueError(f"Unknown op_code {op_code1}")
    if op_code2 not in ['X', 'Y', 'Z', 'I']:
        raise ValueError(f"Unknown op_code {op_code2}")

    if op_code1 == op_code2:
        # Same op
        return True
    elif op_code1 == 'I' or op_code2 == 'I':
        # I commutes with everything
        return True
    else:
        # Otherwise, they do not commute.
        return False


def _all_qubits_diagonal_in_tpb(op1: PauliTerm, op2: PauliTerm):
    """
    Compare all qubits between two PauliTerms to see if they are all diagonal in an
    overall shared tensor product basis.
    """
    all_qubits = set(op1.get_qubits()) | set(op2.get_qubits())
    return all(_ops_diagonal_in_tpb(op1[q], op2[q]) for q in all_qubits)


def construct_tpb_graph(experiments: ExperimentSuite):
    """
    Construct a graph where an edge signifies two experiments are diagonal in a TPB.
    """
    g = nx.Graph()
    for expt in experiments:
        assert len(expt) == 1, 'already grouped?'
        expt = expt[0]

        if expt not in g:
            g.add_node(expt, count=1)
        else:
            g.nodes[expt]['count'] += 1

    for expt1, expt2 in itertools.combinations(experiments, r=2):
        expt1 = expt1[0]
        expt2 = expt2[0]

        if expt1 == expt2:
            continue

        if (_all_qubits_diagonal_in_tpb(expt1.in_operator, expt2.in_operator)
                and _all_qubits_diagonal_in_tpb(expt1.out_operator, expt2.out_operator)):
            g.add_edge(expt1, expt2)

    return g


def group_experiments(experiments: ExperimentSuite) -> ExperimentSuite:
    """
    Group experiments that are diagonal in a shared tensor product basis (TPB) to minimize number
    of QPU runs.

    :param experiments: an Experiment suite
    :return: an Experiment suite with all the same experiments, just grouped according to shared
        TPBs.
    """
    g = construct_tpb_graph(experiments)
    _, cliqs = clique_removal(g)
    new_cliqs = []
    for cliq in cliqs:
        new_cliq = []
        for expt in cliq:
            # duplicate `count` times
            new_cliq += [expt] * g.nodes[expt]['count']

        new_cliqs += [new_cliq]

    return ExperimentSuite(new_cliqs, program=experiments.program, qubits=experiments.qubits)


@dataclass(frozen=True)
class ExperimentResult:
    experiment: Experiment
    expectation: Union[float, complex]
    stddev: float

    def __str__(self):
        return f'{self.experiment}: {self.expectation} +- {self.stddev}'

    def __repr__(self):
        return f'ExperimentResult[{self}]'

    def serializable(self):
        return {
            'type': 'ExperimentResult',
            'experiment': self.experiment,
            'expectation': self.expectation,
            'stddev': self.stddev,
        }


def _validate_all_diagonal_in_tpb(ops: Iterable[PauliTerm]) -> Dict[int, str]:
    """Each non-identity qubit should result in the same op_str among all operations. Return
    said mapping.
    """
    mapping = dict()  # type: Dict[int, str]
    for op in ops:
        for idx, op_str in op:
            if idx in mapping:
                assert mapping[idx] == op_str, 'Improper grouping of operators'
            else:
                mapping[idx] = op_str
    return mapping


def measure_observables(qc: QuantumComputer, experiment_suite: ExperimentSuite, n_shots=1000,
                        progress_callback=None, active_reset=False):
    """
    Measure all the observables in an ExperimentSuite.

    :param qc: A QuantumComputer which can run quantum programs
    :param experiment_suite: The suite of observables to measure
    :param n_shots: The number of shots to take per Experiment
    :param progress_callback: If not None, this function is called each time a group of
        experiments is run with arguments ``f(i, len(experiment_suite)`` such that the progress
        is ``i / len(experiment_suite)``.
    :param active_reset: Whether to actively reset qubits instead of waiting several
        times the coherence length for qubits to decay to |0> naturally. Setting this
        to True is much faster but there is a ~1% error per qubit in the reset operation.
        Thermal noise from "traditional" reset is not routinely characterized but is of the same
        order.
    """
    for i, experiments in enumerate(experiment_suite):
        # Outer loop over a collection of grouped experiments for which we can simultaneously
        # estimate.
        log.info(f"Collecting bitstrings for the {len(experiments)} experiments: {experiments}")

        # 1.1 Prepare a state according to expt.in_operator
        total_prog = Program()
        if active_reset:
            total_prog += RESET()
        in_mapping = _validate_all_diagonal_in_tpb(expt.in_operator for expt in experiments)
        for idx, op_str in in_mapping.items():
            total_prog += _local_pauli_eig_prep(op_str, idx)

        # 1.2 Add in the program
        total_prog += experiment_suite.program

        # 1.3 Measure the state according to expt.out_operator
        out_mapping = _validate_all_diagonal_in_tpb(expt.out_operator for expt in experiments)
        for idx, op_str in out_mapping.items():
            total_prog += _local_pauli_eig_meas(op_str, idx)

        # 2. Run the experiment
        bitstrings = qc.run_and_measure(total_prog, n_shots)
        if progress_callback is not None:
            progress_callback(i, len(experiment_suite))

        # 3. Post-process
        # 3.1 First transform bits to eigenvalues; ie (+1, -1)
        obs_strings = {q: 1 - 2 * bitstrings[q] for q in bitstrings}

        # Inner loop over the grouped experiments. They only differ in which qubits' measurements
        # we include in the post-processing. For example, if `experiments` is Z1, Z2, Z1Z2 and we
        # measure (n_shots, n_qubits=2) obs_strings then the full operator value involves selecting
        # either the first column, second column, or both and multiplying along the row.
        for expt in experiments:
            # 3.2 Special case for measuring the "identity" operator, which doesn't make much
            #     sense but should happen perfectly.
            if is_identity(expt.out_operator):
                yield ExperimentResult(
                    experiment=expt,
                    expectation=1.0,
                    stddev=0.0,
                )
                continue

            # 3.3 Get the term's coefficient so we can multiply it in later.
            assert expt.in_operator.coefficient == 1, 'in_operator should specify a state and ' \
                                                      'therefore cannot have a coefficient'
            coeff = complex(expt.out_operator.coefficient)
            if not np.isclose(coeff.imag, 0):
                raise ValueError(f"{expt}'s out_operator has a complex coefficient.")
            coeff = coeff.real

            # 3.4 Pick columns corresponding to qubits with a non-identity out_operation and stack
            #     into an array of shape (n_shots, n_measure_qubits)
            my_obs_strings = np.vstack(obs_strings[q] for q, op_str in expt.out_operator).T

            # 3.6 Multiply row-wise to get operator values. Do statistics. Yield result.
            obs_vals = coeff * np.prod(my_obs_strings, axis=1)
            obs_mean = np.mean(obs_vals)
            obs_var = np.var(obs_vals) / n_shots
            yield ExperimentResult(
                experiment=expt,
                expectation=np.asscalar(obs_mean),
                stddev=np.asscalar(np.sqrt(obs_var)),
            )