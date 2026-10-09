import os
import capnp
import opendbc

capnp.remove_import_hook()
CEREAL_PATH = os.path.dirname(os.path.abspath(__file__))
OPENDBC_CAR_PATH = os.path.join(os.path.dirname(opendbc.__file__), 'car')
_IMPORTS = [OPENDBC_CAR_PATH, CEREAL_PATH]
# Define car before importing opendbc.car: older supported ABIs reuse cereal.car.
# Every import resolves the same physical schema; symlink-disabled checkouts work too.
car = capnp.load(os.path.join(OPENDBC_CAR_PATH, 'car.capnp'), imports=_IMPORTS)
log = capnp.load(os.path.join(CEREAL_PATH, 'log.capnp'), imports=_IMPORTS)
custom = capnp.load(os.path.join(CEREAL_PATH, 'custom.capnp'), imports=_IMPORTS)
