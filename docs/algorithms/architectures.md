# Architectures

Everything about how the simulation is built, in one place. Each tab holds a
complete document — the same ones that live on the
[`simulation`](https://github.com/TTZ-Unbemannte-Flugsysteme/Swarm_SummerSchool_2026/tree/simulation)
branch, rendered here so you can read them without cloning anything.

!!! tip "Reading these"
    Each document is embedded in its own frame and scrolls independently. Use
    **Open in a full tab** if you would rather have the whole window — the
    diagrams are wide, and several have their own contents list.

=== "Design rationale"

    Why the system is built this way: the hardware it stands in for, where the
    formation logic lives, how the leader's position reaches the followers, and
    the ten sequence diagrams that go with those decisions. Start here if you
    want to understand the *choices* rather than the commands.

    [Open in a full tab](architecture.html){ .md-button .md-button--primary }

    <iframe src="../architecture.html" width="100%" height="900px" loading="lazy"
            title="Design rationale for the leader-follower simulation"
            style="border:1px solid rgba(128,128,128,.3);border-radius:8px;margin-top:16px"></iframe>

=== "Workshop"

    The teaching version: what you are building, software-in-the-loop versus
    hardware-in-the-loop versus real flight, why IP addresses and ports decide
    whether any of it works, and a 16-slide deck with a presenter mode and
    speaker notes.

    [Open in a full tab](workshop.html){ .md-button .md-button--primary }

    <iframe src="../workshop.html" width="100%" height="900px" loading="lazy"
            title="Workshop teaching material"
            style="border:1px solid rgba(128,128,128,.3);border-radius:8px;margin-top:16px"></iframe>

=== "Quickstart"

    Clone, install, fly — on one page, with copy buttons on every command.
    Budget 30–40 minutes, nearly all of it the ArduPilot compile running
    unattended.

    [Open in a full tab](quickstart.html){ .md-button .md-button--primary }

    <iframe src="../quickstart.html" width="100%" height="900px" loading="lazy"
            title="Quickstart: clone, install, fly"
            style="border:1px solid rgba(128,128,128,.3);border-radius:8px;margin-top:16px"></iframe>

=== "Ground station"

    Attaching QGroundControl so one link shows the whole swarm, why that link
    has to be added by hand, and the trap where QGroundControl's automatic
    connection silently stops the leader arming.

    [Open in a full tab](ground-station.html){ .md-button .md-button--primary }

    <iframe src="../ground-station.html" width="100%" height="900px" loading="lazy"
            title="QGroundControl on the swarm"
            style="border:1px solid rgba(128,128,128,.3);border-radius:8px;margin-top:16px"></iframe>

=== "Diagrams on white"

    Every diagram from the documentation on a white background, whatever theme
    your machine is set to — for pasting into slides, a report or a printed
    handout. Right-click a diagram to save it as SVG.

    [Open in a full tab](diagrams-white.html){ .md-button .md-button--primary }

    <iframe src="../diagrams-white.html" width="100%" height="900px" loading="lazy"
            title="Every diagram on a white background"
            style="border:1px solid rgba(128,128,128,.3);border-radius:8px;margin-top:16px"></iframe>

## Running it

The code is on the `simulation` branch, not here. The quickstart tab above is
the fastest route; in short:

```bash
git clone -b simulation https://github.com/TTZ-Unbemannte-Flugsysteme/Swarm_SummerSchool_2026.git
cd Swarm_SummerSchool_2026
./install.sh
flyit
```

Then open **<http://127.0.0.1:8760>** for the dashboard.
