# ROS2_CONTRACT — the sim↔real Transport seam (bible §6.3)

> **Status: IMPLEMENTED and smoke-tested against real ROS 2 (2026-07-29).**
> `transport/ros2_bridge.py` exists and satisfies this contract; `FR-23`'s
> conformance tests are `tests/test_ros2_bridge.py` (35 tests, and they run with
> **no ROS 2 installed** — see §9). Verified end-to-end on this box against ROS 2
> **Jazzy** with `tools/ros2_bridge_smoke.py`: 13/13 legs, real `rclpy`, real
> `sensor_msgs/Image`, real QoS profiles, real DDS round-trip.
>
> ⚠ **Still not proven: Isaac as the publisher.** Both ends of the smoke test are
> ours, which isolates the bridge from Isaac's camera helper on purpose. Driving
> it from a playing sim (§6) is the next step up. **Slice 0 remains sim-native**
> (`CLAUDE.md` golden rule #5) — this seam is now available, not yet the default.
>
> The two questions this doc left open (§7 `capture` semantics, §8 `read_panel`)
> are decided below.

## 1. Why this exists

`Transport` (`transport/base.py`) is the one interface with two real
implementations in Slice 0: `sim_native.py` (default, in-process) and
`ros2_bridge.py` (topics). Both must carry **exactly the same information** so
the orchestrator (`orchestrator/mission.py`) never knows or cares which one is
underneath (bible §2.4). This doc is the contract `ros2_bridge.py` has to
implement to be a drop-in swap.

## 2. Topic table (bible §6.3)

| Topic | Type | Dir (from sim) | QoS |
|---|---|---|---|
| `/<robot_ns>/camera/image_raw` | `sensor_msgs/Image` | pub | **Sensor Data (Best Effort)** |
| `/<robot_ns>/camera/camera_info` | `sensor_msgs/CameraInfo` | pub | Sensor Data |
| `/<robot_ns>/cmd_vel` | `geometry_msgs/Twist` | sub | Reliable |
| `/<robot_ns>/pose` | `geometry_msgs/PoseStamped` | pub | Reliable |
| `/mission/fault` | custom (start: `std_msgs/String`, JSON body) | pub | Reliable |
| `/clock` | `rosgraph_msgs/Clock` | pub | — (sim time, if `use_sim_time` is set) |

`<robot_ns>` is one namespace per robot id in `mission.yaml`'s `fleet:` block
(`ground_bot`, `screen_drone`, `confirm_drone` — see §4).

## 3. `/mission/fault` payload = `FaultReport`

Locked now that `schema/pv_module.py`'s `FaultReport` dataclass exists (Track N,
`docs/TASKS.md` N1) — the run record's `fault_events` and this topic serialize
**the same shape**, so `run.py` and `ros2_bridge.py` never drift independently.

Slice-0 transport: `std_msgs/String` whose `data` field is `FaultReport.to_dict()`
JSON-encoded (upgrade path: a real `custom/FaultReport.msg` once this seam is
built and proven — not needed for Slice 0).

```json
{
  "panel_id": "R00-C002",
  "fault_type": "soiled",
  "confidence": 1.0,
  "note": "ground-truth confirm soiled",
  "timestamp": "2026-07-21T10:27:53+00:00",
  "panel_geo_position": [33.4484, -112.07395262881327, 331.0]
}
```

Field notes:
- `fault_type` — one of the fault taxonomy (§6.5): `healthy · soiled · hotspot
  · crack · string_dropout · diode_fault · shading · unknown`.
- `panel_geo_position` — `[lat, lon, elev]` or `null`; comes from
  `schema.local_to_geo`, so a fault here lines up with the same coordinate a
  real SCADA feed would report for the same panel (§6.2).
- Round-trip with `FaultReport.from_dict(json.loads(msg.data))` — implemented
  and tested in `tests/test_fault_report.py`; `ros2_bridge.py` should reuse
  `to_dict`/`from_dict`, not hand-roll the JSON shape again.

## 4. Namespacing

- One namespace per robot id from `mission.yaml`'s `fleet:` block, e.g.
  `/ground_bot/...`, `/drone1/...`, `/drone2/...` (matching the ids used
  throughout `orchestrator/mission.py`'s `Fleet` and `transport.pose(robot_id)`
  / `transport.capture(robot_id)` calls — `ros2_bridge.py`'s `robot_id` params
  map 1:1 onto these namespaces).
- Prefer explicit per-robot namespaces over Isaac's auto-namespace feature —
  the bible flags it as flaky in deep hierarchies (§6.3).
- `/mission/fault` and `/clock` are global (no robot namespace).

## 5. QoS — the RViz2 gotcha

Isaac Sim's ROS 2 camera bridge publishes with **Sensor Data QoS (Best
Effort)**. If you subscribe with the default Reliable QoS (e.g. RViz2's
default), **you will see nothing and get no error** — set the image display's
Reliability to **Best Effort** explicitly. `cmd_vel`, `pose`, and
`/mission/fault` are Reliable (control/state messages, not high-rate sensor
streams — dropping one matters more than for a video frame).

## 6. Timing gotcha

ROS 2 OmniGraph nodes (`OgnROS2CameraHelper`, etc.) only publish **after you
press Play** in Isaac Sim. A headless run must still trigger the equivalent of
Play (`SimulationApp`/timeline start) before expecting any topic traffic —
this is a Track S concern in `world/sim_runtime.py`, noted here because a
`ros2_bridge.py` smoke test that "sees nothing" is often this, not a bridge bug.

