"""Step 1 — find the Crazyflie.

Plug in the Crazyradio 2.0, switch the Crazyflie on, put it on a flat
surface and leave it still. Then run:  python 1_scan.py
"""
import cflib.crtp

from uri import URI

cflib.crtp.init_drivers()

print('Scanning for Crazyflies...')
found = cflib.crtp.scan_interfaces()

# The scan only looks for the factory address. If uri.py points at a
# different one, look for that too.
own_address = URI.rsplit('/', 1)[-1]
if own_address.upper() != 'E7E7E7E7E7':
    found += [f for f in cflib.crtp.scan_interfaces(int(own_address, 16))
              if f not in found]

if not found:
    print('\nNothing found. Check:')
    print('  - Crazyradio 2.0 is plugged in (try a different USB port / no hub)')
    print('  - the Crazyflie is powered on and its LEDs are lit')
    print('  - the battery is charged')
    print('  - the drone may use a non-default address: connect it over USB')
    print('    and read the config block (see README)')
else:
    print(f'\nFound {len(found)} Crazyflie(s):')
    for uri, comment in found:
        print(f'  {uri}   {comment}')
    print('\nCopy the URI above into uri.py if it differs from the default.')
