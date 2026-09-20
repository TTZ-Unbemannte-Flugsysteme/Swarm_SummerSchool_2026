"""Step 4 — fly a 0.5 m square, then land. Run this only after 3_hover.py worked.

Needs ~2.5 x 2.5 m of clear floor.

    python 4_square.py
"""
import time

import cflib.crtp
from cflib.crazyflie import Crazyflie
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from cflib.positioning.motion_commander import MotionCommander

from uri import URI

HEIGHT = 0.4
SIDE = 0.5        # metres
VELOCITY = 0.3    # m/s — keep it slow, the Flow deck likes slow

cflib.crtp.init_drivers()

with SyncCrazyflie(URI, cf=Crazyflie(rw_cache='./cache')) as scf:
    cf = scf.cf
    time.sleep(1.0)

    if not int(cf.param.get_value('deck.bcFlow2')):
        raise SystemExit('No Flow deck v2 detected — aborting.')

    cf.supervisor.send_arming_request(True)
    time.sleep(1.0)

    try:
        with MotionCommander(scf, default_height=HEIGHT) as mc:
            time.sleep(2.0)  # let the hover settle before moving

            mc.forward(SIDE, velocity=VELOCITY)
            time.sleep(0.5)
            mc.right(SIDE, velocity=VELOCITY)
            time.sleep(0.5)
            mc.back(SIDE, velocity=VELOCITY)
            time.sleep(0.5)
            mc.left(SIDE, velocity=VELOCITY)
            time.sleep(1.0)

            print('Landing...')
    finally:
        time.sleep(1.0)
        cf.supervisor.send_arming_request(False)
        print('Disarmed.')
