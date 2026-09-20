# Simulation Testing

Before flying real hardware, you must test your algorithms in a simulated environment to prevent crashes and verify logic safely.

## Get the Simulation Code

The simulation code lives on the **`simulation`** branch of this repository, separate from `main` (which holds this documentation).

[Browse the `simulation` branch](https://github.com/TTZ-Unbemannte-Flugsysteme/Swarm_SummerSchool_2026/tree/simulation){ .md-button .md-button--primary }

Clone just that branch into its own folder:

```bash
git clone -b simulation --single-branch \
  https://github.com/TTZ-Unbemannte-Flugsysteme/Swarm_SummerSchool_2026.git swarm-simulation
cd swarm-simulation
```

If you already have the repository checked out, fetch the branch and switch to it:

```bash
git fetch origin simulation
git switch simulation
```

!!! note
    The branch is under active development. Run `git pull` at the start of each session to pick up the latest changes.

## Simulator Setup

We recommend using **Gazebo** (or Gazebo Classic depending on your ROS version) paired with Software In The Loop (SITL).

### ArduPilot SITL Setup
1. Clone the ArduPilot repository and run the setup scripts for your OS.
2. Navigate to the ArduCopter directory:
   ```bash
   cd ardupilot/ArduCopter
   ```
3. Run the SITL environment (e.g., paired with Gazebo):
   ```bash
   sim_vehicle.py -v ArduCopter -f gazebo-iris --console --map
   ```
4. You should see a 3D environment open with a drone model, alongside a MAVProxy console and map.

## Running Algorithms

1. **Connect your Algorithm to the Simulator**: Ensure your ROS nodes or Python scripts (using MAVSDK/DroneKit) are pointing to the simulator's IP address (typically `localhost` or `127.0.0.1:14540`).
2. **Takeoff Sequence**: Test basic commands like arming, taking off, and holding a hover.
3. **Complex Logic**: Execute your specific Swarm or navigation logic. Monitor the console output and the drone's behavior in Gazebo.

## Verification Checklist

Before moving to real hardware, ensure:
- [ ] The algorithm does not send conflicting commands.
- [ ] Emergency stop / Land commands work reliably.
- [ ] The drone correctly handles losing communication (failsafe testing).
