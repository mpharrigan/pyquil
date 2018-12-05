import itertools

import numpy as np
import functools
from operator import mul

from pyquil.api import WavefunctionSimulator
from pyquil.operator_estimation import Experiment, ExperimentSuite, to_json, read_json, \
    _all_qubits_belong_to_a_tpb, group_experiments, ExperimentResult, measure_observables
from pyquil.paulis import sI, sX, sY, sZ
from pyquil import Program, get_qc
from pyquil.gates import *


def _generate_random_paulis(n_qubits, n_terms):
    paulis = [sI, sX, sY, sZ]
    all_op_inds = np.random.randint(len(paulis), size=(n_terms, n_qubits))
    operators = []
    for op_inds in all_op_inds:
        op = functools.reduce(mul, (paulis[pi](i) for i, pi in enumerate(op_inds)), sI(0))
        op *= np.random.uniform(-1, 1)
        operators += [op]
    return operators


def test_experiment():
    in_ops = _generate_random_paulis(n_qubits=4, n_terms=7)
    out_ops = _generate_random_paulis(n_qubits=4, n_terms=7)
    for iop, oop in zip(in_ops, out_ops):
        expt = Experiment(iop, oop)
        assert str(expt) == expt.serializable()
        expt2 = Experiment.from_str(str(expt))
        assert expt == expt2
        assert expt2.in_operator == iop
        assert expt2.out_operator == oop


def test_experiment_no_in():
    out_ops = _generate_random_paulis(n_qubits=4, n_terms=7)
    for oop in out_ops:
        expt = Experiment(sI(), oop)
        expt2 = Experiment.from_str(str(expt))
        assert expt == expt2
        assert expt2.in_operator == sI()
        assert expt2.out_operator == oop


def test_experiment_suite():
    expts = [
        Experiment(sI(), sX(0) * sY(1)),
        Experiment(sZ(0), sZ(0)),
    ]

    suite = ExperimentSuite(
        experiments=expts,
        program=Program(X(0), Y(1)),
        qubits=[0, 1]
    )
    assert len(suite) == 2
    for e1, e2 in zip(expts, suite):
        # experiment suite puts in groups of length 1
        assert len(e2) == 1
        e2 = e2[0]
        assert e1 == e2
    prog_str = str(suite).splitlines()[0]
    assert prog_str == 'X 0; Y 1'


def test_experiment_suite_pre_grouped():
    expts = [
        [Experiment(sI(), sX(0) * sI(1)), Experiment(sI(), sI(0) * sX(1))],
        [Experiment(sI(), sZ(0) * sI(1)), Experiment(sI(), sI(0) * sZ(1))],
    ]

    suite = ExperimentSuite(
        experiments=expts,
        program=Program(X(0), Y(1)),
        qubits=[0, 1]
    )
    assert len(suite) == 2  # number of groups
    for es1, es2 in zip(expts, suite):
        for e1, e2 in zip(es1, es2):
            assert e1 == e2
    prog_str = str(suite).splitlines()[0]
    assert prog_str == 'X 0; Y 1'


def test_experiment_suite_empty():
    suite = ExperimentSuite([], program=Program(X(0)), qubits=[0])
    assert len(suite) == 0
    assert str(suite.program) == 'X 0\n'


def test_suite_deser(tmpdir):
    expts = [
        [Experiment(sI(), sX(0) * sI(1)), Experiment(sI(), sI(0) * sX(1))],
        [Experiment(sI(), sZ(0) * sI(1)), Experiment(sI(), sI(0) * sZ(1))],
    ]

    suite = ExperimentSuite(
        experiments=expts,
        program=Program(X(0), Y(1)),
        qubits=[0, 1]
    )
    to_json(f'{tmpdir}/suite.json', suite)
    suite2 = read_json(f'{tmpdir}/suite.json')
    assert suite == suite2


def test_all_ops_belong_to_tpb():
    expts = [
        [Experiment(sI(), sX(0) * sI(1)), Experiment(sI(), sI(0) * sX(1))],
        [Experiment(sI(), sZ(0) * sI(1)), Experiment(sI(), sI(0) * sZ(1))],
    ]
    for group in expts:
        for e1, e2 in itertools.combinations(group, 2):
            assert _all_qubits_belong_to_a_tpb(e1.in_operator, e2.in_operator)
            assert _all_qubits_belong_to_a_tpb(e1.out_operator, e2.out_operator)


def test_group_experiments():
    expts = [  # cf above, I removed the inner nesting. Still grouped visually
        Experiment(sI(), sX(0) * sI(1)), Experiment(sI(), sI(0) * sX(1)),
        Experiment(sI(), sZ(0) * sI(1)), Experiment(sI(), sI(0) * sZ(1)),
    ]
    suite = ExperimentSuite(expts, Program(), qubits=[0, 1])
    grouped_suite = group_experiments(suite)
    assert len(suite) == 4
    assert len(grouped_suite) == 2


def test_experiment_result():
    er = ExperimentResult(
        experiment=Experiment(sX(0), sZ(0)),
        expectation=0.9,
        stddev=0.05,
    )
    assert str(er) == '(1+0j)*X0→(1+0j)*Z0: 0.9 +- 0.05'


def test_measure_observables(forest):
    expts = [
        Experiment(sI(), o1 * o2)
        for o1, o2 in itertools.product([sI(0), sX(0), sY(0), sZ(0)], [sI(1), sX(1), sY(1), sZ(1)])
    ]
    suite = ExperimentSuite(expts, program=Program(X(0), CNOT(0, 1)), qubits=[0, 1])
    assert len(suite) == 4 * 4
    gsuite = group_experiments(suite)
    assert len(gsuite) == 3 * 3  # can get all the terms with I for free in this case

    qc = get_qc('2q-qvm')
    wfn = WavefunctionSimulator()
    for res in measure_observables(qc, gsuite, n_shots=10_000):
        if res.experiment.out_operator in [sI(), sZ(0), sZ(1), sZ(0) * sZ(1)]:
            assert np.abs(res.expectation) > 0.9
        else:
            assert np.abs(res.expectation) < 0.1


def test_append():
    expts = [
        [Experiment(sI(), sX(0) * sI(1)), Experiment(sI(), sI(0) * sX(1))],
        [Experiment(sI(), sZ(0) * sI(1)), Experiment(sI(), sI(0) * sZ(1))],
    ]
    suite = ExperimentSuite(
        experiments=expts,
        program=Program(X(0), Y(1)),
        qubits=[0, 1]
    )
    suite.append(Experiment(sI(), sY(0) * sX(1)))
    assert (len(str(suite))) > 0