## 7. What `ros2_bridge.py` must implement

Same `Transport` interface as `sim_native.py` (`transport/base.py`):

| `Transport` method | ROS 2 realization | Status |
|---|---|---|
| `capture(robot_id)` | subscribe `/<robot_id>/camera/image_raw`; **block for a *fresh* frame, raise `FrameTimeout` on timeout** — decided, see below | ✅ |
| `pose(robot_id)` | subscribe `/<robot_id>/pose`, return the latest `Pose`; **last-seen is fine here** | ✅ |
| `read_panel(panel_id)` | **direct `PanelStore`, not a topic** (§8 option 2 — decided) | ✅ |
| `write_panel(...)` | publish a `FaultReport` (§3) on `/mission/fault` **and** write through to the store, so USD stays the source of truth (bible §2.3) | ✅ |
| `step(dt)` | spin callbacks once; advances **nothing** — the sim process owns real time here, unlike sim-native's single-process step | ✅ |

### `capture` is fresh-or-fail, not last-seen (decision)

Returning the last-seen frame would silently attribute one panel's pixels to the
next panel's verdict, and this project measures its headline KPIs off exactly
those pixels — a mis-attributed diagnosis is invisible in the numbers, whereas a
hang is debuggable. So `capture` returns only a frame it has not already served
(tracked by an internal receipt counter, not header stamps — Isaac's camera helper
does not guarantee those are monotonic across a Stop/Play), waits up to
`capture_timeout_s`, and then raises `FrameTimeout`. The error message names the
two usual causes (§5 QoS and §6 Play) because a bridge that "sees nothing" is
almost always one of them rather than a bug in the bridge.

`pose` is deliberately the opposite: a pose is a continuously-changing quantity,
so a slightly old one approximates the same thing, while a stale *frame* is a
different panel entirely.

### Subscribe eagerly

Pass the fleet's robot ids to `Ros2Transport(..., robot_ids=(...))`. Subscriptions
are otherwise created on first `capture`/`pose`, which races the publishers coming
up and can time out on frames already in flight. Eager subscription lets the
subscriptions exist **before Play** (§6), which is when publishing starts.

## 8. Open question (flag before implementing)

`read_panel` needs the **current panel state**, which lives on the USD stage,
not on any topic in the table above. Options for Track S to decide when
building `ros2_bridge.py`:
1. Add a topic/service exposing panel state (e.g. `/mission/panel_state`
   request-reply), or
2. Keep `read_panel`/`write_panel` as a direct (non-ROS) side-channel into the
   sim process even when `Transport` is otherwise ROS 2 — acceptable since USD
   is the source of truth and panel reads are not a real-time control path.

Default recommendation (Track N, non-binding): **option 2** — it's simpler and
doesn't invent a new topic for something that isn't really sensor/actuator
data. Record the actual decision here once made.

### Decision (2026-07-29): option 2, via a named `PanelStore` protocol

`read_panel` reads a `PanelStore` directly. Panel state is a **query**, not sensor
or actuator data, and a request-reply topic would have added a wire format with no
second implementation to justify it. `write_panel` is deliberately asymmetric: the
verdict is an **event**, which is what a topic is for, so it publishes on
`/mission/fault` *and* writes through to the store. Publishing only would let the
twin's own source of truth drift from what it told the rest of the fleet.

The store is a `typing.Protocol` (`ros2_bridge.PanelStore`: `read_panel` +
`write_panel`), so it is structural — `SimNativeTransport` already satisfies it
without inheriting anything, and on real hardware it would be backed by whatever
owns panel state there (a service, a database, SCADA). The bridge does not care
which, which is the point of naming it rather than hard-coding the sim.

## 9. Why the conformance tests need no ROS 2

`tests/test_ros2_bridge.py` runs on a machine with **no ROS 2 installed**, which is
most machines and all of CI. Two things make that possible, and both are
deliberate design constraints on `ros2_bridge.py`:

1. **Every `rclpy`/`sensor_msgs` import is inside the function that needs it.**
   Importing the module must never require ROS 2 — otherwise the Isaac-free suite
   breaks and the transport could not be offered as a config flip.
2. **`Ros2Transport` accepts an injected `node`.** All message-shape logic lives in
   module-level pure functions taking duck-typed objects, so a node-shaped double
   drives the whole class. A node double also supplies its own `spin_once`, since
   pumping is the node's concern.

That covers the details which are easy to get silently wrong — QoS selection, the
`Image` row stride (`step` may exceed `width*channels`; ignoring it shears the
image diagonally into something that still looks like a plausible photo),
quaternion→yaw, and the fault payload. `tools/ros2_bridge_smoke.py` covers the
wiring those tests cannot: real DDS, real QoS profiles, real message types.

⚠ Two traps found while proving it, worth not rediscovering:
- `PYTHONPATH=src python3 ...` after sourcing ROS 2 **replaces** the `PYTHONPATH`
  that `setup.bash` just populated, so `rclpy` vanishes. Use
  `PYTHONPATH="src:$PYTHONPATH"`.
- `rclpy` coerces `Image.data` to `array.array('B')` on assignment, so
  `msg.data == original_bytes` is False by *type* even when the content matches.
  `frame_from_image_msg` calls `bytes(msg.data)` rather than assuming numpy gets a
  buffer it likes.
