import os
import capnp

capnp.remove_import_hook()
CEREAL_PATH = os.path.dirname(os.path.abspath(__file__))
_IMPORTS = [CEREAL_PATH]
car = capnp.load(os.path.join(CEREAL_PATH, 'car.capnp'), imports=_IMPORTS)
log = capnp.load(os.path.join(CEREAL_PATH, 'log.capnp'), imports=_IMPORTS)
custom = capnp.load(os.path.join(CEREAL_PATH, 'custom.capnp'), imports=_IMPORTS)
