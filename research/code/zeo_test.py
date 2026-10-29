import sys, time, warnings, tempfile, os; warnings.filterwarnings("ignore")
from pyzeo.netstorage import AtomNetwork
from pyzeo.extension import *  # noqa
import pyzeo.extension as ext
print([n for n in dir(ext) if not n.startswith("_")])
from pymatgen.core import Structure
s = Structure.from_file(sys.argv[1])
d = tempfile.mkdtemp(); p = os.path.join(d, "x.cssr")
from pymatgen.io.cssr import Cssr
Cssr(s).write_file(p)
t = time.time()
an = AtomNetwork.read_from_CSSR(p.encode(), True, None) if hasattr(AtomNetwork, "read_from_CSSR") else None
print("read", an is not None, time.time() - t)
print([n for n in dir(an) if not n.startswith("_")])
