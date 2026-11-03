import os
import sys

from evals.audio_rig.rig import main

code = main()
sys.stdout.flush()
sys.stderr.flush()
# The LiveKit client library keeps native threads running after its rooms close, which would hold
# the process open once the results are written.
os._exit(code)
