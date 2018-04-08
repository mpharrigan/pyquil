import numpy as np
import pytest

from pyquil import Program
from pyquil.api.errors import QVMError
from pyquil.api.qam import get_qvm
from pyquil.gates import *
from pyquil.quil import address_qubits
from pyquil.quilatom import QubitPlaceholder


def get_bell_program():
    qs = QubitPlaceholder.register(2)
    prog = Program([
        H(qs[0]),
        CNOT(qs[0], qs[1]),
        MEASURE(qs[0], 0),
        MEASURE(qs[1], 1)
    ])
    return prog, qs


def test_restrict_topology_works():
    bell, qs = get_bell_program()
    bell = address_qubits(bell, {
        qs[0]: 0,
        qs[1]: 1,
    })
    qvm = get_qvm(restrict_topology=True)
    assert qvm.name == 't-qvm'
    bitstrings = qvm.run(bell, [0, 1], 100)
    np.testing.assert_array_equal(np.zeros(100), np.sum(bitstrings, axis=1) % 2)


def test_restrict_topology_fails():
    bell, qs = get_bell_program()
    bell = address_qubits(bell, {
        qs[0]: 0,
        qs[1]: 2,
    })
    qvm = get_qvm(restrict_topology=True)
    with pytest.raises(QVMError) as e:
        bitstrings = qvm.run(bell, [0, 1], 100)

    assert e.match(r'The qubit pair \[0, 2\] is not in the emulated qubit topology for t-qvm.*')


def test_noncontiguous_works():
    bell, qs = get_bell_program()
    bell = address_qubits(bell, {
        qs[0]: 0,
        qs[1]: 2,
    })
    qvm = get_qvm(restrict_topology=True, noncontiguous_qubits=True)
    assert qvm.name == 'tq-qvm'
    bitstrings = qvm.run(bell, [0, 1], 100)
    np.testing.assert_array_equal(np.zeros(100), np.sum(bitstrings, axis=1) % 2)


def test_noncontiguous_fails1():
    bell, qs = get_bell_program()
    bell = address_qubits(bell, {
        qs[0]: 0,
        qs[1]: 1,
    })
    qvm = get_qvm(restrict_topology=True, noncontiguous_qubits=True)
    with pytest.raises(QVMError) as e:
        bitstrings = qvm.run(bell, [0, 1], 100)

    assert e.match(r'The qubit pair \[0, 1\] is not in the emulated qubit topology for tq-qvm.*')


def test_noncontiguous_fails2():
    bell, qs = get_bell_program()
    bell = address_qubits(bell, {
        qs[0]: 1,
        qs[1]: 3,
    })
    qvm = get_qvm(noncontiguous_qubits=True)
    with pytest.raises(QVMError) as e:
        bitstrings = qvm.run(bell, [0, 1], 100)

    assert e.match(r'The qubit 1 is not in the emulated qubit topology for q-qvm.*')
