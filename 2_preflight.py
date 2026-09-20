"""Step 2 — preflight check. Propellers can stay on; nothing spins here.

Connects, confirms the Flow deck v2 is detected, and prints the
supervisor state so you know the drone is willing to arm and fly.

    python 2_preflight.py
"""
import time

import cflib.crtp
from cflib.crazyflie import Crazyflie
from cflib.crazyflie.log import LogConfig
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from cflib.crazyflie.syncLogger import SyncLogger

from uri import URI

cflib.crtp.init_drivers()

with SyncCrazyflie(URI, cf=Crazyflie(rw_cache='./cache')) as scf:
    cf = scf.cf
    print(f'Connected to {URI}')

    # Give the param/log TOCs a moment to settle.
    time.sleep(1.0)

    flow = int(cf.param.get_value('deck.bcFlow2'))
    print(f'Flow deck v2 detected: {"YES" if flow else "NO  <-- fix this first"}')

    ledring = int(cf.param.get_value('deck.bcLedRing'))
    print(f'LED ring detected:     {"YES" if ledring else "no"}')

    multiranger = int(cf.param.get_value('deck.bcMultiranger'))
    print(f'Multi-ranger detected: {"YES" if multiranger else "no"}')

    # pm.batteryLevel only exists on recent firmware; pm.vbat is always there.
    # 1S LiPo: ~4.2 V full, ~3.35 V low (pm.lowVoltage), ~3.0 V critical.
    lg = LogConfig(name='bat', period_in_ms=100)
    lg.add_variable('pm.vbat', 'float')
    with SyncLogger(scf, lg) as logger:
        vbat = next(iter(logger))[1]['pm.vbat']
    print(f'Battery: {vbat:.2f} V  (4.2 full, 3.35 low)')

    states = cf.supervisor.read_state_list()
    print(f'Supervisor state: {states}')

    print()
    if not flow:
        print('No Flow deck -> the drone cannot hold position. Do not run 3_hover.py.')
    elif cf.supervisor.is_tumbled:
        print('Drone reports tumbled. Set it upright on a flat surface and restart it.')
    elif not cf.supervisor.can_be_armed:
        print('Drone will not arm yet. Restart it and let it finish its startup self-test')
        print('while sitting completely still on a level surface.')
    else:
        print('Ready. You can run:  python 3_hover.py')
