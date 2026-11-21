"""uiCA with EMR core parameters overridden: runcore.py [Param=value ...] -- <uiCA args>
e.g. runcore.py issueWidth=5 -- build/x.o -arch EMR -state states/x.json -TPonly"""
import sys, os
U = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..')
sys.path.insert(0, U); os.chdir(os.getcwd())
from microArchConfigs import MicroArchConfigs
i = sys.argv.index('--')
for kv in sys.argv[1:i]:
    k, v = kv.split('=')
    setattr(MicroArchConfigs['EMR'], k, int(v) if v.lstrip('-').isdigit() else (float(v) if v.replace('.','',1).isdigit() else v))
sys.argv = [os.path.join(U, 'uiCA.py')] + sys.argv[i + 1:]
import uiCA
uiCA.main()
