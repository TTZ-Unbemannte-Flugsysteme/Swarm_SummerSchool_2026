"""Step 5 — the bee waggle dance. Run this only after 4_square.py worked.

A honeybee's waggle dance is a figure-eight: a straight "waggle run" where
the bee shimmies side to side, then a loop back to the start — alternating
left and right loops. We do the same:

    waggle run (forward + side-to-side wiggle + yaw wag)
    loop back on the LEFT
    waggle run
    loop back on the RIGHT
    ... repeat

If an LED-ring deck is fitted it lights up too: yellow/black bee stripes
spin round the ring during the waggle runs, amber on the loops.

Needs ~2 x 2 m of clear floor.

    python 5_waggle.py
"""
import math
import time

import cflib.crtp
from cflib.crazyflie import Crazyflie
from cflib.crazyflie.mem import MemoryElement
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from cflib.positioning.motion_commander import MotionCommander

from uri import URI

HEIGHT = 0.4

# Waggle run
RUN_LENGTH = 0.5      # metres forward per waggle run
RUN_SPEED = 0.2       # m/s forward — slow, the Flow deck likes slow
WAGGLE_AMP = 0.15     # m/s peak sideways velocity of the shimmy
WAGGLE_HZ = 2.0       # shimmies per second
WAG_YAW = 40.0        # deg/s peak yaw wag (the bee's body swings too)

# Return loop
LOOP_RADIUS = 0.25    # metres
LOOP_SPEED = 0.25     # m/s along the loop

CYCLES = 2            # figure-eights to dance (each = 2 runs + 2 loops)

TICK = 0.05           # seconds between setpoints

# LED ring
BEE_YELLOW = (255, 160, 0)
BEE_BLACK = (0, 0, 0)
AMBER = (255, 60, 0)
STRIPE_WIDTH = 2      # LEDs per stripe (12 LEDs -> 3 yellow + 3 black stripes)
RING_EFFECT_OFF = 0
RING_EFFECT_SOLID = 7
RING_EFFECT_MEMORY = 13   # colours come from the LED driver memory below


class BeeLights:
    """Wraps the LED-ring deck. Every method is a no-op if no ring is fitted."""

    def __init__(self, cf):
        self.cf = cf
        self.ring = None
        if int(cf.param.get_value('deck.bcLedRing')):
            mems = cf.mem.get_mems(MemoryElement.TYPE_DRIVER_LED)
            self.ring = mems[0] if mems else None
        print('LED ring:', 'found' if self.ring else 'not fitted, dancing dark')

    def stripes(self, offset):
        """Yellow/black stripes, rotated by `offset` LEDs."""
        if not self.ring:
            return
        self.cf.param.set_value('ring.effect', RING_EFFECT_MEMORY)
        for i, led in enumerate(self.ring.leds):
            stripe = ((i + offset) // STRIPE_WIDTH) % 2
            led.set(*(BEE_YELLOW if stripe == 0 else BEE_BLACK))
        self.ring.write_data(None)

    def solid(self, rgb):
        if not self.ring:
            return
        r, g, b = rgb
        self.cf.param.set_value('ring.solidRed', r)
        self.cf.param.set_value('ring.solidGreen', g)
        self.cf.param.set_value('ring.solidBlue', b)
        self.cf.param.set_value('ring.effect', RING_EFFECT_SOLID)

    def off(self):
        if self.ring:
            self.cf.param.set_value('ring.effect', RING_EFFECT_OFF)


def waggle_run(mc, lights):
    """Fly forward while oscillating sideways and wagging yaw."""
    duration = RUN_LENGTH / RUN_SPEED
    t0 = time.time()
    tick = 0
    while (t := time.time() - t0) < duration:
        phase = 2 * math.pi * WAGGLE_HZ * t
        vy = WAGGLE_AMP * math.sin(phase)          # + is left
        yaw = WAG_YAW * math.cos(phase)            # + is turn left
        mc.start_linear_motion(RUN_SPEED, vy, 0.0, rate_yaw=yaw)
        if tick % 2 == 0:                          # spin stripes at 10 Hz
            lights.stripes(tick // 2)
        tick += 1
        time.sleep(TICK)
    mc.stop()
    time.sleep(0.3)


def return_loop(mc, lights, left):
    """Loop round and get back to where the run started, facing forward."""
    lights.solid(AMBER)
    # A full circle ends on the same spot with the same heading, so the
    # loop is the bee's curve and the back() is the leg home.
    if left:
        mc.circle_left(LOOP_RADIUS, velocity=LOOP_SPEED, angle_degrees=360)
    else:
        mc.circle_right(LOOP_RADIUS, velocity=LOOP_SPEED, angle_degrees=360)
    time.sleep(0.3)
    mc.back(RUN_LENGTH, velocity=RUN_SPEED)
    time.sleep(0.3)


cflib.crtp.init_drivers()

with SyncCrazyflie(URI, cf=Crazyflie(rw_cache='./cache')) as scf:
    cf = scf.cf
    time.sleep(1.0)

    if not int(cf.param.get_value('deck.bcFlow2')):
        raise SystemExit('No Flow deck v2 detected — aborting.')

    lights = BeeLights(cf)
    lights.solid(AMBER)

    cf.supervisor.send_arming_request(True)
    time.sleep(1.0)

    try:
        with MotionCommander(scf, default_height=HEIGHT) as mc:
            time.sleep(2.0)  # let the hover settle before dancing

            for i in range(CYCLES):
                print(f'Figure-eight {i + 1}/{CYCLES}: waggle... loop left')
                waggle_run(mc, lights)
                return_loop(mc, lights, left=True)

                print(f'Figure-eight {i + 1}/{CYCLES}: waggle... loop right')
                waggle_run(mc, lights)
                return_loop(mc, lights, left=False)

            print('Dance done. Landing...')
    finally:
        time.sleep(1.0)
        lights.off()
        cf.supervisor.send_arming_request(False)
        print('Disarmed.')
