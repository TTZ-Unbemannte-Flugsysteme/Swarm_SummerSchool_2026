"""Step 3 — your first flight: take off, hover, land.

SAFETY
  - Clear a 2x2 m area, nothing overhead, no people within arm's reach.
  - Put the drone on a floor with visible texture (carpet, patterned rug,
    a sheet of newspaper). The Flow deck is blind over plain glossy floors.
  - Keep a hand on ctrl-C. The script lands and disarms on interrupt.

    python 3_hover.py
"""
import time

import cflib.crtp
from cflib.crazyflie import Crazyflie
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from cflib.positioning.motion_commander import MotionCommander

from uri import URI

HEIGHT = 0.4      # metres
HOVER_TIME = 5.0  # seconds

cflib.crtp.init_drivers()

with SyncCrazyflie(URI, cf=Crazyflie(rw_cache='./cache')) as scf:
    cf = scf.cf
    time.sleep(1.0)

    if not int(cf.param.get_value('deck.bcFlow2')):
        raise SystemExit('No Flow deck v2 detected — aborting.')

    print('Arming...')
    cf.supervisor.send_arming_request(True)
    time.sleep(1.0)

    try:
        # MotionCommander takes off on entry and lands on exit.
        with MotionCommander(scf, default_height=HEIGHT) as mc:
            print(f'Hovering at {HEIGHT} m for {HOVER_TIME} s')
            time.sleep(HOVER_TIME)
            print('Landing...')
    finally:
        time.sleep(1.0)
        cf.supervisor.send_arming_request(False)
        print('Disarmed.')
